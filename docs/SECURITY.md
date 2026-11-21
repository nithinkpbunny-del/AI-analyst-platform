# Security review checklist (M19)

The security review of BUILD_SPEC section 6 (6.1-6.7), 6.5 (safety limits), 6.6 (privacy) and 15 (definition of
done), done on 2026-11-21 against branch `m19-hardening`. Each item is **pass** (already right, with the test that
proves it), **fixed** (wrong or missing, fixed in M19) or **open** (not done, with the reason and what closes it).

Run the automatic parts again with:

```
make test-backend                         # includes tests/security/* and tests/api/test_route_matrix.py
cd backend && uv export --format requirements-txt --no-hashes --all-groups --no-emit-project > /tmp/req.txt \
  && uvx pip-audit -r /tmp/req.txt --no-deps --disable-pip         # Python dependencies (needs the internet)
cd frontend && npm audit --omit=dev       # what ships to browsers; plain "npm audit" includes build tools
make backup-drill                         # restore keeps RLS and the append-only activity log
```

## Summary

| Area | Result |
|---|---|
| Authentication, sessions, 2FA | pass |
| Authorisation, RLS, cross-tenant 404 | pass (route matrix: every route is classified, see below) |
| Read-only layers per connector | pass |
| Secrets and encryption | pass; 1 open (moving from `MASTER_KEY` to KMS on live data) |
| Log redaction | **fixed** (Zoho tokens) |
| Rate limits and safety limits | pass |
| Uploads and request size | **fixed** (body cap on every route) |
| CSRF, CORS | pass |
| Security headers | **fixed** (none were sent) |
| Client address behind a load balancer | **fixed** (`TRUSTED_PROXY_HOPS`) |
| API docs in production | **fixed** (switched off) |
| Privacy (DPDP) | **fixed** (D85 final: anonymised activity on workspace deletion); 2 open |
| Dependencies | pass for Python; 1 open (ECharts advisory, not reachable); dev-only npm advisories open |
| Backup and restore | pass (drill) |

**Open items** (none blocks a pilot; all are listed again at the end): (1) switching an existing installation from
`MASTER_KEY` to KMS needs a re-wrap script; (2) the ECharts 5 advisory, which the app cannot trigger, until the
upgrade to ECharts 6.1 with a visual review; (3) dev-only npm advisories (eslint, Storybook); (4) self-service
account deletion and a way back for an account left with no workspace; (5) a retention limit for the activity log
(kept at least 180 days for CERT-In); (6) a WAF and per-IP limits in front of the API at deploy; (7) real
penetration test and real-provider checks before public launch.

---

## 1. Authentication (BUILD_SPEC 6.1)

| # | Check | Result | Evidence |
|---|---|---|---|
| 1.1 | Passwords argon2id, at least 10 characters, top-100k breached list, no composition rules | pass | `app/auth/passwords.py`; `tests/unit/test_passwords.py` |
| 1.2 | 5 failed sign-ins per (email, IP) in 15 min, then doubling lockouts; generic "Invalid email or password"; similar timing for unknown emails | pass | `app/auth/ratelimit.py`; `tests/security/test_auth_security.py`, `tests/api/test_auth.py` |
| 1.3 | Email confirmed before the first password sign-in | pass | `tests/api/test_auth.py` |
| 1.4 | Access JWT 10 min, HS256 pinned on decode (`algorithms=[...]`), claims sub/org/role/sid; forged, tampered, expired and `alg=none` tokens refused | pass | `app/auth/tokens.py`; `tests/security/test_auth_security.py` |
| 1.5 | Refresh token opaque 256-bit, HttpOnly + Secure + SameSite=Strict, rotated every use, reuse ends the whole session, 12 h absolute, 30 min idle on the server | pass | `app/api/auth.py`; `tests/security/test_auth_security.py` (reuse attack) |
| 1.6 | Role and membership re-read on every request: removal and role change act at once; all sessions revoked on removal | pass | `tests/api/test_team.py`, `tests/security/test_team_security.py` |
| 1.7 | Google OIDC: PKCE, state, nonce; ID token signature, issuer, audience, expiry, verified email; linking only to a verified matching email | pass | `tests/unit/test_google_oidc.py`, `tests/api/test_google.py` |
| 1.8 | Password reset: single-use 60-min link, signs out every device, "password changed" email | pass | `tests/api/test_auth.py` |

