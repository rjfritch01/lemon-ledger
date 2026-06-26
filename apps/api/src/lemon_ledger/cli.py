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
forms_app = typer.Typer(no_args_is_help=True)
app.add_typer(wallet_app, name="wallet")
app.add_typer(forms_app, name="forms")

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


@app.command("classify")
def classify_cmd(
    wallet: Annotated[str, typer.Option("--wallet", help="Wallet address (0x...)")],
    chain: Annotated[str, typer.Option("--chain", help="Chain identifier")] = "lemonchain",
    from_block: Annotated[
        int | None,
        typer.Option("--from-block", help="Reset last_classified_block cursor to N-1"),
    ] = None,
) -> None:
    """Run the classifier for a wallet's settled block range.

    --from-block N resets the cursor to N-1, triggering reclassification of
    blocks [N, last_synced_block].  Use this after backfill writes historical
    prices to upgrade cold-start transfer-in rows to mint/reward.
    """
    from lemon_ledger.classify.tasks import classify_wallet_task

    address = wallet.lower()
    maker = _get_sessionmaker()

    with worker_session(maker) as session:
        db_wallet = session.scalars(
            select(Wallet).where(Wallet.chain == chain, Wallet.address == address)
        ).first()
        if db_wallet is None:
            typer.echo(f"Wallet {address!r} on {chain!r} not found.", err=True)
            raise typer.Exit(1)
        wallet_id = str(db_wallet.id)
        if from_block is not None:
            db_wallet.last_classified_block = from_block - 1
            session.commit()

    result = classify_wallet_task.apply(args=[wallet_id]).get()
    typer.echo(json.dumps(result, indent=2))


# ── forms ─────────────────────────────────────────────────────────────────────


@forms_app.command("generate-8949")
def generate_8949(
    year: Annotated[int, typer.Option("--year", help="Tax year (e.g. 2025)")],
    entity: Annotated[str, typer.Option("--entity", help="Entity UUID")],
    user: Annotated[str, typer.Option("--user", help="User UUID (for audit context)")],
    draft: Annotated[
        bool,
        typer.Option("--draft/--no-draft", help="Proceed through gate with DRAFT watermark"),
    ] = False,
    out: Annotated[
        str,
        typer.Option("--out", help="Output directory"),
    ] = ".",
    recompute: Annotated[
        bool,
        typer.Option(
            "--recompute/--no-recompute",
            help="Apply pending classified events before generating",
        ),
    ] = False,
) -> None:
    """Generate Form 8949, Schedule D, and Schedule 1 Line 8z for a tax year.

    Exit codes: 0 = success or draft, 2 = gate held (unresolved events), 1 = error.
    """
    from pathlib import Path

    from lemon_ledger.domain.forms.form_8949 import build_8949
    from lemon_ledger.domain.forms.gate import check_gate, get_entity_wallet_ids, recompute_lots
    from lemon_ledger.domain.forms.read_model import fetch_disposal_rows, fetch_reward_income
    from lemon_ledger.domain.forms.render.pdf_8949 import render_form_8949
    from lemon_ledger.domain.forms.render.pdf_schedule_1 import render_schedule_1
    from lemon_ledger.domain.forms.render.pdf_schedule_d import render_schedule_d
    from lemon_ledger.domain.forms.schedule_1 import build_schedule_1
    from lemon_ledger.domain.forms.schedule_d import build_schedule_d

    try:
        entity_id = uuid.UUID(entity)
    except ValueError as exc:
        typer.echo(f"Invalid entity UUID: {entity!r}", err=True)
        raise typer.Exit(1) from exc

    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    maker = _get_sessionmaker()

    with worker_session(maker) as session:
        if recompute:
            wallet_ids = get_entity_wallet_ids(session, entity_id)
            n = recompute_lots(session, wallet_ids, year)
            typer.echo(f"recompute: applied {n} events across {len(wallet_ids)} wallets")
            session.commit()

        gate = check_gate(session, entity_id, year)

        if gate.is_held and not draft:
            typer.echo(
                f"Gate held — {len(gate.blocker_rows)} blocking event(s). "
                "Resolve them or pass --draft to proceed with a watermark.",
                err=True,
            )
            typer.echo(json.dumps(gate.blocker_rows, indent=2), err=True)
            raise typer.Exit(2)

        is_draft = gate.is_held and draft
        if is_draft:
            typer.echo(
                f"DRAFT mode: {len(gate.blocker_rows)} blocking event(s) unresolved. "
                "Generating with DRAFT watermark.",
                err=True,
            )

        disposal_rows = fetch_disposal_rows(session, entity_id, year)
        reward_income = fetch_reward_income(session, entity_id, year)

    form_8949 = build_8949(disposal_rows, entity_id, year, is_draft=is_draft)
    sched_d = build_schedule_d(form_8949)
    sched_1 = build_schedule_1(reward_income, is_draft=is_draft)

    prefix = f"form8949_{year}_{entity_id}"
    p8949 = render_form_8949(form_8949, out_dir / f"{prefix}.pdf")
    pscheDD = render_schedule_d(sched_d, out_dir / f"{prefix}_schedule_d.pdf")
    pSched1 = render_schedule_1(sched_1, out_dir / f"{prefix}_schedule_1.pdf")

    manifest = {
        "tax_year": year,
        "entity_id": str(entity_id),
        "is_draft": is_draft,
        "disposals": len(disposal_rows),
        "form_8949": {
            box: {
                "rows": len(sub.rows),
                "proceeds": str(sub.total_proceeds),
                "basis": str(sub.total_basis),
                "adjustment": str(sub.total_adjustment),
                "gain_loss_net": str(sub.total_gain_loss_net),
            }
            for box, sub in form_8949.boxes.items()
            if sub.rows
        },
        "schedule_d": {
            "short_term_net": str(sched_d.short_term_net),
            "long_term_net": str(sched_d.long_term_net),
            "total_net": str(sched_d.total_net),
        },
        "schedule_1_line_8z": str(sched_1.line_8z_income),
        "files": [str(p8949), str(pscheDD), str(pSched1)],
    }
    typer.echo(json.dumps(manifest, indent=2))


