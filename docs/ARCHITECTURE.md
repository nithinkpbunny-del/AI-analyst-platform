# Architecture (one-page summary of BUILD_SPEC §3–7)

An AI business analyst for Indian SMBs. A business connects its data; people ask questions in plain language; an
agent fetches the **latest** data, computes answers with exact code, and replies with evidence, charts and
(when relevant) ₹-impact recommendations.

## Principles that shape every design choice

| Principle | What it means in practice |
|---|---|
| Live data (P1) | Fetch when a question is asked; reuse a snapshot only within `CACHE_TTL_SECONDS`. |
| Read-only (P2) | Several independent layers: read-only OAuth scopes, GET-only HTTP wrapper, SELECT-only SQL guard, read-only DB sessions, DuckDB with no external access, stored copies of files. |
| Tenant isolation (P3) | Every tenant table has `org_id`; workspace requests run each transaction as the restricted `app_tenant` role with `app.org_id` set (`SET LOCAL`), so Postgres Row-Level Security hides other workspaces; the repository layer filters again; cross-tenant access answers **404** (DECISIONS D34). |
| LLM reasons, code computes (P4) | Numbers come from SQL/pandas/statsmodels tools. A post-check verifies each number in an answer against a tool result. |
| Trust (P5) | Each tool result has a `result_id` with source, query, fetch time and row count; data-quality warnings travel with the answer. |
| Least privilege (P6) | One `require(auth, permission, resource)` function (`app/auth/permissions.py`, the 6.2 matrix as data), applied by the `requires()` route dependency; the agent runs with the asking user's `AccessContext`. |

## Stack

- **Backend:** Python 3.12 (uv), FastAPI + Pydantic v2, SQLAlchemy 2 + Alembic, PostgreSQL 16 (RLS).
- **Analysis:** DuckDB in-process per question, over Parquet snapshots; statsmodels/scipy for forecast and anomalies.
- **Background:** Celery workers + Celery beat; Redis is the broker, lock store, rate limiter and metadata cache.
- **Storage:** S3-compatible (MinIO locally, S3 `ap-south-1` in production) for uploads, snapshots, report PDFs.
- **LLM:** a provider interface (`LLMClient.generate`) with Gemini (default), Claude and a scripted Fake client.
  Managed AI uses the platform key with a monthly limit; BYOK uses the org's encrypted key.
- **Auth:** own implementation: argon2id, 10-minute JWT access tokens, rotating opaque refresh tokens with reuse
  detection, email-OTP/TOTP 2FA (mandatory for Owners), Google OIDC, device alerts.
- **Secrets:** AES-256-GCM envelope encryption; per-secret data key wrapped by the master key (env locally, KMS in prod).
- **Frontend:** React 18 + TypeScript + Vite, Tailwind + shadcn/ui, TanStack Query, ECharts, i18next.
  Marketing site is a separate Astro app that shares `design/` tokens.

## Component diagram

```mermaid
flowchart LR
    subgraph Browser
        SPA[React SPA]
        SITE[Astro marketing site]
    end

    subgraph Backend["Backend (FastAPI)"]
        API[API routers + require()]
        AUTH[Auth / sessions / 2FA]
        AGENT[Agent loop + tools]
        CONN[Connectors - read-only]
        DATA[Cache, snapshots, quality, DuckDB workspace]
        LLM[LLM layer - Gemini / Claude / Fake]
    end

    subgraph Workers["Celery"]
        WORKER[Worker: long tasks, briefings, refresh]
        BEAT[Beat: schedules, snapshot purge]
    end

    PG[(PostgreSQL 16 + RLS)]
    REDIS[(Redis)]
    S3[(S3 / MinIO: files, Parquet, PDFs)]
    MAIL[SMTP: Mailpit locally, SES/Resend in prod]
    SRC[(Customer sources: Sheets, Excel/CSV, Razorpay, Shopify, Zoho, DBs, Tally bridge)]
    MODEL[(Gemini / Claude APIs)]

    SPA -->|HTTPS + JWT| API
    SITE -->|links to sign-up| SPA
    API --> AUTH
    API --> AGENT
    AGENT --> LLM --> MODEL
    AGENT --> DATA
    DATA --> CONN --> SRC
    DATA --> S3
    API --> PG
    AUTH --> PG
    API --> REDIS
    BEAT --> REDIS
    REDIS --> WORKER
    WORKER --> AGENT
    WORKER --> PG
    WORKER --> MAIL
    AUTH --> MAIL
```

## Flow of one question

1. Detect the language and load the last 8 turns, the confirmed business dictionary, industry template, lens and
   the user's `AccessContext`.
2. Agent loop (max 12 tool steps): `list_sources` → `describe_dataset` → `fetch_dataset`/`query_source` →
   analytics tools (exact maths) → `make_chart`.
3. The LLM writes the answer in the user's language, citing `result_id`s for every number.
4. Post-check: every number must match a cited tool result; one repair turn, otherwise the number is removed
   and a data note is added.
5. Store message, tool trace, provenance, quality warnings and token usage.

## LLM layer rules (Gemini pitfalls we must follow in M5)

`api_version="v1beta"`; keep the client alive while paging; replay the model's original `Part`s including
`thought_signature`; tool schemas via `parameters_json_schema` with automatic function calling off; generate
function-call ids when missing; list models at runtime; retry 429/503 with backoff but never on daily-quota or
invalid-key errors.

## Safety limits (all configurable by env)

12 tool steps (40 for long tasks) · 3,000 output tokens per call · 150k input tokens per question · 200 rows to the
LLM per tool call · 30 s source timeout (120 s long tasks) · 1M rows per dataset · 10 questions/user/minute ·
2 concurrent long tasks per org · 500 managed questions/month · 50 MB uploads.

## Where things live

`backend/app` (api, auth, security, connectors, data, dictionary, analytics, agent, llm, briefings, tasks, exports,
email, privacy) · `frontend/src` · `design/` · `site/` · `bridge/tally/` · `eval/` (fixtures + golden questions).
Modules are added milestone by milestone; see `docs/PROGRESS.md`.
