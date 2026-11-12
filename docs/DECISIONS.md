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
