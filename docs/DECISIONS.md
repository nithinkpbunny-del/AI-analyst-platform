# Decisions

Choices where the spec was silent, or where I had to deviate from it. Newest last.

## M1: Foundation

### D1. Local object storage is RustFS, not MinIO
The spec names MinIO for local S3. MinIO no longer publishes free community Docker images (`minio/minio` on
Docker Hub and `quay.io/minio/minio` both refuse pulls). **RustFS** (`rustfs/rustfs`) is an S3-compatible
server with the same ports (9000 API, 9001 console). It was verified with boto3: path-style addressing,
bucket create/head, object put/get/delete. Only local development is affected; production still targets AWS S3
`ap-south-1`. The app talks plain S3 (`app/storage.py`), so switching servers again needs no code changes.
The variable names stay generic (`S3_*`). The bucket is created at start-up by a one-shot `storage-init`
service (`python -m app.storage`) instead of the MinIO `mc` client.

### D2. Postgres credentials come from `DATABASE_URL`
The Postgres container needs its user, password and database as separate variables, and containers need
service host names (`postgres`, `redis`) instead of `localhost`. To avoid duplicating the password in `.env`,
`make dev` runs `scripts/compose_env.py`. That script parses `DATABASE_URL`/`REDIS_URL` from `.env` and writes the
derived values to `.compose.env` (git-ignored, mode 600, never printed). Compose then reads both files.
Consequence: start the stack with `make dev`, not with a bare `docker compose up`.

### D3. No module-level FastAPI `app`
Uvicorn runs `app.main:create_app --factory`. Importing `app.main` (e.g. from tests) therefore never reads the
developer's real `.env`. Tests build `Settings(_env_file=None, ...)` with fake secrets.

### D4. Readiness endpoint
`/health` is liveness only (no dependencies). `/health/ready` checks Postgres, Redis and object storage with a 2 s
timeout and returns 503 when one fails. The response names the failing check but never includes error text,
because that can contain connection strings. Details go to the redacted log.

### D5. Log redaction
One redacting formatter wraps every handler (root, uvicorn, celery). It scrubs (1) the exact value of every
configured secret and (2) known patterns: JWTs, bearer/basic tokens, Google/Anthropic/Razorpay/Shopify/AWS keys,
passwords in URLs, `password=`/`token=`/`api_key=` style pairs, and email addresses. The `key=value` rule
is deliberately broad, so it may also hide harmless values such as `token_budget=…`. For logs, safe beats tidy.
Uvicorn's access log is off; our middleware logs method, path and duration (no query strings).

### D6. Demo fixtures (BUILD_SPEC 12.1): interpretations and calibration
- **South decline (finding 1).** At the spec's order volumes, almost all South revenue in Q3 comes from existing
  customers. A literal "45% fewer orders" would produce about −30% revenue, not −18%. I treat **−18% revenue as
  the headline**. The generator removes a share of existing South customers' post-hike orders, solved by
  bisection to land on −18% (within one order). That share is about 31%. Measured against the other regions'
  seasonal growth, existing South customers order about 41% less often (their gap between orders is ~69%
  longer), which matches the spec's "~45% less often". The price hike is applied to the South only, so
  that every other region still grows.
- **"Repeat" customers** follow the spec's definition: signed up before 2026-07-01. "New customers unchanged" is
  checked as orders per customer per 30 days for customers who joined from July, South vs other regions.
- **Channels and categories are stratified per day.** Each day's orders are split 50/30/20 across channels, and
  across categories by the category mix, almost exactly. Independent random draws made daily App counts so
  noisy that the planted 60% outage was not clearly separable from noise.
- **Exact planted values.** The outage removes exactly 60% of that day's App orders. Aggregate ROAS per channel is
  rescaled to the answer-key values (Email 6.4, Instagram 4.1, Google Ads 1.6, YouTube 0.8, Facebook 2.1,
  Influencer 2.6). Campaign-level noise and CTR/CVR/AOV ratios are preserved. Marketing Q3 2026 factors are
  1.20/1.22/1.24 (= +22.0%). Operations Oct/Nov 2025 factors are 1.09/1.11. Other budget months are clipped to
  ±6%, so nothing else looks like a variance.
- **Targets (finding 8)** are derived from actual regional revenue. For the South in Q3 they come from the
  pre-hike run rate, so only the South misses.
- **Data gap (finding 7).** Website orders for 2026-03-09..15 never exist in any source, including Razorpay,
  so the gap is consistent across files.
- **Customer mix.** 1,500 customers signed up Jun–Dec 2024 and 1,500 from Jan 2025 at a rate growing 2% a
  month. This keeps orders per active customer roughly stable.
- **Version control.** Generated CSV/XLSX files are git-ignored (about 9 MB, and XLSX bytes change between runs).
  Run `make fixtures` to regenerate them; `eval/fixtures/ANSWER_KEY.md` is tracked. CSVs and the answer key are
  byte-identical for seed 42; XLSX cell contents are identical.

### D7. Tooling details
- Ruff `E501` is off: the formatter handles line length, and long strings/comments are allowed. The same ruff
  config is applied to `eval/` with `--config backend/pyproject.toml`.
- mypy is strict. Exceptions: `app.tasks.*` may use Celery's untyped decorators, and the pandas-heavy
  `generate_fixtures`/`answer_key` modules allow `Any` returns (their tests check their behaviour).
- Development images use `:latest` tags for RustFS and Mailpit, and fixed major versions
  for Postgres 16, Redis 7, Node 22 and Python 3.12. Exact pins can come in the hardening milestone (M19).
- All container ports are published on `127.0.0.1` only.
- The frontend uses Vite 8 / TypeScript 6 / ESLint 10 (latest at the time), with React pinned to 18 as specified.

## M1b: Design system + app shell

### D8. Token values adjusted for WCAG 2.2 AA
The contrast test checks every colour pair the UI uses, in both themes. Five spec values or gaps failed, so I made
the smallest change that keeps the look:
- light `caution` #A86A0A → **#96600A** (4.1:1 → 4.9:1 on paper);
- dark-mode haldi mark opacity 55% → **40%** (light ink on the mark was 4.1:1, now 5.9:1); light stays 55%;
- new **`line-strong`** for input/control borders: `line` is only 1.3:1, below the 3:1 needed for UI components.
  `line` stays for dividers and ledger rules;
- new **`on-neel`** for primary-button text: white on dark-mode neel #8F8BFF is 2.9:1, so dark mode uses #12112A;
- chart amber #D08A00 → **#B07400** in light (2.9:1 → 3.9:1 against white). The spec left the dark chart variants
  open ("lighter variants"); I chose them, and all are ≥ 5.7:1.
I also added soft backgrounds for status pills (`gain-soft`, `loss-soft`, `caution-soft`) and a `scrim` for overlay
backdrops (a scrim made from `ink` brightens the page in dark mode).

### D9. Tailwind v4: the "preset" is a generated CSS `@theme` file
Tailwind 4 is configured in CSS, so the spec's "Tailwind preset" is `design/generated/tailwind-theme.css`. It resets
Tailwind's default colours, fonts, radii, shadows, text sizes and breakpoints, so a hard-coded `bg-gray-100` or
`sm:` simply does not exist. That enforces "uses only design tokens".

### D10. Library versions under the React 18 constraint
React Router 8 requires React 19, so the app uses **React Router 7** (declarative mode). The rest are current:
Tailwind 4, Storybook 10, Playwright 1.65, i18next 27, Radix (`radix-ui`), Lucide, Motion. Motion is installed
for later orchestrated moments; the haldi sweep and the lead fade are plain CSS keyframes (smaller, and
`prefers-reduced-motion` removes them globally).

### D11. shadcn/ui components are added when first used
11a.5 lists Toast, Dialog, Drawer, Tabs, Table, Select, DatePicker and Slider. M1b ships the ones the shell and
signature components need: Button, Popover (SourceMark), Slider (ScenarioCard) and Sheet (the phone "More" menu,
later "Show my work"). They are hand-written in the shadcn style on Radix primitives and restyled with tokens. The
others arrive with their first screen: Dialog/Toast in M2, Table/Select in M4 and M6, DatePicker/Tabs in M8.

### D12. Visual snapshots run in Docker
Font rendering differs between macOS and Linux, so baselines are recorded and checked in the official Playwright
image (`mcr.microsoft.com/playwright:v1.65.0-noble`) by `make e2e`, the same image CI uses. Snapshots use reduced
motion and a mocked API. There are 46 baselines: 11 screens × 4 projects, plus the phone "More" sheet in light
and dark.

### D13. Shell details the spec left open
- Breakpoints: phone < 768 px, tablet 768–1279 px (icon rail), desktop ≥ 1280 px (sidebar, collapsible; the
  choice is remembered).
- Until auth exists (M2), `useRole()` returns `owner`, so the Owner-only items are shown.
- System status (the M1 health check) moved from the home page into Settings; Today is the home screen.
- Theme: "Match my device" (default), Light or Dark, saved in localStorage. A small inline script applies the saved
  choice before first paint, so there is no flash.
- The development warning when more than one Highlight is visible enforces "one haldi mark per view".

## M2: Auth core

### D14. Session and token design
- **Access token:** HS256 JWT, 10 minutes, claims `sub, org, role, sid` plus `iat, exp, iss, aud, typ`. It is
  kept in memory in the web app (never in localStorage), so a reload exchanges the refresh cookie for a new one.
  Every authenticated request also loads the session row. That is how idle and absolute timeouts, sign-out,
  member removal and role changes take effect immediately. The **role used is the one in the database**,
  not the one in the token.
- **Refresh token:** 256-bit opaque value in an `HttpOnly; SameSite=Strict` cookie, `Path=/auth`. It is
  rotated on every refresh, and only its SHA-256 hash is stored. Presenting a token that was already rotated
  (or revoked) revokes the **whole session** and writes `auth.refresh_token_reused`. Refresh rows are locked
  (`SELECT … FOR UPDATE`), so two parallel refreshes cannot both succeed. The web app shares a single
  in-flight refresh and uses the Web Locks API across tabs, so normal use never trips reuse detection.
- **Idle timeout:** a refresh on its own does **not** count as activity. Otherwise an open, unused tab would
  stay signed in forever. Any other authenticated API call updates `last_seen_at` (at most one write every
  30 s). Screens that make no API calls would let an active person time out, so the web app also tracks
  real interaction (pointer, key, wheel, touch). It pings `/auth/me` every 4 minutes while the person is
  active, and signs out by itself after 30 minutes without interaction.
- **Absolute limit:** 12 hours from sign-in (`sessions.expires_at`); the refresh cookie's Max-Age matches.
- **Timeouts and the clock:** all time checks, including JWT `exp`/`iat`, use an injectable clock. That lets
  tests move time; PyJWT's own time checks are switched off in favour of ours.

### D15. CSRF: double submit, with the token readable from the API
`analyst_csrf` is a random, non-HttpOnly, SameSite=Strict cookie. Cookie-authenticated routes (`/auth/refresh`,
`/auth/logout`) require the same value in `X-CSRF-Token`. In production the web app and API may be on different
hosts, so the app cannot read the API's cookie from `document.cookie`. It gets the value from
`GET /auth/csrf` instead (or from the login/refresh response). CORS only lets the app's own origin read that
response, so another site cannot learn the value. Bearer-token routes need no CSRF check.

### D16. `Secure` cookies and plain-http localhost
The spec asks for `Secure` cookies. Safari does not store `Secure` cookies sent over `http://localhost`. So
`COOKIE_SECURE=auto` (the default) sets `Secure` unless `API_BASE_URL` is `http://localhost` or
`http://127.0.0.1`. Production uses https and always gets `Secure`. The setting can be forced to `true`/`false`.

### D17. Email links: a `user_tokens` table and tokens in the URL fragment
The spec has no table for email-verification or password-reset links. I added `user_tokens` (purpose, hash,
expiry, used_at): single use, only the newest link of each kind works, and only the hash is stored.
Verification links last 24 h, reset links 60 min. Links look like `/verify-email#token=…`: a fragment is never
sent to any server or written to access logs. The page removes it from the address bar and POSTs it, so mail
scanners that pre-open links cannot use it up.

### D18. No account enumeration
Sign-up, resend-verification and forgot-password always answer 202 with the same message. Signing up with a
registered address sends that address an "You already have an account" email instead. Unknown emails at login
still run a dummy argon2 check, so timing is similar, and both cases return "Invalid email or password." Emails
go out in a background task after the response. The one deliberate exception: with a correct password,
login says the email is not confirmed yet (only the account owner can see this).

### D19. Rate limits
- Login: 5 failures per (email, IP) within 15 min lock that pair. Lockouts repeated within 24 h double
  (15 min, 30 min, 1 h … capped at 24 h). While locked, even the right password is refused (429 with
  `Retry-After`), and the attempt is logged as `rate_limited`. A successful sign-in clears the count. Keys
  hold a hash of email+IP, never the address itself.
- Email-sending requests: 30 per hour per IP (429), and at most 5 emails per hour to one address. The
  per-address limit is applied silently, so it reveals nothing.
- The client IP is the direct peer for now. Trusted proxy headers are configured with the deployment (M19).

### D20. Password policy details
Minimum 10 and maximum 128 characters. The maximum bounds the hashing cost per request. The common-password list
is the UK NCSC "100k most used passwords" list (from Have I Been Pwned), lower-cased and bundled as
`app/auth/data/common_passwords.txt.gz` (~400 KB). The check ignores case. Two small extra rules: a password may not be
the account's email or its local part, and it needs at least 3 different characters. argon2id uses the
`argon2-cffi` defaults (t=3, 64 MiB, p=4) and old hashes are upgraded at login. Tests use cheap parameters.

### D21. Activity-log details
- `audit_log` is append-only in the database: row triggers reject UPDATE and DELETE, and a statement trigger
  rejects TRUNCATE. It has no foreign keys, so entries outlive the people and organisations they describe.
  `updated_at` is omitted because rows never change. Org deletion (M10) will need a deliberate, audited
  way around the trigger (an anonymised record, BUILD_SPEC 6.6).
- Events for a person (failed login, password reset) are written once per organisation they belong to, so
  every Owner sees them. A failed login for an unknown email is written once with no organisation and only a
  SHA-256 of the address.
- M2 actions: `auth.signup`, `consent.accepted`, `auth.email_verified`, `auth.login.success`,
  `auth.login.failure` (reason: `bad_password`, `unknown_email`, `no_password`, `inactive`,
  `email_not_verified`, `no_membership`, `rate_limited`), `auth.logout`, `auth.refresh_token_reused`,
  `auth.session.idle|expired|access_removed`, `auth.password_reset.requested|completed`.

### D22. Sign-up details and consent
Sign-up asks for name, email, company name and password, plus a consent checkbox. It creates the organisation
(timezone Asia/Kolkata, INR, Managed plan, monthly question limit from `MANAGED_MONTHLY_QUESTION_LIMIT`), the user and the Owner
membership, and records consent with `POLICY_VERSION`. Industry and timezone are chosen later in onboarding
(M10). The privacy policy and terms links point to the marketing site (`/privacy`, `/terms`), which is built in
M10b; until then those links do not resolve. Login uses the person's first active membership, or the one
named in `organization_id`. Switching workspaces comes with M4.

### D23. Test database and API error shape
- Backend tests run on a real Postgres 16 from testcontainers (or `TEST_DATABASE_URL`). citext, triggers and
  RLS (M4) behave exactly as in production. Tables are truncated between tests in replica mode, which skips
  the audit trigger; only the test superuser can do this. Redis is `fakeredis`, and email uses an in-memory
  sender. Alembic's logging setup no longer disables existing loggers (it hid log lines from other tests).
- Every API error is `{"error": {"code", "message", "fields"?}}`. `code` is stable for the web app;
  `message` is plain language.

### D24. UI details
- Shadcn Dialog and Toast (D11 planned them for M2) were not needed: auth messages are inline `FormAlert`s, which
  keep the message next to the form. They will be added with their first real use.
- Links inside running text are underlined, so they never depend on colour alone (axe `link-in-text-block`).
- `make e2e-update` now rewrites **every** baseline (`--update-snapshots=all`). The default only rewrites
  failing ones, and the 0.2% pixel tolerance let small but real changes pass: the new sidebar "Sign out" item,
  and an M1b empty-state width that the old baselines no longer matched.

## M3: 2FA, Google sign-in, devices

### D25. Sign-in is two steps when a second factor is needed
- A correct password (or a Google sign-in) no longer creates a session by itself. When a second factor is needed,
  `/auth/login` answers `two_factor_required` (enter a code) or `two_factor_setup_required` (an Owner without 2FA
  sets it up now), with an opaque **challenge token**. Only its SHA-256 is stored (`login_challenges`). The web
  app keeps it in memory, never in storage. The session and its cookies are created only after the second step.
- A challenge lasts **15 minutes** (enough time to install an app and scan a QR code). It is single use and allows
  **5 wrong codes**, after which the person signs in again.
- "Mandatory for Owners" means **an Owner of any workspace**. Someone who owns one workspace and is an Analyst in
  another still needs 2FA to sign in to either. That is simpler and safer than tracking it per workspace.
- Google sign-in goes through the same second step. Google's own 2FA cannot be verified by us.
- `AuthContext.two_factor_enabled` is set on every request. **M4's `require()` must refuse every owner
  permission when it is false.** That covers people made Owner later (and any Owner session from before M3).
  Until then, the web app sends such a person to `/setup-two-factor` before any other screen.

### D26. How second-factor secrets are stored
- `two_factor` has one row per person (primary key `user_id`). `pending_method` / `pending_totp_secret_enc` hold a
  method being set up or changed. The active method keeps working until the new one is confirmed with a code.
  `totp_last_step` stops a TOTP code from being used twice (or an older one being used after a newer one).
