# Progress

**Next milestone: M7: Zero setup: profiler + business dictionary** (start when the human says "continue").
**Unfinished work:** none for M6. **First step of M7 (agreed with the human):** make the demo connection data
(`frontend/src/testing/connectionFixtures.ts`) match what the real reader gives for `shopify_orders.csv`:
`total_price` has no unit (the CSV has no ₹ marker, only a `currency` column with `INR`). Then re-record the
affected Playwright screenshots (`make e2e-update`), review them, and check Storybook. Carry into M7 and later:
- Every new tenant table needs RLS, the `tenant_isolation` policy and `app_tenant` grants in its migration. The
  schema test in `tests/security/test_rls.py` enforces it.
- Every new route must be classified in `tests/api/test_route_matrix.py` (it now has `{connection}`, `{dataset}`
  and `{sheets}` placeholders, multipart uploads and fixture-dependent bodies).
- Connection credentials go through `app.security.secrets.SecretStore` (purpose per connector,
  `app.connectors.registry.SECRET_PURPOSES`).
- Every LLM call goes through `keys.resolve(...)` and `usage.MeteredLLM` (D42). **No real LLM calls during builds
  or tests** (CLAUDE.md); M7's dictionary drafting must be tested with the fake LLM client only.
- Read data only through `DatasetCache` and decide visibility only with `app.connectors.access` (D50, D53).
- M7 takes over from M6: "replace a connection with another kind of source and keep the dictionary mappings"
  (R2, D57), and turning Razorpay paise into rupees through the dictionary (D51).
**Deferred:** multilingual answers (originally in M8) and UI translations (M15). Keep all user-visible text in
`frontend/src/locales/en.ts` (DECISIONS D41).

## Milestone checklist (BUILD_SPEC §14)

### Phase 1: MVP
- [x] M1: Foundation
- [x] M1b: Design system + app shell
- [x] M2: Auth core
- [x] M3: 2FA, Google sign-in, devices
- [x] M4: Orgs, roles, invites, RLS
- [x] M5: Secrets, LLM layer, plans, usage
- [x] M6: Connections framework + Excel/CSV + Google Sheets
- [ ] M7: Zero setup: profiler + business dictionary
- [ ] M8: Agent core + Ask UI (multilingual answers deferred, D41)
- [ ] M9: What-if simulator
- [ ] M10: Briefings, long tasks, exports, sharing, eval
- [ ] M10b: Public website (then Phase 1 review, stop)

### Phase 2
- [ ] M11: Razorpay connector
- [ ] M12: Shopify connector
- [ ] M13: Zoho Books connector
- [ ] M14: PostgreSQL/MySQL connector
- [ ] M15: Goals, pinned dashboard, UI translations (translations deferred, D41) (then Phase 2 review, stop)

### Phase 3
- [ ] M16: Tally bridge
- [ ] M17: Industry templates
- [ ] M18: Billing hooks (no payments)
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
