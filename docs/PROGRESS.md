# Progress

**Next milestone: M4: Orgs, roles, invites, RLS** (start when the human says "continue").
**Unfinished work:** none for M3. Carry into M4: `require()` must refuse owner permissions while
`AuthContext.two_factor_enabled` is false (DECISIONS D25).

## Milestone checklist (BUILD_SPEC §14)

### Phase 1: MVP
- [x] M1: Foundation
- [x] M1b: Design system + app shell
- [x] M2: Auth core
- [x] M3: 2FA, Google sign-in, devices
- [ ] M4: Orgs, roles, invites, RLS
- [ ] M5: Secrets, LLM layer, plans, usage
- [ ] M6: Connections framework + Excel/CSV + Google Sheets
- [ ] M7: Zero setup: profiler + business dictionary
- [ ] M8: Agent core + Ask UI
- [ ] M9: What-if simulator
- [ ] M10: Briefings, long tasks, exports, sharing, eval
- [ ] M10b: Public website (then Phase 1 review, stop)

### Phase 2
- [ ] M11: Razorpay connector
- [ ] M12: Shopify connector
- [ ] M13: Zoho Books connector
- [ ] M14: PostgreSQL/MySQL connector
- [ ] M15: Goals, pinned dashboard, UI translations (then Phase 2 review, stop)

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