- The TOTP secret is **envelope-encrypted** (`app/security/crypto.py`): a random AES-256-GCM data key per value,
  wrapped by a key derived from `MASTER_KEY` with HKDF. The associated data names the person, so a ciphertext
  copied into another person's row does not decrypt. A blob that does not decrypt **fails closed** (the code
  is refused and an error is logged, never a 500). M5 reuses this module for the `secrets` table and adds KMS
  behind the same `key_version`.
- Email codes (`two_factor_codes`) and recovery codes are stored as **HMAC-SHA256 with a key derived from
  `MASTER_KEY`**, not plain hashes. A 6-digit code or a ~49-bit recovery code is too short for a plain hash to
  survive a database leak. argon2 would cost ~50 ms per code, ×10 at every recovery-code check.
- Recovery codes: 10 per person, `xxxxx-xxxxx` from 31 letters and digits with no look-alikes (i, l, o, 0, 1).
  Each works once. They are shown once and replaced as a set (on "Create new recovery codes" and on a method
  change).
- TOTP: `pyotp` defaults (SHA-1, 6 digits, 30 s), ±1 step as the spec says, issuer "Analyst". The API sends
  the QR code as rows of modules (`segno`) and the web app draws it, so the secret never goes to an image
  service and no QR library ships in the browser.
- SMS codes stay out of scope. `app/auth/code_delivery.py` is the interface, and email is its only
  implementation.

### D27. Limits on codes
- 5 attempts per email code (spec), and also per challenge (D25).
- Per person: **10 wrong codes within 15 minutes lock code entry** (429 with `Retry-After`), doubling like
  sign-in lockouts. This matters because each new password sign-in starts a fresh challenge with 5 attempts.
  The lock is per person, not per IP, because whoever is guessing already has the password.
- Code emails: at most **10 per person per hour**. Resending during sign-in waits 30 s between sends and allows 5
  sends per challenge.
- Failures are logged as `auth.login.failure` with reason `bad_2fa_code`, `bad_2fa_setup_code` or
  `two_factor_locked`.

### D28. Changing 2FA while signed in ("step-up")
- Changing the method, creating new recovery codes and turning 2FA off all need **a code from the current method
  or a recovery code**. Email-method users ask for one with `/auth/2fa/send-code`. A stolen session alone
  therefore cannot replace someone's second factor.
- Owners cannot turn 2FA off (403 `two_factor_required`); they can switch methods.
- Every change sends a "Your two-step verification changed" email and writes an activity-log entry:
  `auth.2fa.enabled`, `auth.2fa.method_changed`, `auth.2fa.disabled`, `auth.2fa.recovery_codes_regenerated`,
  `auth.2fa.recovery_code_used`, `auth.2fa.code_failed`.

### D29. Google sign-in details
- Authlib builds the authorization request and the PKCE challenge. The ID token is checked with `joserfc`
  (Authlib's JOSE library). The code exchange is one `httpx` POST. Authlib's own httpx client integration is
  deprecated as of Authlib 1.9 (it asks for an `httpx2` package), so it is not used. Google's keys are cached for
  an hour and fetched again once when a token's key is unknown (key rotation).
- `state` and the PKCE verifier live in Redis for 10 minutes. `state` is also bound to the browser by an HttpOnly
  cookie with **SameSite=Lax**: Strict would not be sent on Google's redirect back. Path is `/auth/google`.
- After the callback, the browser goes to the web app with a one-time token in the URL fragment
  (`/signin/google#token=…` or `/signup/google#token=…`). The web app exchanges it with a normal API call, so all
  session cookies are set the same way as for a password sign-in.
- A new person signing up with Google still names the workspace and accepts the terms (`/signup/google`). Their
  account has no password; "Forgot password" can add one later.
- Linking follows BUILD_SPEC 6.1: same email and Google says it is verified. If the existing account never
  confirmed its email, its password is **removed** when linking (it may have been set by someone else: the
  "pre-account takeover" attack). An email already linked to a different Google account is refused
  (`google_other_account`).
- **Stand-in provider:** with no `GOOGLE_OAUTH_CLIENT_ID` and `APP_ENV=development`, a local page on the API
  plays Google's part, so the whole flow can be tried without credentials (BUILD_SPEC 0.4). It refuses to run in
  production, and tests use the same class. See `docs/SETUP_GOOGLE.md`.

### D30. New-device alerts
- Each browser gets a random device id in the `analyst_device` cookie (HttpOnly, SameSite=Strict, path `/auth`,
  400 days), signed with a key derived from `JWT_SECRET`. The database stores only its SHA-256
  (`known_devices`, `sessions.device_id`). A forged or missing cookie counts as a new device.
- **The first device a person ever uses is not announced**: that is their own sign-up. Every later unknown
  device sends "New sign-in to your account" with the time in the workspace's time zone (e.g. "Mon 9 Nov 2026,
  2:30 pm IST"), the IP address and a short browser description ("Chrome on Windows", from a small built-in
  parser).
- "This wasn't me" (`/secure-account#token=…`, valid 7 days, single use) signs out every session, forgets every
  known device (so the next sign-in from any device alerts again) and ends pending sign-ins. It does not change
  the password itself. The page sends the person straight to reset it. Each alert's link keeps working even
  after a later alert.

### D31. Your own sessions
`/auth/sessions` lists the person's active sessions in every workspace. Sessions belong to a person, not a
tenant. "Sign out" for one device, or for all the others. Another person's session id answers 404, exactly like
a missing one.

### D32. UI details
- New colour tokens `qr-dark` / `qr-light` keep QR codes dark-on-light in **both** themes, because many
  authenticator apps cannot read inverted codes. The Google "G" mark keeps its four brand colours. That is the
  one deliberate exception to "tokens only", required by Google's sign-in branding guidelines.
- After set-up during sign-in, the session is accepted only when the person confirms they saved the recovery
  codes, so the codes cannot be skipped by an automatic redirect. If they reload at that moment, they are signed
  in (the cookie is set) and can create new codes in Settings.
- The "Continue with Google" button appears only when `/auth/providers` says Google is available. With the
  stand-in it says "Development: Google is simulated on this computer."
- The 6-digit field uses `autocomplete="one-time-code"` and a numeric keyboard. Spaces and dashes are ignored.
- Fixed in passing: Settings' system status crashed when `/health/ready` sent back an error body without
  `checks`.

### D33. Tests
- The shared test helper `login()` now completes whichever second step applies, using the **email method**:
  codes are read from the in-memory outbox, so tests do not depend on TOTP time steps. TOTP flows have their
  own tests that move the fake clock.
- `tests/unit/test_devices.py` is named `test_device_ids.py` because pytest needs unique basenames
  (`tests/api/test_devices.py` exists).

## M4: Orgs, roles, invites, RLS

### D34. How Row-Level Security is applied
- Migration `0004` creates a cluster role **`app_tenant`** (no login, not a superuser, no BYPASSRLS, owns
  nothing). Every tenant-owned table (all tables with `org_id`, plus `organizations` by `id`) gets RLS and a
  `tenant_isolation` policy: `org_id = NULLIF(current_setting('app.org_id', true), '')::uuid`, for reading
  (`USING`) and writing (`WITH CHECK`). With no `app.org_id` the comparison is NULL and matches nothing: it
  **fails closed**.
- A workspace request is scoped by `scope_to_org(db, org_id)` (`app/db/tenant.py`). From then on **every**
  transaction of that session starts with `SET LOCAL ROLE app_tenant` and `set_config('app.org_id', …, true)`
  (an `after_begin` hook). Services commit more than once per request, so setting it once would not be enough.
  Both are `LOCAL`, so they end with the transaction and never leak to the next user of a pooled connection.
- Sign-in, invitation acceptance and your own sessions are about a **person**, who may belong to several
  workspaces. They run as the connecting role, which owns the tables. RLS is enabled but not *forced*, so the
  owner is not filtered. This is deliberate: those code paths always filter by user id or token hash.
- `app_tenant` gets only what workspace routes need: e.g. it can insert into `audit_log` but not update or delete
  it, cannot delete memberships, cannot read `login_challenges`, and can only read `users` (global, no `org_id`).
- The spec's "repository layer also filters by `org_id`" is kept: every workspace query has an explicit
  `org_id` condition too.
- `tests/security/test_rls.py` checks that **every table with an `org_id` column** has RLS and the policy, so a
  future table without it fails the build.
- Production (M19): the migration grants `app_tenant` to `CURRENT_USER`, and creating a role needs `CREATEROLE`.
  If the production database user cannot do that, a DBA creates the role once, and the migration skips creating
  it when it already exists.

### D35. Team rules
- **At least one active Owner per workspace.** The service locks the organisation row before any member change
  (so two Owners cannot demote each other at the same moment) and refuses with `409 last_owner`. A database
  trigger (`memberships_keep_an_owner`) is the backstop. Some older tests changed the only Owner directly, so
  they now add a second Owner first.
- **Nobody changes their own role or removes themselves** on the Team screen (`409`). That stops an Owner from
  locking themselves out by accident. "Leave workspace" can come later if wanted.
- **Invitations are for Analysts or Viewers** (R15). To add another Owner, invite them, then change their role.
- **Removal** marks the membership `removed`, ends that person's sessions *in this workspace only* (and their
  refresh tokens), and emails them. Their access in other workspaces is untouched. Inviting them again later
  re-activates the same membership with the invitation's role.
- **Role changes** email the person. Someone made Owner without 2FA keeps their session, but `require()`
  refuses everything and `/auth/me` lists no permissions until they turn 2FA on. The web app already sends such
  a person to `/setup-two-factor` (D25).
- **One open invitation per address and workspace** (a partial unique index). Inviting again, or "Send again",
  creates a new link, and the old one stops working at once. Links last 7 days (spec), and only their SHA-256 is
  stored.
- **30 invitation emails per hour per workspace**, so a stolen Owner session cannot be used to spam people.
- Activity-log entries for team events include the other person's email, so an Owner can read the log without
  looking up ids. Only that workspace's Owners can see it.

### D36. Accepting an invitation
- Link: `/accept-invite#token=…`. The token is in the fragment (D17) and kept in page memory only.
- **New person:** name, password and consent. The link proves they own the address, so the email counts as
  confirmed. They are signed in at once (Analysts and Viewers need no second step). Their consent is recorded for
  that workspace.
- **Existing account, signed out:** they type their password on the invitation page. It runs the same checks as
  sign-in (lockouts, failure log, same error text), then the usual second step, and the session opens in the new
  workspace. This also covers someone removed from every workspace, who could not sign in normally.
- **Already signed in as the invited address:** one click, and the session moves to the new workspace. Signed in
  as someone else: the page says whose invitation it is and offers "Sign out". The API answers `403 wrong_account`.
- A link alone never opens an existing account: whoever intercepts the email still needs the password or a
  session.
- **Google:** a new invitee sets a password. "Continue with Google" later links automatically, because the
  address is confirmed (M3 linking rules). An existing Google-only account is told to sign in with Google and
  then open the link again. Carrying the invitation through Google's redirect would have meant keeping the token
  in browser storage, which D17/D25 avoid.

### D37. Permissions in the API and the web app
- `app/auth/permissions.py` holds BUILD_SPEC 6.2 as data (16 permissions × 3 roles). The only checks are
  `require()` and `allowed()`. Routes use the `requires(Permission.X)` dependency, which checks first and then
  scopes the database session (D34). `tests/unit/test_permissions.py` compares every cell with the spec table.
- **Owners without 2FA may do nothing** in a workspace (403 `two_factor_required`), which covers D25's "owner
  permissions" and more. Analysts and Viewers without 2FA keep their permissions (2FA is optional for them).
- **Viewers may export only items shared with them.** `require(…, EXPORT, resource)` asks the item
  (`is_shared_with(user_id)`). Sharing arrives in M10.
- `/auth/me` (and every sign-in answer) includes `permissions`, so the web app does not keep its own copy of the
  matrix. The menu shows items by permission. Two changes from the 11a.4 menu: **Activity** is shown to
  Analysts as well (6.2 gives them their own entries), and **Settings** to everyone (account, security,
  appearance). Connections and Team stay Owner-only.
- `GET /org` (the workspace profile) needs `shared.view` (everyone). `PATCH /org` needs `org.manage`. In M4 the
  profile is the name and time zone. Plan, key and usage come in M5, impact thresholds and privacy actions later.

### D38. Switching workspace
- `POST /auth/switch-organization` moves the **current session** to another workspace the person belongs to.
  It answers 404 for one they are not in, exactly like a missing one. It returns a new access token; the refresh
  cookie stays the same, so the next refresh also opens the new workspace.
- An access token for the old workspace now answers `401 token_expired` (not `invalid_token`). Other tabs refresh
  silently and follow. The web app clears its query cache whenever the workspace changes, so nothing from the
  old one can show.
- The last workspace's id (not secret) is kept in `localStorage` and sent as `organization_id` at the next sign-in.
  The API ignores it if the person is no longer a member there.
- The switcher sits under the brand in the sidebar (just the initial on the tablet rail) and in the phone header.
  With one workspace it is a plain label.

### D39. Activity log screen
- Owners see the whole workspace and Analysts only their own entries. Asking for someone else's entries returns an
  empty list rather than an error. Viewers get 403. Entries without a workspace (failed sign-in with an unknown
  email) are never shown.
- Filters: person, kind (a prefix that matches whole segments: `auth.login` matches `auth.login.failure`, not
  `auth.loginx`), and from/to dates in the **workspace's time zone**. Pages use a keyset cursor on
  (`at`, `id`), 50 per page.
- CSV export uses the same filters and scope. It holds at most 20,000 rows (`X-Rows-Truncated` says when rows were
  left out) and is built in memory: FastAPI closes the database session before a streamed response finishes.
  The file has a UTF-8 BOM for Excel. Cells that start with `= + - @` are prefixed with `'` (CSV injection). Every
  export is itself logged (`audit.exported`).
- The web app describes each action in plain words ("Changed ravi@… from Viewer to Analyst"), and shows the raw
  name for actions it does not know yet. i18n keys use `_` because `.` means nesting in i18next.

### D40. UI and test details
- **Select is a styled native `<select>`** rather than a custom shadcn list. It is accessible as it stands, and
  phones show their own picker. **Dialog** (Radix) is added for confirmations, with a `danger` button variant
  (white on `loss`, contrast tested).
- Button names include their visible text (WCAG 2.5.3): "Send again to dev@…", "Cancel invitation to dev@…". The
  workspace switcher's name is its visible text plus ", switch workspace".
- `tests/api/test_route_matrix.py` lists **every route in the API**. A new route fails the build until it is
  marked public, person-scoped (tested in its own file) or a workspace route. Workspace routes are then tested
  automatically for 401, 403 per role, 403 for an Owner without 2FA, 404 with another workspace's id, 404 for a
  missing id, 422 and success, plus a check that no data from another workspace appears.
- `tests/tenancy.py` creates people in several workspaces and gives them access tokens directly. That makes the
  matrix fast and independent of the sign-in screens, which have their own tests.

## Scope change (decided by the human before M5)

### D41. Multilingual support is deferred until after the core build
- **Deferred:** multilingual answers (R11 and §7.6, originally in M8) and UI translations (originally in M15).
  M5 to M10 do not build language detection, answers in the user's language, or translated UI strings.
- **Until then:** the agent and briefings answer in English. The Hindi/Kannada golden questions (§7.6, §12.2)
  are left out of the eval until the feature returns.
- **Kept ready for later:** every piece of user-visible text in the web app goes in
  `frontend/src/locales/en.ts` (i18next is already wired), so translation is a matter of adding locale files.
  The existing `users.preferred_language` field and the Indic fonts from M1b stay as they are.
- The Phase 1 eval gate (≥ 85%) is measured over the English questions only.

## M5: Secrets, LLM layer, plans, usage

### D42. What the Managed limit counts, and who sees usage (decided by the human)
- The monthly limit (`organizations.monthly_question_limit`, default `MANAGED_MONTHLY_QUESTION_LIMIT` = 500)
  counts **runs**, not LLM calls. A run is one question or one long task, however many calls it makes. Every call
  of a run shares a `usage_events.run_id`, a column added to the BUILD_SPEC 5 table for this.
- Only `question` and `task` runs **made with the platform's key** count. Briefings and connection checks are
  recorded (tokens and cost) but never count. Runs on a workspace's own key never count, even if the workspace
  switches back to Managed later that month.
- The month is the calendar month **in the workspace's time zone**: an Indian workspace's November starts at
  18:30 UTC on 31 October.
- The limit is checked before a question starts (`keys.resolve`, then `usage.ensure_within_limit`). Two questions
  started at the same moment can both pass, so a workspace may go over by one or two. That is accepted rather
  than holding a lock across an LLM call.
- **Owners only** see usage (`org.manage`): it sits with the plan settings. Others see only which plan is in use.

### D43. Envelope encryption and the `secrets` table
- `app/security/crypto.py` now has a `KeyWrapper` interface with two implementations:
  - `MasterKeyWrapper`: AES-256-GCM under an HKDF key from `MASTER_KEY`. Used in development and tests.
  - `KmsKeyWrapper`: AWS KMS `Encrypt`/`Decrypt`. Used when `KMS_KEY_ID` is set.
- Every value gets its own data key. `secrets` stores `ciphertext`, `wrapped_dek` and `key_version` separately,
  as BUILD_SPEC 5 lists them. `key_version` is 1 for the master key and 100 for KMS (KMS keeps its own versions
  inside the blob), so an envelope never opens with the wrong wrapper.
- The encryption context is `secret:{org_id}:{secret_id}:{purpose}`, and it is also the KMS encryption context.
  A row copied into another workspace, or read for another purpose, fails to decrypt. Decryption failures fail
  closed.
