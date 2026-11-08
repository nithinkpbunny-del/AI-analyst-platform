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

## Layout
`backend/` FastAPI + Celery · `frontend/` React + Vite · `design/` design tokens · `eval/` demo-data generator and answer key ·
`scripts/` helpers · `docs/` architecture, design, decisions, progress.
