# BUILD_SPEC.md — AI Business Analyst Platform

> **For Claude Code.** This file is the single source of truth for what to build and how.
> Read it fully before writing any code. Re-read the relevant section before starting each milestone.
> When this file and your own preference disagree, follow this file. When this file is silent,
> choose the simplest secure option and record the decision in `docs/DECISIONS.md`.

---

## 0. How you (Claude Code) must work

1. **Build in the milestone order of Section 14.** Do not start a milestone until the previous one meets
   its acceptance criteria.
2. **After every milestone:**
   - run the full test suite (backend `pytest`, frontend `vitest`, linters, type checks) — all green;
   - update `docs/PROGRESS.md` (what was built, how to try it, known gaps);
   - **do NOT run `git commit`, `git push` or any command that changes git history.** Instead, print the
     suggested commit message `M<n>: <milestone name>` and the list of changed files, so the human can commit;
   - print a short summary for the human.
   - Git rule: you may run read-only git commands (`git status`, `git diff`) and keep `.gitignore` correct.
     Only commit if the human explicitly asks you to in the chat.
3. **Stop after EVERY milestone and wait for the human.** Give: what was built, step-by-step instructions to
   test it yourself (exact commands + what you should see), changed files, suggested commit message. Start the
   next milestone only when the human says "continue". The end of each Phase (after M10b, M15, M19) is a larger
   review. Work continues across several chat sessions: `docs/PROGRESS.md` must always say exactly which
   milestone is next and any unfinished work, so a new session can resume.
4. **When an external credential is needed** (Google OAuth client, Shopify app, Zoho client, SMTP, Gemini key),
   do NOT block: build against the interface, add a fake/mock implementation used in tests and local dev,
   document the setup steps in `docs/SETUP_<SERVICE>.md`, and read the real values from environment variables.
5. **Never** commit secrets, real API keys, or `.env`. Provide `.env.example` with every variable documented.
6. **Tests are mandatory.** Every module ships with unit tests; every API route ships with tests for
   success, validation error, unauthenticated (401), wrong role (403) and **wrong tenant (404)**.
7. **No real LLM calls in tests.** Use the fake LLM client (Section 7.9). A separate, opt-in command runs the
   evaluation suite against a real model.
8. Prefer boring, well-known libraries over clever code. Keep functions small and typed.
9. If a requirement is impossible or unsafe as written, implement the closest safe version and explain it in
   `docs/DECISIONS.md`.

---

## 1. Product summary

An AI business analyst for **small and medium businesses (SMBs), India first**. A business connects its data
(files, Google Sheets, payment/commerce/accounting apps, databases). Users ask questions in plain language —
in any Indian language or English — and the agent fetches the **latest** data from the relevant sources,
analyses it with exact code, and answers with evidence, charts and (when relevant) recommendations with ₹ impact.
It also emails a daily briefing, runs long analyses in the background, simulates "what-if" decisions, tracks
goals, and keeps a pinned live dashboard.

**Core principles (non-negotiable):**

| # | Principle | Meaning |
|---|---|---|
| P1 | Live data | Data is fetched when a question is asked (with a short reuse cache), never a stale one-time import. |
| P2 | Strictly read-only | The platform must never create, update or delete anything in a customer's data source. Enforced in code at several layers (Section 6.3). |
| P3 | Tenant isolation | One company can never see another company's data, connections, chats, files or logs. |
| P4 | The LLM reasons, code computes | Every number shown comes from SQL/pandas/stat code, never from the LLM's arithmetic. |
| P5 | Trust | Every number is traceable to its source, query and fetch time; data-quality issues are surfaced; the agent says when it is unsure. |
| P6 | Least privilege | Users see only what their role and connection grants allow; the agent inherits the asking user's permissions. |

---

## 2. Requirements (numbered — reference these IDs in code comments and tests)

### Data & connections
- **R1 Live data.** On each question, the agent selects the relevant connections and fetches current data.
  Reuse data fetched within `CACHE_TTL_SECONDS` (default 300). Fetch incrementally (only new/changed records)
  where the source supports it.
- **R2 User-managed connections.** Owners add, edit, re-authenticate, replace and remove connections.
  Connections persist. Replacing a connection (e.g. moving from Sheets to Zoho) keeps the business dictionary
  mappings where columns match and flags the rest for review.
- **R3 Strictly read-only** (see P2, Section 6.3).
- **R4 Cross-source analysis.** The agent can join/compare data from multiple connections in one answer
  (e.g. Shopify orders + targets from an Excel file).
- **R8 Connectors**, built in this order: Excel/CSV → Google Sheets → Razorpay → Shopify → Zoho Books →
  PostgreSQL/MySQL (generic database) → Tally (via local bridge app).

### Tenancy, access, login
- **R5 Multi-business platform**: separate workspaces ("organizations"), roles, activity log.
- **R15 Login & access**:
  - Each person has an individual login; credentials are never shared.
  - Sign-up creates an organization with the signer as **Owner**.
  - Owner invites users by email with a role (**Analyst** or **Viewer**); invitee sets their own login via link.
  - Login: email + password, or "Sign in with Google".
  - Two-step verification (2FA) by **email code** or **authenticator app (TOTP)**; **mandatory for Owners**,
    optional for others. (SMS code: later phase, behind an interface.)
  - Auto logout after inactivity (default 30 min idle; absolute session max 12 h).
  - Email alert on login from a new device.
  - Removing a user revokes access immediately (all sessions and refresh tokens).
  - Every login (success and failure) is recorded in the activity log.
  - Owner can restrict which connections each user can see (per-connection grants).

### AI
- **R6 Gemini is the default LLM**; Claude is a supported alternative via the same provider interface.
  Free-tier key for development; paid keys in production.