- Workspace requests may `SELECT`, `INSERT` and `DELETE` secrets, never `UPDATE`: replacing a key writes a new
  secret and deletes the old one. `usage_events` is `SELECT`/`INSERT` only, so usage history cannot be changed
  from a workspace request.
- `llm_keys.secret_ref` uses `ON DELETE NO ACTION`, not `RESTRICT`. The check runs at the end of the statement,
  so deleting a workspace (M10) can cascade into both tables in any order. A test deletes a workspace and checks
  that nothing is left behind.
- TOTP secrets (M3) keep the single-blob `seal`/`unseal` format. It is now built on the same envelope code, and
  the format is unchanged.

### D44. The provider layer
- One message model for every provider: `UserMessage`, `AssistantMessage` and `ToolResultsMessage`. An
  `AssistantMessage` keeps the provider's original content in `raw`, which is replayed unchanged to the same
  provider: Gemini `Content` with thought signatures (pitfall 3), and Claude content blocks including thinking.
  Turns written by the *other* provider (after a workspace changes keys) are rewritten as plain text
  (`portable_history`), because native tool-call structures cannot cross providers.
- **Retries.** google-genai does not retry by default, so the Gemini adapter retries 429/500/502/503 after 5, 15
  and 30 seconds, using the API's `retryDelay` when given (capped at 60 s). It never retries an exhausted daily
  quota or an invalid key. Claude uses the SDK's own retries (`max_retries=3`, honours `retry-after`), and
  nothing is added on top, so they do not multiply.
- **Time limits** (found in the live check: one "Test AI connection" took over 6½ minutes while Google was
  overloaded).
  - Each request has `LLM_REQUEST_TIMEOUT_SECONDS` (default 60). google-genai also sends it to Google as the
    server deadline.
  - A request that used its whole time (a 504, or a timeout on our side) is not retried on the same model; the
    next model is tried instead.
  - Each `generate` call has `LLM_CALL_BUDGET_SECONDS` (default 150) for all its retries and fallbacks. No new
    attempt starts after it, so the worst case is about the budget plus one request (about 3½ minutes).
- **Models** are listed from the key and cached per key for an hour, keyed by a hash of the key (pitfall 6).
  - The configured model (`GEMINI_DEFAULT_MODEL`, `CLAUDE_DEFAULT_MODEL`) is used when the key lists it.
  - Otherwise the newest stable model of the preferred family is used: Gemini Flash, then Pro, then Flash-Lite;
    Claude Opus, then Sonnet, then Haiku. Embedding, image, audio, TTS and live variants are skipped.
- **Model fallback** (found while testing against the real API): Google keeps listing `gemini-2.5-flash` for new
  keys but answers 404 "no longer available to new users". At the same time `gemini-3.8-flash` was overloaded
  (503 "high demand") while older Flash models answered. So the clients try up to two other models:
  - a refused model (404) is skipped for that key for an hour;
  - a model that stays overloaded after the retries is skipped for 5 minutes;
  - a model whose **per-model** daily quota is used up (Gemini free tier: 20 requests a day for
    `gemini-3.8-flash`) is skipped until Google says it resets. A project-wide quota still stops with
    "quota", because another model would not help;
  - if every model fails, the last real error is reported.
- **Claude refusal fallbacks.** For the models that support them (`claude-fable-5-1`, `claude-opus-5-5`,
  `claude-opus-5`, `claude-sonnet-5-5`), requests use the beta `server-side-fallback-2026-07-01` with
  `fallbacks="default"`. If a model declines for a safety reason, the API re-runs the request on a suitable model
  in the same call. Usage is recorded against the model that answered.
- The default Claude model is `claude-opus-5-5`, the current Opus. Its thinking cannot be turned off, and the
  thinking counts towards `LLM_MAX_OUTPUT_TOKENS` (3,000). M8 may need a larger budget for Claude.

### D45. Plans and the workspace's own key (decided by the human: switch freely)
- Owners switch between Managed AI and their own key whenever they like (`PUT /org/ai/plan`). Switching to their
  own key needs a saved key. Removing the key switches the workspace back to Managed AI.
- One key per workspace (`llm_keys.org_id` is unique), for Gemini or Claude. Before it is stored, the key is
  checked by listing the provider's models: no tokens are used. A key the provider rejects is not stored (422 on
  the field). A provider that cannot be reached gives 502, and the key is not stored.
- Key checks are limited to 10 per hour per workspace. Otherwise an Owner account could use the platform to test
  stolen API keys in bulk.
- Saving a key does not switch the plan by itself. The web app's "Save key and switch" makes both calls, so the
  activity log has `llm.key_set` and `org.plan_changed`.
- If the server has no platform key, `GET /org/ai` says `managed.available: false` and the screen suggests adding
  an own key. The managed provider is the server's `LLM_DEFAULT_PROVIDER`, not a per-workspace setting.

### D46. "Test AI connection" (decided by the human: Owners only)
- `POST /org/ai/check` sends one tiny request with the key in use and returns the reply, model and tokens. It is
  limited to 10 per hour per workspace.
- It is recorded as usage of kind `check`. It does not count as a question and still works when the monthly
  limit is used up, so an Owner can always test a key.
- `make llm-check` is the command-line version: a real tool-call round trip using the key in `.env`. It writes
  nothing to the database and never prints the key.

### D47. Cost estimates
- Costs come from a price table (`app/llm/pricing.py`, USD per million tokens, standard paid tier, short prompts).
  The longest matching model prefix wins.
- Sources, checked 2026-11-09: Google's pricing page (aistudio.google.com/docs/pricing) and Anthropic's model
  list. Prices have **effective dates**, because Google has announced rises: `gemini-3.6-flash` and
  `gemini-3.8-flash` double on 1 Jan 2027, and `gemini-4-argon` doubles on 1 Dec 2026. Each call is priced at
  the rate of its own day, so old months keep their old prices.
- Models not on the price list (e.g. `gemini-3.5-flash`, `gemini-3.7-flash`) get `est_cost_usd = NULL`, shown
  as "not known for N calls", rather than a guessed price.
- Costs are shown in US dollars because providers bill in dollars. Free-tier keys cost nothing, but the estimate
  uses paid-tier prices.

### D48. Error text and other details
- The API keeps returning plain-English messages. The web app shows its own text from `locales/en.ts` for every
  AI error code (`ai_invalid_key`, `ai_quota`, `ai_rate_limited`, `ai_model_not_found`, `ai_unavailable`,
  `ai_other`, `ai_key_missing`, `ai_key_unreadable`, `ai_not_configured`, `too_many_checks`), so that later
  translation covers them (D41). The server's message is the fallback.
- The log redactor now also recognises Google's newer `AQ.`-style API keys, not only `AIza…` keys.
- The two SDKs install `httpx2`, and Starlette's `TestClient` now uses it. Its stricter cookie types needed small
  fixes in three test helpers. The "httpx is deprecated" warning noted in M1 is gone.

## M6: Connections framework, Excel/CSV, Google Sheets

### D49. Analysts see connections, read-only (`connections.view`)
- BUILD_SPEC 6.2 gives connection management to Owners only and says nothing about seeing them. Analysts ask
  questions about this data, so they need to know which sources exist, how fresh they are and what the columns
  mean. A new permission `connections.view` (Owners and Analysts) allows the list, a connection's details and a
  preview of the first 20 rows. Viewers do not get it: they cannot ask questions.
- The Connections item in the menu now follows `connections.view`. Analysts see the screen without "Add data",
  actions or the access editor.

### D50. Per-connection grants: an explicit "selected" mode (safer than the spec's "no rows = all")
- BUILD_SPEC 5 says: a person with no `connection_grants` rows sees every connection. Taken literally, removing
  the only connection someone was limited to would leave them with no rows, and so give them **every**
  connection. It would also make "this person sees nothing" impossible.
- Closest safe version: `memberships.connection_access` is `all` (the default, which is the spec's behaviour) or
  `selected`. With `selected`, the person sees exactly their grant rows, which may be none. Removing a connection
  never widens anyone's access (tested).
- Owners always see everything; trying to limit an Owner answers 409. Granting another workspace's connection
  answers 404.
- `app.connectors.access` is the one place that decides visibility; M8's agent tools will use the same function,
  so the agent never reads a connection the asking person could not see.

### D51. Reading files: types, dates, amounts
- The header row is the first of the top 20 rows that is mostly text, unique, and wider than half the table
  (title rows such as "UrbanCart sales report" are skipped). No such row: columns are "Column 1", "Column 2", ...
- A final "Total" / "Grand total" row is left out (it would double sums) and reported as an info note.
- A column gets a type when at least 95% of its non-empty values fit. Values that do not fit are left empty and
  counted (a data note says how many, with up to three short examples).
- Dates are read **day first** (Indian convention) unless the values prove month first. Times with a UTC offset,
  and Unix timestamps in a column whose name says it is a time (`created_at`, `*_date`), are converted to the
  workspace's time zone and stored as local wall-clock time, so days and weeks fall where the business sees them.
- Amounts: ₹ / Rs / INR, Indian (12,34,567) and Western (1,234,567) grouping, `(1,200)` negatives, `%`,
  `L`/`lakh`/`Cr`/`crore`. Grouping that is neither (e.g. `1,2`) is not a number. A column with ₹ and another
  currency keeps both values and gets a "mixes currencies" warning; it is not converted.
- Digits that are identifiers stay text: leading zeros (pincodes), names such as phone/mobile/pincode/GSTIN, and
  more than 15 digits. Razorpay's `amount` stays an integer in paise: converting units is the business
  dictionary's job (M7).
- Hidden sheets/tabs and chart sheets are not datasets. Excel formulas are never evaluated: the values Excel saved
  are read (`data_only`). Old `.xls` and password-protected workbooks get a message asking for a plain `.xlsx`.
- Files are checked before opening: type from the content, ZIP parts and unpacked size limited (no "zip bombs"),
  XML parsed by `defusedxml`. The stored object key never contains the uploaded name.

### D52. Reading runs in the background; previews read live
- Reading a whole source (after an upload, choosing spreadsheets, signing in again, "Refresh now") is a Celery job
  (`app.tasks.jobs.sync_connection`). The connection shows `sync_state` `queued`/`running`, and the web app polls
  every 2 s until it is `idle`. A job lost by a restarted worker may be started again after 15 minutes.
- A crash inside the job still leaves the connection in a clear state (`error`, "Reading the data failed
  unexpectedly"), never stuck on "reading".
- The upload request itself only stores the file and does a quick check (it opens, it has sheets). Parsing every
  sheet happens in the job.
- A new file version that cannot be read is marked `failed` and **never replaces** a working version; the
  connection stays live on the previous one and says so.
- The dataset preview (first 20 rows) reads through the cache inside the request, like the agent will (R1).
- The API uses a `JobQueue` interface: Celery in the app, inline in tests (`create_app(inline_jobs=True)`).

### D53. The cache: reuse, "unchanged", incremental, full
- Order of decisions for one dataset: (1) reuse the newest snapshot if it was read **or confirmed** within
  `CACHE_TTL_SECONDS`; (2) ask the source for its version (file version id; Drive's `version` for a
  spreadsheet) and, if unchanged, keep the snapshot and record `checked_at` (no download); (3) for sources that
  support it, fetch only rows changed since the snapshot's cursor and merge them by key; (4) otherwise read
  everything.
- Snapshots keep two times: `fetched_at` (when rows were read) and `checked_at` (when the source last confirmed
  them), so provenance stays honest.
- One snapshot per dataset is kept; older ones are deleted when a new one is written. Every snapshot expires
  after `SNAPSHOT_MAX_AGE_HOURS` from `fetched_at` and a beat job purges expired ones every 15 minutes (DPDP data
  minimisation). An "unchanged" check does not extend the expiry.
- A Redis lock per dataset stops duplicate concurrent reads. It is a small `SET NX EX` lock with a
  check-and-delete release (`app.data.locks`), because redis-py's Lua-based lock does not run on fakeredis.
- No database transaction is held open while a source is being read.
- Incremental merging is built and tested with a fake source; files and Sheets have no row cursor, so they use
  steps 1, 2 and 4. Razorpay, Shopify and Zoho (M11-M13) will use step 3.

### D54. Data-quality checks
- Run on every new snapshot; findings are stored on the snapshot and the dataset, with `kind`, `details` and an
  English `message`. The web app writes its own sentence from `kind` + `details` (`locales/en.ts`, D41); the
  agent and emails will use `message`.
- Period completeness compares each inner week (or month, for monthly data) with the median of up to four periods
  on each side. A period is flagged below 60% of that **and** when the shortfall is more than three standard
  deviations of a Poisson count, so small, noisy tables raise no false alarms.
- "Missing segment" does the same per value of up to four small category columns (2-12 values), flagging a value
  below 25% of its usual weekly count. On the demo data this finds exactly planted finding 7 (Website orders,
  week of 9 Mar 2026) and nothing else.
- Stale: the newest row is older than max(7 days, 3 × the data's usual gap between dates).
- Also: columns ≥ 20% empty, empty columns, an id-like column that is almost unique, exact duplicate rows, mixed
  currencies, values that did not fit the type, row cap reached, Total row dropped.

### D55. Google Sheets: one connection per Google account; Picker; stand-in
- A Google Sheets connection is one Google sign-in (one refresh token) with the spreadsheets chosen in the Picker;
  each visible tab is a dataset `<spreadsheet id>/<tab id>` (the tab id survives renames). Choosing more
  spreadsheets later reuses the sign-in. Removing the connection revokes the token at Google.
- Scopes: `spreadsheets.readonly`, `drive.file`, plus `openid email` to show which Google account a connection
  uses. Granted scopes are checked after every token exchange and refresh: a write scope is refused (and the new
  token revoked); a missing read scope asks the person to allow it.
- Signing in again for a connection must use the same Google account; another account answers 409 and its token
  is revoked.
- The OAuth state is bound to the Owner who started it (Redis, 10 minutes, single use). Google returns to the
  API, which forwards the code to the web app in the URL **fragment**; the web app removes it from the address at
  once and finishes the sign-in with its own access token.
- The Picker needs a short-lived Google access token in the browser. Only Owners receive it
  (`/connections/google/finish` and `/picker-token`); it is never logged (the redactor knows `ya29.`, `1//` and
  `4/` Google tokens).
- `spreadsheets.readonly` technically allows reading any spreadsheet of the account. The server only ever reads
  the spreadsheet ids the Owner picked (stored in the connection's config).
- Without Google credentials, development uses a **stand-in**: an httpx transport that answers Google's token,
  Sheets and Drive URLs from demo workbooks, a consent page served by the API and a Picker dialog in the web
  app. The real connector code runs unchanged on top of it. It is never used outside `APP_ENV=development`.
- Tests replay Google answers from a VCR-style JSON file written in Google's documented formats (no real Google
  account was available to record from). Re-record it against a real account when credentials exist (M19).

### D56. Uploads
- `POST /connections/files` (new connection) and `POST /connections/{id}/files` (new version), multipart, at most
  `UPLOAD_MAX_MB` (50). The request must say its size (`Content-Length`, else 411); larger answers 413 before the
  body is read. 60 uploads per hour per workspace.
- The file name is reduced to its last path part without control characters, and is only shown, never used in
  storage keys (`orgs/<org>/files/<connection>/<version id>`). The stored original is never changed (tested by
  hash).
- Storage encryption at rest relies on the bucket's default encryption (S3 default SSE in production); checked in
  M19.

### D57. Other details
- Connector error messages (e.g. "Google stopped sharing data. Sign in to Google again to reconnect.") come from
  the API, because they name the source's own things (a spreadsheet, a tab). The web app shows them as sent.
  Data notes, column types, statuses and every other label are in `locales/en.ts`.
- New activity-log entries: `connection.added`, `.file_uploaded`, `.renamed`, `.paused`, `.resumed`, `.removed`,
  `.reauthorized`, `.spreadsheets_changed`, `.access_changed`. They name connections, files and spreadsheets,
  never data rows.
- "Replace a connection" (R2) is done for files by uploading a new version. Moving a connection to another kind
  of source and keeping the dictionary mappings needs the business dictionary, so it comes with M7.
- `datasets.position` keeps sheet/tab order (all datasets of one job share `created_at`).
- New dependencies: `pyarrow` (Parquet), `python-multipart` (uploads), `defusedxml` (safe XML for openpyxl).

### D58. A new file version never silently replaces the one in use (fix found in the human's live check)
- **What went wrong in M6:** a one-line text file uploaded as a new version of a 28,540-row file became "In use".
  The reader correctly took the line as a header with one column and no rows; that is not an error, and M6 only
  refused versions that could not be opened. "In use" also meant "the newest readable version", so nothing
  compared the new file with the working one.
- **Now** a new version is read completely before anything changes, then:
  - **no data rows at all** (a one-line file, a header only, blank lines) -> `failed` with "This file has no data
    rows …"; the version in use stays. A first upload without data marks the connection `error` with that reason.
  - **different structure** -> `needs_confirmation`, with the differences stored (`file_versions.review_json`):
    a sheet gone, a sheet that had rows now empty, columns gone, or a column's type changed (whole number and
    number count as the same; so do date and date and time). The version in use stays until an Owner presses
    **Use this version**.
  - otherwise (same sheets and columns with new rows, or only added sheets/columns, which are safe) -> it
    becomes the version in use at once, as before.
- The version in use is now recorded explicitly (`connections.config["file_version_id"]`), not inferred.
  Connections from before this change fall back to their newest readable version until a version is chosen.
- **Switching back:** `POST /connections/{id}/versions/{version_id}/use` makes any readable version the one in use
  (a reviewed upload, or an earlier version), re-reading its stored file. "Refresh now" reads the version in use,
  not the newest one. `DELETE /connections/{id}/versions/{version_id}` discards a version that is not in use and
  deletes its stored file. Both are Owner-only and logged (`connection.version_used`,
  `connection.version_discarded`). The version in use cannot be discarded, and an unreadable version cannot be used.
- Migration `0007` adds the status and `review_json` (reversible; going back turns waiting versions into `failed`).

### D59. Money and units: evidence in code, the dictionary decides (M7)
- The profiler (`app/dictionary/profiler.py`, no AI) collects evidence per numeric column and the draft proposes a
  unit from it: a ₹/Rs/INR marker in the values or heading (`rupee_marker`); a sibling `currency` column with one
  value, INR (`currency_column`, needs a money-like name too); a money-like name such as price, amount, revenue,
  discount, refund, total, fee, tax (`money_name`); whole numbers that are most likely paise.
- **Paise** (D51 left this to the dictionary): `divide_by: 100` is proposed when the name says paise
  (`paise_name`), in a Razorpay-style payments table (an `amount` + `currency` column and three more payment
  columns, whole numbers: `paise_signature`), or for a whole-number `amount` next to INR with every value >= 100
  (`paise_amount`, a weaker guess). A ₹ marker in the data always beats a paise guess.
- Each proposal carries `evidence` (shown to the Owner in plain words) and, when it is a guess or consequential,
  `needs_review` with a reason code: `paise`, `paise_guess`, `money_name_only` (named like money, no currency in the
  data: rupees assumed, Owner asked), `other_currency` (one non-rupee currency: no unit), `ai_unit`, ...
  Items with `needs_review` block **Confirm** until the Owner checks (✓) or edits them.
- Names such as `discounts` are not mistaken for counts (the "not money" words are whole words: `orders_count`,
  `discount_pct`, `quantity`).
- **The AI never overrides evidence.** A unit the model adds where the data has none is applied but flagged
  `ai_unit`; its roles are only taken among identifier/group/detail changes; it cannot alter paise.
- **Rupees through the dictionary:** `units.to_units(frame, entry)` divides every `divide_by` column. Metric
  formulas run on that, so `SUM(amount)` on Razorpay data is rupees. M8's tools must read tables through it.
- The reader still gives `total_price` of `shopify_orders.csv` no unit (the CSV has no ₹); the demo fixture
  `connectionFixtures.ts` now matches that (the M6 hand-over note), and the profiler finds INR from the
  currency column.

### D60. The dictionary: documents, versions, the draft job (M7)
- **Storage:** `dictionary_versions` (RLS, `tenant_isolation`): one row per version with the whole dictionary as
  JSON (`content`, validated by `app/dictionary/model.py`; BUILD_SPEC 9 shape plus `evidence`, `needs_review`,
  `source` and bindings to datasets/connections). At most one `draft` per workspace; the newest `confirmed` one is
  what answers use; older confirmed versions are `superseded` and kept. Profiles are kept on
  `datasets.profile_json`. YAML export: `GET /dictionary/export.yaml`.
- **Drafting** is a Celery job (`draft_dictionary`) that runs after every successful read of a connection, after
  a connection is removed, and on "Draft again". Steps: profile -> compare the *structure hash* (tables, column
  names, types, units; new rows do not change it) with the dictionary being continued -> if unchanged, stop ->
  draft in code, carrying over everything settled -> AI for only what is new -> test joins and metrics -> save.
  One job per workspace at a time (a Redis slot). A schema change updates the Owner's draft (or makes one from the
  confirmed version) with a `diff`; the confirmed version is never changed.
- **A removed and uploaded-again source** (same name, nearly the same columns) is the same table: the Owner's work
  is kept without flags (`replace.rebind_orphans`).
- **Notifying the Owner** of a new draft: a notice on the Connections page and the Dictionary screen. Email
  notifications come with the briefing emails (M10).
- **"Dictionary not confirmed":** `service.active(db, org_id)` returns the newest confirmed version, or the draft with
  `confirmed=False` and `note == "dictionary not confirmed"`. M8 puts that note on every answer until an Owner
  confirms. Analysts read the confirmed dictionary (or the working draft, labelled, before the first confirmation).
- **Who:** `connections.view` reads; `dictionary.edit` (Owners) edits, confirms, redrafts, discards, replaces.
  Tables of connections a person may not see are left out for them (D50). Analyst *suggestions*
  (`dictionary.suggest`) are not built.
- **Edits:** meanings, roles, units, synonyms, the date column, company, metrics (add, change, remove) and which
  joins to keep. Tables, columns and types come from the data and cannot be edited; joins can be removed but not
  added by hand (they are found by testing the data). Anything the Owner touches becomes theirs (`source: owner`)
  and later drafts do not overwrite it.
- **Validation:** metrics are DuckDB SQL expressions run on a sample (5,000 rows, money in rupees) in an in-memory
  engine with external access off and the configuration locked; formulas with `;`, comments, `SELECT`, file or
  extension functions are refused before they run. A metric must give one number. Metrics may use other metrics by
  name. Joins are measured on the stored values: kept at >= 80% match, kept but flagged at 30-80%, dropped below.
  A metric the AI suggested that does not run is dropped (and counted in the notes); one the Owner wrote stays and
  blocks confirming until fixed.

### D61. AI use in drafting (M7)
- All calls go through the M5 layer (`keys.resolve`, usage recorded as kind `dictionary`; migration `0008` widens the
  check). `check_limit=False`: drafting is not a question and does not count against the Managed limit (D42).
- **The model sees the profile only**: column names, types, counts, ranges and at most five samples per column
  (none for columns that look personal: e-mail, phone, names, addresses, GSTIN, cards, or values that look like
  them). No rows. No tools. Money is shown to it in rupees.
- **Few and small:** one call per `DICTIONARY_AI_TABLES_PER_CALL` (8) tables, and only for tables and columns the
  dictionary has not described; an unchanged structure makes no call. The answer is kept on the version with a
  hash of the input; the same input is never sent twice (`cached`). `fresh_ai` ("Draft again with AI") asks again.
- **Retries** are the layer's (429/503 with backoff); an exhausted daily quota or an invalid key is not retried
  and the draft is made in code, with the reason shown (`unavailable:<code>`, `failed:<kind>`, `unreadable`).
- No transaction is held open while the model answers. Output budget: `DICTIONARY_AI_MAX_OUTPUT_TOKENS`
  (6,000, includes thinking).
- Tests use the fake client only; `make llm-check` was not run.

### D62. Replacing a connection (R2, M7)
- `POST /dictionary/replace {from, to, remove_old}` moves the dictionary's mappings to another connection of any
  kind and saves the result as the draft. Tables are matched by column overlap and name (>= 0.34), columns by name,
  label, then the synonyms the dictionary knows. Matched tables keep their key, meaning, roles, units, synonyms,
  metrics (formulas are renamed with the columns) and joins.
- **Flagged for the Owner:** new columns of the new source (`new_column`), a unit the new data contradicts
  (`unit_differs`); **listed in the draft's diff:** old columns and tables with no match. Nothing is guessed.
- No AI is used (the mappings exist). The new connection must have been read (`not_read` otherwise) and a
  dictionary must exist (`no_dictionary`). The old connection is removed afterwards by default (stored copies and
  credentials deleted, logged as `connection.removed` with `replaced: true`); with "keep it" its tables will be
  drafted again as new tables.
- UI: "Replace with another source" on the connection page (Owners).

### D63. Other details (M7)
- New dependencies: `duckdb` (metric checks) and `pyyaml` (export; dev: `types-pyyaml`).
- New activity entries: `dictionary.edited`, `.confirmed`, `.draft_requested`, `.draft_discarded`, `.replaced`.
- All user-visible text is in `locales/en.ts` (D41). Metric errors, review reasons, evidence and AI notes are codes
  from the API, written as sentences by the web app.
- The Dictionary screen is in the menu for Owners and Analysts (`connections.view`).

### D64. Two sources with a table of the same name (M7 bug, fixed at the start of M8)
- **Problem:** the second source's table gets a prefixed key (`draft.dataset_keys`). Removing the first source
  dropped its table from the draft, but metrics and joins kept pointing at it.
- **Take-over:** when drafting finds a table whose source is gone and another source still has a table of the same
  name with (nearly) the same columns (`replace.SAME_TABLE_SCORE`, 0.8), the remaining table takes over the removed
  table's metrics and joins (columns renamed where they match) under the plain key (`orders`, not
  `orders_csv_orders`). Of two identical automatic metrics (two `COUNT(*)`), the shorter name stays. The Owner's or
  AI's descriptions of the removed table's columns are kept where the remaining table has only the code draft's;
  units and roles follow the remaining table's data. The draft's diff lists it (`metrics_moved`).
