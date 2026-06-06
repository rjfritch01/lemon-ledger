# Lemon Ledger

Read-only crypto portfolio & tax platform.

## Quickstart

### Prerequisites

| Tool | Min version |
|------|-------------|
| git | 2.x |
| Node | 22 LTS (see `.nvmrc`) |
| pnpm | 11.x |
| Python | 3.12 (see `.python-version`) |
| Docker + Compose | recent stable |
| just | 1.x |

### Local dev setup

```sh
# 1. Clone
git clone <repo-url>
cd lemon-ledger

# 2. Copy environment variables and fill in any values you want to change
cp .env.example .env

# 3. Start Postgres and Redis
just up

# 4. Verify services are healthy
just ps
```

**Expected output from `just ps`** — both services should show `healthy` in the Status column.

### Verify connectivity

```sh
# Postgres
docker compose exec postgres pg_isready -U lemon -d lemon_ledger

# Redis
docker compose exec redis redis-cli ping
# → PONG
```

### Useful commands

| Command | Description |
|---------|-------------|
| `just up` | Start all local services (detached) |
| `just down` | Stop all local services |
| `just logs` | Tail logs from all services |
| `just ps` | Show service status and health |
| `just doctor` | Print toolchain versions |

## Repository layout

```
lemon-ledger/
├── apps/
│   ├── api/          # Python 3.12 + FastAPI (scaffolded next)
│   └── web/          # React app (scaffolded later)
├── packages/         # Shared libraries (empty for now)
├── docs/
│   └── decisions/    # Architecture Decision Records
├── docker-compose.yml
├── justfile
├── .env.example      # Document all env vars here; never commit .env
├── .nvmrc            # Node 22 LTS
└── .python-version   # Python 3.12
```

## Secrets

- Local: copy `.env.example` → `.env` and fill in values. `.env` is gitignored.
- Production: managed by **Doppler**. Secrets are never committed.