@forms_app.command("activity-report")
def activity_report_cmd(
    year: Annotated[int, typer.Option("--year", help="Tax year (e.g. 2025)")],
    entity: Annotated[str, typer.Option("--entity", help="Entity UUID")],
    user: Annotated[str, typer.Option("--user", help="User UUID (for audit context)")],
    fmt: Annotated[
        str,
        typer.Option("--format", help="Output format: csv | pdf | both"),
    ] = "both",
    out: Annotated[str, typer.Option("--out", help="Output directory")] = ".",
) -> None:
    """Generate the informational activity / gain-loss report for a tax year.

    Exit codes: 0 = success, 1 = error.
    """
    from pathlib import Path

    from lemon_ledger.domain.forms.activity_report import build_activity_report, to_csv
    from lemon_ledger.domain.forms.gate import check_gate
    from lemon_ledger.domain.forms.read_model import (
        fetch_acquisition_rows,
        fetch_disposal_rows,
        fetch_reward_income,
    )
    from lemon_ledger.domain.forms.render.pdf_activity import render_activity_report

    if fmt not in ("csv", "pdf", "both"):
        typer.echo(f"Invalid --format {fmt!r}. Choose csv, pdf, or both.", err=True)
        raise typer.Exit(1)

    try:
        entity_id = uuid.UUID(entity)
    except ValueError as exc:
        typer.echo(f"Invalid entity UUID: {entity!r}", err=True)
        raise typer.Exit(1) from exc

    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    maker = _get_sessionmaker()

    with worker_session(maker) as session:
        gate = check_gate(session, entity_id, year)
        is_draft = gate.is_held

        acquisitions = fetch_acquisition_rows(session, entity_id, year)
        disposals = fetch_disposal_rows(session, entity_id, year)
        reward = fetch_reward_income(session, entity_id, year)

    report = build_activity_report(
        acquisitions, disposals, reward, entity_id, year, is_draft=is_draft
    )
    prefix = f"activity_report_{year}_{entity_id}"
    files = []

    if fmt in ("csv", "both"):
        csv_path = out_dir / f"{prefix}.csv"
        csv_path.write_text(to_csv(report), encoding="utf-8")
        files.append(str(csv_path))

    if fmt in ("pdf", "both"):
        pdf_path = out_dir / f"{prefix}.pdf"
        render_activity_report(report, pdf_path)
        files.append(str(pdf_path))

    manifest = {
        "tax_year": year,
        "entity_id": str(entity_id),
        "is_draft": is_draft,
        "acquisitions": len(acquisitions),
        "disposals": len(disposals),
        "total_proceeds": str(report.total_proceeds),
        "total_gain_loss": str(report.total_gain_loss),
        "total_reward_income": str(report.total_reward_income),
        "files": files,
    }
    typer.echo(json.dumps(manifest, indent=2))


@forms_app.command("reconcile")
def reconcile_cmd(
    year: Annotated[int, typer.Option("--year", help="Tax year (e.g. 2025)")],
    entity: Annotated[str, typer.Option("--entity", help="Entity UUID")],
    user: Annotated[str, typer.Option("--user", help="User UUID (for audit context)")],
    expected: Annotated[
        str,
        typer.Option("--expected", help="Built-in fixture ID (S1-S8) or path to JSON fixture"),
    ],
    draft: Annotated[
        bool,
        typer.Option("--draft/--no-draft", help="Allow running through held gate with DRAFT"),
    ] = False,
    waivers: Annotated[
        bool,
        typer.Option(
            "--waivers/--no-waivers",
            help="Treat per-figure delta > $5 as warnings only (not failures)",
        ),
    ] = False,
) -> None:
    """Reconcile actual form figures against a fixture's expected values.

    Exit codes: 0 = PASS, 2 = FAIL (gate held or figure mismatch), 1 = error.
    """
    from lemon_ledger.domain.forms.reconcile import ReconcileResult, load_fixture, run_reconcile

    try:
        entity_id = uuid.UUID(entity)
    except ValueError as exc:
        typer.echo(f"Invalid entity UUID: {entity!r}", err=True)
        raise typer.Exit(1) from exc

    try:
        fixture = load_fixture(expected)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    maker = _get_sessionmaker()

    with worker_session(maker) as session:
        result: ReconcileResult = run_reconcile(session, entity_id, year, fixture, is_draft=draft)

    for line in result.summary_lines():
        typer.echo(line)

    if not result.passed and not waivers:
        raise typer.Exit(2)
    if waivers and not result.gate_verdict:
        raise typer.Exit(2)