- **No partner:** the gone table's automatic metrics and its joins are removed (`join_removed` in the diff); metrics
  the Owner or the AI wrote stay, flagged (`metric_orphaned`), and the draft cannot be confirmed until they point at
  another table or are removed. Nothing in a draft points at a table that no longer exists.
- **Warning before removal:** `GET /connections/{id}` returns `dictionary_impact` (which tables take over how many
  metrics, which metrics would have no table, how many joins go); the Remove dialog shows it.
- The confirmed version is never changed (D60). Until the Owner confirms the new draft, the agent (M8) treats a
  metric whose table is gone or hidden as unavailable and says so in the data notes.

### D65. How a question runs (M8)
- **Background job, polled progress.** `POST /conversations` (or `/conversations/{id}/messages`) stores the question
  and an empty answer (`queued`), then queues `app.tasks.jobs.answer_question` (Celery). The worker writes progress
  steps (`progress_json`, codes such as `fetching` with the table and connection) as it goes; the Ask screen polls
  the conversation every second until the answer is `done` or `failed`. Server-sent events were not used: the
  access token lives in memory and `EventSource` cannot send it, and polling one small row is cheap.
- **Before anything is queued:** the per-person rate limit (`QUESTIONS_PER_USER_PER_MINUTE`, 10), the Managed
  monthly limit and whether a key exists (`keys.check_available`, no client is built, nothing is sent). AI not
  available answers 409 like the AI settings do.
- **Short transactions.** No database transaction is open while the model or a source works; each step (progress,
  usage row, final answer) has its own short session.
- **A job that died** (worker restart) would leave an answer `running`: after 30 minutes (longer than the job's
  25-minute time limit) reading it reports `failed` / `timeout`.
- **Reusing an answer:** an identical *first* question (same person, lens, dictionary version; spaces and case
  ignored) within `CACHE_TTL_SECONDS` gets the answer just given, marked "reused": no AI call and nothing counted.
  The data is inside its own reuse window anyway (R1). Follow-ups are never reused.
