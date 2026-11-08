# Developer commands. Run "make help" for the list.
# "make dev" is the one command that starts everything.

COMPOSE := docker compose --env-file .env --env-file .compose.env
BACKEND := cd backend &&
RUFF_ARGS := --config pyproject.toml . ../eval

.DEFAULT_GOAL := help
.PHONY: help dev up down logs ps env test test-backend test-frontend lint lint-backend lint-frontend \
        typecheck typecheck-backend typecheck-frontend format check migrate fixtures seed-demo eval

help: ## Show this list
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'

# --- Run the stack ------------------------------------------------------------------------------------------------
env: ## Derive docker-only variables (Postgres user/password, container URLs) from .env
	@python3 scripts/compose_env.py

dev: env ## Build and start every service in the background, wait until healthy
	$(COMPOSE) up -d --build --wait
	@echo ""
	@echo "Everything is running:"
	@echo "  Web app          http://localhost:5173"
	@echo "  API              http://localhost:8000   (docs: /docs, health: /health, readiness: /health/ready)"
	@echo "  Emails (Mailpit) http://localhost:8025"
	@echo "  File storage     http://localhost:9001   (console, log in with S3_ACCESS_KEY / S3_SECRET_KEY)"
	@echo ""
	@echo "Follow logs with 'make logs'. Stop with 'make down'."

up: dev ## Alias for dev

down: env ## Stop all services (data volumes are kept)
	$(COMPOSE) down

logs: env ## Follow logs from all services (Ctrl+C to stop following)
	$(COMPOSE) logs -f --tail=100

ps: env ## Show service status
	$(COMPOSE) ps

migrate: env ## Apply database migrations inside the running api container
	$(COMPOSE) exec api alembic upgrade head

# --- Quality gates ------------------------------------------------------------------------------------------------
test: test-backend test-frontend ## Run all tests

test-backend: ## Backend tests (pytest)
	$(BACKEND) uv run pytest

test-frontend: ## Frontend tests (vitest)
	cd frontend && npm test

lint: lint-backend lint-frontend ## Linters and format checks

lint-backend:
	$(BACKEND) uv run ruff check $(RUFF_ARGS) && uv run ruff format --check $(RUFF_ARGS)

lint-frontend:
	cd frontend && npm run lint && npm run format:check

typecheck: typecheck-backend typecheck-frontend ## Type checks (mypy, tsc)

typecheck-backend:
	$(BACKEND) uv run mypy

typecheck-frontend:
	cd frontend && npm run typecheck

format: ## Auto-format backend and frontend code
	$(BACKEND) uv run ruff format $(RUFF_ARGS) && uv run ruff check --fix $(RUFF_ARGS)
	cd frontend && npm run format

check: lint typecheck test ## Everything CI runs: lint, types, tests

# --- Demo data and evaluation -------------------------------------------------------------------------------------
fixtures: ## Generate the UrbanCart demo sources and ANSWER_KEY.md into eval/fixtures/
	$(BACKEND) uv run python ../eval/generate_fixtures.py

seed-demo: fixtures ## Alias for fixtures (later milestones also load them into a demo workspace)

eval: ## Golden-question evaluation against a real model (arrives in milestone M10)
	@echo "The evaluation harness is built in milestone M10. Nothing to run yet."