## 2. Sessions and two-step verification

| # | Check | Result | Evidence |
|---|---|---|---|
| 2.1 | 2FA mandatory for Owners (no session and no owner action before it is on), optional for others | pass | `tests/api/test_two_factor.py`; route matrix "owner without 2FA is 403" for every workspace route |
| 2.2 | TOTP ±1 step, no code reuse; email codes 6 digits, 10 min, single use, 5 attempts; 10 hashed recovery codes | pass | `tests/security/test_two_factor_security.py`, `tests/unit/test_codes.py` |
| 2.3 | Code entry locks after 10 wrong codes in 15 min (doubling); 10 code emails an hour; 30 s between resends | pass | same |
| 2.4 | New device: signed device cookie, "New sign-in" email, "This wasn't me" signs out everywhere | pass | `tests/api/test_devices.py`, `tests/unit/test_device_ids.py` |
| 2.5 | Every sign-in success and failure, 2FA change, reset and revocation is in the activity log | pass | `tests/api/test_audit.py` |

## 3. Authorisation, RLS and cross-tenant 404 (BUILD_SPEC 6.2, P3, P6)

| # | Check | Result | Evidence |
|---|---|---|---|
| 3.1 | One permission function, no role checks inline | pass | `app/auth/permissions.py`; `test_every_permission_used_by_a_route_exists` |
| 3.2 | **Route matrix**: every route of the API is classified (public, self, personal, data, stateful, device or a `Case`); a new route fails the build until it is. 154 routes: 86 workspace routes with a `Case`, 29 public, 12 self, 19 personal, 2 data, 2 stateful, 4 Tally device | pass | `tests/api/test_route_matrix.py::test_every_route_is_classified` |
| 3.3 | Every workspace route: 401 signed out, 401 bad token, 403 wrong role, 403 Owner without 2FA, **404 for another workspace's ids**, 404 for a missing id (same answer), 422 malformed id, validation errors; nothing of another workspace appears in lists | pass | `tests/api/test_route_matrix.py` (parametrised over every `Case`) |
| 3.4 | Postgres RLS with the `tenant_isolation` policy on every tenant table, restricted `app_tenant` role per transaction (`SET LOCAL`), grants per table (read-only history tables: SELECT, INSERT only) | pass | `tests/security/test_rls.py` (also checks every table with `org_id` has RLS) |
| 3.5 | RLS survives a backup and restore | pass | `make backup-drill`; `tests/unit/test_backup.py::test_isolation_holds_on_a_real_database` |
| 3.6 | The agent, briefings and dashboard tiles run with the asking person's (or recipient's) permissions and connection grants | pass | `tests/security/test_agent_security.py`, `tests/security/test_goals_dashboard_security.py`, `tests/security/test_reports_security.py` |

## 4. Read-only on customer data, per connector (BUILD_SPEC 6.3, P2)