- **Follow-ups** see the last 8 turns as plain text (questions and the answers' lead and evidence). Earlier tool
  calls are not replayed: a new question fetches fresh data.

### D66. The analytics library (M8, BUILD_SPEC 10)
- Every function works on the DuckDB workspace through SQL built from dictionary names (each checked, then quoted)
  with values as parameters; the workspace checks each statement with the SQL guard too.
- **Dimensions of joined tables** (`customers.region` for orders) use the dictionary's joins; the joined side is
  reduced to one row per key first, so a join can never multiply the metric's rows.
- **New vs repeat customers:** a customer is *new* in a period when their first row in the whole table falls in
  it. The customer column is a key named like a customer, or one joined to a customers table.
- **Mix vs rate:** for metrics that add up (only `SUM`/`COUNT` terms), each value's change is split into a volume
  effect (rows) and a rate effect (per row); they add up to the change exactly.
- **Anomalies:** same weekday over the previous 8 weeks, median and MAD, z >= 3 and >= 10% (and >= ₹10,000 for
  money). The MAD never goes below 0.8 x the natural noise of a total of rows (sqrt of the count; for money
  sqrt(2 x count x value per row)). Without the floor, quiet series of small counts flagged ordinary days (22 flags
  in August 2026 of the demo data); with 0.8 exactly the planted App outage of 2026-08-15 is flagged that month,
  and the missing Website week shows as seven zero days.
- **Forecast:** Holt-Winters with two full seasons, else trend x seasonal index, else a straight line; the 80%
  range is never narrower than the backtest error (an index fitted on one season fits itself too well).
- **Marketing and budget tables** are found by column names (spend, impressions, clicks, conversions, revenue,
  channel, campaign; budget and actual in rupees). P&L and receivables need accounting data (Zoho Books, Tally);
  until then `finance_summary` says so.

### D67. The answer and the number check (M8, BUILD_SPEC 7.4, 7.7, R9)
- The model answers through a `give_answer` tool (lead, key figure, evidence, why it matters, recommendation with
  `impact_inr`/`basis_inr`, data notes, next questions, charts, confidence): one structured call that works the
  same on Gemini and Claude. A plain-text reply is asked once for the structure, then used as it is.
- **Citations:** every figure is followed by its result id, `₹28.0 L [r3]`; a citation up to a few words later
  ("30 orders [r1]") belongs to the figure. A figure without a citation that matches some result gets that
  result's citation added.
- **Matching:** within half a unit of the precision it is written with (₹12.4 L matches ₹12,35,000 to
  ₹12,45,000), lakh/crore/thousand, `%` against both 17.7 and 0.177, "x" ratios, the sign ignored ("fell 17.7%" vs
  -17.7). Dates, years (1900-2100 without a unit), quarters, times, ids (`#1004`, `r12`) and counts up to 12
  ("top 3") are not figures.
- **Repair:** unsupported figures go back to the model once as the `give_answer` call's error, with the list;
  what is still unsupported after that is removed sentence by sentence (a removed lead is replaced by the first
  remaining evidence bullet) and a data note says so.
- **₹ impact (R9):** a recommendation stays when the question asks for advice (recommend, should we, how can I,
  improve, reduce, ...), or when its `impact_inr` matches a result and is material: >= the workspace's
  `impact_threshold_inr` (₹1 lakh) or >= `impact_threshold_pct` (5%) of `basis_inr`. Otherwise it is removed with a
  note.
- **Data notes from code** are codes (the web app writes the sentence, D41): dictionary not confirmed, metrics
  whose table was removed (D64), periods past the data, partial buckets, attribution limits, figures removed, a
  restart. Quality warnings of the tables used come with the answer and show as QualityBanners.

### D68. Limits (M8, BUILD_SPEC 6.5)
- A step is one model call. The last allowed call (`AGENT_MAX_STEPS`, 12) offers only `give_answer` and asks for the
  answer with what was found, so a long question still ends with an answer and says what it could not check.
- `TOKEN_BUDGET_PER_QUESTION` (150k input tokens, summed over the calls): when spent, the question stops with
  `limit:tokens` and the screen suggests narrowing it.
- `ROWS_TO_LLM_PER_TOOL_CALL` (200) rows per tool result, with the total row count; numbers are rounded for the
  model (2 decimals; 4 significant digits below 1). `SOURCE_QUERY_TIMEOUT_SECONDS` (30) interrupts a workspace
  query.
- `LLM_MAX_OUTPUT_TOKENS` stays 3,000 (M5 note): the answer is a short tool call; a cut-off reply is asked once to
  answer briefly.
- **Model fallback mid-question** (M5 note): when the provider layer has moved to another model and the call fails
  with an `other` error, the question starts again once from the plain question on the new model; results already
  computed stay citable. Quota, invalid key and other kinds end the question with their kind (`llm:quota`, ...)
  and are never retried by the agent (the M5 layer already retried 429/503).

### D69. What the agent may see (M8, P3, P6, BUILD_SPEC 6.2)
- `AccessContext` is built from the database for the asking person (the job has no request), with the
  Connections screen's rules (D50); paused connections are left out.
- `visible_dictionary` removes tables of connections the person may not see and every metric and join on them.
  Metrics whose table was **removed** are named in a data note; metrics of **hidden** tables are dropped without a
  word (their names are not the person's to know).
- Tools work on that dictionary only: `run_sql` accepts only its table keys, other tools resolve names through
  it, so a hidden table cannot be reached by any tool.
- The prompt holds the dictionary summary (tables, columns with role and unit, owner-written meanings, metrics,
  joins), never sample values. `describe_dataset` gives the model three sample rows with personal columns (by the
  profiler's flag) shown as "(hidden)".
- `query_source` (push-down SQL to a customer database) is not offered until the database connector (M14).
- The activity log gets `question.asked` with a SHA-256 of the question, the lens, the tables read and whether the
  answer was reused: never the question text or the answer.

### D70. Ask screen (M8)
- `/ask` (new question: lens, starters, saved questions) and `/ask/{id}` (the conversation). History is per person;
  someone else's conversation or saved question answers 404 (route matrix: `PERSONAL`, tested in `test_chat.py`).
- Viewers have no `ask` permission: the Ask screen tells them so (the menu keeps its shape for every role).
- Answers: the lead with the haldi mark on the key figure, numbered SourceMarks for citations, evidence rows,
  charts, data notes, next questions, "Save question" on the question and "Show my work" (steps with time, rows,
  errors and SQL; sources with fetch times) as a drawer, a bottom sheet on phones.
- Charts: the API sends a small neutral spec (type, title, categories, series, unit); `ChartCanvas` turns it into
  ECharts options themed from the tokens (series colours in fixed order, dashed/dotted lines so colour is never
  alone, no animation under reduced motion). ECharts is used directly (`echarts/core`, line and bar only, SVG
  renderer): the `echarts-for-react` wrapper broke in the production build (its CommonJS default export arrived as
  an object). It is a lazily loaded chunk (170 KB gzipped), outside the shell's 200 KB budget, and a chart that
  cannot be drawn never takes the answer down (error boundary).
- Pin, Export and Share are not on the answer yet (M10, M15). The command palette (Ctrl/Cmd+K) is not built yet.
- Multilingual answers stay deferred (D41): the prompt asks for English; all text is in `locales/en.ts`. The
  existing `AnswerSheet` labels moved there too.

### D71. What the what-if simulator can simulate (M9, BUILD_SPEC 10.4, R12)
- **Levers built:** `price` (percent), `discount` (percentage points of the discount rate) and `marketing_spend`
  (percent, one channel). BUILD_SPEC 10.4 also lists staffing/cost line, product mix and stock. They are **not**
  built: they need a cost or inventory model that no connected source can supply yet (accounting data arrives with
  M13/M16, industry defaults with M17). The `what_if` tool and its playbook tell the model to say so and to offer
  the nearest lever. This is a deliberate narrowing, not an oversight.
- **Metrics:** a plain-sum metric in rupees (revenue) or a count (orders), over a table that has a price column
  (`unit_price` or `total_line_items_price`) and, for the discount lever, a discount column. Ratios (AOV) are refused
  (`metric_not_supported`): their parts do not add up. Columns are found by name, label and synonym, like the
  marketing columns (D66).
- **Driver model:** revenue = units x net price. A price change `x` moves revenue by `(1 + x) * (1 + x) ** e`; a
  discount change moves the net price paid by `(1 - d') / (1 - d)` and units by that ratio to the power `e`; spend
  moves a channel's revenue by `(1 + x) ** beta` (diminishing returns). The discount rate is capped at 90%.
- **No AI in the numbers (P4).** The model only turns words into `{lever, change, scope, horizon}`; every figure is
  computed in `app/analytics/whatif.py`. A missing metric defaults to `revenue` when the dictionary has it.

### D72. Where the response comes from: history first, a labelled default second (M9)
- **Estimate:** weekly units of the scope against the average list price (price lever) or the average share of
  the price paid (discount lever), log-log, with a trend and **month-of-year controls** (smooth annual harmonics if a
  year of weeks cannot separate the months). It is used only when the price moved at least 4% (discount: 3%) over
  the history, there are at least 26 weeks, the sign is negative within [-6, -0.05] and |t| >= 1.5.
- **Why month controls:** the demo data raises discounts 8 points in Oct-Nov, exactly when demand peaks. With two
  smooth harmonics the regression read that season as a response (elasticity -3.1, "high confidence"); with month
  controls it correctly says the history cannot tell and falls back to the default. A wrongly confident answer is the
  worse failure, so the stricter control stays.
- **Default:** industry figures in `DEFAULTS` (price, discount, spend), shown as a range of 1.5x to 0.5x of the value
  and always labelled `default` with the reason (`too_little_history`, `history_did_not_move`,
  `history_inconclusive`). The industry templates of M17 may refine them.
- **The person's own figure** (`assumptions` on the request) replaces the estimate and is labelled `you`; the range
  then comes from the forecast alone. Signs are checked: price and discount responses must be negative, spend's
  positive, all within [-10, 10].
- **Marketing:** the response is fitted across all campaign rows with a fixed effect per channel (needs 12 rows).
  Attributed revenue per quarter is not an experiment (spend and revenue move together by construction), so marketing
  confidence is **never above low** (`attribution_estimate`), and the note on platform attribution always shows.

### D73. Range, confidence and side effects (M9, R14)
- **Baseline** is the metric's own forecast for the scope (`forecast.forecast`, monthly, 1 to 6 months); the
  scenario is the baseline times the effect. The 80% range combines the forecast range with the elasticity's own 80%
  range (lowest and highest effect over the three elasticities); `delta_low <= delta_expected <= delta_high` always.
  The range is deliberately wide: it is better to show that a decision's direction is unclear than to hide it
  (the screen says "the range includes no change" when it does).
- **Confidence:** `high` needs |t| >= 3, a year of weeks and R^2 >= 0.5; `medium` |t| >= 2, or the person's own
  figure; everything else, and every default, is `low`. A poor baseline (backtest MAPE above 25%) lowers it a step.
- **Side effects** (code, written in words on the screen): the change in units sold; for a discount, the discount
  given away before and after; the share of the last 12 months' revenue from customers who ordered more than once
  (the ones who react first); for spend, the extra spend and the net effect after it. All from code.
- **Surprising results are shown, not hidden.** On the demo data a 10% rise on South Electronics is estimated
  as inelastic (elasticity about -0.6, medium confidence), so revenue rises about 4%. The playbook asks the model to
  say that this comes from the past and may not hold for a bigger change.

### D74. The What-if API: the same tool, no AI (M9)
- `GET /whatif/options` (what can be simulated, from the dictionary only, no data read), `GET /whatif/values`
  (up to 50 values of a dimension column, to pick a scope) and `POST /whatif/run`. A run calls the agent's
  `what_if` tool directly: same code, same numbers, **no model call and nothing metered**, so moving a slider costs
  nothing and counts as no question (BUILD_SPEC 10.4 "no LLM call for slider moves").
- Permission `ask` (Owners and Analysts). The context is the asking person's own visible dictionary (P6): hidden
  connections' tables are not there. No dictionary -> 409 `no_dictionary`; no visible tables -> 409 `no_sources`;
  a scenario that cannot be worked out -> 422 `whatif_<code>` (the web app writes the sentence, D41). 60 runs per
  person per minute (429 `too_many_runs`).
- The database transaction is released before data is fetched. A slider move recomputes on the server (cache-aware
  reads, a fraction of a second on the demo data) rather than duplicating the model in the browser.
- No new table: results are not stored. The activity log is not written for a run (no model, no stored answer); a
  question that uses the tool is logged as any question (D69).
- Route matrix: `GET /whatif/options` is a normal case; `values` and `run` need a connection with data, so they are
  in `DATA_ROUTES` and tested in `tests/api/test_whatif.py` (401, 403 for Viewers and Owners without 2FA, success,
  other workspaces, 422, the limit).

### D75. The What-if screen (M9)
- `/what-if` (Owners, Analysts; Viewers are told their role cannot run what-ifs): a form (lever, metric, scope,
  how long, how much) and, after "Run scenario", the headline with the haldi mark on the expected change **and its
  range**, the `ScenarioCard` (the lever slider re-runs on release; baseline vs scenario bars with the range
  whisker; the assumptions in words, each labelled history / typical figure / your figure; confidence with its
  reason; "Edit assumptions"), a chart, the side effects and a month-by-month ledger with the data source and age.
- `ScenarioCard` text moved to `locales/en.ts` and it gained `onLeverCommit`. The page loads on first visit (its
  own 12.6 KB chunk); the shell grew from 181.2 to 184.2 KB of 200 KB.
- "Describe it in words" opens Ask, where the `what_if` tool and its playbook do the translation. `/ask?q=...` opens
  the box with a question typed in.
- Golden scenarios: `eval/whatif_scenarios.yaml` (five, with expected ranges), run in `tests/eval/` directly and
  through the agent with the fake model. The same file feeds `make eval` in M10.

### D76. Dictionary review fixes after removing and re-uploading a source (M9)
- **Flagged metrics are visible and can be cleared.** A metric flagged `needs_review` (today only when its table was
  removed, D64) counted towards "items left to check" but showed only "Works", with no way to clear it except editing
  its formula. It now has the same caution strip and "Looks right" button as a column or join; checking it keeps the
  formula and its test result.
- **A table that comes back clears the flags it caused.** When a dataset is added under the key of a removed one (the
  same file uploaded again), metrics that point at that key lose the flag and the draft's change list says so
  (`metrics_restored`). They are tested like every metric, so one that no longer runs still blocks confirming. A flag
  on a metric whose table is still gone stays.
- **Removing a source warns that edits go with it.** The Remove dialog says that meanings, units (paise) and formulas
  for its tables are lost and that uploading the same file again starts from a new guess; it points to "Upload a new
  version" on the connection, which keeps them. Keeping a removed table's edits for a re-upload was considered and
  not built (the Owner asked for the small fix).


### D77. Growth signals (M10, BUILD_SPEC 10.3)
- `app/analytics/growth.py`, exact code over the workspace, also an agent tool (`growth_signals`). Complete periods
  only: a month still running is left out.
- **Streak:** a segment (a value of one dimension, or a pair such as category × region) whose metric rose in each of
  the last 3+ periods **and** whose share of the whole rose each time. The share rule takes out the season (in the
  demo, every segment sells more from June to September; only West × Beauty gains share every month). A segment must
  be at least 1% of the whole and have grown at least 10% over its streak. A single value that only echoes a pair's
  streak is dropped.
- **Beats last year:** the whole metric above the same period a year earlier for 3+ periods in a row.
- For the top 3 streaks: the share of the increase from **repeat customers** (customers first seen in the table before
  the streak began) and an **estimated upside** (one more period at the streak's average step), labelled an estimate.
- Default dimensions are chosen by name (region, category/type, channel, segment, outlet, store, state, city) and size
  (2-40 values); status, gateway, currency, method, ids and names are never used.
- On the demo data: West × Beauty, 5 months in a row, 48 to 210 orders, about 81% from repeat customers (the answer key
  says 101% with a slightly different definition of "repeat": an order with an earlier order in the same table).
  North × Electronics also shows a 3-month streak; that is in the data, not planted.

### D78. Briefings: facts by code, words by one AI call (M10, BUILD_SPEC 13)
- **Facts** (`app/briefings/facts.py`) come from the agent's own tools, run with the **recipient's** `AccessContext` and
  visible dictionary, so every figure has a result id and provenance: KPI snapshot (latest day vs the same weekday a
  week earlier; last 7 days vs the 7 before), unusual numbers (`detect_anomalies`, last 7 days, by the best segment
  dimension), growth signals (last 12 months), risks (data-quality warnings, connections in error, data that stopped
  arriving), finished long tasks. Goals arrive in M15.
- **Latest day of data** = the newest day the main table covers, up to yesterday in the recipient's time zone. A
  workspace whose data ends earlier (the demo ends 2026-09-30) still gets a briefing about its last days, with a note
  saying how old it is, and "stale data" becomes an alert.
- **Alerts** = an unusual drop on the latest day, a connection in error or needing sign-in, data that stopped.
- **Words:** one small AI call with a `write_briefing` tool (subject, headline, key figure, sections), from the compact
  facts only (no rows, no personal data), following the daily-briefing playbook. Then the number check (R14): an item
  with an unsupported figure is removed (note `numbers_removed`); an unsupported headline makes the whole text unusable.
  No repair turn (one call per briefing).
- **Fallback:** when AI cannot be used (no key, quota, error, unusable answer), code writes plain sentences from the
  same facts, with the same citations. A briefing is never lost because of the AI. `written_by` says which.
- **Cache:** identical facts + language reuse the written words for a day (Redis), checked before any AI client is
  made, so two people with the same access cost one call.
- **Metering:** each call is a `usage_events` row of kind `briefing`; briefings do not count towards the Managed
  question limit (D42) and are made even when it is reached.
- **Suppression:** unusual numbers already sent to the person in the last 3 days, and growth signals in the last 7,
  are left out (fingerprints in `briefing_runs.findings`).
- **Email:** HTML for phones + plain text, the haldi mark on the headline's key figure, every figure followed by a
  small number that links to the report in the app, a button to Today and a link to the briefing settings. Stored as a
  report (source `briefing`).
- **Language:** stored per person; English only for now (D41). The settings screen says so.

### D79. Briefing schedule (M10, BUILD_SPEC 13.1)
- Beat job every 5 minutes. A slot is due from 5 minutes before its time to 30 minutes after it (a short worker or
  beat outage is caught up). Defaults: Owners on at 06:00 daily in the workspace's time zone; Analysts and Viewers off
  until they turn it on. Frequencies: daily, weekly (chosen weekday), alerts only (made daily, sent only when there is
  an alert or an unusual number), off. A person may choose their own time zone.
- Idempotent: a **partial unique index** on (workspace, person, slot) for `kind = 'scheduled'` and
  `INSERT ... ON CONFLICT DO NOTHING`; only a newly added run is queued. "Send me a briefing now" runs (`kind = 'test'`)
  are not limited by it (3 an hour per person instead).
- Only active members with a confirmed email get briefings; a workspace waiting for deletion gets none.
- The scheduler reads across workspaces unscoped (like the snapshot purge); each run is written with its workspace id
  and composed by a scoped job.

### D80. Long tasks (M10, BUILD_SPEC R10, 13.5, 6.5)
- One agent runner for questions and tasks (`agent.service.run_agent`, refactored out of M8's question job). A task uses
  the long limits: 40 steps, the long source timeout (120 s), and a token budget scaled with the steps (3 × 150k).
- Before each model call the loop checks whether the author cancelled (`should_stop`); a cancelled task stops without
  another AI call. A queued task is cancelled at once.
- At most `CONCURRENT_LONG_TASKS_PER_ORG` (2) tasks queued or running per workspace (429). The same rate and Managed
  limits as a question apply; a task counts as one run towards the monthly limit (D42).
- A task running for more than 2 hours is reported as failed (`timeout`), like a stale answer.
- A finished task becomes a report (the answer, its provenance with the cited results' rows, data notes); its PDF is
  made at once and kept in object storage. The author gets an email with the lead, the evidence, a link to the report
  and the PDF attached (if 5 MB or less). A failed task is emailed with the reason in words.
- Submitting is in the activity log (`task.submitted`, a hash of the text); so is cancelling.

### D81. Exports (M10, BUILD_SPEC X3)
- One `Document` shape (`app/exports/document.py`) for answers, task reports and briefings. Citations become numbered
  source marks; a Sources list gives tables, connection, fetch time, rows and the query.
- **PDF** (WeasyPrint): the design system in print (indigo ink, ledger rules, one haldi mark on the key figure, Noto
  fonts for Indian scripts). Charts are drawn as SVG by code (ECharts cannot run there) with a one-line text summary.
  WeasyPrint gets no base URL and a fetcher that refuses everything: text the model wrote can never load a file or a
  web address. Every text is escaped.
- **Excel** (openpyxl): Summary, Sources, one sheet per cited result's rows, the data behind each chart. Values only;
  text starting with `=`, `+`, `-`, `@`, tab or CR is stored with a leading apostrophe (formula injection).
- To export rows, **cited results keep the rows they showed** (at most 200, the same rows the model saw) in their
  provenance (`data`). Uncited results keep provenance only.
- An answer's files are made on each export and not kept (BUILD_SPEC 6.6, nothing extra stored); a report's files are
  made once and kept in object storage (`pdf_key`, `xlsx_key`), deleted with the workspace.
- Note sentences for PDF, Excel and email are an English copy of `ask.notes` in `locales/en.ts`
  (`app/exports/text.py`); a test keeps the two in step.
- Viewers export only items shared with them (BUILD_SPEC 6.2; `require(EXPORT, resource=SharedItem)`). Every export is
  in the activity log (`export.created`, format, shared or own).
- The backend image installs Pango and `fonts-noto-core` for WeasyPrint; on a Mac, Pango from Homebrew is used.

### D82. Sharing with the recipient's permissions (M10, BUILD_SPEC X3, P6)
- A person shares their own answer or report with active members of the same workspace. The recipient may see it only
  when their connection access covers **every connection its results read** (Owners and "all connections": always;
  "only these": each one granted; a removed connection counts as not granted). Checked when sharing (409 naming who
  cannot see it; the dialog lists them but they cannot be chosen) and again on every view: if access shrinks later,
  the item disappears for them (404).
- Paused connections still count as theirs: pausing stops new reads, it does not take away what was shared.
- The person who shared it or the item's owner can stop sharing. The recipient gets an email with a link
  (`/shared/{id}`). Creating and removing a share are in the activity log. Sharing an item again changes nothing.

### D83. Today and the first insight (M10, BUILD_SPEC 11a.8)
- `/today` costs no AI call and reads no source: the latest briefing (top 3 items per section, key numbers), three
  suggested questions written by code from the briefing's findings and the dictionary the person may see, the
  onboarding checklist, the latest long tasks and items shared with them, and a banner while the workspace waits for
  deletion.
- Checklist (Owners): **Company details** = the kind of business is chosen (new "Kind of business" field in Settings,
  `PATCH /org` `industry`), **Connect data** = any connection, **Confirm dictionary** = a confirmed version exists,
  **Ask first question** = this person asked one. Each step links to where it is done.
- **First insight:** after an Owner's first dictionary confirmation (no question asked yet), the app opens Ask and runs
  a health-check question at once (`/ask?q=...&run=1`). That is one AI question, counted as usual.
- "Send me a briefing now" on Today and Briefings makes one on demand (3 an hour).

### D84. Command palette (M10, BUILD_SPEC 11a.8)
- Ctrl/Cmd + K opens a dialog that filters screens (as the menu shows them to this person), "Shared with you", saved
  questions and connections; arrow keys and Enter go there. It loads on first use (not part of the shell's first load).

### D85. Privacy: data export and workspace deletion (M10, BUILD_SPEC 6.6)
- **Export** (Owners, 5 an hour): a ZIP of JSON files (workspace, members, connections and tables, dictionary
  versions, conversations with answers and sources, saved questions, tasks, reports, shares, briefing settings and
  runs, AI usage) and the activity log as CSV, with a README. Never included: passwords, connection credentials and AI
  keys; cached copies of the sources (they expire within a day); uploaded files (the Owner has the originals).
- **Deletion:** an Owner types the workspace's name; `deletion_requested_at` is set and every Owner is emailed the date
  (7 days later). Any Owner can cancel until then. Meanwhile the workspace works, with a banner, and briefings stop.
- **Purge** (beat job, hourly): every connection is removed the normal way (files, snapshots, secrets deleted, Google
  token revoked), report files are deleted, then the workspace row is deleted and the database cascades to every
  table with its id. One anonymised activity entry `org.deleted` stays: no names, only counts and dates.
- **Kept on purpose:** people's accounts (an account is global and may belong to other workspaces; a person can still
  sign in and create a new workspace), and the workspace's earlier activity-log entries (the log is append-only by
  design, BUILD_SPEC 5). Both are open questions for M19: deleting accounts that belong to no workspace, and a
  retention limit for the activity log.
- **Approved by the Owner (2026-11-13):** keep accounts and keep the earlier activity-log entries when a workspace is
  deleted; both stay open for M19.

### D86. Accuracy evaluation (M10, BUILD_SPEC 12.2, X4)
- `eval/golden_questions.yaml`: 48 questions (every planted finding, everyday KPI questions, 4 Hindi and 4 Kannada
  questions, the 5 what-ifs of `whatif_scenarios.yaml` by id), each with expected facts (numbers within a tolerance,
  named drivers, a change range for what-ifs) and the ideal tool plan.
- `eval/harness.py`: one pipeline (the agent loop, its tools over the demo tables, the number check, the scorer).
  **CI** replays each plan with a stand-in model that answers with the figures the tools returned: it checks the
  harness and the tool layer, and must pass 100%. **`make eval`** (opt-in; `ONLY=`, `LIMIT=`, `LIST=1`) runs the
  configured real model through the app's LLM layer (retries, fallbacks and the Gemini pitfalls apply) and writes
  `eval/reports/<date>.md` with the pass rate (Phase 1 needs 85%), each answer, and tokens and estimated cost per run.
- Scoring ignores signs ("fell 17.7%" = "-17.7%"); money may be written in lakh/crore. Hindi and Kannada questions
  are scored on their facts; answers are in English for now (D41).

### D87. Two M8 bugs found while building M10
- `detect_anomalies` raised a TypeError when a series had too little history for a baseline (empty scores compared
  with a date). The agent saw "bad input"; briefings on short data failed. Now such a series is skipped (nothing to
  compare with).
- Opening a conversation marked only the first stale answer as failed (`any(...)` stops at the first match); now every
  stale answer (and every stale long task) is marked.

### D88. Dictionary metric check label (confirmed 2026-11-13)
- The Owner confirmed the label stays **"Checked"**: no change. A flagged metric's button reads "Looks right" until it
  is pressed, then the metric shows "Checked" (`dictionary` strings `check` / `checked` in `locales/en.ts`, D76).

### D89. Razorpay credentials, read-only, key handling (M11, BUILD_SPEC 8.4)
- An Owner enters a **key id** (`rzp_live_…` or `rzp_test_…`) and the **key secret**. The secret is checked with one
  read (`GET /payments?count=1`) **before** anything is saved, then encrypted in the secret store (purpose
  `razorpay_key_secret`, bound to the workspace and row). The key id is not a secret and sits in the connection's
  `config` next to the mode (live/test); the API and the screens show it masked (`rzp_live_AbCd••••`). The secret is
  write-only, never in a response, log or activity entry (`connection.added` records type, name and mode only).
- **Read-only twice:** the connector uses `ReadOnlyHttpClient` with no allowed POSTs (GET only; the wrapper and the
  request hook both refuse anything else; tested). Razorpay keys cannot be limited to read-only, so the dialog and the
  connection page say so and recommend a dedicated key. Test-mode keys are labelled as such.
- Replacing a key (revoked, rotated) checks the new key first, stores a new secret, deletes the old one and reads again;
  nothing changes if the new key is refused. Key checks: 20 per workspace per hour.
- **Left out on purpose:** subscriptions (optional in the spec), customers, and any personal data (email, phone, card
  and UPI details): the four tables hold only what an analysis needs. Razorpay's lists are all `GET`.

### D90. Razorpay reading: windows, incremental, refresh (M11, BUILD_SPEC 8.1, 8.4)
- A read pages with `count=100` and `skip` inside **31-day windows** (`from`/`to`), newest window first, so `skip` never
  gets deep and a row cap drops the oldest rows. The first read goes back `RAZORPAY_HISTORY_DAYS` (365).
- **Incremental:** the cursor is the newest `created_at`. A payment changes after creation (authorized → captured →
  refunded), so the next read starts `RAZORPAY_REFRESH_LOOKBACK_DAYS` (30) before the cursor and its rows replace
  earlier copies by `id`. Refunds and settlements that appear later than that window after a payment are still found
  in their own tables. A payment whose status changes more than 30 days after its creation is not noticed until
  "Refresh now" (a full read of the history window).
- "Refresh now" is a **full** read (the cache's `force`), not incremental. Questions asked after the cache time passes
  use the incremental read.
- Razorpay cannot say "nothing changed", so `source_version` is empty and an expired cache always asks for the delta.
- Retries (429/5xx with backoff, `Retry-After`) come from the shared read-only client. A 401/403 is `needs_reauth`.

### D91. Money in paise, declared by the source (M11)
- Razorpay money columns are **whole paise** and stay so in the stored table: payments `amount`, `amount_refunded`,
  `fee`, `tax`; orders `amount`, `amount_paid`, `amount_due`; refunds `amount`; settlements `amount`, `fees`, `tax`.
- The connector marks these columns `stored_in="paise"` (new optional field on `ColumnInfo`); the profiler turns that
  into `INR`, `divide_by: 100` with the evidence `source_paise` ("Razorpay sends this amount in paise"). Before this,
  only a payments table recognised by its column names was detected, so `settlements` (no currency column) would have
  been missed. The dictionary and every metric, as for the demo CSV, divide by 100 (`app/dictionary/units.py`).
- **Development stand-in:** keys starting `rzp_test_demo` are answered by a local stand-in serving the demo
  `razorpay_payments.csv` (orders, refunds and settlements derived from it) in Razorpay's format. It exists only when
  `APP_ENV=development`; every other key goes to the real Razorpay. CI and tests use a fake Razorpay, never the network.

### D92. Shopify sign-in, scopes and personal data (M12, BUILD_SPEC 8.5)
- **OAuth authorization-code flow** of a Shopify app (custom or public distribution; client id and secret from
  `SHOPIFY_API_KEY` / `SHOPIFY_API_SECRET`). The Owner types the store address (`your-store`, `your-store.myshopify.com`
  or an `admin.shopify.com/store/…` address; anything that is not a `*.myshopify.com` host is refused, so the token
  request cannot be sent elsewhere). `POST /connections/shopify/start` keeps a 10-minute state in Redis with the
  workspace, person and store.
- **Callback** (`GET /connections/shopify/callback`, public): Shopify's HMAC over the query string (signed with the
  app secret) must be valid, the `state` known and the `shop` the one the flow started with; only then is the state
  marked verified. The code travels to the web app in the URL fragment (as for Google, D55) and the signed-in Owner
  who started the flow finishes it (`POST /connections/shopify/finish`, single use). Finish refuses a state that was
  never verified, so a code can only come back through a callback Shopify signed.
- **Scopes:** `read_orders`, `read_products`, `read_customers`, `read_inventory` (+ `read_all_orders` when Shopify
  approved it; without it Shopify returns only the last 60 days of orders, which the test result and the connection
  page say). `SHOPIFY_SCOPES` can change the list but anything that is not `read_*` is dropped. After the token
  exchange the granted scopes are checked: any scope that is not `read_*` refuses the connection and nothing is stored;
  orders, products and customers are required. `read_inventory` is optional (variant cost is then left empty).
- The token is checked with one read (shop name and currency) before it is stored encrypted (purpose
  `shopify_access_token`); `config` keeps the store address, shop name, currency and granted scopes (not secrets).
  Signing in again (needs sign-in, or "Sign in to Shopify again") replaces the token and deletes the old secret.
- **Personal data:** never requested: customer names, e-mail addresses, phone numbers, street addresses, postcodes,
  notes, billing addresses. Customers carry only their default address's **city, state and country**, and orders the
  shipping **city, state and country** (regional analysis such as "South revenue fell" needs the state). A test
  checks that no query asks for the personal fields.
- **Removing** a Shopify connection deletes its token and cached data at once. The app is not uninstalled in Shopify:
  that needs a write (Shopify revokes a token with a DELETE), which the read-only client never sends. The connection
  page tells the Owner to uninstall the app in the Shopify admin to stop it completely.
- Shopify's newer **expiring offline tokens** (with refresh tokens) are not used yet: the flow asks for a normal
  offline token. When Shopify stops accepting a token, the connection shows "Sign in to Shopify again".

### D93. GraphQL queries only, with one read-only exception (M12, BUILD_SPEC 6.3, 8.5; approved by the Owner)
- Every GraphQL document is parsed with `graphql-core` before it is sent (`app/connectors/graphql_guard.py`): exactly
  one operation; a **query** passes; a **subscription** or any **mutation** raises `ReadOnlyViolation`.
- **The exception:** large histories need Shopify's bulk export, which is started with the mutation
  `bulkOperationRunQuery`. It only reads (it creates an export job on Shopify's side and works with read-only scopes).
  The Owner chose (2026-11-16) to allow **only this mutation, only in its exact shape**: one root field of that name,
  no alias or directives, the single argument `query` (a string or a variable holding one), a selection of
  `bulkOperation { id status … }` and `userErrors { field message … }` only, no fragments, and the inner document must
  itself pass as a pure query. `orderUpdate`, `customerDelete`, `productCreate`, `orderCancel`, `bulkOperationCancel`,
  `bulkOperationRunMutation`, a bulk read with a write beside it, and every other mutation are refused (tested).
- **Checked twice:** by the connector before sending, and again in `ReadOnlyHttpClient` (a new `post_guard` hook that
  parses the JSON body of every POST to a `graphql.json` endpoint), so even code that skipped the first check cannot
  send a mutation. The connector's client allows POST only to the store's GraphQL endpoint; the sign-in client only to
  its token endpoint. PUT, PATCH, DELETE and REST POSTs are refused as for every connector.

### D94. Shopify reading: tables, paging, bulk exports, cost limits, incremental (M12, BUILD_SPEC 8.1, 8.5)
- **Five tables:** `orders` (one row per order; test orders left out), `order_items` (one row per line item, with the
  order's id and dates, product type and SKU), `customers`, `products`, `variants` (price, cost per item, stock).
  Ids are Shopify's numeric ids (the end of the GID), times are converted to the workspace's time zone.
  `total_line_items_price` = subtotal + discounts (Shopify's GraphQL has no such field; the same meaning as the CSV
  export and the demo file).
- **Paged queries** (`first`/`after`, newest first by `UPDATED_AT` where Shopify can sort that way) for small tables and
  every incremental read. An order with more line items than one page holds gets its remaining items with a small
  follow-up query.
- **Bulk export** for a **first read** of a table with more rows than `SHOPIFY_BULK_THRESHOLD` (1,000; counted with
  `ordersCount` etc.): start the export (D93), poll `currentBulkOperation` until it is done (`SHOPIFY_BULK_TIMEOUT_SECONDS`,
  900), stream the JSONL file and rebuild nested line items from `__parentId`. If another export of this app is running,
  wait for it and start again once. A failed or timed-out export is a plain "try again later" error.
- **Cost-based limits:** every answer reports Shopify's cost bucket; before the next request the connector waits while
  the bucket cannot pay for another request like the last one; a `THROTTLED` answer waits and retries (up to 6 times,
  then "Shopify is limiting requests"). 429/5xx retries come from the shared client.
- **Incremental:** the cursor is the newest `updatedAt`; later reads ask for `updated_at:>=` the cursor minus one hour
  and the rows replace earlier copies by `id`. A deleted line item (an order edited after it was read) can stay until
  "Refresh now" (a full read). The first read of orders goes back `SHOPIFY_HISTORY_DAYS` (730).
- **Errors:** 401/403 → needs sign-in ("Shopify stopped sharing data. Sign in to Shopify again to reconnect.");
  404 → store not found; 402 → store not active; `ACCESS_DENIED` → "Shopify did not allow reading this data".
- **Development stand-in** (`app/connectors/shopify_stand_in.py`): with `APP_ENV=development` and no app credentials,
  "Connect Shopify" opens a local approval page, the callback is signed like Shopify's, and the token endpoint and the
  Admin GraphQL API are imitated from the demo `shopify_*.csv` files (one line item per order, as in the file). Its
  GraphQL documents are validated against a small copy of Shopify's schema; paging, search filters, counts, cost
  extensions and bulk exports behave like Shopify's; any other mutation is refused and counted. Tests use the same
  stand-in behind a "real" setup (`tests/recorded_shopify.py`), never the network. The schema copy is hand-written from
  Shopify's documentation: **re-check the queries against a real store** before launch (docs/SETUP_SHOPIFY.md).
- **API version** `SHOPIFY_API_VERSION` (2026-07). Shopify supports each version for about a year: move it forward
  then.

### D95. Money in rupees, declared by the source (M12)
- Shopify sends money as a decimal in the shop's currency, i.e. **rupees** for an Indian store (not paise). The
  connector marks every money column `unit="INR"`, `stored_in="rupees"`: orders `total_price`, `total_refunds`,
  `total_discounts`, `total_line_items_price`, `total_shipping`, `total_tax`; order items `unit_price`,
  `total_discount`; customers `total_spent`; products `price`, `price_max`; variants `price`, `cost_per_item`.
- The profiler turns `stored_in="rupees"` into `INR` with **no** `divide_by`, evidence `source_rupees` ("Shopify sends
  this amount in rupees"), and skips the paise guesses, so a whole-number amount in a payments-like table is never
  divided by 100. Razorpay keeps `stored_in="paise"` (D91). Counts (`items_count`, `orders_count`) are not money.
- A store selling in another currency: the columns get no unit and carry the currency code, and the dictionary leaves
  the unit for the Owner, as for any non-rupee file.
- **Uploaded Shopify files keep working** next to the connector: an uploaded `shopify_orders.csv` stays its own
  connection with its own table; both appear in the dictionary (same-named tables follow D64) and both are in rupees.


### D96. Zoho Books sign-in, scopes, data centres and organisation (M13, BUILD_SPEC 8.6, 6.3)
- **OAuth 2.0 authorization code** with `access_type=offline` and `prompt=consent`: a refresh token, stored encrypted
  (purpose `zoho_refresh_token`). Access tokens (one hour) are kept **in process memory only**, one per refresh token,
  until five minutes before they expire; a 401 from Zoho gets one renewal and one retry, then "Sign in to Zoho again".
  Zoho answers token errors with HTTP 200 and `{"error": ...}`; both shapes are handled.
- **Read scopes only.** Defaults: `ZohoBooks.invoices.READ`, `.bills.READ`, `.expenses.READ`, `.contacts.READ`,
  `.settings.READ` (organisations, items), `.accountants.READ` (chart of accounts). `ZOHO_SCOPES` may change the list,
  but anything that is not `ZohoBooks.<module>.READ` is dropped before it is sent. When Zoho reports the granted scopes
  with the token, a write scope (`CREATE`, `UPDATE`, `DELETE`, `ALL`, `fullaccess`) or missing core reads refuse the
  connection and nothing is stored.
- **No "reports" scope by default:** Zoho's documented scope list has no reports module and an unknown scope would
  break the consent page. The profit and loss report is requested with the scopes above; if Zoho refuses it, only that
  table fails with a plain message (kind `invalid`, never `needs_reauth`), and docs/SETUP_ZOHO.md says how to add the
  scope Zoho names. **Untested against real Zoho.**
- **Data centres:** India (`accounts.zoho.in`, `www.zohoapis.in`) by default, `ZOHO_REGION` for others (`com`, `eu`,
  `com.au`, `jp`, `ca` = `accounts.zohocloud.ca`, `sa`). Zoho's callback names the person's own data centre
  (`accounts-server`); it is used only when it is exactly one of these origins (https, no look-alike hosts), and the
  token answer's `api_domain` must be the same data centre's API host, else the region's own host is used. The
  read-only HTTP client allows a POST only to that data centre's token and revoke endpoints.
- **Callback and finish** follow Shopify's pattern (D92): `GET /connections/zoho/callback` is public, checks the
  `state` (10 minutes, issued to one Owner of one workspace) and the data centre, and forwards the code to the web app
  in the URL fragment; the same Owner finishes with `POST /connections/zoho/finish` (the state is used once). There
  is no HMAC (Zoho does not sign callbacks); the state and the single-use code are the protection.
- **Organisation:** one per connection. At finish the account's **default** organisation is chosen (or, when signing
  in again, the one already used if the account still sees it). The connection keeps the ids and names of the other
  organisations the account can see (not secrets; at most 50), and Owners can switch with
  `PUT /connections/{id}/zoho/organization`: stored copies of the old organisation are deleted first (never merged)
  and every table is read again; activity entry `connection.organization_changed`. Zoho's organisation list also
  contains a contact person and e-mail; they are dropped on arrival.
- **Removal** revokes the refresh token at Zoho (best effort, like Google's) and deletes the secret and snapshots.

### D97. Zoho's API limits: counted, paced, and the stored copy when near the daily limit (M13, BUILD_SPEC 8.6, R1)
- Zoho Books limits API calls **per organisation per day** (by plan: 1,000 / 2,000 / 5,000 / 10,000) and per minute
  (100). Every Books call is counted in Redis (`zoho:calls:<organisation id>:<UTC date>`, kept 2 days), so the API,
  the worker and every workspace connected to the same organisation see one number. The UTC date is an
  approximation of Zoho's own reset.
- **Per minute:** above 90 calls in the current minute the reader waits for the next minute.
- **Per day:** reading stops at **90%** of `ZOHO_DAILY_CALL_LIMIT` (default 1,000, the lowest plan), leaving the rest
  for the Owner's own tools. A Zoho "daily limit reached" answer (or 429) marks the day as used up.
- **Degrade to cache:** such refusals carry `ConnectorError.stale_ok`. The cache (`DatasetCache._fetch`) then returns
  the stored snapshot, whatever its age, instead of failing; its `fetched_at` (shown in sources and provenance) says
  how old it is. Without a stored copy the plain error is shown. Only Zoho uses `stale_ok` today.
- The connection page shows "Zoho API calls today: N of L" (detail view only; the list does not read Redis).
- Cost of reads: one call per 200 rows of a list; incremental reads after the first; the profit and loss report one
  call per month (24 at first, then the last two months).

### D98. Zoho Books tables, money, personal data and the demo organisation (M13, BUILD_SPEC 8.6, 9, 12.1)
- **Seven tables:** `invoices`, `bills`, `expenses`, `contacts`, `items`, `chart_of_accounts` (list endpoints, pages
  of 200 via `page`/`per_page`/`page_context.has_more_page`), and `profit_and_loss` (one row per account line per
  month: `month`, `section`, `account_name`, `amount`, key `line_id`), flattened from Zoho's nested report; a line is
  filed under the nearest of Operating Income / Cost of Goods Sold / Operating Expense / Non Operating Income /
  Non Operating Expense; zero lines are left out.
- **Only declared fields are kept** from every row, so anything Zoho adds (e-mail, phone, addresses, contact persons,
  GSTIN/PAN, bank details, free-text descriptions) never reaches a snapshot. Contacts keep `contact_name` and
  `company_name` (needed for "who owes me"); the profiler now treats `contact_name` (like `customer_name`) as personal,
  so no samples go to the AI.
- **Incremental:** `last_modified_time` filter from the newest one seen minus one hour, rows replaced by id; the chart
  of accounts is read whole; the P&L repeats the last two months (an account line that disappears from a re-read
  month can stay until "Refresh now"). First reads of invoices, bills and expenses go back `ZOHO_HISTORY_DAYS` (730)
  with `date_start`. The `last_modified_time` filter is the format Zoho documents for list searches; if an
  organisation ignores it, the read is simply a full one (still correct).
- **Money is in rupees** (`stored_in="rupees"`, D95), nothing divided; an organisation in another currency leaves the
  unit to the Owner.
- **Analytics:** `finance.pnl` now works on a profit and loss table (a table whose key names profit/P&L, with section,
  account and rupee amount columns): lines for the period, gross profit, operating profit, net profit and margins on
  operating income. `receivables_ageing` finds Zoho `invoices` (due date + balance).
- **No budgets:** Zoho Books' API has no budgets endpoint, so budget vs actual still needs the budget in a spreadsheet.
- **Development stand-in** (`app/connectors/zoho_stand_in.py`, `APP_ENV=development` without a client): a consent page,
  the accounts server and the Books API for every data centre host. Organisation **UrbanCart** (default) is built from
  the demo fixtures: expenses = the monthly **actual** spend per department of `zoho_budget_expenses.xlsx` in three
  entries each (account = department), so Marketing Q3 2026 is +22.0% against that workbook's budget, to the rupee;
  invoices = monthly sales to Small Business customers; bills = monthly cost of goods per category; the P&L sums them.
  A second, tiny organisation (UrbanCart Labs) shows switching. Without fixture files a small built-in sample is used
  (tests). Tests run the stand-in behind a "real" setup (`tests/recorded_zoho.py`).

### D99. UI strings per screen, and the Connections and Dictionary screens on demand (M13, BUILD_SPEC 11a.9)
- The first-load shell was 197.4 KB of 200 KB. The `connections` strings (except `connections.quality`, the data notes
  that Ask and reports also show) and the `dictionary` strings moved from `locales/en.ts` to `locales/en/connections.ts`
  and `locales/en/dictionary.ts`. Each screen's modules import a small `strings.ts` that adds them with
  `addStrings` (i18next `addResourceBundle`, deep merge) when the code loads. Keys did not change.
- The Connections, connection detail, Google return, Zoho return and Dictionary screens load on first visit (like
  Today since M10). Shell after M13: **176.9 KB**.
- A test (`src/locales/locales.test.ts`) checks that every literal `t('…')` key in the code exists in the shell or a
  screen file, and that the screen strings are not in the shell. All text stays English (D41); translation remains a
  data change (one file per screen per language).

### D100. Customer databases are read-only in four layers (M14, BUILD_SPEC 6.3 item 3, 8.7)
- **One connection type, two engines:** `sql_database` with `engine` `postgresql` or `mysql` (migration `0014`).
  Drivers: psycopg 3 (already used for the app's own database) and PyMySQL (new, pure Python). Connection details are
  **fields, not a URL** (host, port, database, user, password, SSL mode), so no driver option can be smuggled in
  through a URL's query string. BUILD_SPEC 8.7's "SQLAlchemy URL" is replaced by these fields for that reason.
- **Layer 1, the account:** setup asks for a dedicated read-only user (docs/SETUP_DATABASES.md). The connection check
  asks what the user could change. PostgreSQL: superuser/create-role/create-database, INSERT/UPDATE/DELETE/TRUNCATE on
  any table, CREATE on the database or a schema. MySQL: `SHOW GRANTS` (INSERT/UPDATE/DELETE; CREATE/DROP/ALTER/INDEX/
  TRIGGER; ALL/SUPER/FILE/GRANT OPTION). A user who can write is **accepted with a warning** (stored as `can_write` and
  shown on the connection page until a read-only user is entered); the spec says "warn", and the other layers hold.
- **Layer 2, the session:** PostgreSQL startup options `default_transaction_read_only=on`, `statement_timeout`,
  `lock_timeout=5s`, `idle_in_transaction_session_timeout`, then `SET SESSION CHARACTERISTICS AS TRANSACTION READ
  ONLY` and psycopg's `read_only` (every transaction `BEGIN READ ONLY`). MySQL: `SET SESSION TRANSACTION READ ONLY`,
  `MAX_EXECUTION_TIME` (MariaDB: `max_statement_time`), `START TRANSACTION READ ONLY` before every statement,
  `local_infile` off. Every transaction ends with a rollback; nothing is ever committed. A test shows a user who can
  write is still refused by the session (`cannot execute UPDATE in a read-only transaction`).
- **Layer 3, the SQL:** the table reads the connector builds (with sqlglot, quoted identifiers) and every push-down
  query pass `sql_guard.check_select` in the source's dialect, with only the exposed tables allowed. The guard gained
  `qualified=True` for `schema.table` on PostgreSQL (unqualified names are refused there, because the search path
  could point them elsewhere; a catalog part is always refused). `SET`, `set_config`, several statements, locks,
  `INTO`, `COPY`, sleeping/file/network functions are refused, so the session cannot be switched back to writing.
  The only other statements are fixed catalog reads in the connector; the table names they embed come from the
  database's own catalog and names with quotes, backslashes or control characters are refused.
- **Layer 4, rows and time:** reads stream in chunks (`DB_FETCH_CHUNK_ROWS`, server-side cursors) up to
  `MAX_ROWS_PER_DATASET` (one extra row tells "truncated"); push-down answers are wrapped in an outer `LIMIT` and
  return at most `rows_to_llm_per_tool_call` (200); the statement timeout is the question's or long task's.
- **Tables:** the Owner chooses which tables and views are exposed (at most 100; `PUT /connections/{id}/database/
  tables`, checked against what the user can read now). Only exposed tables are read and queried. Binary and spatial
  columns are not read. A table with an updated-at column (`updated_at`, `modified_at`, `last_modified`, ...) and a
  single-column primary key is read incrementally (rows changed since the newest seen, minus 5 minutes, replace their
  copies by key); others are read whole within the row limit.
- **No SSH tunnel yet** (BUILD_SPEC 8.7 says optional): a firewall allow-list is the documented way. To be built
  when a customer needs it.

### D101. Network safety for customer databases (M14)
- The host is resolved **before** connecting and refused when **any** answer is not a global address (Python's
  `ipaddress.is_global`, IPv4-mapped IPv6 unwrapped; multicast refused): loopback, 10/8, 172.16/12 (incl. the Docker
  network), 192.168/16, 169.254/16 (incl. cloud metadata), 100.64/10, documentation and reserved ranges, IPv6 local
  ranges. Names `localhost`, `*.localhost`, `*.local`, `*.internal`, `*.localdomain` are refused without asking DNS.
- **No second DNS answer:** PostgreSQL connects to the checked address (`hostaddr`) while TLS still checks the name.
  PyMySQL cannot separate the two: it connects to the checked address too, except with "certificate checked", where
  it must connect by name (a small window between check and connect; accepted and documented here).
- **Development exception:** only the host names in `DB_DEV_ALLOWED_HOSTS` (default `demo-postgres,demo-mysql`, the
  demo containers) may be private, and only with `APP_ENV=development`; elsewhere the setting is ignored. The app's
  own database (`postgres`) is not on the list.
- **SSL** is required by default (`require`: encrypted, certificate not checked; `verify-full`: certificate and name
  checked against the system's authorities). `disable` is accepted only in development. The Owner picks the mode;
  "certificate checked" is labelled "best".
- **Credentials:** the password is envelope-encrypted (`secrets`, purpose `database_password`) and never returned;
  host, port and database are shown; the user name is shown masked. Replacing credentials checks them first; moving to
  another host or database drops the stored copies first. Driver errors are turned into plain messages that never
  contain the password (wrong password -> "Enter the credentials again", SSL refused, unknown database, timeout).

### D102. Push-down: the agent's `query_source` tool (M14, BUILD_SPEC 7.3, 8.7)
- Offered to the model only when the asking person can see a table of a database connection (and the run can reach
  databases: not in briefings). The SQL is written over the **dictionary's table keys** (like `run_sql`), checked
  SELECT-only, then the keys are replaced by the source's own names (`"public"."orders"`), rendered in the database's
  dialect by sqlglot, and handed to the connector, which checks it again against the exposed tables, wraps it in a
  row limit and runs it in a read-only session with the run's timeout. All tables of one query must come from one
  database; other tables get "use run_sql".
- The runner refuses connections outside the person's access (`AccessContext.connection_ids`) and paused ones, and
  holds no database transaction of the app while the customer database answers.
- Results carry provenance (connection, tables, the translated SQL, the time) and the note "values as stored in the
  database (no unit conversion)": push-down skips the dictionary's unit conversion (e.g. paise), so the agent is told.
- Development stand-ins: `demo-postgres` and `demo-mysql` in docker compose, loaded from the demo fixtures by
  `scripts/demo_db/*` with a read-only user (`analyst_ro`) and an owner who can write (`demo_owner`); demo passwords,
  documented, not secrets. Tests use a separate database in the test container (real PostgreSQL behind a "public"
  name) and a fake MySQL that answers catalog questions and runs the connector's SQL on DuckDB.

### D103. Goals: who sees them, how progress is projected, and the status (M15, BUILD_SPEC X7, 10.1, 6.2)
- **One table, `goals`** (migration `0015`, RLS + `tenant_isolation`): a target for one dictionary metric over a
  period, "at least" (sales, orders) or "at most" (refunds, discounts, costs; the default when the metric's good
  direction is down), an optional name and up to 3 filters (`column = value`, or `table.column` of a joined table).
  Goals belong to the workspace: Owners and Analysts set, change and remove any goal they can see; Viewers look.
- **Visibility.** A person sees a goal only when its metric and every filter column are in the part of the
  dictionary they may see (connection access, D50). Others get 404, like another workspace's id, and the agent's
  `list_sources` does not list it. Setting a goal on a metric you cannot see is refused (422 `unknown_metric`).
- **Progress (code only, P4).** Actual = the metric from the period's start to the latest day of data (the newest
  day its table covers, up to yesterday). For a metric that adds up (SUM/COUNT): run-rate = actual × days in
  period / days so far; seasonal = actual × (last year's whole period / last year's same days), used as the
  projection when last year covers the whole period. An average or ratio (AOV) is projected at its value so far.
  "Needed per day" = what is still missing / days left (at-least goals).
- **Status.** `not_started` (no data in the period yet), `on_track` (projection meets the target), `at_risk`
  (misses by less than 10% of the target), `behind`, `achieved` (met; an at-least goal that adds up is achieved as
  soon as the actual reaches it), `missed` (period over and not met; an at-most goal that adds up is missed as soon
  as the actual passes the cap). A period is over by the calendar (yesterday ≥ its end), even when the data stopped
  earlier; a note says where the data ends. The 10% margin is a constant for now.
- **Computed for the person looking, in the background.** Progress reads live data (cache-aware, R1), so
  `POST /goals/refresh` queues a Celery job (`refresh_goals`) with the person's permissions; results are kept in
  Redis per person for 15 minutes (24 h at most; aggregates only, BUILD_SPEC 6.6). Changing a goal drops everyone's
  kept progress. No AI call.

### D104. Goals in the agent, briefings and Today (M15, BUILD_SPEC 7.3, 13.2, 11a.8)
- **Agent tool `goal_progress`**: no arguments = every goal the person may see; `goal_ids` = some; or an ad-hoc
  `metric` + `target` + `period` for a target the person has in mind. Same analytics and provenance as the screen.
  The goal-tracking playbook now starts with it and says to use the tool's status, never to decide one.
- **Briefings** get a **Goals** section (after "Working well"): goals running today or ended in the last week, off
  track first, each sentence cited. A goal at risk or behind makes the briefing notable (no "Business health"
  summary alone) and can lead the headline; it is the early warning of X7. When the AI's text leaves goals out,
  code writes them, so the warning is never lost.
- **Today** shows running goals without reading any source: the progress the Goals screen last computed for the
  person, else the goals of their latest briefing.

### D105. Pinned tiles keep the analytics, not the AI's words (M15, BUILD_SPEC X8, P4)
- Pinning an answer stores, in `dashboard_tiles.spec`, the tool calls that produced the results the answer cited
  and the charts it showed (plus data-loading calls), never `list_sources`/`describe_dataset` or calls that failed;
  at most 16 calls. It also stores where the answer's **key figure** lives (the result, column and the row's text
  labels, e.g. `metric = revenue`), so a refresh shows the same figure from new data, with its change when the row
  has one. An answer with no results from data cannot be pinned (409 `not_pinnable`).
- **Refreshing re-runs those calls only** (no AI call, nothing metered): the tile shows the key figure, the charts
  or, without a chart, the cited result as a small table (8 rows), each with its source marks. Up to 30 tiles per
  person; new pins go on top.
- **Dates (`rolling` by default, or `fixed`).** Rolling moves each period forward by the time since the answer, in
  the period's own unit: calendar months, quarters and years by whole months/quarters/years ("last month" stays last
  month), Monday-to-Sunday weeks by weeks, anything else by days. SQL the agent wrote itself (`run_sql`,
  `query_source`) keeps its dates; the tile says so. Changing the dates drops the kept results.
- **Out of date.** When a re-run fails because the dictionary changed (a metric or column is gone), the tile says
  "ask again and pin the new answer" instead of guessing.

### D106. Tiles run with the viewer's permissions; "Show on everyone's dashboard" (M15, BUILD_SPEC 6.2, P6)
- A tile is its owner's (Owners and Analysts pin; changes to someone else's tile are 404). Its owner may show it on
  everyone's dashboard (`shared`); this is a workspace-wide switch rather than per-person sharing, since BUILD_SPEC
  6.2 only says Viewers "view shared" tiles. Each person sees a shared tile only when they may see every table it
  reads (otherwise even its title could tell them about data they have no access to), and it re-runs with **their**
  permissions: the results are kept per tile and person (Redis, 24 h, aggregates only).
- Refresh: opening the dashboard re-runs tiles whose `refresh_minutes` (15 min, 1 h, 4 h, 1 day; default 1 h) have
  passed; "Refresh all" forces it. One Celery job (`refresh_tiles`) per person and request; a Redis marker stops the
  same tile being queued twice for a person; the live-data cache (R1) stops repeated fetches.
- A stored spec only reaches the same tools as the agent: the SQL guard, the read-only DuckDB workspace and the
  person's visible tables (tested with a tampered spec: writes, file access and hidden tables are refused).

### D107. Every screen in the navigation is built; the placeholder screen is gone (M15)
- With Dashboard and Goals built, no navigation item used the M1b placeholder screen any more, so
  `features/placeholder/PlaceholderPage.tsx` and its route loop were removed. The `pages.*` strings stay.
- The Dashboard and Goals screens load on first visit with their own strings files (`locales/en/dashboard.ts`,
  `locales/en/goals.ts`, D99); the Pin dialog loads from Ask only when opened. The app shell is 177.3 KB of 200 KB.
- UI translations stay deferred (D41): M15 adds no language files.

### D108. Pairing a Tally PC: one-time code, per-device token, revocation (M16, BUILD_SPEC 8.8)
- **Connect Tally** creates a `tally` connection and shows a **pairing code** (`K7QM-3XRP`: 8 characters from 31 that
  cannot be confused, about 40 bits). Only its SHA-256 hash is kept, in Redis, for `TALLY_PAIRING_CODE_MINUTES` (10).
  Redeeming deletes it (`GETDEL`), so a code works **once**; a new code for the same connection cancels the old one.
  Pairing attempts are limited to 10 per IP address per 15 minutes; Owners can make 20 codes per hour.
- Redeeming (`POST /bridge/tally/pair`) returns a **device token** (`tlyd_` + 256 random bits) shown to the bridge
  once. Only its SHA-256 hash is stored (`tally_devices.token_hash`), so a database copy cannot be used to impersonate
  a PC. The token is never logged or audited. On Windows the bridge keeps it protected with DPAPI (only that Windows
  user can read it); elsewhere in a file only its owner can read.
- **One PC per connection** (a partial unique index): pairing another PC revokes the first. **Unpair this PC** revokes
  at once (the next request is 401); the data already sent stays until the connection is removed. Removing the
  connection deletes its devices, syncs and rows (foreign keys) and any code or "read again" request in Redis.
- Codes live in Redis rather than a table: they expire by themselves and are never more than a hash. The activity log
  records `connection.tally_paired` (with the person who made the code), `connection.tally_code_created`,
  `connection.tally_unpaired` and `connection.refresh_requested`.

### D109. Outbound only, and the platform cannot send anything to Tally (M16, BUILD_SPEC 6.3, 8.8)
- The bridge always calls the platform (HTTPS, `Authorization: Bearer`); the platform never connects to the PC, so no
  port is opened on the customer's network. Plain `http` is allowed only to `localhost` (development); the bridge
  refuses other addresses, credentials or query strings in the platform address and never follows redirects (one
  could carry the token elsewhere). TLS certificates are always checked.
- The **only** request the platform can make is "read again now" (`refresh`, optionally `full`), returned by the
  bridge's long-poll (`POST /bridge/tally/poll`, up to `TALLY_POLL_WAIT_SECONDS` = 25 s, which is also the heartbeat).
  The answer has exactly `refresh`, `full` and `settings`; the bridge clamps the settings to safe ranges. Even a
  compromised platform could at most make the bridge read Tally more often.
- **Read-only toward Tally** in three layers: (1) the bridge's requests come only from fixed templates (inline TDL
  collections with `ISMODIFY="No"`, `TALLYREQUEST` = `Export`); (2) `check_export_only` parses every request right
  before it is sent and refuses anything else with an allow-list (only the tags the templates use, no `ACTION`
  attribute, no `TALLYMESSAGE`/`IMPORTDATA`, no TDL functions, one collection, formulas limited to `$AlterID > n`,
  XML export format, no DTD/entities, at most 64 KB); `ReadOnlyViolation` is never caught; (3) the platform has no code
  that builds a Tally request (a test fails if `app/` contains Tally's request vocabulary). The development stand-in
  records and refuses every non-export request; every test that syncs asserts that list stays empty.
- "Refresh now" on a Tally connection sets the flag (kept 24 h, so an offline PC reads when it is back) instead of
  queuing a read; the page says the PC was asked.

### D110. What the bridge sends and how the platform stores it (M16)
- The bridge sends Tally's **own text** (`{"DATE": "20260701", "AMOUNT": "-1281.72"}`), only the fields it asked Tally
  for; the platform does all typing (amounts, quantities, rates, dates). A fix to how Tally's text is read is then a
  platform deploy, not a re-install on every PC. The platform also keeps only the fields it knows per dataset
  (`RAW_FIELDS`), so a modified bridge cannot store anything else (no narration, addresses, phone numbers, GSTIN, bank
  details).
- Datasets sent: `voucher_types`, `groups`, `ledgers`, `stock_items` (masters, always whole; ledger and stock balances
  for the current financial year up to the sync day, the year start taken from the company) and `vouchers` with their
  ledger and inventory lines (an inventory line's accounting allocations, e.g. the sales ledger of an invoice, become
  ledger lines, so a voucher's debits equal its credits).
- Stored in **`tally_records`** (`org_id`, RLS), keyed by (connection, dataset, Tally GUID): an upsert, so a re-read can
  never duplicate a row; a voucher's lines are replaced with it. These rows are the platform's **source copy** of
  Tally (Tally cannot be read from here), kept like uploaded files until the connection is removed (BUILD_SPEC 6.6
  keeps sources; snapshots built from them still expire). A composite primary key, not a UUID: it is a copy of source
  rows, not an entity.
- **Batches** (`POST /bridge/tally/batches`): one dataset, at most `TALLY_MAX_BATCH_ROWS` (2,000) rows and
  `TALLY_MAX_BATCH_BYTES` (2 MB) of JSON, gzip allowed but limited after un-gzipping too (a gzip bomb is 413), 300
  requests per minute per device. Idempotent by (sync, dataset, batch number) in `tally_syncs`: a batch already received
  answers "duplicate" and changes nothing.
- **Complete** (`POST /bridge/tally/syncs/{id}/complete`) checks every batch number 0..n-1 arrived (409 naming the
  missing ones otherwise), then for datasets read whole removes rows this sync did not send. **Vouchers are only removed
  after a full read**; incremental reads (by Tally's `AlterID`) cannot see deletions, so a voucher deleted in Tally stays
  until the next full read (at least every `TALLY_FULL_SYNC_HOURS` = 24 h; `tally-bridge sync-now --full` on the PC).
  Then the connection's tables are rebuilt in the background (the usual sync job, dictionary included).
- **One company per connection:** the first company's GUID is remembered; another company is refused (409) so two sets
  of books never mix. Another company needs another Tally connection.
- Completed sync records are kept 7 days.

### D111. Tally tables, money and freshness (M16, BUILD_SPEC 8.8, 9)
- Seven tables: `groups`, `ledgers`, `stock_items`, `vouchers` (the day book: one row per voucher, amount = its debits,
  `voucher_class` from the voucher type followed through custom types, e.g. "Online Sales" -> sales), `voucher_ledger_
  entries` (debit, credit, signed amount, the ledger's group, primary group and nature), `voucher_inventory_entries`
  (quantity, unit, rate, discount %, amount) and `trial_balance` (closing balances split into debit and credit).
- **Cancelled and optional vouchers are left out** of every table (they are not in the books); they stay in the raw rows.
- **Money is in rupees** (Tally keeps amounts in the company's currency with two decimals): every money column is
  declared `unit="INR"`, `stored_in="rupees"`, so the dictionary never divides (the D95 pattern; the profiler's evidence
  text is now "The source sends this amount in rupees"). Tally writes a debit as a negative number; these tables show a
  **debit as positive** (`amount`, `closing_balance`) with separate positive `debit`/`credit` columns. Forex amounts use
  the rupee value after "=". A company kept in another currency is not detected yet (see SETUP_TALLY.md).
- `party_ledger` is treated as personal by the profiler (customers can be people): no samples of it go to the AI.
- **Freshness is the last push, not the last table build:** `FetchResult.data_as_of` (new, optional) is the bridge's
  last completed sync; the cache stores it as the snapshot's `fetched_at` (so provenance says "sent 3 h ago") and a
  pushed source's "unchanged" check does not move `last_success_at` forward. `source_version` is the last sync's id, so
  the tables are rebuilt only after new data arrived.

### D112. The PC's status on the Connections screen (M16)
- Every poll is a heartbeat with the bridge's state (Tally reachable, company, syncing, last error code, next sync);
  only known, bounded fields are stored (`tally_devices.status_json`).
- States: **not paired**, **offline** (no heartbeat for `TALLY_OFFLINE_AFTER_MINUTES` = 5: "Tally PC offline since …"),
  **Tally not open** on the PC, **no company open**, **Tally error**, **reading for the first time**, **connected**.
  Problems the bridge reports (another company, could not reach the platform, paused) are explained in plain words.
- The connection's own `status` is not changed by the PC going offline or by "Test" (the data sent stays usable);
  unpairing sets it to `needs_reauth` with "Pair a PC to send Tally data again".

### D113. Surviving sleep, offline and restarts (M16)
- The bridge saves its progress after every accepted batch (`state.json`) and writes each batch to `outbox.json`
  before sending it, removing it only after the platform accepted it: after a crash or a lost answer the same batch is
  re-sent unchanged and the platform answers "duplicate". Full reads go month by month and continue from the month
  after the last one finished. All files are written to a temporary file and renamed.
- Retries: within a sync, 6 attempts with exponential backoff and jitter (honouring `Retry-After`); after that the sync
  pauses and the loop retries after 30 s, 1, 2, 4 ... minutes up to 15 minutes (+/-50% jitter). Tally closed: retry
  every 5 minutes. Times are wall-clock, so a sync is due right after the PC wakes up.
- A 413 halves the batch; a 409 (another company, closed sync, missing batches) or 422 abandons the sync and the next
  one starts over (rows already sent are upserts, so starting over is safe). A 401 stops the bridge with a message to
  pair again. Only one bridge runs per Windows user (a lock file).
- **Development stand-ins:** `tally_bridge.dev.stand_in` answers Tally's XML requests for the demo company
  "UrbanCart Retail (demo)" built from the demo fixtures (one sales voucher per Shopify order, party = customers of the
  order's region, sales ledger per category, credit notes for refunds, monthly receipts, purchases at cost, supplier
  payments, the departments' actual expenses), with Tally's quirks (UTF-16, `&#4;`, `ACTION="Create"`, `" 2 Nos"`).
  The **fake helper** (`tally_bridge.dev.fake_helper`) runs the real bridge against it with switches for a flaky
  network, a PC going offline and Tally being closed. Neither is part of the Windows build.
- The bridge is plain Python 3.12 (httpx, defusedxml) built for Windows with PyInstaller (`bridge/tally/
  tally_bridge.spec`). It is tested, linted and type-checked with the backend (same interpreter); the Windows build,
  DPAPI and real TallyPrime are untested here (docs/SETUP_TALLY.md).

## M17: Industry templates

### D114. A template is plain data, validated, with no AI call (M17, BUILD_SPEC X9, 9 step 2, 10.4)
- Seven YAML files in `backend/app/templates_industry/` (retail, d2c, restaurant, fuel_station, services,
  manufacturing, and a generic `other`), loaded and validated by `templates_industry.load` (pydantic, unknown fields
  refused, elasticities within the what-if bounds). The keys are exactly the industries the workspace
  (`organizations.industry_template`), the settings request and the dictionary already accept; a test keeps the three lists
  equal. A workspace with no industry, or an unknown one, gets the generic template.
- A template holds: the name the prompts use (`business`, "a retail business in India"), a summary, the business's own
  words (bills not orders, litres not units), 3-8 KPIs (key, label, plain meaning, unit, good direction), 3-5
  starter questions, playbook tweaks (a sentence or two per playbook file) and the what-if elasticities with a `basis`.
  Nothing in a template reads customer data or creates a metric by itself: KPIs are names and meanings only.
- It does not add an AI call anywhere. It changes text the existing calls already send, so the cost is a few hundred
  characters per question (only the playbooks that were chosen add their tweak; the generic template adds nothing).
- Where each part goes: KPIs, words and the chosen playbooks' tweaks -> the agent's system prompt ("What matters in a
  retail business"); KPI keys -> `typical_metrics` in the dictionary draft's existing call (names only, never data;
  BUILD_SPEC 9 step 2); elasticities -> the what-if defaults; starter questions -> Ask and Today.

### D115. What-if defaults live in the templates and say where they come from (M17, BUILD_SPEC 10.4)
- `whatif.DEFAULTS` is now built from the templates. The numbers are the ones M9 shipped (d2c -1.4/-1.8/0.45,
  retail -1.0/-1.3/0.35 ...), so no earlier result changes. The generic template keeps M9's "other" values.
- A default elasticity carries its `basis` ("Online shoppers compare prices easily ...") into the assumption list of
  the result, so the agent and the screen can say why the assumption is what it is. History still wins whenever it is
  informative; a default is still labelled `default` and the confidence is still low.
- These are starting assumptions, not measured industry data. They were written from general business reasoning, not
  from a dataset: say so when asked, and invite the person's own number (the what-if playbook already does).

### D116. Choosing the template (M17, BUILD_SPEC 11a.8, X9)
- "Chosen at onboarding": the checklist's first step ("Company details") now opens a dialog on Today where an Owner
  picks the kind of business and sees what the template sets up before saving. Settings keeps its "Kind of business"
  choice and shows the same preview, also before saving. Both use `PATCH /org` (Owners, written to the activity log as
  before); the list comes from `GET /org/templates` (any signed-in member; reference data, the same for everyone; the
  playbook tweaks and elasticities are not sent).
- Both screens load on demand (dialog and preview are separate chunks), so the app shell did not grow beyond a few
  hundred bytes (the starter-question hook in Ask).
- Starter questions: Ask shows the template's questions once an industry other than "something else" is chosen and the
  templates loaded; otherwise the three general ones. Today's three suggestions still come from the person's own
  findings and metrics first; the template's questions only fill the empty places.
- Changing the template later changes the prompt, suggestions and what-if defaults from the next question on. It does
  not rewrite the confirmed dictionary (its own `company.industry` stays what the Owner confirmed, as in M7).
- Language: the template texts are English (multilingual is deferred, D41); the UI words around them are in
  `locales/en/templates.ts`.

## M18: Billing hooks (no payments)

### D117. What "billing hooks" means here: record, warn, summarise, never charge (M18, BUILD_SPEC R7, 14 M18)
- Scope as the milestone says: a **plan change flow**, **limit warnings at 80% and 100%** and **invoice-ready usage
  summaries**. Nothing collects money: no gateway, no key, no price, no card or customer-payment record, no network
  call. Every screen says so ("Billing is not switched on"); prices read "coming soon" (BUILD_SPEC 11b).
- Plans, limits and usage are M5's, unchanged: two plans (`managed`, `byok`), the Managed plan's monthly question
  limit (`organizations.monthly_question_limit`), usage counted by `app.llm.usage.questions_used` (question and long-task
  runs on the platform's key in the workspace's calendar month and time zone; briefings, checks and dictionary
  drafts never count; BYOK has no platform limit). The new code calls those functions and never re-counts.
- Everything is Owner-only (`org.manage`), like the AI plan and usage (BUILD_SPEC 6.2). No new role or permission.

### D118. The provider interface and its one stand-in (M18)
- `BillingProvider` (`app/billing/provider.py`) has one method, `deliver(event) -> Delivery`. Every billing event is
  offered to the configured provider before it is stored, so a real provider added later receives events with no
  change elsewhere.
- **STAND-IN:** `StandInProvider` is the only provider. It accepts every event and sends it nowhere (`delivery =
  not_sent`, `provider = stand-in`). `BILLING_PROVIDER` accepts only `stand_in`; any other value fails at start-up, so no
  one can believe a real provider is connected. A test makes sure the package imports no HTTP or socket code and that
  the stand-in opens no connection.
- **Everything that must be replaced when a real provider is chosen** (a checklist, not a design): the provider class and
  `BILLING_PROVIDER`; prices (`PlanInfo.price_inr_per_month` is `None` and the screens say "coming soon"); the
  `amount_due_inr: 0` and `draft_no_payment` status in the summary; a checkout / invoice / webhook flow (none exists);
  tax details (GST, invoice numbers) which the summary deliberately does not pretend to have; the "not switched on" and
  "Nothing is charged" texts in `locales/en/billing.ts` and the warning email.

### D119. Warnings at 80% and 100%: once a month, from the run that crossed them (M18)
- `billing.limits.after_limited_run` runs once after each question or long task that used the platform's key (called from
  `agent.service.run_agent`, which both use). It reads M5's count and, if the workspace is at 80% or more (or at its
  limit), records **one** `usage_warning` or `usage_limit_reached` billing event per workspace per month (a unique key
  `(org_id, kind, period)` makes this safe when two runs finish together) and emails each Owner **once** then. Going
  straight past 80% to 100% records only the 100% event.
- It never raises: a failing check or email is logged and the answer is unaffected (tested).
- Asking is still stopped by M5's rule (`ensure_within_limit`, 429 "usage_limit_reached"); billing only tells people
  earlier. The limit is still checked before a question starts, so two questions asked at the same moment may overshoot
  by one or two, as M5 accepted (D42).
- The Owner sees it too on Today (`usage_warning` in `GET /today`, Owners only) and in Settings > Plan and billing
  (meter, same words at 80% and 100%). Analysts and Viewers are not told (they get M5's message when a question is
  refused). BYOK workspaces have no level and get no warning.
- Not built: warnings at other percentages, per-person limits, a "buy more questions" action (nothing can be bought).

### D120. Invoice-ready monthly summaries (M18)
- `GET /org/billing/summary?month=` (and `.csv`) lists the lines a future invoice would carry: questions included in the
  plan, questions used on the platform's AI, the amount above the limit, runs on the workspace's own key (billed by its
  AI provider, not by us), briefings (not counted), and the estimated AI cost in USD (information only), plus plan
  changes inside the month. Months are the workspace's calendar months in its own time zone (like M5's usage screen).
- **It is not an invoice:** `amount_due_inr` is 0, `price_status` is `not_priced`, `status` is `draft_no_payment` and the
  notes say so. No invoice number, no tax lines.
- A finished month is **frozen once**: a beat job (every 6 hours, `close_billing_months`) stores last month's summary as a
  `usage_summary` billing event for each workspace that existed in that month; later usage rows do not change it, and
  the screen and API show the frozen copy ("Final: saved on ..."). The current month is always a live draft. A month
  nobody froze yet (job not run) is shown live.
- The CSV has one row per line with the workspace and month on every row; the workspace's name is guarded against
  spreadsheet formulas like the activity-log export. Downloads are written to the activity log
  (`billing.summary_exported`). The data export (DPDP) includes `billing.json`.

### D121. The plan change flow: preview, then confirm (M18)
- `POST /org/billing/plan-change/preview` reads and answers `allowed`, `blockers` (own key missing, Managed not set up,
  same plan) and `effects` as codes the screen writes in words (limit, what this month already holds and counts from
  now, near/at the limit, effective at once, history kept, nothing charged). `PUT /org/billing/plan` repeats the same
  check and then changes the plan (409 with the blocker's code otherwise).
- Rules are M5's: switching to your own key needs a saved key; switching to Managed needs the platform's AI to be set up;
  switching to Managed when the month's questions are already used is allowed and says so (M5 allowed it too). Saving or
  removing the key keeps its own routes and words (AI plan section).
- One function changes a plan, `api.ai.change_plan`: it writes the activity-log entry (`org.plan_changed`, as before) and
  offers a `plan_changed` billing event to the provider. The older routes (`PUT /org/ai/plan`, `DELETE /org/ai/key`) use
  it too, so no path changes a plan without the event. Plan changes have no period, so any number is allowed.
- Billing events (`billing_events`, migration 0017): RLS and `tenant_isolation`; workspace requests can read and add rows,
  never change or delete them (like `usage_events`); deleted with the workspace.
- Not built, on purpose: a trial, proration, a downgrade date, a "contact us" form for more questions (there is no
  platform admin or sales channel in scope, BUILD_SPEC 2 "out of scope").
