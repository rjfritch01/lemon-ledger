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
            id=uuid.uuid4(),
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
            id=uuid.uuid4(),
            wallet_id=w.id,
            entity_id=uuid.UUID(entity_id),
            effective_from=date.today(),
            classification="initial-assignment",
        )
        session.add(assignment)
        session.commit()

    typer.echo(json.dumps({"wallet_id": str(w.id), "address": address, "chain": chain}))
