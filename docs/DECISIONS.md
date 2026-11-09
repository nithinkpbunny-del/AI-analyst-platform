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