| # | Connector | Layers | Result | Evidence |
|---|---|---|---|---|
| 4.1 | All HTTP connectors | `ReadOnlyHttpClient`: GET/HEAD only, POST only to an allow-list; checked in the wrapper and again as an httpx request hook | pass | `tests/connectors/test_http.py`; `tests/security/test_connections_security.py::test_no_connector_can_send_a_write` tries POST/PUT/PATCH/DELETE through **every** connector type |
| 4.2 | Google Sheets | `spreadsheets.readonly` + `drive.file` with the Picker; GET only | pass | same, `tests/connectors/test_google_sheets.py` |
| 4.3 | Razorpay | GET only (keys can write, the platform never does; the UI says so) | pass | same, `tests/connectors/test_razorpay.py` |
| 4.4 | Shopify | `read_*` scopes; GraphQL POST allowed only for documents that parse as queries (mutations refused, D93) | pass | `tests/unit/test_graphql_guard.py`, the mutation attempts in 4.1 |
| 4.5 | Zoho Books | `ZohoBooks.<module>.READ` scopes only (others dropped); GET only | pass | `tests/connectors/test_zoho_books.py`, 4.1 |
| 4.6 | PostgreSQL / MySQL | read-only session, sqlglot single SELECT, deny-listed functions, statement timeout, write-privilege warning, private addresses refused | pass | `tests/unit/test_sql_guard.py`, `tests/connectors/test_sql_database.py`, 4.1 (DELETE/UPDATE/INSERT/DROP/TRUNCATE/FOR UPDATE/SET TRANSACTION READ WRITE) |
| 4.7 | Tally | no network client in the platform; the bridge sends export requests only, never import/alter | pass | `tests/security/test_tally_security.py`, `bridge/tally/bridge_tests` |
| 4.8 | Files | stored copies, original never changed; xlsx zip-bomb limits; XML through defusedxml | pass | `tests/security/test_connections_security.py` |
| 4.9 | DuckDB workspace | external access off, locked configuration, SELECT-only guard, timeout, row cap | pass | `tests/agent/test_agent.py`, `tests/unit/test_sql_guard.py` |

## 5. Secrets and encryption (BUILD_SPEC 6.4)

| # | Check | Result | Evidence |
|---|---|---|---|
| 5.1 | AES-256-GCM envelope encryption, a data key per secret, wrapped by `MASTER_KEY` (dev) or AWS KMS (`KMS_KEY_ID`, behind the same `KeyWrapper` interface), context bound as associated data, `key_version` stored | pass | `app/security/crypto.py`; `tests/unit/test_crypto.py` |
| 5.2 | Secrets write-only through the API; responses show `last4` and status only | pass | `tests/security/test_ai_security.py`, `tests/security/test_connections_security.py` |
| 5.3 | No secrets in the database in clear, decryption fails closed | pass | `tests/security/test_auth_security.py` |
| 5.4 | Production refuses to start with a missing or short `MASTER_KEY` / `JWT_SECRET` | pass | `tests/unit/test_config.py` |
| 5.5 | Moving an **existing** installation from `MASTER_KEY` to KMS | **open** | Secrets wrapped by the master key are not re-wrapped automatically; a new installation should start with KMS (docs/DEPLOY_AWS.md). Closes with a one-off re-wrap command when an installation needs to move. |
| 5.6 | Backups contain encrypted secrets, never the key | pass | `ops/backup.py` holds no key; the runbook keeps the key's backup apart |

## 6. Log redaction

| # | Check | Result | Evidence |
|---|---|---|---|
| 6.1 | Every handler (API, Celery) redacts: exact secret values from settings; JWTs, bearer/basic headers, Google, Anthropic, Razorpay, Shopify and AWS keys, Google OAuth tokens, URL passwords, `password=` style pairs, emails | pass | `app/security/redaction.py`; `tests/unit/test_redaction.py` |
| 6.2 | **Zoho tokens** (`Zoho-oauthtoken 1000.x.y`): the key/value rule redacted only the word after "authorization", leaving the token | **fixed** | new patterns; two new cases in `tests/unit/test_redaction.py` |
| 6.3 | Request log lines carry the path only (query strings can hold tokens), never bodies | pass | `app/main.py` request middleware |
| 6.4 | The activity log stores a hash of each question, never the answer or data rows | pass | `tests/api/test_chat.py` |

## 7. Rate limits and safety limits (BUILD_SPEC 6.5, X5)

