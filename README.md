# AI Business Analyst Platform

An AI business analyst for Indian SMBs. The full specification is in [BUILD_SPEC.md](BUILD_SPEC.md); progress is in
[docs/PROGRESS.md](docs/PROGRESS.md).

## Requirements
Docker Desktop (running), Python 3 with [uv](https://docs.astral.sh/uv/), Node 22+ and npm.

## Quick start
```bash
cp .env.example .env      # first time only, then fill in your values
make dev                  # builds and starts everything, waits until healthy
```
| What | Where |
|---|---|
| Web app | http://localhost:5173 |
| API (docs) | http://localhost:8000/docs |
| Health / readiness | http://localhost:8000/health, http://localhost:8000/health/ready |
| Emails (Mailpit) | http://localhost:8025 |
| File storage console | http://localhost:9001/rustfs/console/ |

`make down` stops everything (data is kept). `make logs` follows the logs.

## Everyday commands
```bash
make check       # lint + type checks + all tests (what CI runs)
make test        # backend (pytest) and frontend (vitest) tests
make fixtures    # generate the UrbanCart demo data and eval/fixtures/ANSWER_KEY.md
make storybook   # component workshop at http://localhost:6006
make e2e         # visual snapshots + accessibility + layout checks (Playwright in Docker)
make tokens      # regenerate design/generated/ after editing design/tokens.json
make help        # every command
```
Backend tests start a throwaway Postgres 16 with testcontainers, so Docker must be running (or set
`TEST_DATABASE_URL` to an empty database you own). They never touch your dev database or `.env`.

## Signing in locally
Open http://localhost:5173, choose **Create an account**, then open the confirmation email in Mailpit
(http://localhost:8025). Owners will also need two-step verification from milestone M3.

## Layout
`backend/` FastAPI + Celery · `frontend/` React + Vite · `design/` design tokens · `eval/` demo-data generator and answer key ·
`scripts/` helpers · `docs/` architecture, design, decisions, progress.
