# Lemon Ledger – local dev task runner
# Requires: just, docker, docker compose

set dotenv-load := true

default:
    @just --list

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

# Print toolchain versions for this project
doctor:
    @echo "=== Lemon Ledger – Toolchain Versions ==="
    @git --version
    @node --version | xargs -I{} echo "node {}"
    @pnpm --version | xargs -I{} echo "pnpm {}"
    @python3.12 --version
    @docker --version
    @docker compose version
    @just --version

# ── API (apps/api) ────────────────────────────────────────────────────────────

# Install Python dependencies for the API
api-install:
    cd apps/api && uv sync

# Start the API dev server with hot-reload
api-dev:
    cd apps/api && uv run uvicorn lemon_ledger.main:app --reload --host 0.0.0.0 --port 8000

# Apply all pending Alembic migrations
migrate:
    cd apps/api && uv run alembic upgrade head

# Generate a new Alembic migration  (usage: just makemigration initial_schema)
makemigration name="":
    cd apps/api && uv run alembic revision --autogenerate -m "{{name}}"

# Run the test suite with coverage gate (>=80%)
api-test:
    cd apps/api && uv run pytest tests/ -v

# Run ruff linter + formatter check
api-lint:
    cd apps/api && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/

# Run mypy type-checker in strict mode
api-typecheck:
    cd apps/api && uv run mypy src/ tests/

# Run Bandit + Semgrep security scans
api-security:
    cd apps/api && uv run bandit -c pyproject.toml -r src/lemon_ledger
    uvx semgrep --config tools/semgrep/no-transaction-sending.yml --error apps/

# Start Celery worker (reads broker/backend from .env)
api-worker:
    cd apps/api && uv run celery -A lemon_ledger.worker worker -l info

# Start Celery beat scheduler
api-beat:
    cd apps/api && uv run celery -A lemon_ledger.worker beat -l info

# Run pre-commit hooks against all files
precommit:
    pre-commit run --all-files