| # | Check | Result | Evidence |
|---|---|---|---|
| 7.1 | Questions 10 a minute per person; Managed plan monthly limit checked before a question starts; 2 long tasks per workspace | pass | `tests/api/test_chat.py`, `tests/api/test_tasks.py`, `tests/api/test_billing.py` |
| 7.2 | Limits on sign-in, email sending (per address and per IP), 2FA codes, invitations, data export, AI key checks, "send briefing now", what-if, Tally pairing | pass | the respective API tests |
| 7.3 | Agent: 12 steps (40 long), 3,000 output tokens per call, 150k input tokens per question, 200 rows to the model per tool call, 30 s source timeout (120 long), 1M rows per dataset | pass | `app/config.py`; `tests/agent/test_agent.py` |
| 7.4 | Behind a load balancer every caller had the balancer's address: the per-IP limits would have become one shared limit for everyone (and the activity log would show one IP) | **fixed** | `TRUSTED_PROXY_HOPS` reads the right-most trusted `X-Forwarded-For` entry; caller-written entries are ignored; `tests/security/test_hardening.py::TestClientAddress` |
| 7.5 | Load: 50 people asking at once with the fake AI, no errors, no pool exhaustion | pass | `make loadtest` (docs/PROGRESS.md M19 has the numbers); questions now have their own worker queue (D122) |

## 8. Uploads and request size

| # | Check | Result | Evidence |
|---|---|---|---|
| 8.1 | Uploads: `Content-Length` required and checked before reading, read capped at 50 MB, one file per request, filename cleaned (no paths, no control characters), stored under ids not names | pass | `app/api/connections.py`; `tests/api/test_connections.py`, `tests/security/test_connections_security.py` |
| 8.2 | xlsx: limited parts and unpacked size; defusedxml | pass | `app/connectors/file_formats.py` |
| 8.3 | **Every other route** accepted a body of any size (FastAPI reads JSON whole) | **fixed** | `app/security/body_limit.py`: 4 MB for JSON routes, the upload limit for `/files`, the batch limit for the bridge, counted as bytes arrive (a missing or false length does not help); `tests/security/test_hardening.py::TestBodyLimit` |
| 8.4 | Exports guard CSV cells against spreadsheet formulas (activity log, billing summary) | pass | `tests/api/test_audit.py`, `tests/api/test_billing.py` |

## 9. CSRF, CORS and cookies

| # | Check | Result | Evidence |
|---|---|---|---|
| 9.1 | Cookie-authenticated endpoints (refresh, logout) need the double-submit CSRF token; everything else uses a bearer token held in memory (not sent automatically by the browser) | pass | `app/auth/deps.py::require_csrf`; `tests/security/test_auth_security.py` |
| 9.2 | CORS: only the app's and the site's origins, credentials allowed for those only | pass | `app/main.py` |
| 9.3 | OAuth callbacks (Google, Shopify, Zoho) check a single-use `state` | pass | the connector API tests |

## 10. Security headers

