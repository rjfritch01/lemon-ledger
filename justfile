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
