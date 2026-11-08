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