| # | Check | Result | Evidence |
|---|---|---|---|
| 10.1 | API responses had **no** security headers | **fixed** | every response: `Content-Security-Policy` (`default-src 'none'`, no scripts, `frame-ancestors 'none'`, forms only to the API and the app), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy`, `Permissions-Policy`, `Cache-Control: no-store` unless a route sets its own, HSTS when served over HTTPS; `tests/security/test_hardening.py::TestHeaders` |
| 10.2 | The interactive API docs (`/docs`, `/openapi.json`) were public in every environment | **fixed** | off when `APP_ENV=production` (the Swagger page needs a CDN script, so it also gets no CSP in development); `TestDocs` |
| 10.3 | The web app's own headers (CSP for the SPA, HSTS) | pass (at deploy) | set by CloudFront's response-headers policy; the exact policy is in docs/DEPLOY_AWS.md section 6 |

## 11. Privacy (BUILD_SPEC 6.6, DPDP Act)

| # | Check | Result | Evidence |
|---|---|---|---|
| 11.1 | Consent recorded at sign-up with the policy version | pass | `tests/api/test_auth.py` |
| 11.2 | Snapshots expire after `SNAPSHOT_MAX_AGE_HOURS` and are purged; disconnecting deletes snapshots and secrets at once | pass | `tests/connectors/test_cache.py`, `tests/api/test_connections.py` |
| 11.3 | Data export (ZIP) without passwords, credentials or AI keys | pass | `tests/api/test_today_privacy.py::TestExport` |
| 11.4 | Workspace deletion: 7-day grace, hard delete, files and report files removed, one anonymised `org.deleted` entry | pass | `tests/api/test_today_privacy.py` |
| 11.5 | **Earlier activity of a deleted workspace** stayed with names, IPs, browsers and details | **fixed** (D124, the final D85) | kept, anonymised: who, IP, browser, target and details removed; action and time kept. The append-only trigger allows exactly this change, only once the workspace is gone and only when the purge asks (migration 0018); `tests/security/test_audit_anonymise.py` |
| 11.6 | Accounts are kept when a workspace is deleted (global, may belong to other workspaces) | pass (decision D124) | `tests/api/test_today_privacy.py` |
| 11.7 | Self-service account deletion, and a way back for an account left with no workspace (today it can only be invited again) | **open** | D124: not built; until then a person's erasure request is handled by the operator (runbook). |
| 11.8 | A retention limit for the activity log | **open** | D124: kept, at least 180 days (CERT-In directions 2022 ask for 180 days of logs in India); an upper limit needs a legal decision. |
| 11.9 | Only aggregates and at most 5 sample rows go to the model; no raw datasets | pass | `tests/agent/test_agent.py`, `tests/unit/test_briefing_engine.py::test_the_writer_sees_no_rows` |

## 12. Dependencies

Run on 2026-11-21.

| # | Check | Result | Notes |
|---|---|---|---|
| 12.1 | Python (pip-audit over the locked `uv` export, 387 packages incl. dev) | pass | "No known vulnerabilities found" |
| 12.2 | npm, what ships to browsers (`npm audit --omit=dev`) | **open** (not reachable) | 1 moderate: ECharts < 6.1.0, GHSA-fgmj-fm8m-jvvx, an XSS in the tooltip of the **`lines`** series when `data.name` holds HTML. The app registers only the bar and line series and leaves tooltips to ECharts' escaping formatter, so the path cannot run; `src/components/chartSecurity.test.ts` keeps it that way. Close by upgrading to ECharts 6.1 (a major version: chart layout defaults change, so the Playwright snapshots must be re-recorded and reviewed). |
| 12.3 | npm, build and test tools (`npm audit`) | **open** (dev only) | 12 more moderate advisories, all in eslint / typescript-eslint (`minimatch`) and Storybook (`glob`); none is shipped. Close with the next eslint and Storybook upgrades. |

## 13. Backup and restore

| # | Check | Result | Evidence |
|---|---|---|---|
| 13.1 | Postgres and object storage can be backed up, restored into an empty database, and verified (row counts, policies, triggers, object checksums, tenant isolation, append-only log) | pass | `make backup-drill`; docs/RUNBOOK_BACKUP_RESTORE.md |
| 13.2 | A restore never overwrites a database that has tables | pass | `tests/unit/test_backup.py` |
| 13.3 | A damaged dump or object is refused | pass | same |

## Open items, with what closes each

1. **KMS for an existing installation** (5.5): start production on KMS; if an installation ever moves, write and
   run a re-wrap command first.
2. **ECharts 6.1** (12.2): upgrade, re-record and review the chart snapshots.
3. **Dev-tool npm advisories** (12.3): next eslint / Storybook upgrade.
4. **Account deletion and a way back for an account with no workspace** (11.7): a product decision; the operator
   handles requests meanwhile.
5. **Activity-log retention limit** (11.8): a legal decision; at least 180 days.
6. **Edge protection** (docs/DEPLOY_AWS.md section 6): AWS WAF with a rate-based rule per IP and the managed common
   rule set in front of the load balancer, to back up the app's own limits.
7. **Before public launch**: an external penetration test; the real-provider checks still untested (Gemini answers,
   Google, Shopify, Zoho, Razorpay, Tally, a managed customer database), listed in docs/PROGRESS.md.