- **R7 Two AI plans per organization**: **Managed AI** (platform's key, monthly usage limit) and
  **BYOK** (org pastes its own Gemini/Claude key; encrypted; never shown again). Billing/payments are
  **out of scope** for now, but store `plan`, limits and usage so billing can be added later.
- **R9 ₹ impact.** Give recommendations with estimated ₹ impact **when the user asks**, or proactively only
  when the finding is material (default: impact ≥ 5% of the relevant monthly metric or ≥ ₹1 lakh — configurable
  per org).
- **R11 Multilingual.** The agent answers in the language the user writes in (English, Hindi, Kannada, Tamil,
  Telugu, Malayalam, Marathi, Bengali, Gujarati at minimum). UI is English in Phase 1; UI translations in Phase 2.
- **R12 What-if simulator.** Predict the effect of a proposed decision from the company's own history, with a
  range, assumptions and confidence.
- **R13 Zero setup.** After a connection is added, the platform profiles the data and drafts a **business
  dictionary** automatically; the Owner reviews/confirms/edits it.
- **R14 Trust features.** Provenance for every number, data-quality warnings, explicit uncertainty.

### Automation & outputs
- **R10 Email briefings & long tasks.**
  - Daily briefing, default **06:00 in the user's timezone** (default `Asia/Kolkata`); each user can change
    time, frequency (daily / weekly / alerts-only / off) and language.
  - Content: today's problems and upcoming risks; alerts; suggested changes; unusual numbers/patterns (flagged);
    repeated positive patterns that indicate growth; if nothing notable, a short "business health" summary.
  - On by default for Owners; opt-in for Analysts and Viewers.
  - **Long tasks:** a user can submit a complex analysis; it runs in the background and the finished report is
    emailed (and available in-app).
- **X2 Chat history & saved questions.** History per user, follow-ups with context, saved questions re-runnable
  with one click.
- **X3 Export & share.** Answers and reports export to **PDF** and **Excel**; share inside the org (respecting
  the recipient's permissions).
- **X7 Goals & targets.** Owner/Analyst set targets (metric, value, period); progress tracked; early warning
  when the projection falls short; included in briefings.
- **X8 Pinned dashboard.** Pin any answer as a live tile that re-runs its queries on refresh.
- **X9 Industry templates.** Retail, D2C/e-commerce, restaurant, fuel station, services, manufacturing:
  KPI sets, playbooks, starter questions; chosen at onboarding.
- **X12 Mobile-friendly.** Fully responsive web UI (works well at 375 px width).

### Design & growth
- **R16 Professional, distinctive UI.** Neat, crisp and memorable, built on the design system in Section 11a.
  Every screen must feel like one product, work on a phone first, and get a new user from sign-up to their
  first real insight in under 5 minutes.
- **R17 Public website (landing page).** A fast, SEO-friendly marketing site that explains the product with a
  live-feeling demo, shows connectors, the morning briefing, the what-if simulator, trust/privacy, plans
  (Managed AI / BYOK), FAQ, and sends visitors to sign-up (Section 11b).

### Platform quality
- **X1 Privacy & compliance (India DPDP Act 2023).** Data minimisation, encryption, consent, deletion on
  request, retention limits (Section 6.6).
- **X4 Accuracy testing.** Golden-question evaluation suite (Section 12).
- **X5 Safety limits.** Timeouts, row caps, step caps, token budgets, rate limits (Section 6.5).

### Explicitly OUT of scope (do not build now; keep the design open for them)
Billing & payment collection · WhatsApp briefings · platform super-admin panel · public demo workspace ·
answer 👍/👎 feedback · SMS 2FA (interface only).

---

## 3. Tech stack (decided — do not substitute without recording why)

| Layer | Choice | Why |
|---|---|---|
| Backend language | **Python 3.12** (managed with **uv**) | Analytics ecosystem (pandas, DuckDB, statsmodels). |
| API framework | **FastAPI** + **Pydantic v2** | Typed, async-capable, auto OpenAPI docs. |
| App database | **PostgreSQL 16** | Users, orgs, connections, chats, audit, jobs. Row-Level Security for tenant isolation. |
| ORM / migrations | **SQLAlchemy 2.0** + **Alembic** | Standard, explicit migrations. |
| Analysis engine | **DuckDB** (in-process, per request) | Fast SQL over fetched DataFrames/Parquet; cross-source joins. |
| Background jobs | **Celery** + **Redis** (broker), **Celery beat** (scheduler) | Briefings, long tasks, cache refresh. |
| Cache | **Redis** (metadata, locks, rate limits) + **Parquet files in object storage** (dataset snapshots) | |
| Object storage | **S3-compatible** (MinIO locally; AWS S3 `ap-south-1` in prod) | Uploaded files (versioned), snapshots, report PDFs. |
| LLM | **Gemini** via `google-genai` (default), **Claude** via `anthropic` | Provider adapter (Section 7.2). |
| Auth | Own implementation: **argon2id** (`argon2-cffi`), JWT access tokens (`PyJWT`), rotating refresh tokens, **Authlib** for Google OIDC, **pyotp** for TOTP | Full control of 2FA, sessions, audit. |
| Encryption of secrets | **AES-256-GCM** envelope encryption (`cryptography`), master key from env / KMS | Connection credentials, BYOK keys. |
| SQL parsing (safety) | **sqlglot** | Dialect-aware SELECT-only validation. |
| Stats | **statsmodels**, **scipy**, **numpy** | Forecast, anomaly detection, elasticity. |
| Email | SMTP interface; **Mailpit** locally; **Amazon SES** or **Resend** in prod | |
| PDF / Excel | **WeasyPrint** (HTML→PDF), **openpyxl** | |
| Frontend | **React 18 + TypeScript + Vite** | |
| UI kit | **Tailwind CSS** + **shadcn/ui** | Fast, accessible, responsive. |
| Data fetching | **TanStack Query**, **React Router** | |
| Charts | **Apache ECharts** (`echarts-for-react`) | Bands, dual axes, waterfall; mobile-friendly. |
| i18n (UI) | **i18next** | Phase 2 translations; wire it in Phase 1. |
| Fonts | **Bricolage Grotesque** (display), **Noto Sans** + **Noto Sans Devanagari / Kannada / Tamil / Telugu / Malayalam / Bengali / Gujarati** (body, all Indian scripts), self-hosted via `@fontsource` | Section 11a. |
| Icons | **Lucide** | Consistent 1.5 px stroke set. |
| Motion | CSS transitions + **Motion** (`motion/react`) for the few orchestrated moments | Section 11a.6. |
| Component workshop | **Storybook** | Every shared component documented with light/dark/mobile stories. |
| Marketing site | **Astro** (static, separate `site/` app) sharing the design tokens | Fast loading and SEO; the React app stays an SPA. |
| Testing | **pytest**, **pytest-asyncio**, **httpx**, **testcontainers** (Postgres/Redis), **vitest**, **Playwright** (E2E smoke) | |
| Quality | **ruff**, **mypy**, **eslint**, **prettier** | |
| Local dev | **docker compose**: api, worker, beat, web, postgres, redis, minio, mailpit | One command: `make dev`. |
| Tally bridge | Python app packaged with **PyInstaller** for Windows | Section 8.8. |

---

## 4. Repository layout

```
/
├── BUILD_SPEC.md                ← this file
├── docker-compose.yml
├── Makefile                     ← dev, test, lint, migrate, eval, seed-demo
├── .env.example
├── docs/                        ← PROGRESS.md, DECISIONS.md, SETUP_*.md, ARCHITECTURE.md, SECURITY.md
├── backend/
│   ├── pyproject.toml
│   ├── alembic/
│   ├── app/
│   │   ├── main.py              ← FastAPI app, middleware, routers
│   │   ├── config.py            ← pydantic-settings; all env vars
│   │   ├── db/                  ← session, base, RLS helpers
│   │   ├── models/              ← SQLAlchemy models (Section 5)
│   │   ├── schemas/             ← Pydantic request/response models
│   │   ├── api/                 ← routers: auth, orgs, users, connections, dictionary, chat, tasks,
│   │   │                           briefings, goals, dashboard, exports, audit, usage, health
│   │   ├── auth/                ← passwords, tokens, sessions, 2fa, google_oidc, permissions
│   │   ├── security/            ← crypto (envelope encryption), rate limiting, sql_guard
│   │   ├── audit/               ← audit writer
│   │   ├── connectors/          ← base.py, registry.py, files.py, google_sheets.py, razorpay.py,
│   │   │                           shopify.py, zoho_books.py, sql_database.py, tally.py
│   │   ├── data/                ← cache.py, snapshots.py, quality.py, profiler.py, workspace.py (DuckDB)
│   │   ├── dictionary/          ← business dictionary model, drafting, validation
│   │   ├── analytics/           ← kpis.py, drivers.py, anomalies.py, forecast.py, whatif.py, rfm.py,
│   │   │                           cohorts.py, growth.py, goals.py, finance.py, marketing.py
│   │   ├── agent/               ← loop.py, tools.py, prompts/, playbooks/, lenses.py, provenance.py,
│   │   │                           language.py, budget.py
│   │   ├── llm/                 ← base.py, gemini.py, claude.py, fake.py, keys.py (managed/BYOK), usage.py
│   │   ├── briefings/           ← compose.py, schedule.py, templates/
│   │   ├── tasks/               ← celery_app.py, jobs.py (long tasks, briefings, refresh)
│   │   ├── exports/             ← pdf.py, excel.py, templates/
│   │   ├── email/               ← sender.py, templates/
│   │   ├── templates_industry/  ← retail.yaml, d2c.yaml, restaurant.yaml, fuel_station.yaml, ...
│   │   └── privacy/             ← retention.py, deletion.py, consent.py
│   └── tests/                   ← unit/, api/, security/, connectors/, agent/, eval/
├── frontend/
│   ├── package.json
│   └── src/
│       ├── app/                 ← routes, layout, auth guard
│       ├── features/            ← auth, onboarding, connections, dictionary, chat, tasks, briefings,
│       │                           goals, dashboard, team, audit, settings
│       ├── components/          ← shared UI, ChartRenderer, ProvenanceChip, QualityBanner
│       ├── lib/                 ← api client, i18n, formatters (₹ lakh/crore)
│       └── tests/
├── design/                      ← tokens.json (single source), generated tokens.css + tailwind preset
├── site/                        ← Astro marketing site (Section 11b)
├── bridge/tally/                ← Tally bridge Windows app (Phase 3)
└── eval/
    ├── generate_fixtures.py     ← builds the demo company dataset (Section 12.1), deterministic
    ├── fixtures/                ← generated demo sources (CSV/XLSX)
    └── golden_questions.yaml
```

This is a brand-new project built from scratch. There is no earlier code to reuse; everything is defined in
this file.

---

## 5. Data model (app database, PostgreSQL)

Every tenant-owned table has `org_id UUID NOT NULL` + index, and an RLS policy
`USING (org_id = current_setting('app.org_id')::uuid)`. All ids are UUIDv7. All tables have
`created_at`, `updated_at` (UTC). Soft-delete only where stated.

| Table | Key columns | Notes |
|---|---|---|
| `organizations` | id, name, industry_template, timezone (default `Asia/Kolkata`), currency (`INR`), plan (`managed`/`byok`), monthly_question_limit, impact_threshold_pct, impact_threshold_inr, deletion_requested_at | |
| `users` | id, email (unique, citext), password_hash (nullable for Google-only), name, preferred_language, google_sub, email_verified_at, is_active | Global; a user can belong to several orgs. |
| `memberships` | id, org_id, user_id, role (`owner`/`analyst`/`viewer`), status (`active`/`removed`) | Unique (org_id, user_id). At least one active owner per org. |
| `invitations` | id, org_id, email, role, token_hash, expires_at (7 days), accepted_at, invited_by | Token is single-use; store only its hash. |
| `two_factor` | user_id, method (`email`/`totp`), totp_secret_enc, recovery_codes_hash[], enabled_at | |
| `sessions` | id, user_id, org_id, device_id, ip, user_agent, created_at, last_seen_at, expires_at, revoked_at | |
| `refresh_tokens` | id, session_id, token_hash, expires_at, rotated_at, revoked_at | Rotation + reuse detection (revoke whole session on reuse). |
| `known_devices` | user_id, device_id_hash, first_seen_at | New device → alert email. |
| `connections` | id, org_id, type, name, status (`active`/`error`/`needs_reauth`/`disabled`), config (JSON, non-secret), secret_ref, last_success_at, last_error, created_by | |
| `secrets` | id, org_id, ciphertext, wrapped_dek, key_version | Envelope-encrypted; never returned by any API. |
| `connection_grants` | id, org_id, connection_id, user_id | If a user has **no grants rows**, they see **all** connections (default); if any exist, only those. Owners always see all. |
| `datasets` | id, org_id, connection_id, external_ref (sheet/tab/table/endpoint), name, schema_json, row_count_estimate | One logical table per dataset. |
| `snapshots` | id, org_id, dataset_id, storage_key (Parquet), fetched_at, cursor, row_count, quality_json, expires_at | Short-lived cache (Section 6.6). |
| `file_versions` | id, org_id, connection_id, storage_key, original_name, sha256, uploaded_by, version | Re-upload = new version; latest is used. |
| `dictionary_versions` | id, org_id, version, status (`draft`/`confirmed`), content (JSON, Section 9), created_by, confirmed_by | |
| `conversations` | id, org_id, user_id, title, lens | |
| `messages` | id, org_id, conversation_id, role, content, language, tool_trace (JSON), provenance (JSON), quality_warnings (JSON), token_usage (JSON) | |
| `saved_questions` | id, org_id, user_id, text, lens | |
| `shares` | id, org_id, object_type (`message`/`report`), object_id, shared_with_user_id, shared_by | |
| `tasks` | id, org_id, user_id, prompt, status (`queued`/`running`/`done`/`failed`/`cancelled`), progress, result_report_id, error | Long tasks. |
| `reports` | id, org_id, title, body (JSON blocks), pdf_key, xlsx_key, source (`task`/`briefing`/`export`) | |
| `briefing_settings` | id, org_id, user_id, enabled, local_time (default 06:00), frequency, language, timezone | |
| `briefing_runs` | id, org_id, user_id, scheduled_for, sent_at, status, report_id | Unique (user_id, scheduled_for) → idempotent. |
| `goals` | id, org_id, metric_key, target_value, period_start, period_end, filters (JSON), created_by | |
| `dashboard_tiles` | id, org_id, user_id, title, spec (JSON: tool calls/queries to re-run), position, refresh_minutes | Re-runs with the viewer's permissions. |
| `usage_events` | id, org_id, user_id, kind (`question`/`task`/`briefing`), provider, model, input_tokens, output_tokens, est_cost_usd, key_source (`managed`/`byok`) | |
| `llm_keys` | id, org_id, provider, secret_ref, last4, created_by | BYOK only. |
| `audit_log` | id, org_id, user_id, action, target_type, target_id, ip, user_agent, details (JSON), at | **Append-only** (DB trigger forbids UPDATE/DELETE). |
| `consents` | id, user_id, org_id, policy_version, accepted_at | |

---

## 6. Security architecture

### 6.1 Authentication
- Passwords: argon2id; minimum 10 chars; check against a breached-password list (top 100k, bundled); no
  composition rules.
- Login rate limit: 5 attempts / 15 min per (email, IP); then exponential backoff. Generic error messages
  ("Invalid email or password").
- Email verification required before first login for password sign-ups.
- Tokens: access JWT (HS256 or EdDSA), **10 min** lifetime, claims `sub, org, role, sid`; refresh token
  (opaque, 256-bit) in `HttpOnly; Secure; SameSite=Strict` cookie, rotated on every use, 12 h absolute.
  Idle timeout 30 min enforced server-side via `sessions.last_seen_at`.
- CSRF: double-submit token for cookie-authenticated endpoints.
- Google OIDC: Authlib; verify `iss`, `aud`, `exp`, `email_verified`; link to existing account only if the
  email matches and is verified.
- 2FA: email OTP (6 digits, 10 min, single use, 5 attempts) and TOTP (pyotp, ±1 step); 10 recovery codes
  (hashed). Owners cannot finish login or perform owner actions until 2FA is enabled.
- New device: device id cookie (random, signed); unknown device → "New sign-in" email with time, IP, browser
  and a "This wasn't me" link that revokes all sessions.
- Removing a member: set membership `removed`, revoke all sessions/refresh tokens for that org immediately.

### 6.2 Authorization
Single permission function `require(user, org, permission, resource=None)` used by every route. Never check
roles inline.

| Permission | Owner | Analyst | Viewer |
|---|---|---|---|
| Manage org settings, plan, BYOK key | ✅ | ❌ | ❌ |
| Invite/remove users, change roles | ✅ | ❌ | ❌ |
| Add/edit/remove connections, grants | ✅ | ❌ | ❌ |
| Confirm/edit business dictionary | ✅ | ❌ (can suggest) | ❌ |
| Ask questions, what-if, long tasks | ✅ | ✅ | ❌ |
| Saved questions, export, share | ✅ | ✅ | export of shared items only |
| Goals: create/edit | ✅ | ✅ | view |
| Dashboard: pin/edit own tiles | ✅ | ✅ | view shared |
| View shared answers/reports, own briefings | ✅ | ✅ | ✅ |
| View activity log | ✅ | own entries | ❌ |

- **Tenant scoping:** every request sets `SET LOCAL app.org_id` inside its transaction; RLS enforces it. The
  repository layer also filters by `org_id` (defence in depth). Cross-tenant access returns **404**, not 403.
- **Agent permissions:** the agent only sees connections the asking user may see. Tools receive an
  `AccessContext` and cannot bypass it. Briefings and dashboard tiles run with the recipient's permissions.

### 6.3 Read-only enforcement (P2) — every layer
1. **OAuth scopes:** request read-only scopes only (Section 8).
2. **HTTP client allow-list:** each connector's HTTP client is a wrapper that only permits `GET` (and the
   specific read-only `POST`s a vendor requires for queries, e.g. GraphQL **queries** — mutations are
   rejected by parsing the GraphQL document). Any other method raises `ReadOnlyViolation`.
3. **Databases:** `sqlglot` parses with the source dialect; only a single `SELECT`/`WITH … SELECT`/`UNION` is
   allowed; no `INTO`, no locking clauses, no functions on a deny-list (`pg_sleep`, `dblink`, `lo_*`,
   `COPY`, file functions). Session is opened read-only (`SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`
   on Postgres; `SET SESSION TRANSACTION READ ONLY` on MySQL) with `statement_timeout`. Setup docs instruct
   customers to create a dedicated read-only DB user; the connection test warns if the user has write grants.
4. **DuckDB workspace:** `enable_external_access=false` after loading; the agent's SQL is validated
   SELECT-only; it only ever sees copies of data.
5. **Files:** stored copies; the original is never modified.
6. **Tests:** a security test suite tries writes through every connector and must see them blocked.

### 6.4 Secrets
- Envelope encryption: a random 256-bit data key per secret (AES-256-GCM), wrapped by the master key
  (`MASTER_KEY` env in dev; AWS KMS in prod, behind an interface). Store `key_version` for rotation.
- Secrets are write-only through the API; responses show only `last4` and status.
- Never log secrets, tokens, full API keys, or customer data rows. Add a log filter that redacts patterns
  (keys, JWTs, emails in error payloads).

### 6.5 Safety limits (X5) — all configurable via env
| Limit | Default |
|---|---|
| Agent max tool steps per question | 12 (long tasks: 40) |
| LLM max output tokens per call | 3,000 |
| Token budget per question | 150k input tokens |
| Rows returned to the LLM per tool call | 200 (summaries/aggregates preferred) |
| Source query timeout | 30 s (long tasks: 120 s) |
| Rows fetched per dataset per question | 1,000,000 (stream to Parquet) |
| Questions per user per minute | 10 |
| Concurrent long tasks per org | 2 |
| Managed plan monthly questions | 500 per org (configurable) |
| Upload size | 50 MB per file |

When a limit is hit, stop gracefully and tell the user what happened and how to narrow the question.

### 6.6 Privacy & DPDP Act (X1)
- Consent screen at sign-up (privacy policy + terms versioned; record in `consents`).
- Data minimisation: we do not keep copies of customer source data beyond the cache: snapshots expire after
  `SNAPSHOT_MAX_AGE_HOURS` (default 24) and are purged by a beat job. Uploaded files are kept (they are the
  source) until the connection is deleted.
- Encryption in transit (TLS) and at rest (S3 SSE, Postgres disk encryption in prod).
- Org deletion: Owner requests → 7-day grace → hard delete of all org rows, files, snapshots, reports;
  audit an anonymised record. Disconnecting a connection deletes its snapshots and secrets immediately.
- User data export (DPDP right to access): Owner can export org data (connections metadata, chats, reports,
  audit) as a ZIP.
- Data residency: deploy in an Indian region by default.
- LLM data: send only aggregated/necessary rows to the LLM (P4 helps). Show a notice that Managed AI uses a
  paid API tier (no training on customer data); BYOK customers are responsible for their own provider terms.

### 6.7 Activity log (R5)
Log at least: login success/failure, logout, 2FA changes, password reset, invites sent/accepted, role changes,
member removal, connection add/edit/reauth/remove, grant changes, dictionary confirm, BYOK key set/remove,
question asked (text hash + datasets accessed, not the answer), long task submitted, exports, shares,
briefing settings changes, org deletion request. Owner UI with filters (user, action, date) and CSV export.

---

## 7. AI architecture

### 7.1 Flow of one question
```
user question (any language)
  → detect language, load conversation context (last 8 turns)
  → load: org dictionary (confirmed version), industry template, lens, user's AccessContext
  → agent loop (LLM + tools), max steps 12:
       list_sources → describe_dataset → fetch_dataset / query_source (live, cache-aware)
       → analytics tools (exact maths in DuckDB/pandas/statsmodels)
       → make_chart
  → answer composed by LLM in the user's language, with provenance ids for every number
  → post-checks: every number in the answer must match a tool result (Section 7.7)
  → store message + tool trace + provenance + quality warnings + usage
```

### 7.2 LLM provider layer (`app/llm/`)
- Interface `LLMClient.generate(system, messages, tools, max_tokens) -> LLMResponse` with normalised
  `text_blocks`, `tool_calls[{id,name,input}]`, `stop_reason`, `usage`.
- **Gemini (`google-genai`) known pitfalls — must follow:**
  1. Create the client with `http_options={"api_version": "v1beta"}` — function calling is **not** enabled on
     `v1` (error: "Function calling is not enabled for api version v1").
  2. Keep the client object alive while iterating lazy pagers (use `with genai.Client(...) as c:` or store it);
     a temporary client gets garbage-collected → "Cannot send a request, as the client has been closed."
  3. Replay the model's original `Part` objects (including `thought_signature`) in the next turn; newer models
     reject function-call turns without them.
  4. Pass tool schemas via `parameters_json_schema`; disable automatic function calling.
  5. Function call ids may be missing — generate ids and map id → function name for `function_response`.
  6. List available models from the key at runtime; never hard-code a single model name.
- Claude (`anthropic`): native tool use; `max_retries=3`.
- Retries: 429/503 with backoff 5s/15s/30s honouring `retryDelay`; do not retry daily-quota exhaustion or
  invalid key. Map errors to friendly messages (invalid key / quota / model not found / other).
- **Key resolution (`llm/keys.py`):** `plan == managed` → platform key from env (`GEMINI_API_KEY`) and check the
  org's monthly limit first; `plan == byok` → decrypt the org's key. Record a `usage_events` row per call.
- **Fake client (`llm/fake.py`):** scripted responses (tool calls then text) for tests.

### 7.3 Agent tools (JSON-schema; every tool receives `AccessContext`)
| Tool | Purpose |
|---|---|
| `list_sources` | Connections + datasets the user may see, with freshness and dictionary descriptions. |
| `describe_dataset` | Columns, types, meanings (from dictionary), sample rows (≤ 5), basic stats, quality summary. |
| `fetch_dataset` | Load a dataset (optionally filtered by date range/columns) into the per-question DuckDB workspace as a table; cache-aware; returns row count, date coverage, quality warnings. |
| `query_source` | For SQL databases only: run a validated read-only SELECT on the source (push-down), result loaded into the workspace. |
| `run_sql` | DuckDB SELECT over workspace tables (joins across sources, R4). Returns ≤ 200 rows. |
| `kpi_summary` | Metric values for a period vs comparison period (metrics defined in the dictionary). |
| `trend` | Metric over time (day/week/month/quarter), optional split by a dimension. |
| `driver_analysis` | Why a metric changed: contribution by dimension(s), mix vs rate effects, new vs repeat. |
| `detect_anomalies` | Unusual values vs seasonal baseline (Section 10.2). |
| `growth_signals` | Repeated positive patterns (Section 10.3). |
| `forecast` | Metric forecast with low/high range and method used. |
| `what_if` | Scenario simulation (Section 10.4). |
| `segment_customers` | RFM segments, cohort retention. |
| `marketing_efficiency` | Spend, CTR, CPA, ROAS by channel/campaign where data exists. |
| `finance_summary` | P&L lines, margins, budget vs actual, receivables ageing (Zoho/Tally). |
| `goal_progress` | Goals vs actual + projected end-of-period value. |
| `make_chart` | Chart spec (ECharts-compatible JSON) from a workspace query; title must state the insight. |

Tool results returned to the LLM are compact JSON with a `result_id`; the full result is stored for the UI and
provenance.

### 7.4 Prompts
- System prompt = role ("senior business analyst for {org name}, a {industry} business in India") +
  lens (Business / Marketing / Finance — Section 7.5) + confirmed dictionary summary + industry KPIs +
  rules + answer format + language instruction.
- **Rules:** never invent numbers; every figure must come from a tool result and carry its `result_id`; dig one
  level deeper than asked for "why" questions; state data gaps; ₹ impact only per R9; amounts in ₹ with
  lakh/crore; answer in the user's language (keep metric names and numbers readable); be concise.
- **Answer format:** **Answer** (1–2 sentences) · **Evidence** (2–4 bullets) · **Why it matters** (only if material)
  · **Recommendation** (only if asked or material, with ₹ impact) · **Data notes** (quality warnings, assumptions)
  · **Next question**.
- **Playbooks** (`agent/playbooks/*.md`), loaded by question type: why-did-X-change, forecasting, what-if,
  anomaly review, campaign efficiency, customer retention, profitability, budget variance, goal tracking,
  daily briefing. Each is a numbered method the agent must follow.

### 7.5 Lenses
User picks a lens per conversation (default Business). Lenses change emphasis, KPIs and preferred tools, not
permissions. Store each lens as a prompt file in `agent/prompts/lenses/`.

| Lens | Lens text (core of the prompt) | Focus KPIs | Preferred tools |
|---|---|---|---|
| Business / Data Analyst | "You are a senior business analyst. Your lens is overall business health and root-cause analysis. You think in KPIs, trends and drivers, and always separate what happened from why it happened. For 'why' questions, split the change by several dimensions, then by new vs repeat customers, before concluding." | Revenue, orders, AOV, active customers, repeat rate, gross margin | kpi_summary, trend, driver_analysis, run_sql |
| Marketing Analyst | "You are a senior marketing analyst. Your lens is where to spend the next rupee and which customers to target. You think in funnels (impressions → clicks → conversions), efficiency (CTR, CPA, ROAS) and customer value (RFM, lifetime revenue by acquisition channel). High spend with low ROAS is a reallocation opportunity. Mention attribution limits before big budget calls." | ROAS, CPA, CTR, conversion rate, RFM segments, customer lifetime revenue | marketing_efficiency, segment_customers, driver_analysis, what_if |
| Financial Analyst | "You are a senior financial analyst (FP&A). Your lens is profitability, cost control and planning. You think in P&L lines, margins, budget vs actual and forecasts. Flag variances above 5%, quantify discount leakage, always give forecast ranges with assumptions, and separate one-off effects (festive season) from structural trends." | Gross margin, operating margin, budget variance, discount leakage, cash/receivables, forecast | finance_summary, forecast, goal_progress, what_if |

### 7.6 Multilingual (R11)
- Detect the language of the question (`langdetect` or the LLM's own detection); answer in it.
- Numbers formatted Indian-style (₹12.4 L, ₹1.24 Cr); keep KPI acronyms (ROAS, AOV) with a short local gloss
  the first time.
- Briefings use the user's `preferred_language`.
- Evaluate a subset of golden questions in Hindi and Kannada.

### 7.7 Trust (R14)
- **Provenance:** every tool result stores `{result_id, connection, dataset, query/filters, fetched_at,
  row_count}`. The answer references `result_id`s; the UI renders each number with a clickable provenance chip
  ("Shopify · orders · fetched 2 min ago · query").
- **Number check:** after the LLM answers, extract numbers from the text and verify each matches (within
  rounding) a value present in a cited tool result; if not, run one repair turn ("these numbers are not
  supported: …"); if still unsupported, remove them and add a data note.
- **Data quality** (`data/quality.py`) on every fetch: period completeness (e.g. "October has 60% of the usual
  daily rows"), null rates for key columns, duplicate keys, stale data (last record older than expected),
  currency/unit mismatches. Warnings flow into the answer's **Data notes** and the UI's QualityBanner.
- **Uncertainty:** forecasts and what-ifs always show ranges and assumptions; the agent says "I'm not sure"
  when data is insufficient, and asks for the missing input instead of guessing.

### 7.8 Usage metering (R7)
Count tokens and estimated cost per call; aggregate per org/user/month; enforce Managed plan limit before a
question starts; show usage in Settings. BYOK orgs have no platform limit (rate limits still apply).

### 7.9 Fake LLM & testability
`FakeLLM(script=[...])` returns predefined tool calls / texts; used by unit, API and E2E tests so CI never calls
a real model.

---

## 8. Connectors (R8)

### 8.1 Common interface (`connectors/base.py`)
```python
class Connector(Protocol):
    type: str
    def test(self) -> ConnectionTestResult           # credentials valid, read-only check, sample
    def list_datasets(self) -> list[DatasetInfo]     # tables/sheets/endpoints
    def fetch(self, dataset, since_cursor=None, filters=None) -> FetchResult  # Arrow/DataFrame batches + new cursor
    def supports_pushdown(self) -> bool              # SQL databases only
```
- Fetch writes Parquet snapshots; `data/cache.py` decides reuse (`CACHE_TTL_SECONDS`) vs incremental fetch
  (cursor = `updated_at`/id) vs full fetch; Redis lock prevents duplicate concurrent fetches.
- Every connector: read-only HTTP wrapper (6.3), retries with backoff on 429/5xx, respects vendor rate limits,
  maps errors to `needs_reauth`/`error` status with a human message.
- Each connector has a recorded-fixture test suite (VCR-style JSON fixtures; no live calls in CI).

### 8.2 Excel / CSV (Phase 1)
Upload `.csv`, `.xlsx` (all sheets), max 50 MB; detect header row, types, dates (Indian formats `dd/mm/yyyy`),
₹ symbols and lakh separators; each sheet = dataset; re-upload creates a new `file_version` and the latest is used.

### 8.3 Google Sheets (Phase 1)
OAuth 2.0 with scopes `spreadsheets.readonly` + `drive.file`, using the **Google Picker** so the user selects
specific spreadsheets (avoid broad Drive scopes). Each tab = dataset; live read on question (cache TTL applies).
Document that Google app verification is required before public launch (`docs/SETUP_GOOGLE.md`).

### 8.4 Razorpay (Phase 2)
API key id + secret (Basic auth). **GET only**: payments, orders, refunds, settlements, (optionally)
subscriptions. Pagination via `count` (≤100) + `skip`, time windows via `from`/`to` (unix). Store amounts in
paise → convert to ₹. Warn in UI: "Razorpay keys can make changes; this platform only reads." Recommend a
dedicated key.

### 8.5 Shopify (Phase 2)
Custom/public app OAuth with scopes `read_orders`, `read_products`, `read_customers`, `read_inventory`
(+ `read_all_orders` if approved, needed for orders older than 60 days). Use the Admin **GraphQL** API with
**queries only** (parse document, reject mutations); bulk operations for large histories; incremental by
`updated_at`. Respect cost-based rate limits.

### 8.6 Zoho Books (Phase 2)
OAuth 2.0 with region-aware domains (`accounts.zoho.in` / `www.zohoapis.in` for India; support .com/.eu);
read scopes only (invoices, bills, expenses, contacts, items, chart of accounts, reports). Handle the org id,
pagination, and daily API-call quotas (track usage per connection; degrade to cache when near the limit).

### 8.7 PostgreSQL / MySQL (Phase 2)
SQLAlchemy URL + optional SSH tunnel; read-only session; sqlglot guard; `statement_timeout`; schema discovery
from `information_schema`; push-down queries (`query_source`). Connection test reports any write privileges.

### 8.8 Tally (Phase 3) — local bridge
Tally runs on the customer's Windows PC and has no cloud API. Build `bridge/tally/`:
- Windows app (PyInstaller) the Owner installs on the Tally PC; pairs with the org via a one-time pairing code
  → receives a device token (revocable).
- Talks to TallyPrime's local XML/HTTP server (default `http://localhost:9000`, enabled in Tally connectivity
  settings) using **export (read) requests only**: ledgers, groups, stock items, vouchers (sales, purchase,
  receipts, payments), day book, trial balance.
- Pushes data to the platform API over HTTPS every 15 min and on-demand (the platform requests a refresh via a
  long-poll/WebSocket; bridge responds if online). Freshness = last successful push; UI shows "Tally PC offline
  since …" when stale.
- Never sends import/alter requests to Tally (tested).

---

## 9. Business dictionary & zero setup (R13)

**Format** (stored as JSON, editable in UI, also exportable as YAML):
```yaml
company: {name: UrbanCart, industry: d2c, currency: INR, fiscal_year_start: "04-01"}
datasets:
  shopify.orders:
    meaning: "One row per customer order"
    time_column: created_at
    grain: order
    columns:
      total_price: {meaning: "Order value incl. tax", unit: INR, role: measure}
      customer_id: {meaning: "Customer", role: key, joins: ["zoho.contacts.shopify_customer_id"]}
      shipping_state: {meaning: "Customer state", role: dimension, synonyms: ["region", "state"]}
metrics:
  revenue: {expr: "SUM(total_price) - SUM(total_refunds)", dataset: shopify.orders, unit: INR, good_direction: up}
  orders: {expr: "COUNT(*)", dataset: shopify.orders}
  aov: {expr: "revenue / orders"}
relationships:
  - {left: shopify.orders.customer_id, right: shopify.customers.id, type: many_to_one}
```
**Drafting pipeline (on connection add / schema change):**
1. Profile each dataset: types, null %, distinct counts, min/max, sample values, likely time column, likely keys.
2. Industry template suggests standard metrics.
3. LLM drafts meanings, roles, metrics and cross-source joins from the profile (no raw rows beyond 5 samples).
4. Validate: every metric expression compiles in DuckDB on the sample; joins tested for match rate.
5. Show the Owner a review screen: plain-language summary, each item with ✓ / edit; confirm creates a new
   confirmed version. Until confirmed, the agent can still work but adds a data note "dictionary not confirmed".
6. On schema change (new/missing columns), create a draft diff and notify the Owner.

---

## 10. Analytics library (generic, driven by the dictionary)

All functions take `(workspace, dictionary, metric/dimension keys, periods, filters)`, return DataFrames +
metadata, are deterministic and unit-tested against the demo fixtures.

### 10.1 Core
`kpi_summary`, `trend`, `driver_analysis` (contribution by dimension; mix vs rate decomposition; new vs repeat
customers), `rfm_segments`, `cohort_retention`, `marketing_efficiency`, `pnl`, `budget_variance`,
`receivables_ageing`, `goal_progress` (projection = run-rate and seasonal-adjusted).

### 10.2 Anomaly detection (briefings + `detect_anomalies`)
- Daily/weekly metric series per key dimension (top N values by volume).
- Baseline: same weekday over the last 8 weeks (daily) or seasonal decomposition (STL) for longer series.
- Flag when robust z-score (median/MAD) ≥ 3 **and** absolute change ≥ configurable minimum (e.g. ₹10,000 or 10%).
- Rank by ₹ impact; suppress duplicates already reported in the last 3 days.

### 10.3 Growth signals
Patterns that repeat and point to growth: a segment/product/channel with ≥ 3 consecutive periods of
improvement; a metric beating its baseline repeatedly; a cohort retaining better than previous cohorts.
Report the pattern, evidence and estimated upside.

### 10.4 What-if simulator (R12)
- Input: natural-language scenario → structured `{lever, change, scope, horizon}` (levers: price, discount,
  marketing spend by channel, product mix, staffing/cost line, stock).
- Method: driver model for the metric (e.g. revenue = orders × AOV; orders depend on price/discount/spend).
  Estimate sensitivity from the company's own history where natural experiments exist (past price/discount/spend
  changes) using log-log regression with controls for seasonality; otherwise use industry-template default
  elasticities **clearly labelled as assumptions** and ask the user to confirm/override.
- Output: baseline vs scenario for the horizon, low/expected/high range, side effects (e.g. repeat-customer
  impact), assumptions list, confidence (high/medium/low with reason), and which historical periods informed it.
- UI: scenario card with sliders to adjust the lever and assumptions, recomputing via the same tool (no LLM call
  for slider moves).

### 10.5 Forecast
Holt-Winters (statsmodels) when ≥ 2 seasonal cycles exist; otherwise linear trend × seasonal index; report
the method and an ~80% range; backtest error (MAPE) on the last 3 periods shown in data notes.

---

## 11. Features by screen (frontend)

| Screen | Content |
|---|---|
| Sign up / Login / 2FA / Accept invite / Forgot password | Email+password, Google button, 2FA challenge, consent checkbox. |
| Onboarding (Owner) | Company name, industry template, timezone → add first connection → dictionary review → first suggested questions. |
| Connections | List with status, freshness, owner actions (add, test, re-auth, replace, remove), per-type setup wizards, grants editor. |
| Business dictionary | Plain-language review, edit meanings/metrics/joins, version history, confirm. |
| Ask (chat) | Lens picker, conversation list, messages with charts, provenance chips, quality banner, "Show my work" (tool trace + queries), save question, pin to dashboard, export PDF/Excel, share. Streaming progress ("Fetching Shopify orders…"). |
| What-if | Scenario input, result card, sliders, assumptions editor. |
| Long tasks | Submit, status/progress, cancel, open report. |
| Dashboard | Pinned tiles (live), drag to reorder, refresh. |
| Goals | Create/edit, progress bars, projection warning. |
| Briefings | Personal settings (time, frequency, language), past briefings archive. |
| Team | Members, roles, invite, remove (Owner). |
| Activity log | Filterable table, CSV export (Owner). |
| Settings | Org profile, AI plan (Managed/BYOK, key entry, usage meter), impact thresholds, privacy (export data, delete org). |

Responsive: all screens usable at 375 px; chat and dashboard tested on mobile viewport in Playwright.
Amounts: shared formatter `formatINR` (₹8,450 / ₹12.4 L / ₹1.24 Cr) and percentages to 1 decimal.
Every screen follows the design system in Section 11a.

---

## 11a. Design system (R16)

### 11a.1 Direction: "the marked ledger"
The product is a business owner's trusted analyst. The visual idea comes from the Indian business ledger
(*bahi-khata*) and the analyst's habit of **highlighting the one number that matters**. Calm, precise screens in
deep indigo ink on cool paper, and one signature gesture: a **turmeric (haldi) highlighter mark** behind the key
number of every answer, briefing and tile. That mark is the brand's memorable element. Spend boldness there
and keep everything else quiet and disciplined.

Avoid the generic looks: no cream background with a terracotta accent, no black background with a neon accent,
no identical rounded cards with the same grey shadow everywhere, no gradient washes, no ALL-CAPS eyebrow labels
above headings, no monospace for data labels, no "→" appended to button text.

### 11a.2 Colour tokens (`design/tokens.json` → CSS variables; light and dark)
| Token | Light | Dark | Use |
|---|---|---|---|
| `ink` | #1E1C3A | #ECEBF7 | Primary text |
| `ink-muted` | #5A5874 | #A9A7C2 | Secondary text, captions |
| `paper` | #F5F6FA | #12112A | App background (cool, never cream) |
| `surface` | #FFFFFF | #1C1B3A | Panels, answer sheets, inputs |
| `line` | #E2E4EE | #2E2D52 | Dividers, input borders, ledger rules |
| `neel` (brand) | #3A36A8 | #8F8BFF | Primary buttons, links, active nav, focus ring |
| `neel-soft` | #ECEBFB | #26245A | Selected rows, active chips |
| `haldi` (signature) | #F2C230 | #E2B21E | Highlighter mark (used at 55% opacity behind text), small "new" dots. Never a text colour. |
| `gain` | #11795A | #4FD1A1 | Positive change |
| `loss` | #C0392B | #FF7A6B | Negative change, errors |
| `caution` | #A86A0A | #F0B44C | Data-quality warnings |
| Chart series | #3A36A8, #11795A, #D08A00, #8C4FBF, #2A8FB5, #C0392B | lighter variants | Fixed order; never rely on colour alone (labels/patterns too) |

All text/background pairs meet **WCAG 2.2 AA** (4.5:1 body, 3:1 large text and UI). A token test checks contrast.

### 11a.3 Typography
- **Display: Bricolage Grotesque** (600–700) for page titles, the answer's lead sentence, big KPI numbers and
  marketing headlines. Slightly tight tracking (-1%) at large sizes.
- **Body/UI: Noto Sans** (400/500/600) with the matching Noto Sans script family for every Indian language,
  so Hindi, Kannada or Tamil text looks as good as English.
- **Numbers:** `font-variant-numeric: tabular-nums` everywhere data appears; ₹ amounts use `formatINR`.
- Scale (rem, 1.25 ratio): 0.75 caption · 0.875 small/UI · 1 body · 1.25 h4 · 1.563 h3 · 1.953 h2 · 2.441 h1 ·
  3.815 marketing hero. Body line-height 1.55 (1.7 for Indic scripts); headings 1.15.
- Line length ≤ 75 characters for prose; sentence case everywhere (buttons, titles, menus).

### 11a.4 Layout & shape
- 4 px spacing grid (4, 8, 12, 16, 24, 32, 48, 64). Content left-aligned, never centred in the app.
- **App shell:** desktop = left sidebar (Today, Ask, Dashboard, What-if, Goals, Briefings, Tasks; then
  Connections, Team, Activity, Settings for Owners), collapsible; tablet = icon rail; mobile = bottom tab bar
  (Today, Ask, Dashboard, More) with a floating "Ask" button.
- **Radius by hierarchy, not one value:** page panels 16 px, inputs/buttons 10 px, chips/badges full pill, tables
  and the answer sheet 4 px (ledger-like). Depth comes from `line` borders and background contrast; shadow only
  for overlays (menus, dialogs, the "Show my work" drawer).
- **Ledger rules:** tables and answer evidence use thin `line` horizontal rules with generous row height (44 px);
  no zebra stripes, no vertical borders.

```
Desktop – Ask screen
┌──────────┬──────────────────────────────────────────────┬────────────────┐
│ Sidebar  │  Conversation (max 760 px, left-aligned)     │ Show my work   │
│ Today    │  ┌ Question bubble (neel-soft)               │ (drawer, opens │
│ Ask  ●   │  └ Answer sheet (surface, 4 px radius)       │  on demand):   │
│ Dashboard│     Lead sentence, key number ▇haldi mark▇   │ steps, SQL,    │
│ What-if  │     Evidence (ledger rows, ¹ source marks)   │ sources,       │
│ Goals    │     Chart                                    │ fetch times    │
│ …        │     Data notes (caution) · Next question     │                │
│          │  [ Ask a question…               ] [Ask]     │                │
└──────────┴──────────────────────────────────────────────┴────────────────┘
Mobile – same content, single column, bottom tab bar, drawer becomes a full-height sheet.
```

### 11a.5 Signature components (build in Storybook first)
- **Highlight** — wraps the key number; renders the haldi marker (skewed, slightly uneven edge, 55% opacity)
  behind the text. Used once per answer, once per tile, once per briefing headline. Never more than one per view.
- **AnswerSheet** — lead sentence (display font, 1.25–1.563 rem) with Highlight; Evidence rows; ChartRenderer;
  DataNotes (caution colour, icon, plain text); NextQuestion chip; actions (Save, Pin, Export, Share, Show my work).
- **SourceMark** — small superscript number after each figure; hover/tap opens a popover: source, dataset, query
  summary, "fetched 2 min ago". This is the visible face of the trust features.
- **QualityBanner** — inline caution strip ("October data is 60% complete; numbers may be low").
- **ProgressSteps** — live list while the agent works ("Checking connected sources", "Fetching Shopify orders",
  "Comparing Q3 with Q2"), each with a tick; makes waiting feel productive.
- **KpiTile** — label, big number (display font), delta with gain/loss colour + arrow + text ("▲ 12.4% vs last
  week"), sparkline; Highlight on the tile's headline number only.
- **ScenarioCard** (what-if) — lever slider, baseline vs scenario bars with low–high range whisker, assumptions
  list with edit, confidence badge.
- **ConnectionCard** — source logo/initial, status (Live / Needs sign-in / Error) as pill, freshness, actions.
- **EmptyState** — one sentence that says what to do + one primary action (e.g. "Connect your first data
  source to start asking questions." [Connect data]).
- **Toast, Dialog, Drawer, Tabs, Table, Select, DatePicker, Slider** from shadcn/ui, restyled with tokens.

### 11a.6 Motion
- One orchestrated moment: when an answer arrives, the lead sentence fades in (150 ms) and the haldi mark
  **sweeps left-to-right behind the key number** (400 ms, ease-out). Same sweep on the marketing hero.
- Otherwise motion only responds to actions: drawers slide (200 ms), dialogs fade/scale (150 ms), progress ticks.
  No entrance animations on scroll, no hover lift on cards.
- `prefers-reduced-motion`: no sweep (mark appears instantly), no slides.

### 11a.7 Writing in the UI
Plain, active, sentence case. Buttons say what happens: "Connect Google Sheets", "Ask", "Run scenario",
"Send invite", "Export PDF". The same action keeps the same word through the flow ("Run scenario" → toast
"Scenario ready"). Errors say what happened and how to fix it ("Shopify stopped sharing data. Sign in to Shopify
again to reconnect." [Reconnect Shopify]) and never apologise. Use the user's business words from the dictionary
("orders", "outlets", "pumps") rather than system words ("datasets", "entities").

### 11a.8 Engagement (make people come back and get work done)
- **Today** is the home screen: the morning briefing's top items, today's alerts, goal progress, and three
  suggested questions generated from the user's own data. The briefing email links straight here.
- **First insight in under 5 minutes:** onboarding checklist (Company details → Connect data → Confirm
  dictionary → Ask first question), progress shown, each step resumable; after the dictionary is confirmed the
  app immediately runs one "health check" analysis and shows the first answer.
- Suggested follow-up questions under every answer; saved questions one tap away; pinned tiles refresh live.
- Positive moments are shown plainly: growth signals appear as "Working well" items with the evidence, no
  confetti or gamification.
- Command palette (Ctrl/Cmd + K): jump to screens, saved questions and connections.
- Keyboard: Enter to ask, Shift+Enter for a new line, `/` focuses the ask box.

### 11a.9 Quality floor & design QA
- Responsive at 375, 768, 1280, 1536 px; touch targets ≥ 44 px; visible focus ring (2 px `neel`, 2 px offset);
  full keyboard navigation; screen-reader labels; charts have text summaries.
- Light and dark mode (follow system, user can override).
- Performance: app first load < 200 KB JS gzipped for the shell; marketing site Lighthouse ≥ 95 on all four.
- Playwright visual snapshots for every main screen in light/dark × desktop/mobile; review them at each milestone
  and fix misalignment, overflow, truncation and contrast problems before finishing the milestone.

---

## 11b. Public website (R17)

Astro static site in `site/`, sharing `design/` tokens and fonts. Pages: Home, Pricing, Security & privacy,
Connectors, Sign in / Start free (links into the app), Privacy policy, Terms.

**Home page sections (in order):**
1. **Hero — a live-feeling demo, not a stock illustration.** Headline: "Your business, explained every
   morning." Sub-line: "Connect Tally, Sheets, Shopify or Zoho. Ask in your own language. Get the reason behind
   every number and what to do next." Beside it, an animated answer sheet: a question types itself (alternating
   English and Hindi, e.g. "Why did South sales fall in July?" / "दक्षिण में बिक्री क्यों घटी?"), progress steps
   tick, the answer appears and the haldi mark sweeps behind "−18%". Built from real demo-fixture output, not
   lorem ipsum. Primary button "Start free", secondary "See how it works".
2. **How it works** — three real steps (Connect your data · Ask a question · Get answers and a 6 AM briefing).
3. **Works with the tools you use** — Tally, Zoho Books, Shopify, Razorpay, Google Sheets, Excel, databases;
   text names or official brand assets only, used as each brand's guidelines allow.
4. **Your morning briefing** — a realistic email preview on a phone frame.
5. **Try a decision before you make it** — small interactive what-if slider using demo data.
6. **Answers you can check** — the SourceMark popover shown in action; "Read-only. Encrypted. Your data stays
   yours." with links to the Security page.
7. **Plans** — Managed AI and BYOK side by side (prices shown as "Coming soon" until billing exists).
8. **FAQ** — 6–8 plain questions (Is my data safe? Do I need a technical person? Which languages? …).
9. **Final call to action** — "Connect your first data source in minutes." [Start free].

Mobile-first, works without JavaScript except the hero demo and slider, Open Graph images, sitemap, analytics
hook (privacy-friendly, e.g. Plausible) behind an env flag.

---

## 12. Accuracy evaluation (X4)

### 12.1 Demo company fixture (`eval/generate_fixtures.py`)
A synthetic but realistic dataset with **known, planted findings**, so accuracy can be checked against an
answer key. Deterministic (`numpy` seed 42). Also used for local demos, the landing-page hero and screenshots.

**Company:** "UrbanCart", an Indian D2C e-commerce brand. Period **2025-01-01 to 2026-09-30**. Currency INR.
- **Regions → cities:** North (Delhi, Chandigarh, Lucknow, Jaipur), South (Bengaluru, Chennai, Hyderabad, Kochi),
  East (Kolkata, Bhubaneswar, Patna, Guwahati), West (Mumbai, Pune, Ahmedabad, Goa). Customer share
  27/30/18/25%.
- **Customers:** ~3,000; signup dates from 2024-06 to 2026-09; segment Consumer 85% / Small Business 15%
  (Small Business buys ~1.6× more often and double quantities); acquisition channel Organic 30%, Google Ads 25%,
  Instagram 20%, Referral 12%, Email 8%, YouTube 5%; per-customer purchase propensity ~ Gamma(2,1).
- **Products:** 8 per category, 40 total. Categories with (cost as % of price, typical discount, price range ₹):
  Electronics (74%, 5%, 1,500–15,000), Fashion (42%, 22%, 499–4,999), Home & Kitchen (55%, 8%, 399–8,999),
  Beauty (38%, 10%, 199–2,499), Sports (57%, 7%, 599–12,999). Category order mix 22/28/20/18/12%.
- **Orders:** ~1,100/month growing 2%/month, seasonality index Jan 0.85, Feb 0.90, Mar–Apr 0.95, May 1.0,
  Jun 0.95, Jul 1.0, Aug–Sep 1.05, Oct 1.35, Nov 1.40, Dec 1.15; festive extra discount +8 pts in Oct–Nov;
  sales channel App 50% / Website 30% / Marketplace 20%; revenue = qty × unit price × (1 − discount).
- **Marketing campaigns** (one per channel per quarter; spend/month ₹, CTR, CVR, AOV, target ROAS):
  Google Ads (2.3 L, 3.5%, 3.0%, 2,400, 1.7), Instagram (1.3 L, 2.2%, 4.0%, 1,900, 4.2), Facebook (0.9 L, 1.5%,
  2.5%, 1,800, 2.1), Email (0.2 L, 6.0%, 5.0%, 2,100, 6.5), YouTube (1.1 L, 0.8%, 1.2%, 2,600, 0.8), Influencer
  (0.75 L, 3.0%, 2.0%, 2,200, 2.6), each with ±15–25% noise.
- **Budget vs actual** (monthly budget ₹, growing 1.5%/month): Marketing 7 L, Operations 9 L, Technology 5.5 L,
  Sales 4.5 L, HR 2.5 L; actual = budget × N(1, 0.035).

**Planted findings (the answer key):**
1. **South revenue falls ~18% in Q3 2026 vs Q2 2026 while every other region grows.** Cause: a 12% Electronics
   price hike on 2026-07-01, after which **repeat** South customers (signed up before July 2026) order ~45% less
   often. New-customer revenue in the South is roughly unchanged.
2. **Channel efficiency:** Email (~6.4×) and Instagram (~4.1×) have the best ROAS; **YouTube ROAS ≈ 0.8× (loses
   money)**; Google Ads takes the largest spend at ~1.6×.
3. **Marketing is ~22% over budget in Q3 2026** (actual = 1.20–1.30 × budget from July 2026); Operations runs
   8–12% over in Oct–Nov (festive logistics).
4. **Fashion has the heaviest discounting (~23% average)**; Electronics has the thinnest gross margin.
5. **One anomaly day:** 2026-08-15, App orders drop ~60% for that day only (app outage).
6. **One growth signal:** Beauty orders in the West grow for 4 consecutive months (Jun–Sep 2026), driven by repeat
   customers.
7. **One data-quality gap:** Website orders for the week of 2026-03-09 are missing.
8. **Cross-source check:** monthly targets in the Sheets file; Q3 2026 revenue misses target in South only.

**Split into realistic sources** (written to `eval/fixtures/`):
- `shopify_orders.csv` + `shopify_customers.csv` + `shopify_products.csv` (Shopify-like column names),
- `razorpay_payments.csv` (amounts in paise, status captured/refunded, created_at as unix time),
- `marketing_sheet.xlsx` (Google-Sheets-like: one tab per quarter with campaign rows; plus a `Targets` tab with
  monthly revenue targets per region),
- `zoho_budget_expenses.xlsx` (department budget vs actual by month),
- `ANSWER_KEY.md` describing every planted finding with the exact numbers computed from the generated data.

Unit tests assert each planted finding is detectable by the analytics library (e.g. `driver_analysis` shows
South as the only shrinking region and repeat customers as the driver).

### 12.2 Golden questions
- `eval/golden_questions.yaml`: ≥ 40 questions with expected facts (numbers within tolerance, named drivers,
  required data notes), including 6 Hindi/Kannada questions and 5 what-if scenarios with expected ranges.
- `make eval` runs them against a real model (opt-in, needs key) and writes a scored report
  (`eval/reports/<date>.md`); CI runs the same pipeline with the fake LLM to check the harness and tool layer.
- Gate: Phase 1 sign-off requires ≥ 85% pass on the real-model eval.

---

## 13. Email briefing pipeline (R10)

1. Beat job every 5 min finds `briefing_settings` due in the next window (per user timezone); creates a
   `briefing_runs` row (unique per user+slot → idempotent).
2. Worker computes, with the user's AccessContext: KPI snapshot vs yesterday/last week, anomalies (10.2),
   goal progress and projection, growth signals (10.3), upcoming risks (e.g. stock running out, receivables due,
   goal shortfall), data-quality issues, and open long-task results.
3. LLM writes the briefing in the user's language using the briefing playbook: subject line with the single most
   important item; sections **Today's alerts**, **Unusual numbers**, **Suggested changes** (₹ impact per R9),
   **Growth signals**, **Goals**, or a short **Business health** summary when nothing is notable. Every number
   carries provenance (links into the app).
4. Render HTML (mobile-friendly) + plain text; send; store as `report`; unsubscribe/settings link.
5. Long tasks: Celery job with larger budgets; progress updates; on completion create a report (PDF + in-app) and
   email it.

---

## 14. Milestones (build in this order)

### Phase 1 — MVP
| # | Milestone | Acceptance criteria |
|---|---|---|
| M1 | **Foundation** | `make dev` starts all services; health endpoint; CI (lint, type, test); config via env; logging with redaction; `.env.example`; `eval/generate_fixtures.py` produces the demo company sources + `ANSWER_KEY.md` (12.1), with tests that each planted finding is present in the data. |
| M1b | **Design system + app shell** | `design/tokens.json` → CSS variables + Tailwind preset (light/dark); fonts incl. all Indic scripts; Storybook with every 11a.5 component (Highlight sweep, AnswerSheet, SourceMark, QualityBanner, ProgressSteps, KpiTile, ScenarioCard, ConnectionCard, EmptyState); app shell (sidebar / rail / bottom tabs) with placeholder screens; contrast test passes; Playwright snapshots light/dark × desktop/mobile. |
| M2 | **Auth core** | Sign-up (org + Owner), email verification, login, refresh rotation + reuse detection, logout, idle/absolute timeouts, rate limiting, password reset, consent recording; audit entries. Tests incl. token reuse attack. |
| M3 | **2FA, Google sign-in, devices** | Email OTP + TOTP + recovery codes, mandatory for Owners; Google OIDC; new-device alert email (Mailpit). |
| M4 | **Orgs, roles, invites, RLS** | Invite/accept/remove, role changes, permission matrix (6.2) enforced via `require()`; Postgres RLS; cross-tenant test suite (every route returns 404 for other orgs). Activity log UI (Owner). |
| M5 | **Secrets, LLM layer, plans, usage** | Envelope encryption; Gemini + Claude + Fake clients following the known pitfalls (7.2); Managed/BYOK key resolution; usage metering + monthly limit; Settings UI for plan/key/usage. |
| M6 | **Connections framework + Excel/CSV + Google Sheets** | Connector interface, read-only HTTP wrapper, cache/snapshots/incremental logic, quality checks, file versions, Sheets OAuth + Picker (mock in tests), Connections UI, grants editor. |
| M7 | **Zero setup: profiler + business dictionary** | Profiling, LLM draft, validation, review/confirm UI, versioning, schema-change drafts. |
| M8 | **Agent core + Ask UI** | Tools (7.3) minus what-if/goal/growth, lenses, playbooks, provenance + number check, quality notes, multilingual answers, ₹ impact rule, safety limits, streaming progress, chat history, saved questions, "Show my work", charts (ECharts), mobile layout. |
| M9 | **What-if simulator** | 10.4 complete with sliders; 5 eval scenarios pass on fixtures with fake LLM; ranges + assumptions always shown. |
| M10 | **Briefings, long tasks, exports, sharing, eval** | Daily briefing pipeline (13), anomalies + growth signals, long tasks with emailed report, PDF/Excel export, share; eval harness + golden questions; DPDP features (6.6) incl. org deletion and data export. Today screen + onboarding checklist (11a.8). |
| M10b | **Public website** | Astro site (11b) with hero demo built from fixture output, what-if slider, pricing, security, FAQ; Lighthouse ≥ 95; mobile-first; links to sign-up. Final design QA pass over the whole app (11a.9). **→ Phase 1 review (stop).** |

### Phase 2
| # | Milestone | Acceptance criteria |
|---|---|---|
| M11 | Razorpay connector | GET-only wrapper tested; payments/orders/refunds/settlements datasets; incremental; fixtures. |
| M12 | Shopify connector | OAuth, GraphQL queries only (mutation rejection test), bulk ops, incremental. |
| M13 | Zoho Books connector | Region-aware OAuth, read scopes, quota tracking, reports datasets. |
| M14 | PostgreSQL/MySQL connector | Read-only session, sqlglot guard, push-down `query_source`, write-privilege warning. |
| M15 | Goals, pinned dashboard, UI translations | Goals + projections in chat/briefings; dashboard tiles re-run with viewer permissions; i18next UI in Hindi + Kannada (others scaffolded). **→ Phase 2 review (stop).** |

### Phase 3
| # | Milestone | Acceptance criteria |
|---|---|---|
| M16 | Tally bridge | Pairing, read-only XML export requests, scheduled + on-demand push, offline status in UI; no import/alter requests (test). |
| M17 | Industry templates | Six templates with KPIs, playbook tweaks, starter questions, default what-if elasticities; chosen at onboarding. |
| M18 | Billing hooks (no payments) | Plan change flow, limit warnings at 80%/100%, invoice-ready usage summaries. |
| M19 | Hardening | Load test (50 concurrent questions), security review checklist (`docs/SECURITY.md`), backup/restore runbook, prod deploy guide (AWS `ap-south-1`). **→ Phase 3 review (stop).** |

---

## 15. Definition of done (every milestone)
- Acceptance criteria met and demonstrable locally with the demo fixtures.
- Tests: unit + API (incl. 401/403/404-cross-tenant) + security tests for the new surface; all green.
- No secrets in code/logs; new env vars documented in `.env.example`.
- Migrations reversible; `docs/PROGRESS.md` and, if needed, `docs/DECISIONS.md` updated.
- UI works on desktop and 375 px mobile, uses only design tokens (no hard-coded colours/sizes), passes the
  11a.9 checklist; visual snapshots reviewed.
- Suggested commit message `M<n>: <name>` printed for the human (no automatic commit).

## 16. Environment variables (minimum; extend as needed)
```
APP_ENV, APP_BASE_URL, API_BASE_URL
DATABASE_URL, REDIS_URL
S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION
MASTER_KEY (dev) | KMS_KEY_ID (prod)
JWT_SECRET, ACCESS_TOKEN_MINUTES=10, SESSION_IDLE_MINUTES=30, SESSION_MAX_HOURS=12
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM
GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_PICKER_API_KEY
GEMINI_API_KEY, GEMINI_API_VERSION=v1beta, GEMINI_DEFAULT_MODEL, ANTHROPIC_API_KEY
LLM_DEFAULT_PROVIDER=gemini
CACHE_TTL_SECONDS=300, SNAPSHOT_MAX_AGE_HOURS=24
MANAGED_MONTHLY_QUESTION_LIMIT=500
SHOPIFY_API_KEY, SHOPIFY_API_SECRET, ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET
```

## 17. First instruction to execute
This is a **new project built from scratch**; there is no earlier code to reuse.
1. Create `.gitignore` (must include `.env`, virtual environments, `node_modules`, build output). Do not run
   `git init` or any commit unless the human asks.
2. Before coding, write `docs/ARCHITECTURE.md` (a one-page summary of Sections 3–7 in your own words, plus a
   component diagram in Mermaid), `docs/DESIGN.md` (Section 11a in your own words, with the token table), and
   `docs/PROGRESS.md` with the milestone checklist.
3. Build M1, then stop and wait. Continue one milestone at a time (M1 → M1b → M2 … → M10 → M10b), stopping after
   each as described in Section 0 and `CLAUDE.md`.
