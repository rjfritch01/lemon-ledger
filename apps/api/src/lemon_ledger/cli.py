from __future__ import annotations

import json
import re
import uuid
from datetime import date
from typing import Annotated

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lemon_ledger.config import get_settings
from lemon_ledger.db.sync_session import build_sync_engine, build_sync_sessionmaker, worker_session
from lemon_ledger.models.wallet import Wallet
from lemon_ledger.models.wallet_entity_assignment import WalletEntityAssignment

app = typer.Typer(name="lemon-ledger", no_args_is_help=True)
wallet_app = typer.Typer(no_args_is_help=True)
app.add_typer(wallet_app, name="wallet")

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _get_sessionmaker() -> sessionmaker[Session]:
    settings = get_settings()
    engine = build_sync_engine(settings)
    return build_sync_sessionmaker(engine)


@app.command()
def sync(
    wallet: Annotated[str, typer.Option("--wallet", help="Wallet address (0x...)")],
    chain: Annotated[str, typer.Option("--chain", help="Chain identifier")],
    local: Annotated[
        bool, typer.Option("--local/--remote", help="Run in-process vs via broker")
    ] = False,
    full: Annotated[bool, typer.Option("--full/--incremental", help="Reset cursor to 0")] = False,
) -> None:
    """Trigger an incremental (or full) wallet sync."""
    from lemon_ledger.tasks.sync import sync_wallet_task

    address = wallet.lower()
    settings = get_settings()
    maker = _get_sessionmaker()

    with worker_session(maker) as session:
        db_wallet = session.scalars(
            select(Wallet).where(Wallet.chain == chain, Wallet.address == address)
        ).first()
        if db_wallet is None:
            typer.echo(f"Wallet {address!r} on {chain!r} not found — add it first.", err=True)
            raise typer.Exit(1)
        wallet_id = str(db_wallet.id)

    from_block: int | None = 0 if full else None

    if local:
        result = sync_wallet_task.apply(args=[wallet_id, from_block]).get()
    else:
        task = sync_wallet_task.apply_async(args=[wallet_id, from_block])
        typer.echo(f"Task ID: {task.id}")
        result = task.get(timeout=settings.sync_cli_wait_timeout_s)

    typer.echo(json.dumps(result, indent=2))


@wallet_app.command("add")
def wallet_add(
    address: Annotated[str, typer.Option("--address", help="Wallet address (0x...)")],
    chain: Annotated[str, typer.Option("--chain")],
    user_id: Annotated[str, typer.Option("--user-id")],
    entity_id: Annotated[str, typer.Option("--entity-id")],
    name: Annotated[str | None, typer.Option("--name")] = None,
    role: Annotated[str, typer.Option("--role")] = "live",
) -> None:
    """Register a wallet (dev stopgap — superseded by Phase 2 wallet onboarding flow).

    Requires an existing user_id and entity_id. Inserts a Wallet row and the
    initial WalletEntityAssignment (classification='initial-assignment') to
    satisfy the SCD invariant.
    """
    if not _ADDR_RE.match(address):
        typer.echo(f"Invalid address {address!r} — must match ^0x[0-9a-fA-F]{{40}}$", err=True)
        raise typer.Exit(1)

    address = address.lower()
    maker = _get_sessionmaker()

    with worker_session(maker) as session:
        w = Wallet(
            user_id=uuid.UUID(user_id),
            chain=chain,
            address=address,
            name=name,
            role=role,
            added_via="cli",
        )
        session.add(w)
        session.flush()

        assignment = WalletEntityAssignment(
            wallet_id=w.id,
            entity_id=uuid.UUID(entity_id),
            effective_from=date.today(),
            classification="initial-assignment",
        )
        session.add(assignment)
        session.commit()

    typer.echo(json.dumps({"wallet_id": str(w.id), "address": address, "chain": chain}))


@app.command("backfill-prices")
def backfill_prices(
    chain: Annotated[str, typer.Option("--chain", help="Chain to backfill")] = "lemonchain",
    resume: Annotated[
        bool, typer.Option("--resume/--no-resume", help="Resume from Redis cursor")
    ] = False,
) -> None:
    """Backfill DailyAverageFinalized events from oracle genesis to chain head.

    Safe to re-run: idempotent upserts; manual-override rows are never
    overwritten.  Use --resume to continue after a crash without re-scanning
    already-processed blocks.
    """
    import httpx
    import redis as redis_lib

    from lemon_ledger.clients.blockscout import build_blockscout_client
    from lemon_ledger.clients.rate_limit import RedisTokenBucket
    from lemon_ledger.pricing.historical_backfill import (
        RedisCursor,
        _NullCursor,
        backfill,
    )

    settings = get_settings()

    oracle_contract: str | None = getattr(settings, "oracle_contract_lemonchain", None)
    if not oracle_contract:
        typer.echo(
            "ORACLE_CONTRACT_LEMONCHAIN must be set in environment to run backfill.", err=True
        )
        raise typer.Exit(1)

    r = redis_lib.Redis.from_url(settings.redis_url)
    http = httpx.Client(timeout=settings.explorer_request_timeout_s)
    limiter = RedisTokenBucket(
        r,
        key=f"ratelimit:{chain}",
        rate_per_sec=settings.explorer_rate_limit_rps,
        burst=settings.explorer_rate_limit_burst,
    )
    chain_client = build_blockscout_client(chain, settings, http=http, rate_limiter=limiter)
    cursor_store = RedisCursor(r) if resume else _NullCursor()
    maker = _get_sessionmaker()

    # Registry must be wired when token_registry table is populated
    # (this command is a thin runner; pass a real SQLAlchemy-backed repo in prod)

    class _NullRegistry:
        def get_by_id(self, token_id: str) -> None:
            return None

        def historical_price(self, chain: str, token_id: str, day: date) -> None:
            return None

        def list_tier1_by_chain(self, chain: str) -> list:  # type: ignore[type-arg]
            return []

        def id_for_address(self, chain: str, contract_address: str) -> str | None:
            return None

        def tier1_lemonchain(self) -> list:  # type: ignore[type-arg]
            return []

    typer.echo(f"Starting backfill on {chain!r} (resume={resume}) …")
    with worker_session(maker) as session:
        backfill(
            chain_client,
            oracle_contract,
            _NullRegistry(),
            session,
            cursor_store=cursor_store,
            chain=chain,
        )
    typer.echo("Backfill complete.")
