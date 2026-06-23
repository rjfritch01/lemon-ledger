# Lemon Ledger – local dev task runner
# Requires: just, uv, docker, docker compose

set dotenv-load := true

_api := "apps/api"

default:
    @just --list

# ── infra ──────────────────────────────────────────────────────────────────────

# Start Postgres and Redis in the background
up:
    docker compose up -d

# Stop all services
down:
    docker compose down

# Tail logs from all services (Ctrl-C to exit)
logs:
    docker compose logs -f

# Show service status and health
ps:
    docker compose ps

# ── api dev ────────────────────────────────────────────────────────────────────

# Run the API with live reload
dev:
    cd {{_api}} && uv run uvicorn lemon_ledger.api.app:create_app --factory --reload

# Start a Celery worker
worker:
    cd {{_api}} && uv run celery -A lemon_ledger.workers.celery_app worker -l info

# ── migrations ─────────────────────────────────────────────────────────────────

# Apply all pending migrations to the local Postgres
migrate:
    cd {{_api}} && uv run alembic upgrade head

# Autogenerate a new migration; usage: just migrate-rev msg="add user table"
migrate-rev msg='':
    cd {{_api}} && uv run alembic revision --autogenerate -m "{{msg}}"

# Fail if there is more than one alembic head (indicates a branch)
migrate-check:
    @test "$( cd {{_api}} && uv run alembic heads | wc -l | tr -d ' ')" -eq 1

# ── quality gates ──────────────────────────────────────────────────────────────

# Run all tests (with coverage gate)
test:
    cd {{_api}} && uv run pytest

# Lint: ruff + black (check) + isort (check)
lint:
    cd {{_api}} && uv run ruff check . && uv run black --check . && uv run isort --check-only .

# Auto-format: black + isort
fmt:
    cd {{_api}} && uv run black . && uv run isort .

# mypy strict type check
typecheck:
    cd {{_api}} && uv run mypy src

# bandit security scan
security:
    cd {{_api}} && uv run bandit -r src -ll

# ── misc ───────────────────────────────────────────────────────────────────────

# Print toolchain versions for this project
doctor:
    @echo "=== Lemon Ledger – Toolchain Versions ==="
    @git --version
    @uv --version
    @python3.12 --version
    @docker --version
    @docker compose version
    @just --version
