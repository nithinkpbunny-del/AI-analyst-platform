**Next: M19 (Hardening, then the Phase 3 review)** when the human says "continue". M17 (Industry templates) and M18
(Billing hooks, no payments) were built together on branch `m17-industry-templates` and wait for the human's check and
commit (suggested: "M17: Industry templates" and "M18: Billing hooks (no payments)", or one combined commit).
M1 to M16 are committed and merged to main. **M10b (public website, then the Phase 1 review) is still not built**; the
human chose to go on first, ask before skipping it for good.
**Unfinished work for M17/M18:** no real AI call was made (the Ask prompt with the industry section, the dictionary
draft's `typical_metrics` hint and the AI-written text around templates are tested with the fake model only).
The template texts (KPIs, playbook tweaks, default elasticities) are written from general business knowledge, not from
data: have someone who knows each trade read them before launch (D115). **Nothing in M18 can charge anyone**: when a
payment provider is chosen, D118 lists everything that is a stand-in and must be replaced.
**Unfinished work for M16:** real TallyPrime and the Windows build are untested (no Windows PC, no Tally here); see
docs/SETUP_TALLY.md "What is least certain" and the M16 section below. Before launch: build `tally-bridge.exe` on
Windows, sign it, pair one real company and compare a month's day book, a ledger and the trial balance with Tally.
**Carried over** (Phase 2 review, M10b and later):
- **Never run against a real model yet** (M8-M10): the agent, the briefing writer and `make eval` are tested with the
  fake client only. The real-AI checks of M10 (AI-written briefing, health check, export and share of a real answer, a
  long task) are still untested (Gemini was overloaded). Phase 1 sign-off needs >= 85% on `make eval` with a real model
  (BUILD_SPEC 12.2): run it by name when you are ready (`make eval LIST=1` lists the 48 questions).
- M10b's final design QA (11a.9) should look again at the Today, Briefings, Tasks and report screens on phones.
- The app shell is at **179.2 KB** of its 200 KB budget after M18 (177.4 after M16): Connections, Dictionary,
  Dashboard and Goals load on demand with their own strings files (`locales/en/*.ts`, D99). New screens keep loading
  lazily; `src/locales/locales.test.ts` checks every literal key exists.
- New agent tools go in `app/agent/tools.py` (`SPECS` + `HANDLERS`); questions and long tasks run through
  `agent.service.run_agent`. Every number comes from code and cites a result id (D67).
- Every new tenant table needs RLS, the `tenant_isolation` policy and `app_tenant` grants; every new route goes in
  `tests/api/test_route_matrix.py` (`CASES`, `PERSONAL`, `DATA_ROUTES` or `STATEFUL`, the last three tested in their
  own files).
- Every LLM call goes through `keys.resolve(...)` and is metered (D42); no real LLM calls in builds or tests.
- Open privacy questions for M19 (D85, approved by the Owner 2026-11-13: keep accounts and keep earlier activity-log
  entries on workspace deletion for now): deleting accounts that belong to no workspace; a retention limit for the
  activity log.
- Dictionary metric label confirmed: it stays "Checked" (D88); no code change.
- Real Razorpay is untested (no key used): the connector is tested against a fake Razorpay in Razorpay's format. When you
  have a key, try a **test-mode** key first (Connections > Add data > Connect Razorpay).
- **Real Shopify is untested** (no app credentials, no store): the connector is tested against a local imitation of
  Shopify's OAuth and Admin GraphQL API whose schema copy was written from Shopify's documentation. Before launch,
  create the app (docs/SETUP_SHOPIFY.md Part B), connect a development store once and compare a few orders with the
  Shopify admin. Shopify's expiring offline tokens are not used yet (D92).
- **Real Zoho Books is untested** (no Zoho client): the connector is tested against a local imitation of Zoho's
  accounts server and Books API written from Zoho's documentation. The profit and loss report's format and the
  `last_modified_time` filter are the least certain parts (D96, D98). Before launch, create the client
  (docs/SETUP_ZOHO.md Part B), connect a test organisation and compare invoices and a month's P&L with Zoho.
- **Real customer databases are untested outside the test container and the demo containers:** PostgreSQL is tested
  against a real PostgreSQL 16 (test container and the `demo-postgres` container), MySQL against the `demo-mysql`
  container (MySQL 8.4) by hand and a fake in the automated tests. Try a managed database (RDS, Cloud SQL) with SSL
  "certificate checked" before launch. SSH tunnels are not built (D100).
- `make dev` now also starts `demo-postgres` (127.0.0.1:5433) and `demo-mysql` (127.0.0.1:3307), development only.
- Small fix asked for later: removing a source and uploading the same file again loses its dictionary edits (D76).
- Goals and dashboard tiles have never run against real customer data or a real model's answers: pinning is tested
  with answers made by the fake model and the real tools. How often real answers carry a key figure the tile can find
  again (D105) is worth a look in the review.
**Deferred:** multilingual answers, briefings and UI translations (D41): everything is in English, all UI text in
`locales/en.ts` and `locales/en/*.ts`. What-if levers for stock, staffing and product mix (D71).

## Milestone checklist (BUILD_SPEC §14)

### Phase 1: MVP
- [x] M1: Foundation
- [x] M1b: Design system + app shell
- [x] M2: Auth core
- [x] M3: 2FA, Google sign-in, devices
- [x] M4: Orgs, roles, invites, RLS
- [x] M5: Secrets, LLM layer, plans, usage
- [x] M6: Connections framework + Excel/CSV + Google Sheets
- [x] M7: Zero setup: profiler + business dictionary
- [x] M8: Agent core + Ask UI (multilingual answers deferred, D41)
- [x] M9: What-if simulator
- [x] M10: Briefings, long tasks, exports, sharing, eval
- [ ] M10b: Public website (then Phase 1 review, stop) (skipped for now by the human's choice, still to do)

### Phase 2
- [x] M11: Razorpay connector
- [x] M12: Shopify connector
- [x] M13: Zoho Books connector
- [x] M14: PostgreSQL/MySQL connector
- [x] M15: Goals, pinned dashboard, UI translations (translations deferred, D41) (then Phase 2 review, stop)

### Phase 3
- [x] M16: Tally bridge
- [x] M17: Industry templates
- [x] M18: Billing hooks (no payments)
- [ ] M19: Hardening (then Phase 3 review, stop)

---

## M1: Foundation (done)

**Built**
- `make dev` starts api, worker, beat, web, postgres, redis, storage (RustFS, S3-compatible), mailpit, plus a
  one-shot bucket creator. It waits until everything is healthy. Ports are bound to 127.0.0.1 only.
- FastAPI app factory with `/health` (liveness) and `/health/ready` (Postgres, Redis, storage; 503 on failure),
  request-id middleware, CORS for the web app, OpenAPI at `/docs`.
- Settings from env (`app/config.py`) covering BUILD_SPEC §16 and the §6.5 safety limits. Secrets are
  `SecretStr`; production refuses weak `MASTER_KEY`/`JWT_SECRET`.
- Logging with redaction on every handler (exact secret values + token/key/URL-password/email patterns); JSON
  logs in production.
- Alembic set up; migration `0001` enables `citext`. Runs automatically when the api container starts.
- Celery app with a `ping` task and beat heartbeat; UUIDv7 id helper; S3 client + bucket helper.
- Minimal React 18 + TS + Vite page that shows API status and each readiness check (design system is M1b).
- CI (`.github/workflows/ci.yml`): backend ruff/mypy/pytest, frontend eslint/prettier/tsc/vitest/build,
  compose-file validation.
- `eval/generate_fixtures.py` (seed 42) writes the UrbanCart sources (`shopify_orders/customers/products.csv`,
  `razorpay_payments.csv`, `marketing_sheet.xlsx` with quarterly tabs + Targets, `zoho_budget_expenses.xlsx`)
  and `ANSWER_KEY.md` with every planted finding measured from the data. `backend/tests/eval/test_fixtures.py`
  checks all 8 findings, integrity and determinism, independently of the generator.

**Test status:** backend 82 tests passed, ruff + mypy (strict) clean. Frontend 6 tests passed, eslint/prettier/tsc
clean, build OK.

**How to try it:** see the testing steps given at the end of M1 (also in README.md).

**Known gaps / notes**
- The route-test rule (401/403/404-cross-tenant) doesn't apply yet: the only routes are public health checks.
  It starts with M2/M4.
- Playwright, Storybook and design tokens were not part of M1 (added in M1b).
- Starlette prints a deprecation warning about `httpx` in the test client; harmless for now.
- Deviations and interpretations are recorded in `docs/DECISIONS.md` (D1 RustFS instead of MinIO, D6 fixture
  calibration).

---

## M1b: Design system + app shell (done)

**Built**
- `design/tokens.json` is the single source. `make tokens` generates the CSS variables (light/dark; follows the
  device or a forced choice) and the Tailwind v4 theme, which removes Tailwind's defaults so only tokens can be used.
- Self-hosted fonts: Bricolage Grotesque (display) and Noto Sans with Devanagari, Bengali, Gujarati, Kannada,
  Malayalam, Tamil and Telugu. Indic text gets line-height 1.7.
- Signature components: Highlight (haldi sweep, one per view), AnswerSheet, SourceMark (provenance popover),
  QualityBanner, ProgressSteps, KpiTile (+ Sparkline), ScenarioCard (slider, range whisker, assumptions,
  confidence), ConnectionCard, EmptyState. Primitives: Button, Popover, Slider, Sheet. Formatters: `formatINR`
  (₹8,450 / ₹12.4 L / ₹1.24 Cr), `formatPct`, `formatNumber`, `formatRelativeTime`.
- Storybook 10 (`make storybook`): every component in Light, Dark and Phone, plus a Foundations/Tokens page
  (colours, type scale, all Indic scripts, radii) and the app shell. Accessibility addon included.
- App shell: desktop sidebar (collapsible, remembered), tablet icon rail, phone bottom tabs (Today, Ask, Dashboard,
  More sheet) with a floating Ask button. Skip link, 2 px focus ring, i18next wired (English strings in
  `src/locales/en.ts`), TanStack Query, theme setting. Placeholder screens for all 11 nav items, each with a
  one-sentence empty state and one action. Settings shows the theme choice and system status.
- Checks: contrast test over every colour pair in both themes; generated-files-up-to-date check; 46 Playwright
  baselines (11 screens × light/dark × desktop/mobile, plus the "More" sheet); axe WCAG 2.2 AA scan of every
  screen in all 4 projects; layout checks at 375/768/1280/1536 px (no horizontal scroll, rail/sidebar/tabs, 44 px
  touch targets); shell JS budget check (111.5 KB of 200 KB gzipped). CI runs all of it, including Playwright
  in its Docker image.

**Test status:** backend 82 passed (unchanged). Frontend: vitest 129 passed; eslint, prettier, tsc and
tokens:check clean; build and Storybook build OK. Playwright 99 passed (29 skipped by design: phone-only and
desktop-only checks).

**Known gaps / notes**
- During development one Playwright run had a single failure that I could not reproduce afterwards (396
  repeated runs passed). If CI ever shows a flaky snapshot, look at the "More" sheet screenshot first.
- Remaining shadcn primitives (Toast, Dialog, Tabs, Table, Select, DatePicker) are added with their first screen
  (DECISIONS D11). The command palette and the `/` shortcut arrive with Ask (M8).
- The role is fixed to Owner until auth (M2).

---

## M2: Auth core (done)

**Built**
- Database (migration `0002`, reversible): `organizations`, `users` (citext email), `memberships`, `sessions`,
  `refresh_tokens`, `user_tokens` (email links), `consents`, `audit_log` (append-only: triggers reject UPDATE,
  DELETE and TRUNCATE).
- `/auth` API: `csrf`, `signup`, `verify-email`, `resend-verification`, `login`, `refresh`, `logout`,
  `forgot-password`, `reset-password`, `me`. One error shape: `{"error": {"code", "message", "fields"}}`.
- Sign-up creates the organisation, makes the person its Owner, records consent (policy version) and emails a
  confirmation link. Login is refused until the email is confirmed.
- Passwords: argon2id; at least 10 characters; checked against the bundled NCSC list of the 100k most common
  passwords; no composition rules.
- Sessions: 10-minute access JWT kept in memory. HttpOnly SameSite=Strict refresh cookie, rotated on every use.
  Reuse of an old refresh token ends the whole session. 30-minute idle and 12-hour absolute limits are enforced
  on the server. Double-submit CSRF protects refresh and logout. Role and membership are re-read on every request,
  so removal takes effect immediately.
- Rate limits: 5 failed sign-ins per email+IP per 15 min, then lockouts that double. Limits on email-sending
  requests per IP and per recipient. No account enumeration: same answers and similar timing.
- Password reset: single-use 60-minute link; signs out every device; sends a "password changed" email.
- Activity log entries for sign-up, consent, email confirmation, every login success/failure (with reason),
  logout, token reuse, session timeouts and password resets.
- Emails (HTML + text) through SMTP, i.e. Mailpit locally.
- Web app: sign in, create account, check email, confirm email, forgot password and reset password screens.
  Route guard (signed-out visitors go to sign in and come back afterwards). Session restored on reload.
  "Why you were signed out" note. Client-side idle sign-out and activity pings. Sign out in the sidebar,
  the phone "More" sheet and Settings (new Account section). `useRole()` now uses the real role.
- Design decisions D14–D24 in `docs/DECISIONS.md`.

**Test status:** backend 204 passed (API flows, security attacks incl. refresh-token reuse, forged/tampered JWTs,
append-only audit, no secrets in DB or logs, migration round-trip); ruff + mypy strict clean. Frontend: vitest 162
passed; eslint, prettier, tsc, tokens:check clean; build OK (shell 119.3 KB of 200 KB gzipped); Storybook builds.
Playwright 151 passed (29 skipped by design), now including 24 sign-in-screen baselines and their axe checks.
A live check against `make dev` (sign-up → Mailpit → confirm → login → refresh → replay of the old refresh
token) behaved as expected.

**How to try it:** see the testing steps given at the end of M2 (summary: `make dev`, open
http://localhost:5173, create an account, confirm it from Mailpit at http://localhost:8025, sign in).

**Known gaps / notes**
- 2FA (mandatory for Owners), Google sign-in and new-device alerts are M3, so Owners currently sign in with a
  password only. The `sessions.device_id` column is ready for M3.
- Route-test rule: auth routes are public or self-only, so 401 is tested. 403 (wrong role) and 404
  (cross-tenant) begin with the first role-restricted and tenant-owned routes in M4, together with RLS and
  `require()`.
- Privacy policy and terms links point to the marketing site, which is built in M10b.
- Behind a load balancer the client IP needs trusted proxy headers (M19).
- Visual-snapshot tolerance (0.2% of pixels) is loose enough to miss small changes such as one nav item.
  `make e2e-update` now rewrites every baseline, so baselines match the code exactly, but a check alone
  can still miss small changes. Consider a tighter threshold when it proves stable.
- Backend tests need Docker running (testcontainers), as CI already has.

---

## M3: 2FA, Google sign-in, devices (done)

**Built**
- Database (migration `0003`, reversible): `two_factor`, `two_factor_codes`, `login_challenges`, `known_devices`;
  `user_tokens` gains the "This wasn't me" link purpose.
- **Two-step sign-in.** After a correct password (or Google), the API returns either a session or a challenge:
  "enter your code", or for an Owner without 2FA, "set it up now". The session exists only after the second
  step. Methods: an **authenticator app** (TOTP, QR code + typed key, ±1 step, no code reuse) or **6-digit
  email codes** (10 minutes, single use, 5 attempts). **10 recovery codes** each work once. Mandatory for
  anyone who owns a workspace; optional for everyone else.
- Limits: 5 wrong codes per sign-in attempt; 10 wrong codes in 15 minutes lock code entry for that person
  (doubling); 10 code emails per person per hour; 30 s between resends.
- Signed-in settings (`/auth/2fa/*`): turn on, change method, create new recovery codes, turn off (not for
  Owners). Changes need a code from the current method. Every change emails the person and is logged.
- **Google sign-in** (`/auth/google/*`): OIDC with PKCE, state and nonce; ID token checked (signature, issuer,
  audience, expiry, nonce, verified email). Linking follows the spec, and an unconfirmed password is removed when
  Google proves the email. New people name their workspace and accept the terms. Google still goes through our
  2FA. With no Google credentials, development uses a **local stand-in page** (`docs/SETUP_GOOGLE.md`).
- **Devices:** signed device cookie. A sign-in from a new device emails "New sign-in" (time in IST, browser, IP)
  with a **"This wasn't me"** link that signs out every device. Settings lists **signed-in devices**, with "Sign
  out" for one or all others.
- Envelope encryption module (`app/security/crypto.py`, AES-256-GCM + HKDF from `MASTER_KEY`), used now for
  TOTP secrets and by M5 for connection secrets.
- SMS codes: interface only (`app/auth/code_delivery.py`), as the spec says.
- Web app: "Continue with Google" on sign-in and sign-up; code step (`/signin/verify`) with "Use a recovery code
  instead" and "Send a new code"; set-up during sign-in (`/signin/setup`): choose a method, QR code or emailed
  code, then recovery codes (copy, download, "I have saved them"). Google return and sign-up pages, "This
  wasn't me" page, and a forced set-up page for signed-in Owners without 2FA. Settings gains "Two-step
  verification" and "Signed-in devices". New tokens `qr-dark`/`qr-light`. Storybook stories for the new
  screens, recovery codes and QR code.
- Activity log: `auth.2fa.*`, `auth.google.linked`, `auth.device.new`, `auth.device.reported`,
  `auth.session.revoked`, `auth.session.revoked_others`; `auth.login.success` now records method, second factor
  and new device; new failure reasons for codes.
- Design decisions D25–D33 in `docs/DECISIONS.md`.

**Test status:** backend 314 passed (2FA flows, TOTP replay, lockouts, recovery codes, step-up, Google flow with
every callback failure, ID-token checks with locally signed tokens, device alerts, "This wasn't me", sessions
incl. 404 for someone else's, secrets at rest, fail-closed decryption, no codes in logs, migration round-trip);
ruff + mypy strict clean. Frontend: vitest 185 passed; eslint, prettier, tsc, tokens:check clean; build OK (shell
127.5 KB of 200 KB gzipped); Storybook builds. Playwright 203 passed (29 skipped by design), including new
baselines for the code step, set-up choice, QR step, recovery codes, forced set-up and "This wasn't me", with axe
checks. A live run against `make dev` passed 21 checks: sign-up, Mailpit, TOTP set-up, second device, replay
refused, new-sign-in email, "This wasn't me", Google stand-in sign-up.

**How to try it:** see the testing steps given at the end of M3 (summary: `make dev`, create an account, confirm it
in Mailpit, sign in, set up an authenticator app or email codes, save the recovery codes; sign in from a private
window to get the "New sign-in" email).

**Known gaps / notes**
- No owner-only actions exist yet. M4's `require()` must check `two_factor_enabled` for owner permissions
  (D25).
- Real Google sign-in needs your OAuth client (`docs/SETUP_GOOGLE.md`). Until then the stand-in is used locally.
- The IP address in "New sign-in" emails and the devices list is the direct peer. Behind a load balancer it
  needs trusted proxy headers (M19, as noted in M2).
- The browser description is a small built-in user-agent parser: good for "Chrome on Windows", not for exotic
  browsers.
- Workspace switching (choosing which organisation to sign in to) is still the M2 behaviour: first membership.


---

## M4: Orgs, roles, invites, RLS (done)

**Built**
- **Permission matrix (BUILD_SPEC 6.2) and `require()`** (`app/auth/permissions.py`): 16 permissions for 3
  roles, stored as data. Routes use `requires(Permission.X)` and never compare roles themselves. An Owner without
  two-step verification may do nothing until it is on (D25). Viewers may export only items shared with them.
  `/auth/me` and every sign-in answer include `permissions`, and the web app shows its menu from them.
- **Row-Level Security** (migration `0004`, reversible): the restricted `app_tenant` role, plus RLS and a
  `tenant_isolation` policy on `organizations`, `memberships`, `invitations`, `sessions`, `consents`, `audit_log`
  and `login_challenges`. Workspace requests run every transaction as `app_tenant` with `app.org_id` set
  (`SET LOCAL`). With nothing set, no rows are visible. A trigger keeps at least one active Owner per workspace.
- **Team API** (`/team/*`, Owners): list members (role, 2FA, joined), change role (emails the person), remove
  (signs them out of that workspace at once and emails them), invite by email as Analyst or Viewer (7-day
  single-use link, 30 per hour per workspace), list, resend (new link; the old one stops working) and cancel
  invitations. You cannot change your own role or remove yourself.
- **Accepting invitations** (`/invitations/*`): preview; new person (name + password + consent, then signed in);
  existing account with its password (normal lockouts and second step); or one click when already signed in as
  that address (the session moves to the new workspace). Wrong account: 403 with an explanation.
- **Workspaces:** `POST /auth/switch-organization` moves the session to another workspace you belong to. Old
  tokens refresh into the new workspace. `GET/PATCH /org` reads or changes the workspace name and time zone
  (Owners change them).
- **Activity log API** (`/audit`, `/audit/export.csv`): Owners see everything in the workspace, Analysts their
  own entries, Viewers nothing (403). Filters: person, kind, dates in the workspace's time zone. Keyset pages. CSV
  export with formula-injection protection; every export is logged.
- **Activity-log entries:** `team.invite_sent|resent|revoked|accepted`, `team.role_changed`,
  `team.member_removed`, `org.settings_changed`, `auth.workspace_switched`, `audit.exported`.
- **Emails:** invitation, "Your role changed" (says when an Owner must turn on 2FA), "Your access was removed".
- **Web app:**
  - **Team** screen: people as a ledger table with a role picker and Remove (with a confirmation dialog); an
    invite form with what each role can do; and open invitations with "Send again" and "Cancel invitation".
  - **Activity** screen: filters, plain-language entries, "Show older activity", Export CSV. Analysts get a note
    that they see their own activity.
  - **Accept invitation** page for each case.
  - **Workspace switcher** in the sidebar, rail and phone header. The last workspace is remembered for the next
    sign-in.
  - **Settings → Workspace**: editable for Owners, read-only for others.
  - Menu: Activity is shown to Analysts as well, Settings to everyone (D37).
  - New primitives: Dialog/ConfirmDialog, Select, `danger` button. Storybook stories for them, the Team and
    Activity screens and the switcher.
- Design decisions D34–D40 in `docs/DECISIONS.md`.

**Test status:**
- **Backend:** 578 passed; ruff and mypy (strict) clean. Covered:
  - RLS isolation per table, refused cross-workspace writes, fail-closed without a workspace, no leaks through
    the connection pool, the tenant role's limits, the keep-an-Owner trigger, and a schema check that every
    `org_id` table has the policy.
  - The permission matrix cell by cell.
  - A route matrix over **every** API route: 401, 403 per role, 403 for an Owner without 2FA, 404 for another
    workspace's ids, 404 for missing ids, 422, success, and no data leaking between workspaces.
  - Team, invitation, activity-log and workspace flows; security tests for invitations and removal; the
    migration round trip.
- **Frontend:** vitest 216 passed; eslint, prettier, tsc and tokens:check clean. Build OK (shell 145.5 KB of
  200 KB gzipped). Storybook builds.
- **Playwright:** 237 passed (31 skipped by design). New baselines: Team, Activity, Settings, accept-invite, the
  remove dialog, an Analyst's activity and the open workspace switcher, all with axe checks.
- **Live check against `make dev`:** 19 of 19 passed (invite, accept, roles, removal, cross-workspace 404, CSV).

**How to try it:** see the testing steps given at the end of M4. In short: `make dev`; sign in as an Owner; open
Team and invite someone; open the email in Mailpit (http://localhost:8025) in a private window and accept it;
change their role or remove them; then look at Activity.

**Known gaps / notes**
- Per-connection grants (R15 "Owner can restrict which connections each user can see") arrive with connections in
  M6. The grants editor and the `connection_grants` table come then.
- Sharing (needed for "Viewers export shared items only") is M10. The rule is already in `require()`.
- A new invitee cannot use "Continue with Google" on the invitation page. They set a password, and Google links
  automatically later (D36).
- Org deletion and data export (DPDP) are M10. "Leave workspace" for non-Owners is not built (not in the spec).
- Production needs the `app_tenant` role and the connecting role's membership in it (D34, M19 deploy guide).
- The full-page screenshots show the sidebar ending at the first screen height on long pages. It is sticky, so in
  a real browser it stays in view. This has been the case since M1b.


---

## M5: Secrets, LLM layer, plans, usage (done)

**Built**
- **Database** (migration `0005`, reversible): `secrets`, `llm_keys` and `usage_events`. Each has RLS, the
  `tenant_isolation` policy and least-privilege grants: secrets are never updated in place, and usage history is
  insert-only for workspace requests.
- **Envelope encryption** (`app/security/crypto.py`, `app/security/secrets.py`): a fresh AES-256-GCM data key per
  secret, wrapped by `MASTER_KEY` (development) or AWS KMS (`KMS_KEY_ID`, production), behind one `KeyWrapper`
  interface. Each secret is bound to its workspace, row and purpose. Secrets are write-only: no API returns
  them.
- **LLM layer** (`app/llm/`):
  - One interface, `generate(system, messages, tools, max_tokens)`, with normalised text, tool calls, stop
    reason and usage.
  - The **Gemini** adapter follows all six pitfalls of BUILD_SPEC 7.2: `v1beta`, one long-lived client, replay
    of the original parts with thought signatures, `parameters_json_schema` with automatic calling off,
    generated ids for calls without one, and models listed from the key. It retries 429/503 after 5/15/30 s
    (honouring `retryDelay`), but not an exhausted daily quota, an invalid key or a 504.
  - The **Claude** adapter: native tool use, `max_retries=3`, replay of the original blocks, and server-side
    refusal fallbacks on the models that support them.
  - **Model fallback:** a model the key lists but the provider refuses (404) or that stays overloaded is skipped,
    and the next best model is used.
  - Errors map to plain kinds: invalid key, quota, rate limited, model not found, unavailable, other.
  - **Fake LLM** (`FakeLLM`, `FakeLLMFactory`): every test uses it; no test calls a provider.
- **Plans and keys** (`app/llm/keys.py`):
  - Managed AI uses the platform key from `.env` after checking the monthly limit.
  - BYOK decrypts the workspace's own Gemini or Claude key.
  - A key is checked with the provider (a model list, no tokens) before it is saved.
- **Usage** (`app/llm/usage.py`): one `usage_events` row per LLM call, with tokens, estimated cost
  (`app/llm/pricing.py`), provider, model and key source. `MeteredLLM` records automatically. The Managed limit
  counts question and long-task runs on the platform key in the workspace's calendar month (D42).
  `ensure_within_limit` is ready for M8 to call before each question.
- **API** (Owners, `org.manage`):
  - `GET /org/ai`: the plan and the key's last 4 characters.
  - `PUT /org/ai/plan`: switch between Managed and the workspace's own key.
  - `PUT` and `DELETE /org/ai/key`: save or remove the workspace's key.
  - `POST /org/ai/check`: test the connection, 10 per hour.
  - `GET /org/usage?month=YYYY-MM`: totals, per person and the last 6 months.
- **Activity log:** `org.plan_changed`, `llm.key_set` and `llm.key_removed`. They contain the provider and the
  last 4 characters, never the key.
- **Web app** (Settings):
  - **AI plan:** choose Managed AI or your own key; add, replace or remove the key (password field, show/hide,
    checked before saving, shown as "••••x7Qa"); the privacy notice for each plan; "Test AI connection".
  - **AI usage:** month picker; a meter of questions used against the limit (caution colour at 80%, loss colour
    at 100%); questions, briefings, tokens and estimated cost; a per-person ledger; the last six months.
  - Analysts and Viewers see only which plan is in use. The activity log describes the new entries.
  - All text is in `locales/en.ts` (D41). Storybook has stories for the new sections.
- **`make llm-check`**: one real tool-calling round trip with the key in `.env`. It is opt-in, writes nothing and
  never prints the key. Setup guide: `docs/SETUP_AI.md`.
- The log redactor recognises Google's newer `AQ.`-style keys.
- Design decisions D42–D48 in `docs/DECISIONS.md`.

**Test status:**
- **Backend:** 770 passed (578 in M4); ruff and mypy (strict) clean. Covered:
  - The Gemini and Claude adapters against stubs that return the SDKs' real types and raise their real errors:
    every pitfall of BUILD_SPEC 7.2, retries and their delays, the 504 rule, model fallback and error kinds.
  - Envelopes: tampering, wrong key, wrong context, KMS against a stand-in client.
  - The secret store in Postgres; usage counting and the time-zone month; the limit; key resolution.
  - The AI and usage routes (route matrix and behaviour); rate limits.
  - RLS on the new tables; append-only usage; no key in any response, log or activity entry.
  - Deleting a workspace cascades cleanly; the migration round trip.
- **Frontend:** vitest 233 passed; eslint, prettier, tsc and tokens:check clean. Build OK (shell 150.5 KB of
  200 KB gzipped). Storybook builds.
- **Playwright:** 237 passed (31 skipped by design). The four Settings baselines (light/dark × desktop/phone)
  were re-recorded and reviewed, and axe finds no problems on Settings with the new sections.
- **Real provider:** `make llm-check` against Gemini with the `.env` key: a two-call tool round trip with thought
  signatures replayed, answered correctly (`gemini-3.5-flash`). The automatic fallback was also seen live:
  `gemini-2.5-flash` was refused (404) and `gemini-3.8-flash` was overloaded (503).
- **Live check against `make dev`:** 12 of 12 passed:
  - sign-up, Mailpit, email two-step set-up;
  - `GET /org/ai`, and usage at 0 of 500;
  - real Google rejected a made-up key (422), and switching without a key was refused;
  - "Test AI connection" answered "OK" from `gemini-3.6-flash` after the automatic fallback past a refused
    model and two models whose daily quota was used up;
  - the check was recorded as usage but not as a question;
  - the pasted key appears in neither the activity log nor the API container's logs.

**How to try it:** see the testing steps given at the end of M5. In short:
1. `make dev`.
2. Sign in as an Owner and open **Settings → AI plan**.
3. Press **Test AI connection**: Gemini answers with your `.env` key.
4. Try saving a made-up key: Google rejects it and nothing is stored.
5. Look at **AI usage** and the **Activity** screen.

**Known gaps / notes**
- No questions exist yet, so the monthly limit is not met in practice until the Ask screen (M8). It is enforced
  by `keys.resolve(...)` and fully tested.
- **Gemini models on your key (seen 2026-11-09):**
  - `gemini-2.5-flash` (your `GEMINI_DEFAULT_MODEL`) is still listed but refused: "no longer available to new
    users". The app falls back automatically, but each new API process first wastes one call on it. Clear
    `GEMINI_DEFAULT_MODEL` in `.env`, or set it to a model that answers.
  - `gemini-3.8-flash` was overloaded ("high demand"), and other new Flash models were slow, during the checks.
  - The free tier allows only **20 requests a day per model** for `gemini-3.8-flash`. The testing for this
    milestone used them up; Google resets them about 14 hours after these checks, at midnight US Pacific time.
    Until then the app uses the next Flash model automatically (D44).
- Prices are from Google's and Anthropic's lists as of 2026-11-09, with the price rises they have announced
  (D47). `gemini-3.5-flash` and `gemini-3.7-flash` are not on Google's price list, so their cost shows "not
  known". Check `app/llm/pricing.py` when providers change prices.
- `LLM_MAX_OUTPUT_TOKENS` (3,000) includes thinking tokens for thinking models (Claude Opus 5.5, newer Gemini).
  M8 should check that this is enough for an answer.
- The connection check keeps its database transaction open while the provider answers. That is fine for one
  tiny call, but the M8 agent loop should not hold a transaction across LLM calls.
- Model fallback switches models between calls. If that happens in the middle of an M8 tool loop, the new model
  receives the old model's signed Gemini parts, which it may reject. The M8 agent loop should restart the
  question on the new model in that case. A single AI call now waits at most about 3½ minutes
  (`LLM_CALL_BUDGET_SECONDS` plus one request, D44). The M8 Ask screen needs its own latency budget and
  progress messages.
- `KmsKeyWrapper` is tested against a stand-in KMS client only; check it against real KMS in M19.
- Plan changes have no billing rules yet (M18).


---

## M6: Connections framework + Excel/CSV + Google Sheets (done)

**Built**
- **Database** (migration `0006`, reversible): `connections`, `connection_grants`, `datasets`, `snapshots`,
  `file_versions`, each with RLS, the `tenant_isolation` policy and least-privilege grants; plus
  `memberships.connection_access` (`all`/`selected`, D50).
- **Connector framework** (`app/connectors/`): the `Connector` interface (test, list datasets, fetch, source
  version, incremental, push-down), a registry that builds a connector with its decrypted credentials, and
  `ReadOnlyHttpClient`: GET only plus POST to allow-listed token endpoints, enforced twice (wrapper and httpx
  request hook), retries on 429/5xx honouring `Retry-After`.
- **Reading tables** (`connectors/tabular.py`, `file_formats.py`): header row detection (title rows skipped),
  Total row left out, types per column, Indian day-first dates, offsets and Unix times converted to the
  workspace's time zone, ₹/Rs/INR, Indian and Western digit grouping, `(1,200)`, `%`, lakh/crore; identifiers stay
  text. Safe file opening: type from content, zip-bomb limits, `defusedxml`, formulas never evaluated (D51).
- **Cache and snapshots** (`app/data/`): Parquet snapshots in object storage; reuse within `CACHE_TTL_SECONDS`,
  then an "unchanged" check against the source's version, incremental merge where supported, else a full read;
  a Redis lock against duplicate reads; `fetched_at` and `checked_at` kept apart; expiry after
  `SNAPSHOT_MAX_AGE_HOURS` with a beat purge every 15 minutes (D53).
- **Data quality** on every new snapshot: short weeks/months, a category value missing for a week, stale data,
  empty columns, near-unique ids with repeats, duplicate rows, mixed currencies, unreadable values, row cap, Total
  row (D54). On the demo orders it reports exactly planted finding 7.
- **Excel/CSV connector**: each sheet is a dataset; re-upload makes a new version; the stored original is never
  changed (D52, D56). A new version is checked before it is used (D58, see the fix below).
- **Google Sheets connector**: OAuth with PKCE, read-only scopes checked after every token exchange/refresh (write
  scopes refused and revoked), the Google Picker, one dataset per visible tab, Drive version for "unchanged",
  token revoked on removal, same-account rule for signing in again (D55). Without credentials, development uses
  a **stand-in** (consent page + Picker dialog + Google's APIs answered from the demo workbooks). Tests replay
  recorded Google answers.
- **Background jobs**: `sync_connection` (after upload, choosing spreadsheets, signing in again, refresh) and
  `purge_snapshots`, behind a `JobQueue` interface; a crashed job never leaves a connection "reading".
- **API** (`/connections`): list (with which sources can be added), detail (tables, columns, data notes, file
  versions), preview of the first 20 rows (live, through the cache), upload and new version (50 MB, 411/413),
  rename, pause/resume, remove (files, snapshots, secret and Google token deleted at once), test, refresh; access
  list and per-person access; Google start / callback / finish / picker token / spreadsheets. Owners manage;
  Analysts see their visible connections read-only (`connections.view`, D49); others' ids answer 404.
- **Activity log**: `connection.added`, `.file_uploaded`, `.renamed`, `.paused`, `.resumed`, `.removed`,
  `.reauthorized`, `.spreadsheets_changed`, `.access_changed`; a "Connections" filter on the Activity screen.
- **Web app**:
  - **Connections** screen: "Add data" (upload with drop zone and checks, "Connect Google Sheets"), connection
    cards (status, freshness, "Reading the data…", tables and rows, data notes, fix-it message) and "Who sees which
    connections" (all / only these / none).
  - **Connection** page: status and actions (Refresh now, Test connection with its checks, Rename, Pause/Resume,
    Remove, Reconnect Google Sheets), spreadsheets (choose with the Picker, stop reading one), tables with data
    notes, columns (type, unit, how dates were read) and a preview of the first rows, file versions with "Upload
    a new version".
  - Google return page (finishes the sign-in, opens the Picker or its stand-in, saves the choice).
  - Analysts get the read-only screens; the menu shows Connections to them.
  - All text in `locales/en.ts`; data notes are written from their details there (D41, D54). ConnectionCard now
    uses i18n too.
- `docs/SETUP_GOOGLE.md` Part B (real Google Sheets credentials, scopes, Picker key, project number, verification
  before launch); `docs/ARCHITECTURE.md` connections section; decisions D49-D57.
- `docker-compose.yml` mounts `eval/` read-only into the backend containers for the stand-in's demo workbooks.

**Test status:**
- **Backend:** 1109 passed (770 in M5); ruff and mypy (strict) clean. New: the parser and file formats (incl. zip
  bomb, XML entities, encodings, delimiters), quality checks (incl. planted finding 7 and no false alarms on noisy
  data), the read-only HTTP client, the lock, object storage, the Google connector against recorded answers and
  the stand-in, the cache (reuse, unchanged, incremental, force, lock, failures, purge), sync jobs, the API
  flows, the route matrix for all 15 new routes (401/403/2FA/404 cross-tenant/422/leaks), security (writes blocked
  through every connector, tokens never in responses/logs/audit/DB, storage keys, RLS on all new tables).
- **Frontend:** vitest 256 passed (233 in M5); eslint, prettier, tsc and tokens:check clean. Build OK (shell
  161.6 KB of 200 KB gzipped). Storybook builds (new "Screens/Connections" stories).
- **Playwright:** 261 passed (31 skipped by design). New baselines: Connections (now a real screen), the connection
  page with columns and preview open, the access dialog and an Analyst's list, in light/dark × desktop/phone, with
  axe checks; the 375 px no-scroll check now also covers the connection page.
- **Live check against `make dev`:** 15 of 15 passed: sign-up with two-step, upload of the demo orders CSV read
  by the Celery worker (28,540 rows in 3 s) with finding 7 reported, preview from the snapshot, an Excel file with
  Indian formats, a damaged file refused, the Google Sheets stand-in end to end (consent, callback, Picker, 10 tabs
  read, test), activity log, removal. No AI calls.

**How to try it:** see the testing steps given at the end of M6. In short: `make fixtures` (once), `make dev`,
open http://localhost:5173/connections as an Owner, upload `eval/fixtures/shopify_orders.csv`, open it and look at
its data notes and preview; then "Connect Google Sheets" (stand-in) and choose the demo spreadsheets.

**Known gaps / notes**
- Real Google Sheets is untested against Google itself (no credentials yet). Follow `docs/SETUP_GOOGLE.md` Part B;
  then re-record `backend/tests/connectors/fixtures/google_sheets.json` from a real account (M19).
- Moving a connection to another kind of source while keeping dictionary mappings needs the dictionary (M7).
- Incremental fetching is built and tested with a fake source; the first real users are Razorpay, Shopify and Zoho
  (M11-M13).
- Full-page screenshots with a dialog open show the overlay over the first screen height only (same as the Team
  dialog since M4); in a real browser the overlay covers everything.
- Uploads are held in memory while checked (at most 50 MB per request); fine for local use, revisit with the
  load test in M19.

### M6 fix after the human's live check: new file versions (D58)
- **Problem:** a one-line text file (`broken.csv`) uploaded as a new version became "In use" and replaced the
  working 28,540-row version.
- **Fix:** a new version with no data rows is refused (`failed`); one with sheets or columns gone, an emptied sheet
  or changed column types waits as **Needs your review** with the differences listed; the version in use stays.
  Owners choose a version with **Use this version** (also to switch back to an earlier one) and can **Discard**
  versions that are not in use. The version in use is recorded explicitly; "Refresh now" reads it.
- Migration `0007`; routes `POST /connections/{id}/versions/{version_id}/use` and
  `DELETE /connections/{id}/versions/{version_id}` (in the route matrix); activity entries
  `connection.version_used` and `connection.version_discarded`.
- Tests: `backend/tests/api/test_file_versions.py` (incl. the exact one-line file, header only, blank lines,
  different columns, changed types, use, switch back and refresh, discard, refusals), sync-level tests for a
  removed sheet and an added sheet, and web app tests for the review, use, switch-back and discard flows.
- **Your existing workspace:** connections uploaded before this fix have no recorded version in use, so the
  newest readable one is used. Open the connection and press **Use this version** on the version you want.
- **Test status after the fix:** backend 1142 passed (ruff, mypy clean; route matrix 287 cases; migration round
  trip incl. `0007`). Frontend vitest 260 passed; eslint, prettier, tsc clean; build 162.5 KB of 200 KB; Storybook
  builds. Playwright 261 passed (the connection page baseline now shows a version waiting for review). Live check
  against `make dev`: 10 of 10 for versions (the one-line `broken.csv` refused, review, use, switch back,
  refresh, discard) and the original 15 of 15 again. No AI calls.


---

## M7: Zero setup: profiler + business dictionary (done)

**Built**
- **Demo-data fix:** `connectionFixtures.ts` gives `total_price` no unit, like the real reader for
  `shopify_orders.csv`. Baselines re-recorded and checked; Storybook builds.
- **Profiler (code, no AI):** per column type, empty %, distinct, range, <= 5 samples (none for personal-looking
  columns), role guess, time column. Money/unit **evidence**: ₹ markers, a single-value INR currency column,
  money-like names, paise-like values (Razorpay-style tables, `*paise*`, integer `amount` beside INR). D59.
- **Draft in code** (roles, units, time column, standard metrics, joins found and measured on the data), then the
  **AI step**: profile only, only what is new, one small call per 8 tables, cached by input hash, through the M5
  layer and metered as `dictionary` (not a question). Without a key, quota, or a bad answer the code draft stands
  and says why. D61.
- **Validation:** metrics tested in DuckDB on a sample (locked, no file access); joins by match rate; AI metrics
  that do not run are dropped. **Versions:** draft / confirmed / superseded, history, YAML export, schema-change
  drafts with a diff (Celery job after each read; unchanged structure = nothing). D60.
- **Review screen** (Dictionary in the menu): summary, company, tables and columns (meaning, role, unit, paise,
  synonyms, ✓ per flagged item), metrics, joins, version history, Save / Confirm / Discard / Draft again.
  Confirm waits until every flagged item is checked and every metric runs. Until confirmed, `service.active`
  carries "dictionary not confirmed" for M8. Analysts read it (tables of hidden connections left out).
- **R2:** "Replace with another source" on the connection page: mappings, metrics and joins move to the new
  connection (any kind) where tables/columns match; the rest is flagged or listed. D62.
- Migration `0008` (reversible): `dictionary_versions` (RLS), `datasets.profile_json`, usage kind `dictionary`.
  New deps: `duckdb`, `pyyaml`. New env (in `.env.example`): `DICTIONARY_AI_MAX_OUTPUT_TOKENS`,
  `DICTIONARY_AI_TABLES_PER_CALL`.

**Test status**
- Backend: 1326 passed; ruff and mypy (strict) clean. New: profiler/evidence, drafting, joins, metrics, AI step,
  replace (unit); the full API flow with the fake LLM (AI ok/cached/quota/invalid key/no key/unreadable,
  prompt contains no rows, metering, confirm rules, schema-change drafts, access, export, replacing, cross-tenant
  404); route matrix for the 8 new routes; security (RLS, read-only, formulas that cannot reach files).
- Frontend: vitest 282 passed; eslint, prettier, tsc, tokens:check clean; build OK (171.3 KB of 200 KB gzipped);
  Storybook builds (new Screens/Dictionary stories).
- Playwright (one `make e2e-update` run): 269 passed, 31 skipped by design; all baselines re-recorded because the
  menu gained Dictionary, plus new Dictionary baselines; axe clean.
- No AI calls were made.

**Known gaps / notes**
- Not run against a real model; the AI prompt/answer format is only tested with the fake client.
- Joins cannot be added by hand; analysts cannot suggest edits (`dictionary.suggest` unused).
- After a replace without removing the old connection, its tables are drafted again as new tables.
- A removed-then-re-uploaded file is matched to its old table by name and columns (>= 0.8), else it is new.


---

## M8: Agent core + Ask UI (done)

**First: the M7 bug (two sources with a table of the same name), D64**
- Removing the first source no longer leaves metrics pointing at a table that is gone. Its same-named twin in the
  other source (same name, nearly the same columns) takes over its metrics, joins and the Owner's descriptions,
  under the plain key (`orders`). With no twin, automatic metrics and joins of the gone table are removed, and
  metrics people wrote are flagged; the draft cannot be confirmed until they are fixed. The draft's diff says what
  happened (`metrics_moved`, `metric_orphaned`, `join_removed`).
- **Warning before removal:** the connection page's Remove dialog says which metrics move and where, which would
  have no table, and how many joins go (`dictionary_impact` on `GET /connections/{id}`).

**Built**
- **Database** (migration `0009`, reversible): `conversations`, `messages`, `saved_questions`, each with RLS, the
  `tenant_isolation` policy and grants.
- **SQL guard** (`app/security/sql_guard.py`, sqlglot): one SELECT/WITH/UNION only; no writes, `INTO`, locks,
  file/network/sleep functions, table functions or file paths; only known tables.
- **DuckDB workspace** per question (`app/data/workspace.py`): copies only, external access off, configuration
  locked, a time limit per query, at most 200 rows back.
- **Analytics library** (`app/analytics/`): `kpi_summary`, `trend`, `driver_analysis` (contribution by dimension,
  volume vs rate, new vs repeat customers, dimensions of joined tables), `detect_anomalies` (same weekday over 8
  weeks, robust z with a noise floor), `forecast` (Holt-Winters / trend x seasonal index, 80% range, backtest
  MAPE), `rfm_segments`, `cohort_retention`, `marketing_efficiency`, `budget_variance`, `receivables_ageing`
  (P&L needs accounting data, M13/M16). D66.
- **Agent** (`app/agent/`): 13 tools (BUILD_SPEC 7.3 without what-if, goals, growth signals; `query_source` waits
  for M14), each with `AccessContext`; the three lenses; 10 playbooks chosen by the question's words; the system
  prompt with the dictionary summary and the rules; provenance for every result; the **number check** (one repair
  turn, then unsupported figures are removed with a note); the **₹ impact rule**; data notes from code (quality
  warnings, "dictionary not confirmed", removed metrics, incomplete periods); **limits** (12 steps with a final
  "answer now" step, 150k input tokens, 200 rows per tool, query timeout); restart once if the provider switches
  models mid-question. D65-D69.
- **Background job** `answer_question` (Celery): progress steps as it works, one `usage_events` row per model call
  (kind `question`), `question.asked` in the activity log (hash + tables, never the text or the answer), a stale
  answer reported after 30 minutes. An identical first question within the cache window reuses the answer
  without an AI call.
- **API**: `GET/POST /conversations`, `GET/PATCH/DELETE /conversations/{id}`, `POST /conversations/{id}/messages`
  (follow-up; 409 while an answer is running), `GET /conversations/{id}/messages/{message_id}` (polling),
  `GET/POST /saved-questions`, `DELETE /saved-questions/{id}`. Before queueing: 10 questions per person per minute
  (429), the Managed monthly limit (429) and an AI key (409). History is per person (others' ids are 404).
- **Web app** (`/ask`, `/ask/{id}`): lens picker, starter questions, history and saved questions (side column on
  desktop, a sheet on tablet and phone), live progress steps (polling every second), the answer sheet with the
  haldi mark on the key figure, numbered source marks with their popovers, evidence, charts (ECharts, loaded
  lazily), why it matters, recommendation with its ₹ impact, data notes, quality banners, next questions, "Save
  question", "Show my work" (steps, SQL, sources and fetch times), failed answers with what to do, delete and
  lens change. Enter asks, Shift+Enter adds a line, `/` focuses the box. Viewers are told their role cannot ask.
  All text in `locales/en.ts` (the AnswerSheet labels moved there too). Storybook: Screens/Ask and the charts.
- New dependencies: `statsmodels`, `scipy`, `sqlglot` (backend); `echarts` (web). No new
  environment variables (the M1 settings for limits are used).

**Test status**
- **Backend:** 1540 passed (1326 in M7); ruff and mypy (strict) clean. New: SQL guard (accepts and 30 kinds of
  refusals), workspace walls (no file access, locked settings, timeout, row cap); analytics against the answer key
  (planted findings 1-5 and 7 with exact numbers: South -17.7% and repeat customers, ROAS by channel, Marketing
  +22.0%, Operations festive overrun, Fashion discounts / Electronics margin, only the App outage flagged in
  August, the missing Website week); the agent with the fake LLM (tools, provenance, hidden tables unreachable,
  repair turn, removal, step and token limits, plain text, model errors, model switch, prompt); the Ask API (flow,
  progress, metering, audit hash, polling, follow-ups, 409, stale answers, reuse, rate and monthly limits, no AI,
  failures, per-person history, saved questions, per-person id routes); security (RLS on the new tables, other
  workspaces' and paused tables invisible, write SQL through the agent leaves stored data unchanged, no rows,
  personal values or secrets in prompts or logs); route matrix with the new routes; the M7 bug (unit + API).
- **Frontend:** vitest 297 passed (282 in M7); eslint, prettier, tsc and tokens:check clean. Build OK: shell
  181.2 KB of 200 KB gzipped (ECharts is a separate 170 KB chunk loaded with the first chart). Storybook builds.
- **Playwright:** 281 passed (31 skipped by design). The Ask baselines were re-recorded and reviewed (the new-question
  screen in light/dark × desktop/phone) and new ones added: a finished answer with its chart, sources and notes
  (`ask-answer`) and the "Show my work" drawer (`ask-work`), each with axe checks; the 375-1536 px no-scroll check
  now covers an answer. Unrelated baselines that an update run re-rendered with sub-pixel noise were restored and
  pass against the committed images.
- **No AI calls were made.** Not run against a real model or `make dev` (that would call Gemini): the prompt,
  tool use and answer format are tested with the fake client only.

**How to try it:** see the testing steps given at the end of M8.

**Known gaps / notes**
- **Never run against a real model.** The first real questions may show prompt or tool-use issues the fake client
  cannot (e.g. how well the model cites `[rN]`). When you are ready, a short live check by name is the next step.
- Polling, not streaming: progress appears within a second of each step (D65).
- Pin/Export/Share (M10/M15), the command palette (Ctrl/Cmd+K) and the "health check" first answer after
  confirming the dictionary (M10 onboarding) are not built.
- Cross-source joins work through `run_sql` or the dictionary's joins; a dimension of a joined table needs a join
  in the dictionary.
- The anomaly noise floor (0.8) was tuned on the demo data (D66); real data may need a per-workspace setting.
- The shell grew to 181.2 KB of the 200 KB budget (171.3 KB in M7); later screens may need lazy loading.


---

## M9: What-if simulator (done)

**First: two text fixes in the dictionary's "What changed" list**
- "The metrics moved to another table" message: it now always names the metrics. The sentence has three forms: two
  different sources ("The metrics revenue, orders moved from orders in Old file to orders in New file."), the same
  name for both ("The metrics revenue, orders now use the table orders, because the removed source had a table with
  the same name.", so "shopify_orders in shopify_orders" cannot appear), and no names ("The metrics of orders in A now
  use orders in B."). `changeText` never lets a `{{...}}` placeholder through.
- **I could not reproduce the raw `{{metrics}}` you saw.** The server sends the metric names and the screen filled
  them in every time I tried (unit and component tests), so the likely cause is a draft saved before this field
  existed or a stale web build. If you see it again after a hard refresh, send me the draft's "What changed" JSON
  (`GET /dictionary`, field `draft.diff`) and I will trace it.

**Built**
- **Engine** (`app/analytics/whatif.py`, no AI): levers `price`, `discount` and `marketing_spend` (D71). Baseline =
  the forecast for the scope; effect = driver model with an elasticity; **the elasticity is learned from the company's
  own weekly history** (log-log with trend and month-of-year controls) and used only when the history moved enough
  and is clear; otherwise an industry default is used and **labelled as an assumption** (D72). Output: baseline vs
  scenario by month, low / expected / high (80%), assumptions (each marked history / typical figure / yours),
  confidence with a reason, side effects (units, discount given away, extra spend, repeat-customer share) and the
  weeks that informed it. A person can set their own figure.
- **Agent tool `what_if`** with a rewritten playbook: turn words into one scenario, never state the expected change
  without its range, name each assumption and its source, say when a result is surprising. Missing metric defaults to
  `revenue`; stock, staffing and product-mix scenarios are refused with a hint. What-if questions now pick the what-if
  playbook first.
- **API** (`/whatif/options`, `/whatif/values`, `/whatif/run`): a run is the same tool with **no AI call and nothing
  metered**, with the asking person's own permissions; 60 runs a minute per person (D74). No new table, no migration,
  no new environment variable.
- **What-if screen** (`/what-if`): form, headline with the haldi mark on the expected change and its range, the
  ScenarioCard (slider re-runs on release; assumptions in words; "Edit assumptions" dialog), chart, side effects and a
  month ledger with the data source and age (D75). Loads as its own chunk. "Describe it in words" opens Ask, and
  `/ask?q=...` now opens the box with the question typed in.
- **Golden scenarios** (`eval/whatif_scenarios.yaml`): five, with expected ranges, run directly and through the agent
  with the fake model (the number check accepts every figure). They are the what-if part of `make eval` in M10.

**What the demo data says** (so you can check it against `ANSWER_KEY.md`; 3 months, UrbanCart)
- Price +10% on **South Electronics**: the history shows the July 2026 rise, so the estimate comes from history
  (about -0.6, medium confidence): revenue about +4% (+1.5% to +7%). Customers barely reacted in Electronics itself;
  the big South drop in the answer key is in orders from existing customers across categories.
- Discount +5 points on **Fashion**: the history cannot separate discount depth from the festive season, so a typical
  figure is used (low confidence, labelled). The discount given away rises sharply.
- **YouTube** spend -30%: revenue about -25%, but the spend saved is larger than the revenue lost (ROAS about 0.8).
- **Instagram** spend +20%: revenue about +16% and worth the extra spend (ROAS about 4). Marketing is never above
  low confidence (D72).
- **All prices -5%**: the range includes no change, and the screen says the direction is not clear.

**Test status**
- Backend: 1652 passed (1540 in M8); ruff and mypy (strict) clean. New: engine unit tests (arithmetic, scenario checks, what the
  demo data can and cannot teach, marketing, estimation on synthetic data), the five golden scenarios (direct + agent),
  agent tool tests (errors the model can act on, hidden tables, playbook choice, no raw rows), API tests with the real
  demo orders (401, 403 for Viewers and Owners without 2FA, success, other workspace, 422, the limit, no AI used),
  route matrix updated.
- Frontend: vitest 312 passed (297 in M8); eslint, prettier, tsc, tokens:check clean. Build OK: shell 184.2 KB of 200 KB gzipped
  (181.2 in M8); What-if is a separate 12.6 KB chunk. Storybook builds (new Screens/WhatIf).
- Playwright: one full run: 287 passed, 31 skipped by design, plus 2 axe failures on phones (the month table scrolled without keyboard access); fixed (the table box is now focusable and labelled) and the what-if specs re-run: 16 passed. Only the four What-if baselines changed and four `what-if-result` baselines were added; no other baseline was touched. The full suite was not re-run after that fix.
- **No AI calls were made.**

**How to try it:** see the testing steps given at the end of M9.

**Known gaps / notes**
- Levers for stock, staffing and product mix are not built (D71). Marketing spend needs a table with spend, revenue
  and a channel column; with fewer than 12 campaign rows a typical figure is used.
- The range is wide on purpose (forecast range plus the elasticity's range): D73. A smaller business with a short
  history will often see "the range includes no change".
- A slider move recomputes on the server: the calculation takes about 15 ms on the demo orders once the table is
  loaded; the first run also reads the table through the cache. Fitted elasticities are not cached.
- The what-if view is not saved or pinned (Pin arrives in M15); sharing a what-if as an export arrives in M10.
- The industry default elasticities are generic figures (D72), not researched benchmarks; M17 should replace them.
- Never run against a real model: how well the model fills `{lever, change, scope}` from words is only tested with
  the fake client.

### M9 follow-up: dictionary review fixes (D76)
Found by the human after M9: remove a source, upload the same file again, and the draft showed "12 items need your
check" that could not be found or cleared, with Confirm disabled.
- **Metrics can be checked on screen**: flagged metrics get a strip and a "Looks right" button.
- **A returning table clears its metrics' flags** (`metrics_restored` in "What changed"); metrics whose table is still
  gone stay flagged.
- **The Remove dialog warns** that edits (paise unticks, meanings, formulas) go with the source and points to "Upload
  a new version".
- Tests: unit (flags cleared when the table returns, kept when it does not), API (remove then upload the same file),
  web app (strip, button, Confirm enabled after checking, text of the change, dialog warning).
- Not done on purpose: keeping a removed table's edits for a re-upload.



---

## M10: Briefings, long tasks, exports, sharing, eval (done)

**Built**
- **Database** (migration `0010`, reversible): `reports`, `tasks`, `briefing_settings`, `briefing_runs` (one scheduled
  run per person and slot: a partial unique index), `shares`; each with RLS, the `tenant_isolation` policy and
  least-privilege grants (briefing history and shares cannot be edited by a workspace request).
- **Growth signals** (`app/analytics/growth.py`, agent tool `growth_signals`): segments and pairs that grew 3+ periods
  in a row while gaining share, the whole beating last year, repeat-customer share and an estimated upside (D77).
  Finds planted finding 6 (West × Beauty).
- **Briefings** (`app/briefings/`): facts by code with the recipient's permissions (KPI snapshot, unusual numbers,
  growth signals, risks, finished long tasks), one small AI call to write them (number-checked) or a code-written
  fallback, a cache for identical facts, suppression of findings already sent, alerts-only mode, HTML + text email with
  numbered source links, stored as a report (D78). Beat job every 5 minutes, per-person time zone, idempotent (D79).
- **Long tasks** (`app/tasks/long_tasks.py`): the agent with the long limits (40 steps), progress, cancel before the
  next AI call, 2 per workspace, report + PDF, email with the PDF attached (D80). Questions and tasks now share one
  runner (`agent.service.run_agent`).
- **Exports** (`app/exports/`): PDF (WeasyPrint, the design system in print, SVG charts, sources list, no outside
  fetches) and Excel (summary, sources, the cited results' rows, chart data, formula-safe) for answers, task reports
  and briefings (D81).
- **Sharing** (`/shares`, `/shared`): with members whose connection access covers every source the item used, checked
  when sharing and on every view; Viewers can export what was shared with them; emails to recipients (D82).
- **Today** (`/today`): onboarding checklist (Company details, Connect data, Confirm dictionary, Ask first question),
  the latest briefing with key numbers, three suggested questions written by code, "Run a health check", latest
  analyses and shared items, a deletion banner. No AI call (D83). After an Owner's first dictionary confirmation the
  app runs the health check at once.
- **Privacy** (Settings, Owners): download the workspace's data as a ZIP (no secrets, no cached source data);
  delete the workspace (type its name; 7-day grace with emails to every Owner; cancel any time; hourly purge removes
  connections, files, snapshots, secrets, reports and every row; an anonymised `org.deleted` entry stays) (D85).
- **Dictionary draft email** (carried from M7): when a change in the data makes a new draft after a confirmed one,
  every Owner gets an email with a link to the review screen.
- **Command palette** (Ctrl/Cmd + K): screens, saved questions, connections (D84).
- **Eval harness** (`eval/harness.py`, `eval/golden_questions.yaml`, `make eval`): 48 golden questions (all eight
  planted findings, 4 Hindi + 4 Kannada, the 5 what-ifs); CI replays each question's ideal plan with a stand-in model
  through the real agent loop and scorer (100%); `make eval` runs a real model, opt-in, with a report, pass rate and
  token cost (D86).
- **Web app**: Today, Briefings (settings, "Send me a briefing now", past briefings), Tasks (start, progress, cancel,
  open report), report pages, "Shared with you", Export and Share on every answer and report, Privacy and "Kind of
  business" in Settings, activity-log sentences for the new entries. New screens load lazily. All text in
  `locales/en.ts`.
- **Fixed on the way** (D87): `detect_anomalies` failed on short histories (M8); only the first stale answer was marked
  failed (M8).
- New dependency: `weasyprint` (backend; the Docker image now installs Pango and Noto fonts). No new environment
  variables.

**Test status**
- Backend: 1933 passed (1652 in M9); ruff and mypy (strict, app + tests + eval) clean. New: growth signals against the
  answer key and synthetic data; exports (sources, citations, the haldi mark, escaping, no fetches, Hindi text, Excel
  formula safety, notes in step with the web app); briefing facts on the demo data (the 15 Aug outage as the alert,
  growth, old data, suppression, provenance, no rows to the AI), the writer (fake AI: good answer, invented figures
  removed, invented headline refused, errors and plain text fall back), the schedule (IST slots, window, weekly, off,
  own time zone); API: tasks (run, report, PDF email, failure email, limits, stale, cancel before the next AI call,
  access), briefings (settings, send now with and without AI, reuse of written words, rate limit, scheduler idempotency,
  settings followed, deleting workspace, alerts only, not sent twice, dictionary-draft email), exports and sharing
  (PDF/Excel, Viewer rules, recipients who cannot see the sources, shrinking access, stop sharing, 404s), Today,
  data export (no secrets, no other workspace), deletion (request, cancel, emails, purge after 7 days with storage
  cleared); security (RLS on the five new tables, no cross-workspace writes, no deleting briefing history, growth tool
  cannot reach hidden tables); route matrix for 11 new routes (466 cases) plus 10 personal and 1 stateful route tested
  in their own files; the 48 golden questions through the harness.
- Frontend: vitest 335 passed (312 in M9); eslint, prettier, tsc, tokens:check clean. Build OK: shell 195.2 KB of
  200 KB gzipped (184.2 in M9). Storybook builds (new "Screens/Today, briefings and tasks").
- Playwright: one full run (`--update-snapshots=changed`): 301 passed, 31 skipped by design, none failed. Re-recorded
  and reviewed: Today, Briefings, Tasks, Settings (Privacy, kind of business), the workspace switcher and the phone
  "More" sheet (they open over Today), and the phone Ask answer (Export and Share buttons); new baselines for a
  briefing report and the Share dialog; axe clean on all of them. The review found "vs vs" on the key-number tiles and
  raw `[r3]` marks in the past-briefings list; both fixed, and only the Today/Briefings/report specs were re-run
  (28 passed twice; one screenshot mismatch on the first check did not come back).
- **No AI calls were made.** `make eval` and `make llm-check` were not run.

**How to try it:** see the testing steps given at the end of M10.

**Known gaps / notes**
- Not run against a real model: how well a real model writes briefings and passes the golden questions is unknown
  until `make eval` runs (by name, when you are ready). The briefing always has the code-written version to fall back on.
- Briefings and answers are in English (D41); the briefing language setting shows English only.
- Goals in briefings and Today arrive with M15; "upcoming risks" are data-quality and connection risks for now
  (stock and receivables risks need Tally/Zoho, M13/M16).
- A deleted workspace's people keep their accounts, and its earlier activity-log entries stay (D85; M19).
- Task and briefing PDFs use the fonts installed on the server (Noto in Docker); Bricolage Grotesque is not embedded.


---

## M11: Razorpay connector (done)

**Built**
- **Connector** (`app/connectors/razorpay.py`): four tables, `payments`, `orders`, `refunds`, `settlements`. GET only
  (the shared read-only client with no allowed POSTs), Basic auth, `count`/`skip` paging inside 31-day `from`/`to`
  windows, retries on 429/5xx, plain-language errors (a refused key is "needs sign-in"). Incremental reads: a cursor
  on `created_at` with a 30-day look-back, rows replaced by `id` (D90). No personal data is read. D89.
- **Money stays in paise** and the source says so (`ColumnInfo.stored_in`), so the dictionary divides all 11 money
  columns by 100, including `settlements` which the old name-based guess missed (D91).
- **API:** `POST /connections/razorpay` (key id + secret, checked with one read, then stored encrypted; 422 with
  Razorpay's reason if refused; 20 checks per hour) and `PUT /connections/{id}/razorpay/key` (replace a revoked or
  rotated key). Connection answers carry `razorpay: {key_id_masked, mode}`; the list says Razorpay is available.
  Migration `0011` (reversible) allows the new connection type. New env (in `.env.example`, both optional):
  `RAZORPAY_HISTORY_DAYS`, `RAZORPAY_REFRESH_LOOKBACK_DAYS`.
- **Development stand-in:** a key id starting `rzp_test_demo` reads the demo payments (`make fixtures` first) through
  a local imitation of Razorpay's API. Production never uses it (D91).
- **Web app:** "Connect Razorpay" on Add data (the key form loads on demand), the warning "Razorpay keys can make
  changes; this app only reads" with the advice to use a dedicated key, the masked key and live/test mode on the
  connection page, "Enter a new key" when Razorpay stops accepting the key, "Replace the API key". Strings in
  `locales/en.ts`.
- The dictionary metric label "Checked" (D88) needed no change.

**Test status**
- Backend: 2005 passed (1933 in M10); ruff and mypy (strict) clean. New: connector tests against a fake Razorpay (read-only,
  paging, windows, history limit, row cap, incremental merge, errors, 429/503 retry, no personal data, typing, the
  stand-in), API tests (connect, refusals, rate limit, roles, secrets never in responses/logs/audit/DB, preview in
  paise, incremental read after the cache expires, revoked key and replacement, pause, removal deletes the secret,
  cross-workspace 404, all 11 money columns detected as paise), route matrix for the 2 new routes.
- Frontend: vitest 343 passed; eslint, prettier, tsc, tokens:check clean; build OK, first-load shell **196.1 KB of
  200 KB** (195.2 before; the dialog is a separate lazy chunk).
- Playwright: `make e2e` once at the end: 292 passed, 9 failed, all on the Connections screen (and the two dialogs that
  open over it), which gained the Razorpay block. Those 9 baselines were re-recorded and reviewed; a re-run passed
  301, 31 skipped by design. The M10 screens did not change.
- **No AI calls were made.**

**Known gaps / notes**
- **Never run against the real Razorpay.** The fake follows Razorpay's documented list format; try a test-mode key first.
- Subscriptions are not read (optional in the spec). Payments whose status changes more than 30 days after creation
  appear after "Refresh now" (D90).
- No Storybook story for the key dialog.


---

## M12: Shopify connector (done)

**Built**
- **Connector** (`app/connectors/shopify.py`): five tables, `orders`, `order_items`, `customers`, `products`,
  `variants`. Shopify's OAuth (store address → Shopify's approval page → callback checked with Shopify's HMAC → token
  exchange), read scopes only (`read_orders`, `read_products`, `read_customers`, `read_inventory`, plus
  `read_all_orders` when Shopify approves it); a granted scope that is not `read_*` refuses the connection. The token
  is checked with one read, then stored encrypted. No names, e-mail addresses, phone numbers or street addresses are
  requested (city, state and country only). D92.
- **GraphQL queries only** (`app/connectors/graphql_guard.py`): every document is parsed before sending and again in
  the read-only HTTP client (new `post_guard`); every mutation is refused except `bulkOperationRunQuery` in its exact
  shape with a pure query inside (the Owner's decision). D93.
- **Reading:** paged queries for small tables and incremental reads, **one bulk export** for a first read of a table
  over 1,000 rows (JSONL streamed, line items rebuilt), Shopify's cost bucket respected (waits, `THROTTLED` retries),
  incremental by `updated_at`, test orders left out, 730 days of history. D94.
- **Money in rupees**, declared by the source (`stored_in="rupees"`, evidence `source_rupees`): nothing is divided,
  unlike Razorpay's paise. Uploaded `shopify_orders.csv` files keep working next to the connector. D95.
- **API:** `POST /connections/shopify/start`, `GET /connections/shopify/callback` (public, HMAC-checked),
  `POST /connections/shopify/finish`; connection answers carry `shopify: {shop, shop_name, currency, all_orders}`; the
  list says whether Shopify is available. Migration `0012` (reversible) allows the new type. New dependency
  `graphql-core`. New env (in `.env.example`, all optional): `SHOPIFY_SCOPES`, `SHOPIFY_API_VERSION`,
  `SHOPIFY_HISTORY_DAYS`, `SHOPIFY_BULK_THRESHOLD`, `SHOPIFY_BULK_TIMEOUT_SECONDS` (`SHOPIFY_API_KEY`/`_SECRET` existed).
- **Development stand-in** (`app/connectors/shopify_stand_in.py`): without app credentials and with
  `APP_ENV=development`, "Connect Shopify" opens a local approval page and reads the demo store (28,540 orders, the same
  totals as `shopify_orders.csv`) through an imitation of Shopify's OAuth and GraphQL API, bulk exports included. Tests
  use the same stand-in behind a "real" setup.
- **Web app:** "Connect Shopify" on Add data (the address dialog loads on demand), the return page
  `/connections/shopify/return` (loads on demand), store, currency, the 60-day note and the read-only note on the
  connection page, "Sign in to Shopify again" (also when Shopify stops sharing data), a removal note to uninstall the
  app in Shopify. Strings in `locales/en.ts`; test-check labels for orders and all orders; "Signed in to Google" became
  "Signed in to the source". `docs/SETUP_SHOPIFY.md`.
- **Also fixed:** `test_envelopes_fail_closed[wrapped_dek]` (and `[ciphertext]`) now flip a bit instead of writing a
  fixed byte, which was a no-op about 1 run in 256. The Ask fixture's fetch time (2026-11-12) showed "N days ago" and
  changed the phone "Show my work" screenshot every day; it is now more than a week old (a fixed date). The Add data
  intro now names Razorpay and Shopify. The "Checked" label (D88) needed no change.

**Test status**
- Backend: **2171 passed** (2005 in M11); ruff, ruff format and mypy (strict) clean. New: the guard (56: queries, every
  kind of mutation incl. `orderUpdate`, `customerDelete`, `bulkOperationCancel`, a write beside the bulk read, every
  wrong bulk shape, the body check and the HTTP client); the connector against the stand-in (58: store addresses,
  HMAC, scopes, code exchange once, tables and rupees, no personal fields in any query, paging, many line items, row
  cap, throttling, bulk export = paged result, waiting for another export, failed and slow exports, incremental,
  errors, no writes); API (31 + 4 for the stand-in: start, callback with forged HMAC / other store / unknown state, finish once and only by
  the starter, write scopes refused, secrets never in responses/logs/audit/DB, preview in rupees, incremental refresh,
  bulk per table, revoked token and signing in again, cross-workspace 404, removal, dictionary units, the uploaded CSV
  next to the connector, modes); the stand-in flow end to end; security (writes through every connector incl. Razorpay
  and Shopify mutations); profiler units; route matrix for the 2 new routes (+1 public).
- Frontend: vitest **352 passed** (343 in M11); eslint, prettier, tsc, tokens:check clean; build OK, first-load shell
  **197.4 KB of 200 KB** (196.1 before; the dialog and the return page are separate chunks). Storybook builds.
- Playwright: `make e2e` once: 291 passed, 10 failed: the 8 Connections baselines (the Shopify block) and the phone
  "Show my work" drawer (the drifting date above). Those were re-recorded and reviewed, and a full re-run passed **301,
  31 skipped by design**.
- **No AI calls were made.** `make eval` and `make llm-check` were not run.

**How to try it:** see the testing steps given at the end of M12 (summary: `make dev`, Connections > Add data >
Connect Shopify, any store address, Install app on the stand-in page).

**Known gaps / notes**
- **Never run against real Shopify** (no app, no store): see "Unfinished work" above and docs/SETUP_SHOPIFY.md.
- Removing a connection does not uninstall the app in Shopify (that is a write); the page says to uninstall it there.
- Expiring offline tokens are not used; a deleted line item can stay until "Refresh now" (D94).
- No Storybook story for the Shopify dialog (as for Razorpay's).


---

## M13: Zoho Books connector (done)

**Built**
- **Connector** (`app/connectors/zoho_books.py`): seven tables, `invoices`, `bills`, `expenses`, `contacts`, `items`,
  `chart_of_accounts` and `profit_and_loss` (monthly, one row per account line). Zoho's OAuth with a refresh token
  (stored encrypted; access tokens only in memory), **read scopes only** (`ZohoBooks.<module>.READ`; others dropped
  before sending; a token that can write is refused), **India data centre by default** (`ZOHO_REGION` for others; the
  person's own data centre is accepted only when it is one of Zoho's). One organisation per connection: the default
  one at sign-in, and Owners can switch to another the account sees. GET only (POST only to the token and revoke
  endpoints). Pages of 200, incremental by `last_modified_time`, P&L repeats the last two months. D96-D98.
- **Limits:** every Zoho call is counted per organisation per day in Redis; reading slows near 90 calls a minute and
  stops at 90% of `ZOHO_DAILY_CALL_LIMIT`; then the stored copy is used instead of failing (`ConnectorError.stale_ok`
  in the cache), D97. The connection page shows "Zoho API calls today: N of L".
- **Money in rupees** (declared by the source, nothing divided). **No personal details:** only declared fields are
  kept (no e-mails, phones, addresses, GSTIN/PAN, bank details, descriptions); `contact_name` is treated as personal
  by the profiler (no samples to the AI).
- **Analytics:** `finance.pnl` now computes a P&L from a profit and loss table (lines, gross/operating/net profit,
  margins); the `finance_summary` tool describes it. Receivables ageing works on Zoho invoices.
- **API:** `POST /connections/zoho/start`, `GET /connections/zoho/callback` (public), `POST /connections/zoho/finish`,
  `PUT /connections/{id}/zoho/organization`; connection answers carry `zoho: {region, region_name, organization_id,
  organization_name, currency, organizations, calls_today (detail only), daily_limit}`; the list says whether Zoho is
  available. Removing revokes the token at Zoho. Activity entry `connection.organization_changed`. Migration `0013`
  (reversible). New env (all optional, in `.env.example`): `ZOHO_REGION`, `ZOHO_SCOPES`, `ZOHO_HISTORY_DAYS`,
  `ZOHO_REPORT_MONTHS`, `ZOHO_DAILY_CALL_LIMIT` (`ZOHO_CLIENT_ID`/`_SECRET` existed).
- **Development stand-in** (`app/connectors/zoho_stand_in.py`): without a Zoho client and with `APP_ENV=development`,
  "Connect Zoho Books" opens a local consent page and reads the demo organisation **UrbanCart**, whose expenses are the
  monthly actual spend per department of `eval/fixtures/zoho_budget_expenses.xlsx` to the rupee (Marketing Q3 2026 is
  +22.0% against the workbook's budget), plus invoices, bills, items, accounts and a P&L built from the demo orders. A
  second small organisation shows switching. Zoho's API has no budgets, so the budget still comes from the workbook.
- **Web app:** "Connect Zoho Books" on Add data, the return page `/connections/zoho/return`, and on the connection
  page the organisation, data centre, currency, today's API calls, the read-only note, "Read another organisation"
  and "Sign in to Zoho again". **Shell budget:** the Connections and Dictionary screens load on demand and their strings
  moved to `locales/en/connections.ts` and `locales/en/dictionary.ts` (D99): shell **176.9 KB** (197.4 before).
  Storybook: Zoho connection stories. `docs/SETUP_ZOHO.md`.

**Test status**
- Backend: **2291 passed** (2171 in M12); ruff, ruff format and mypy (strict) clean. New: the connector against the
  stand-in (55: read scopes only, write scopes refused, data centres and look-alike hosts, code exchange, token renewal
  once, revoked token, organisations without personal details, paging past 200, history window, row cap, incremental,
  rupees, no personal columns in any table, P&L flattening and month-by-month reads, a refused report fails only that
  table, the daily limit at 90%, per-minute pacing, Zoho's own limit answer, retries, GET only; the demo organisation's
  expenses equal the workbook's actuals and give Marketing +22.0%); API (32: start, callback with unknown state /
  refusal / foreign data centre / the person's own data centre, finish once and only by the starter, write scopes
  refused with nothing stored, no organisation, secrets never in responses/logs/audit/DB, preview, dictionary units,
  incremental refresh, the stored copy at the daily limit, a refused report, switching organisation, revoked token and
  signing in again, cross-workspace 404, removal revokes the token, analysts read-only, stand-in pages off); the
  stand-in flow end to end (3); P&L and receivables on Zoho tables (5); route matrix for the 3 new routes (+1 public);
  security (writes through every connector incl. Zoho).
- Frontend: vitest **362 passed** (352 in M12; new: Zoho on Add data, return page, connection page, switching,
  signing in again, analysts; a check that every literal string key exists); eslint, prettier, tsc, tokens:check
  clean; build OK, first-load shell **176.9 KB of 200 KB** (197.4 before).
- Playwright: run once at the end of M14 (the human asked for `make e2e` once, at the very end).
- **No AI calls were made.**

**How to try it:** see the testing steps given at the end of M13/M14 (summary: `make fixtures`, `make dev`,
Connections > Add data > Connect Zoho Books > Accept on the stand-in page).

**Known gaps / notes**
- **Never run against real Zoho** (no client): see "Unfinished work" above and docs/SETUP_ZOHO.md.
- The P&L report is requested without a "reports" scope (none is documented); if Zoho refuses it, only that table
  says so (D96).
- Zoho's daily count uses the UTC date; Zoho's own reset time may differ by a few hours (D97).
- Budgets are not read (no Zoho API for them); journals, bank transactions, payments and credit notes are not read yet.


---

## M14: PostgreSQL/MySQL connector (done)

**Built**
- **Connector** (`app/connectors/sql_database.py`): a customer's PostgreSQL or MySQL database, one connection type
  `sql_database` with an engine. **Read-only in four layers** (D100): a read-only user is asked for and the check warns
  when the user can write; every session is opened read-only with statement and lock timeouts and never commits; every
  read and push-down query passes the sqlglot SELECT-only guard in the source's dialect, exposed tables only (the guard
  now accepts `schema.table`); reads stream in chunks within the row limits, push-down answers are wrapped in a LIMIT.
- **Network safety** (D101): the host is resolved first and refused when any address is private or internal
  (localhost, 10.x, 172.16/12 incl. Docker, 192.168.x, 169.254.x, CGNAT, IPv6 local, `*.local`/`.internal` names);
  PostgreSQL connects to the checked address. In development only, `DB_DEV_ALLOWED_HOSTS` (the demo containers) may
  be private. SSL is required by default ("certificate checked" or "not checked"); "not encrypted" only in development.
- **Tables:** the Owner lists the tables and views the user can read and chooses which ones questions may use (up to
  100). Tables with an updated-at column and a primary key are read incrementally; others whole within the limits.
- **Push-down:** new agent tool `query_source` (D102), offered only when a database table is visible: SQL over the
  dictionary's table keys, translated to the database's names and dialect, checked twice, run read-only with the
  question's timeout and the person's access; results with provenance. The Ask screen shows "Querying <source>
  directly".
- **API:** `POST /connections/database` (checked, then the password stored encrypted), `PUT /connections/{id}/database/
  credentials`, `GET` and `PUT /connections/{id}/database/tables`; connection answers carry `database: {engine, host,
  port, database, username_masked, ssl_mode, tables, can_write, write_reasons, server_version}`; the list says whether
  SSL may be off and which demo hosts exist (development). Activity entry `connection.tables_changed`. Migration `0014`
  (reversible). New dependency `pymysql`. New env (optional, in `.env.example`): `DB_DEV_ALLOWED_HOSTS`,
  `DB_CONNECT_TIMEOUT_SECONDS`, `DB_FETCH_CHUNK_ROWS`.
- **Development stand-ins:** `make dev` starts `demo-postgres` and `demo-mysql` (docker compose, init scripts in
  `scripts/demo_db/`) with the UrbanCart orders, customers (no names or e-mails), products and a `monthly_sales` view,
  a read-only user `analyst_ro` and an owner `demo_owner` who can write (to see the warning).
- **Web app:** "Connect a database" on Add data (the dialog loads on demand): engine, host, port, database, user,
  password, encryption, with the demo details in development. The connection page shows where the database is (user
  masked), the encryption, the write warning, "Tables in the database" with checkboxes and "Save tables", "Replace
  credentials" and "Enter the credentials again". The shell stays at **176.9 KB**. `docs/SETUP_DATABASES.md`.

**Test status**
- Backend: **2444 passed** (2291 after M13); ruff, ruff format and mypy (strict) clean. New:
  - The connector (66): every private/internal address and local name refused in production; only the demo names
    and only in development; the checked address is what the driver gets; SSL rules.
  - SQL built, quoted and checked in both dialects; a cursor cannot leave its literal; writes, locks, sleeping,
    file/network functions, other tables, unqualified names, `SET ... READ WRITE`, `set_config` and several
    statements refused.
  - A real PostgreSQL in the test container: the read-only user passes; the writer is flagged and still cannot
    UPDATE/DELETE/CREATE through the session; statement timeout; tables the user may read (an ungranted table is not
    listed); chunked reads, IST times, cursor and incremental read; push-down with a join; a vanished table; a
    wrong password; SSL refused in plain words.
  - The fake MySQL: grants, typing, incremental, row cap, push-down LIMIT, and the real session's read-only set-up
    with the driver stood in.
  - API (35): add, writer warning, 8 refused hosts, SSL off refused, wrong password, MySQL, validation, roles,
    rate limit, password never in responses/logs/audit/DB, listing and choosing tables, refused table, preview and
    incremental read, replacing credentials, the engine fixed, other kinds of connection refused, analysts read-only,
    cross-workspace 404, removal.
  - `query_source` (12): on PostgreSQL with provenance, writes and other tables refused, 200-row cap, offered only
    with a visible database, other tables sent to `run_sql`, people without access refused, MySQL dialect.
  - Route matrix for the 4 new routes; security (no writes through any connector, now including databases).
- Frontend: vitest **368 passed** (362 after M13; new: connecting a database with checks, the server's refusal, the
  development-only options, the connection page with the write warning and choosing tables, entering credentials
  again, analysts); eslint, prettier, tsc, tokens:check clean; build OK, shell **176.9 KB of 200 KB**. Storybook
  builds (new Zoho and database connection stories).
- By hand: both demo containers start and load the demo data (28,540 orders, 3,000 customers); the connector reads
  them, flags `demo_owner`, refuses writes in the session and refuses `localhost` outside development.
- **No AI calls were made.** `make eval` and `make llm-check` were not run.

**How to try it:** see the testing steps given at the end of M13/M14 (summary: `make fixtures`, `make dev`,
Connections > Add data > Connect a database, host `demo-postgres`, database `urbancart_demo`, user `analyst_ro`,
password `demo-readonly`, encryption "Not encrypted (development only)"; for MySQL host `demo-mysql` and "Encrypted,
certificate not checked").

**Known gaps / notes**
- No SSH tunnel (use a firewall allow-list). Binary and spatial columns are not read.
- Push-down results skip the dictionary's unit conversion (the agent is told; D102).
- PyMySQL with "certificate checked" connects by name after the address check (small window, D101).
- MySQL is covered by a fake in the automated tests and by the demo container by hand.


---

## M15: Goals, pinned dashboard (UI translations deferred, D41) (done)

**Built**
- **Database** (migration `0015`, reversible): `goals` and `dashboard_tiles`, each with RLS, the `tenant_isolation`
  policy and grants.
- **Goals** (`app/analytics/goals.py`, `app/goals/service.py`, `/goals`): a target for a dictionary metric over a
  period, at least or at most, optional name and filter. Progress by code: actual so far, run-rate and seasonal
  (last year's pattern) projections, the gap, what is needed per day, and a status (on track, at risk, behind,
  achieved, missed, not started) (D103). Each person sees only goals on data they may see; progress is computed in
  the background with their permissions and kept 15 minutes; no AI call.
- **Agent tool `goal_progress`** (all goals, some, or an ad-hoc target), listed goals in `list_sources`, and the
  goal-tracking playbook uses it (D104).
- **Briefings**: a cited **Goals** section; an off-track goal is the early warning and makes the briefing notable;
  code writes the goals when the AI left them out. **Today** shows running goals as last computed (D104).
- **Pinned dashboard** (`app/dashboard/`, `/dashboard`): pin your own answer (Pin button on every finished answer);
  the tile keeps the answer's tool calls, its key figure's place and its charts, and re-runs them, never the AI.
  Rolling or fixed dates; refresh every 15 min / hour / 4 h / day or on "Refresh all"; rename, reorder (drag, or Move
  up/down), remove; "Show on everyone's dashboard". Every tile re-runs with the permissions of the person looking,
  and people who cannot see its tables do not see it (D105, D106).
- **Web app**: Goals screen (progress bars with a time marker, projection, warning, source marks, set/edit/remove
  dialog), Dashboard screen (tiles with key figure and change, charts or a small table, source marks, notes, team
  tiles), Pin dialog on Ask, goals on Today, "Goals" heading in briefings, activity-log sentences. The screens load on
  demand with their own strings (`locales/en/goals.ts`, `locales/en/dashboard.ts`); the unused placeholder screen
  is gone (D107). Storybook: "Screens/Goals and dashboard".
- New jobs: `refresh_goals`, `refresh_tiles` (Celery, no AI). No new dependencies and no new environment variables.

**Test status**
- **Backend:** 2612 passed; ruff and mypy (strict) clean. New: goal analytics against the demo company (actual,
  run-rate and seasonal projections computed independently with pandas, every status rule, at-most goals, averages,
  filters, data that ends early), the `goal_progress` tool (all, some, ad hoc, hidden tables), goals in briefings
  (cited, number-checked, early warning first); tile specs from real tool traces, rolling dates (months, quarters,
  years, weeks, days), replay for another person (no access, out of date, fixed SQL dates); API: goals (set, progress,
  options, validation, change, remove, freshness, visibility, other workspace, briefing and Today) and the dashboard
  (pin, no AI call on refresh, limits, refresh due/forced, settings, reorder, sharing with the viewer's permissions,
  hidden data, personal routes); security (RLS on both tables, a tampered tile spec cannot write, read files or
  reach hidden tables); route matrix with the 9 new workspace routes (634 cases) plus 3 personal routes tested in
  their own file.
- **Frontend:** vitest 383 passed; eslint, prettier, tsc and tokens:check clean. Build OK: shell 177.3 KB of 200 KB
  gzipped. Storybook builds ("Screens/Goals and dashboard").
- **Playwright** (run once, `--update-snapshots=changed`): 317 passed, 31 skipped by design. Re-recorded and reviewed:
  Goals and Dashboard (they were placeholders), Today (goals block) and the workspace switcher (it opens over Today),
  light/dark × desktop/phone, axe clean. One unrelated baseline re-rendered with sub-pixel noise (desktop-dark
  what-if result) was restored. The review found an empty footer line on team tiles with no result; fixed, and only
  the Dashboard spec was re-run (8 passed).
- **No AI calls were made.** `make eval` and `make llm-check` were not run.

**How to try it:** see the testing steps given at the end of M15.

**Known gaps / notes**
- Read-only: goals and tiles only read through the analytics and the read-only workspace; nothing in a source changes.
- Rolling dates move `{start, end}` periods; SQL the agent wrote itself keeps its dates (the tile says so).
- A tile whose answer had no findable key figure shows its chart or table only.
- The 10% "at risk" margin and the 15-minute freshness of goal progress are constants for now (D103).
- Goals use calendar months/quarters in the "Period" presets; India's financial-year quarters can be chosen as dates.
- UI translations (Hindi, Kannada) stay deferred (D41).


---

## M16: Tally bridge (done)

**Built**
- **The bridge** (`bridge/tally/`, Python 3.12 + httpx + defusedxml, PyInstaller spec for Windows): `tally-bridge pair
  <CODE> --server <url>`, `run`, `sync-now [--full]`, `status`, `companies`, `forget`.
  - Reads TallyPrime's XML server (default `http://127.0.0.1:9000`, this PC or the office network only) with **export
    requests only**: fixed inline-TDL collection templates (`ISMODIFY="No"`), and `check_export_only` checks every
    request right before it is sent (allow-list; import, alter, create, delete, TDL functions, `ACTION`, entities are
    refused with `ReadOnlyViolation`). D109.
  - Sends voucher types, groups, ledgers, stock items (whole, every sync) and vouchers with their ledger and inventory
    lines (changed ones by AlterID every 15 minutes; all of the last 36 months, month by month, at least daily), as
    Tally's own text, only the fields asked for. D110.
  - Outbound HTTPS only, device token in every request (DPAPI-protected on Windows), gzip, no redirects followed;
    plain http only to localhost. Settings from the platform are clamped. D108, D109.
  - Survives sleep, offline and restarts: progress saved after every accepted batch, each batch kept in an outbox
    until accepted and re-sent unchanged, backoff with jitter (6 tries in a sync, then 30 s ... 15 min), Tally closed
    retried every 5 minutes, one instance per user. D113.
- **Platform** (migration `0016`, reversible): `tally_devices`, `tally_syncs`, `tally_records`, each with `org_id`, RLS
  and the `tenant_isolation` policy; `connections.type` accepts `tally`.
  - Pairing: one-time 8-character code (hash in Redis, 10 minutes, once, a new code cancels the old one; 10 tries per
    IP per 15 min) -> device token (only its hash stored). One PC per connection; **Unpair this PC** revokes at once
    (401). D108.
  - Bridge API `/bridge/tally/*`: `pair`, `poll` (heartbeat + 25 s long-poll for "read again now", the only request
    the platform can make), `batches` (2,000 rows / 2 MB before and after gzip, unknown fields dropped, idempotent by
    batch number, one company per connection), `syncs/{id}/complete` (all batches present, then rows Tally no longer has
    are removed; vouchers only after a full read; tables rebuilt in the background). D109, D110.
  - Connector `tally` (`app/connectors/tally.py`): seven tables, `groups`, `ledgers`, `stock_items`, `vouchers`,
    `voucher_ledger_entries`, `voucher_inventory_entries`, `trial_balance`; cancelled and optional vouchers left out;
    **money in rupees** (`stored_in="rupees"`, debit positive); voucher classes through custom voucher types; group
    nature (Assets/Liabilities/Income/Expenses). Freshness = the last push (`FetchResult.data_as_of`, used by the cache
    for `fetched_at`, so provenance never makes an offline PC's data look new). D111.
  - Owner routes: `POST /connections/tally` (connection + pairing code), `POST /connections/{id}/tally/pairing-code`,
    `POST /connections/{id}/tally/unpair`; "Refresh now" on a Tally connection asks the PC; "Test" reports the PC
    without marking the connection failed. Activity: `connection.tally_paired`, `.tally_code_created`,
    `.tally_unpaired`, `.refresh_requested`. D112.
  - The dictionary's rupee evidence text is now source-neutral; `party_ledger` is treated as personal (no samples to
    the AI).
- **Development stand-ins** (not in the Windows build): the Tally stand-in (`make tally-stand-in`, demo company
  "UrbanCart Retail (demo)" from the fixtures, Tally's quirks, records and refuses any change request) and the fake
  helper (`make tally-fake-helper CODE=...` with `LIVE`, `FLAKY`, `OFFLINE_AFTER`, `TALLY_CLOSED_AFTER`, `SAMPLE`).
- **Web app:** Add data > **Connect Tally** (dialog on demand): a name, then the code, its expiry, the three steps on
  the PC and the copyable pair command. Connection card: "Tally PC offline since …", "Tally is not open on …",
  "Waiting for a Tally PC to pair". Connection page: state, PC and bridge version, company, last seen, last data,
  problems in plain words, the read-only note, **Get a new pairing code**, **Unpair this PC** (confirmation), and
  "Refresh now" asking the PC. Strings in `locales/en/connections.ts`. Storybook: Tally connection stories.
- New env (optional, `.env.example`): `TALLY_PAIRING_CODE_MINUTES`, `TALLY_SYNC_INTERVAL_MINUTES`,
  `TALLY_FULL_SYNC_HOURS`, `TALLY_HISTORY_MONTHS`, `TALLY_MAX_BATCH_ROWS`, `TALLY_MAX_BATCH_BYTES`,
  `TALLY_OFFLINE_AFTER_MINUTES`, `TALLY_POLL_WAIT_SECONDS`, `TALLY_BRIDGE_DOWNLOAD_URL`. No new dependencies.
- The bridge's code and tests run with the backend's tools (`make test-backend`, `make lint`, `make typecheck`).
- Docs: `docs/SETUP_TALLY.md` (try it locally, a customer's PC, building the .exe, what is least certain),
  decisions D108-D113.

**Test status**
- Backend: **2791 passed** (2612 after M15); ruff, ruff format and mypy (strict) clean, now including `bridge/tally`.
  New: the bridge (42: request templates and the export-only guard, UTF-16 and forbidden characters, only asked
  fields kept, voucher lines and allocations, platform/Tally address rules, gzip, error mapping, no redirects, the
  token file, progress after a restart, full/incremental/daily-full, 413 splitting, refusals, another company,
  backoff, Tally closed, platform down, the loop's refresh and unpairing); Tally text and tables (32: amounts with
  Tally's signs, Dr/Cr, forex, quantities, rates, dates, voucher classes through custom types, primary groups,
  voucher amounts, trial balance, every table empty, rupees declared); the bridge API and Owner routes (42: one-time,
  expiring, cancelled and rate-limited codes, hashed token never logged or audited, a new PC revokes the old one,
  401 without/with a bad or revoked token, heartbeat fields bounded, the poll can only say "read again", batches
  idempotent, unknown fields dropped, validation, 2,000 rows, 2 MB, gzip bomb, 415, another company, paused,
  missing batches named, complete twice, no voucher removal after an incremental read, analysts, other connection
  types, test, another workspace 404); the whole path with the real bridge (11: typed tables in rupees, freshness =
  the push, debits = credits and a balanced trial balance, cancelled/optional left out, incremental + alteration +
  no duplicates, deletion only on a full read, resuming after a lost answer, status changes, refresh through the
  poll, and **planted finding 1 (South falls in Q3 2026, others grow) visible in the demo company's Tally data**);
  security (26: 14 kinds of change request refused, every built request passes, a refused request never reaches
  Tally, no platform module contains Tally's request vocabulary, settings clamped, rows only in the device's
  workspace under RLS); route matrix with the 3 new Owner routes, the 4 bridge routes listed as `DEVICE`; the
  read-only test now covers `tally`; RLS on the three new tables (generic test).
- Frontend: vitest **390 passed** (383 after M15); eslint, prettier, tsc and tokens:check clean; build OK, first-load
  shell **177.4 KB of 200 KB**. Storybook builds (new Tally connection stories).
- Playwright (run once, `--update-snapshots=changed`): **317 passed**, 31 skipped by design, axe clean. Re-recorded
  and reviewed: Connections and the access dialog over it (the new Tally block in Add data), light/dark x
  desktop/phone; no overflow at 375 px.
- **Live check against `make dev`: 18 of 18** (script in the session scratchpad, not committed): sign-up with
  two-step, Connect Tally, a wrong code and a re-used code refused, the real bridge pairing and reading the demo
  company from the stand-in (30,112 vouchers in 18 s, 28 requests, all exports), the Celery worker building seven
  tables (30,110 vouchers in the books), rupees, "Connected", preview, "Refresh now" heard through the long-poll,
  an incremental sync of one new sale, "Tally is not open", unpair, the revoked token refused, removal.
- **No AI calls were made.** `make eval` and `make llm-check` were not run.

**Known gaps / notes**
- **Real TallyPrime and the Windows build are untested** (docs/SETUP_TALLY.md lists the least certain parts: nested
  voucher-line FETCH, debit/credit signs, period balances, the AlterID filter, text formats, Windows-only code).
- A voucher deleted in Tally disappears at the next full read (at least daily), not at the next 15-minute sync.
- One company per connection; companies kept in another currency than rupees are not detected yet.
- The bridge's IP limit for pairing sees the proxy's address behind a load balancer until trusted forwarding headers
  are set up (M19, same as sign-in).

---

## M17: Industry templates (done)

**Built**
- **Seven templates** as validated YAML in `backend/app/templates_industry/` (retail, d2c, restaurant, fuel_station, services,
  manufacturing, and a generic "something else"): the business's own words, 5-7 KPIs (name, plain meaning, unit,
  good direction), 4 starter questions, playbook tweaks for 3-4 playbooks, and default what-if elasticities with their
  basis. No AI call and no customer data are involved. D114.
- **Where they work:** the agent's system prompt gets a short "What matters in a ... business" section (KPIs, words and
  only the chosen playbooks' tweaks; nothing for "something else"); the dictionary draft's existing AI call gets
  `typical_metrics` (names only); `whatif.DEFAULTS` now comes from the templates with the same numbers as M9, and a
  default assumption carries its `basis`; the briefing writer names the business kind properly. D115.
- **Choosing:** the Today checklist's "Company details" step opens a picker dialog (an Owner chooses, sees the KPIs and
  starter questions, saves); Settings shows the same preview under "Kind of business", also before saving. Ask shows the
  template's starter questions once a kind is chosen; Today's suggestions use them to fill empty places. New route
  `GET /org/templates` (any signed-in member). Both screens load on demand. D116.
- No migration and no new env variables.

**Test status:** see M18 (counts are for both milestones together).

**Known gaps / notes**
- The template texts and default elasticities come from general business reasoning, not from data; they should be read
  by someone who knows each trade (D115). They are English only (D41).
- Changing the template does not rewrite a confirmed dictionary's own industry field (D116).
- Not run with a real model: the effect of the industry section on real answers is untested (fake client only).

---

## M18: Billing hooks, no payments (done)

**Built** (hooks and placeholders only: no gateway, no price, no real money, no keys; D117-D121)
- **Plans** (`app/billing/plans.py`): the two M5 plans with their M5 limits; prices are `None` ("coming soon").
- **Provider interface + STAND-IN** (`app/billing/provider.py`): every billing event is offered to a `BillingProvider`;
  the only one is `StandInProvider`, which sends nothing. `BILLING_PROVIDER` accepts only `stand_in`. D118 lists
  everything to replace when a real provider is chosen.
- **Table `billing_events`** (migration `0017`, reversible, RLS, read and add only): plan changes, 80% warnings, limit
  reached, monthly summaries. Included in the data export (`billing.json`).
- **Warnings at 80% and 100%** (`app/billing/limits.py`): after each question or long task on the platform's key, once
  per workspace per month each, the Owners get one email (`usage_warning`) and the event is recorded; never raises.
  Owners also see a notice on Today and the meter in Settings. Counting is M5's (`questions_used`). D119.
- **Plan change flow** (`/org/billing/plan-change/preview`, `PUT /org/billing/plan`): preview what changes, then
  confirm; same rules as M5; one function (`api.ai.change_plan`) changes plans everywhere and records the event. D121.
- **Invoice-ready summaries** (`GET /org/billing/summary`, `.csv`): invoice lines for a month (included, used, over limit,
  own-key runs, briefings, estimated AI cost); `amount_due_inr` 0, status `draft_no_payment`. A beat job
  (`close_billing_months`, every 6 hours) freezes last month's once. CSV downloads are logged
  (`billing.summary_exported`) and guarded against spreadsheet formulas. D120.
- **Web app:** Settings > **Plan and billing** (Owners, loads on demand): not-switched-on notice, meter with 80%/100%
  words, plan cards, plan change dialog (preview then confirm), month picker with the summary table and CSV download,
  what billing recorded. Today shows the usage notice to Owners. Strings in `locales/en/billing.ts`.
- New env (optional, `.env.example`): `BILLING_PROVIDER=stand_in`. No new dependencies.

**Test status (M17 + M18 together)**
- Backend: **2915 passed** (2791 after M16); ruff, ruff format and mypy (strict) clean. New: templates (files complete and
  consistent with the accepted industries and playbook files, validation, prompt notes, what-if defaults and basis,
  dictionary hints, the system prompt per industry), `/org/templates`, choosing the industry, Today's suggestions;
  billing (levels, M5-equal counting, warnings once per month and per threshold, emails to Owners only, never raising, a
  real question through the API, plan preview and confirm, blockers, the old plan routes, summaries and CSV, freezing,
  January rollover, a failing workspace not stopping the others, the stand-in opens no network and the package imports
  none); security (RLS, append-only history, no cross-workspace writes, once-per-month key, cascade on deletion, Owners-only
  Today notice, export); route matrix with the 5 new routes (`PUT /org/billing/plan` as STATEFUL, tested in
  `test_billing.py`). The migration round trip and the generic RLS test cover `billing_events`.
- Frontend: vitest **409 passed** (390 after M16); eslint, prettier, tsc and tokens:check clean; build OK, first-load shell
  **179.2 KB of 200 KB** (177.4 after M16).
- Playwright (`make e2e` once, at the end): 313 passed, 31 skipped by design, axe clean; the 4 Settings snapshots changed
  as intended (new Plan and billing section) and were re-recorded for light/dark x desktop/phone and reviewed: no overflow
  at 375 px.
- **No AI calls were made.** `make eval` and `make llm-check` were not run.

**Known gaps / notes**
- **Nothing can be paid for.** No price, checkout, invoice number, tax lines or webhook exist (D118, D120).
- Warnings are recorded when a run finishes, so the email may arrive just after the question that crossed the line.
- A month is frozen by the beat job up to 6 hours after it ends; until then the summary is a live draft.
- The 80% notice is not shown to Analysts and Viewers (they see M5's message when a question is refused).
- The summary line under the month picker reads "Draft: this month is still running. Managed AI" (plan name appended);
  worth a wording pass in the M19 review.
