# Progress

**Next milestone: M2: Auth core** (start when the human says "continue").
**Unfinished work:** none for M1b.

## Milestone checklist (BUILD_SPEC §14)

### Phase 1: MVP
- [x] M1: Foundation
- [x] M1b: Design system + app shell
- [ ] M2: Auth core
- [ ] M3: 2FA, Google sign-in, devices
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
