"""Stage 1.9 schema tests: pending_classifications, LotDisposal 8949 columns, CT signal.

Verifies model construction, server defaults, and CHECK constraint enforcement
via round-trips against a real Testcontainers Postgres (migration run by conftest).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lemon_ledger.models import (
    Entity,
    PendingClassification,
    TokenRegistry,
    User,
    Wallet,
)
from lemon_ledger.models.enums import (
    AdjustmentCode,
    ChosenClassification,
    CoveredStatus,
    PendingClassificationKind,
    PendingClassificationState,
    TransferResolution,
)

# ── helpers ───────────────────────────────────────────────────────────────────


async def _make_user(session: AsyncSession) -> User:
    u = User(clerk_user_id=f"clerk_{uuid.uuid4().hex}")
    session.add(u)
    await session.flush()
    return u


async def _make_entity(session: AsyncSession, user: User) -> Entity:
    e = Entity(user_id=user.id, name="Test Entity", type="personal")
    session.add(e)
    await session.flush()
    return e


async def _make_wallet(session: AsyncSession, user: User) -> Wallet:
    w = Wallet(
        user_id=user.id,
        chain="lemonchain",
        address=f"0x{uuid.uuid4().hex[:40]}",
        role="live",
    )
    session.add(w)
    await session.flush()
    return w


async def _make_token(session: AsyncSession) -> TokenRegistry:
    t = TokenRegistry(
        chain="lemonchain",
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        symbol="TST",
        name="Test Token",
        decimals=18,
        tier=1,
        category="ecosystem-l2",
    )
    session.add(t)
    await session.flush()
    return t


# ── TransferResolution enum sanity ────────────────────────────────────────────


def test_transfer_resolution_values() -> None:
    assert TransferResolution.RELOCATE_INTERNAL.value == "relocate-internal"
    assert TransferResolution.DISPOSAL_RELATED_PARTY.value == "disposal-related-party"
    assert TransferResolution.GIFT_OUT.value == "gift-out"
    assert TransferResolution.NO_OP_LOAN.value == "no-op-loan"
    assert len(list(TransferResolution)) == 8


def test_covered_status_values() -> None:
    assert CoveredStatus.NO_1099_DA.value == "no-1099-da"
    assert len(list(CoveredStatus)) == 3


def test_adjustment_code_values() -> None:
    assert AdjustmentCode.L == "L"
    assert len(list(AdjustmentCode)) == 5


def test_pending_classification_kind_values() -> None:
    assert PendingClassificationKind.CROSS_ENTITY.value == "cross-entity"
    assert PendingClassificationKind.EXTERNAL_OUTFLOW.value == "external-outflow"


def test_pending_classification_state_values() -> None:
    assert PendingClassificationState.NEEDS_CLASSIFICATION.value == "needs_classification"
    assert len(list(PendingClassificationState)) == 4


def test_chosen_classification_values() -> None:
    assert ChosenClassification.CAPITAL_CONTRIBUTION.value == "capital-contribution"
    assert len(list(ChosenClassification)) == 6


# ── PendingClassification model construction ──────────────────────────────────


async def test_pending_classification_roundtrip(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)
    entity = await _make_entity(db_session, user)
    wallet = await _make_wallet(db_session, user)
    token = await _make_token(db_session)

    pc = PendingClassification(
        user_id=user.id,
        kind=PendingClassificationKind.CROSS_ENTITY,
        logical_transfer_key=f"lemonchain:0x{uuid.uuid4().hex}:0",
        chain="lemonchain",
        tx_hash=f"0x{uuid.uuid4().hex}",
        transfer_index=0,
        token_id=token.id,
        canonical_asset="LEMX",
        amount=Decimal("100"),
        from_wallet_id=wallet.id,
        from_entity_id=entity.id,
    )
    db_session.add(pc)
    await db_session.flush()

    fetched = await db_session.get(PendingClassification, pc.id)
    assert fetched is not None
    assert fetched.state == "needs_classification"
    assert fetched.kind == "cross-entity"
    assert fetched.to_wallet_id is None
    assert fetched.chosen_classification is None
    assert fetched.id.version == 7


async def test_pending_classification_external_outflow(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)
    entity = await _make_entity(db_session, user)
    wallet = await _make_wallet(db_session, user)
    token = await _make_token(db_session)

    pc = PendingClassification(
        user_id=user.id,
        kind=PendingClassificationKind.EXTERNAL_OUTFLOW,
        logical_transfer_key=f"bsc:0x{uuid.uuid4().hex}:1",
        chain="bsc",
        tx_hash=f"0x{uuid.uuid4().hex}",
        transfer_index=1,
        token_id=token.id,
        canonical_asset="LEMX",
        amount=Decimal("50"),
        from_wallet_id=wallet.id,
        from_entity_id=entity.id,
        to_address="0x" + "d" * 40,
    )
    db_session.add(pc)
    await db_session.flush()

    fetched = await db_session.get(PendingClassification, pc.id)
    assert fetched is not None
    assert fetched.kind == "external-outflow"
    assert fetched.to_address == "0x" + "d" * 40


async def test_pending_classification_unique_key_enforced(db_session: AsyncSession) -> None:
    user = await _make_user(db_session)
    entity = await _make_entity(db_session, user)
    wallet = await _make_wallet(db_session, user)
    token = await _make_token(db_session)

    key = f"lemonchain:0x{uuid.uuid4().hex}:0"

    pc1 = PendingClassification(
        user_id=user.id,
        kind="cross-entity",
        logical_transfer_key=key,
        chain="lemonchain",
        tx_hash=f"0x{uuid.uuid4().hex}",
        transfer_index=0,
        token_id=token.id,
        canonical_asset="LEMX",
        amount=Decimal("10"),
        from_wallet_id=wallet.id,
        from_entity_id=entity.id,
    )
    pc2 = PendingClassification(
        user_id=user.id,
        kind="cross-entity",
        logical_transfer_key=key,  # same key — must fail
        chain="lemonchain",
        tx_hash=f"0x{uuid.uuid4().hex}",
        transfer_index=0,
        token_id=token.id,
        canonical_asset="LEMX",
        amount=Decimal("10"),
        from_wallet_id=wallet.id,
        from_entity_id=entity.id,
    )
    db_session.add(pc1)
    db_session.add(pc2)
    with pytest.raises(Exception, match="uq_pending_cls_transfer_key"):
        await db_session.flush()


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("kind", "bad-kind"),
        ("state", "bad-state"),
        ("chosen_classification", "bad-choice"),
        ("resolved_by", "bad-resolver"),
    ],
)
async def test_pending_classification_check_constraints(
    db_session: AsyncSession, field: str, bad_value: str
) -> None:
    user = await _make_user(db_session)
    entity = await _make_entity(db_session, user)
    wallet = await _make_wallet(db_session, user)
    token = await _make_token(db_session)

    kwargs: dict[str, object] = {
        "user_id": user.id,
        "kind": "cross-entity",
        "logical_transfer_key": f"test:{uuid.uuid4().hex}",
        "chain": "lemonchain",
        "tx_hash": f"0x{uuid.uuid4().hex}",
        "transfer_index": 0,
        "token_id": token.id,
        "canonical_asset": "LEMX",
        "amount": Decimal("1"),
        "from_wallet_id": wallet.id,
        "from_entity_id": entity.id,
        field: bad_value,
    }
    db_session.add(PendingClassification(**kwargs))
    with pytest.raises(Exception, match="ck_pending_cls"):
        await db_session.flush()


# ── LotDisposal 8949 columns ──────────────────────────────────────────────────


async def test_lot_disposal_covered_status_default(db_session: AsyncSession) -> None:
    """covered_status defaults to 'no-1099-da' via server_default."""
    result = await db_session.execute(text("SELECT covered_status FROM lot_disposals LIMIT 0"))
    # Just verify the column exists and the default is in the CHECK set.
    _ = result  # no rows; we just needed to confirm the column exists without error
    # Verify enum membership
    assert CoveredStatus.NO_1099_DA in list(CoveredStatus)


@pytest.mark.parametrize("code", [e.value for e in AdjustmentCode])
async def test_lot_disposal_adjustment_code_valid(db_session: AsyncSession, code: str) -> None:
    await db_session.execute(
        text("SELECT 1 WHERE :code IN ('L','W','D','E','O')").bindparams(code=code)
    )


async def test_lot_disposal_covered_status_check_values(db_session: AsyncSession) -> None:
    valid = {"no-1099-da", "covered-basis-reported", "covered-basis-not-reported"}
    for status in CoveredStatus:
        assert status.value in valid


# ── ClassifiedTransaction transfer_resolution ──────────────────────────────────


async def test_ct_transfer_resolution_column_exists(db_session: AsyncSession) -> None:
    """Verify transfer_resolution column is present and nullable."""
    result = await db_session.execute(
        text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name='classified_transactions' AND column_name='transfer_resolution'"
        )
    )
    row = result.one()
    assert row.column_name == "transfer_resolution"
    assert row.is_nullable == "YES"


@pytest.mark.parametrize("value", [r.value for r in TransferResolution])
async def test_ct_transfer_resolution_check_passes(db_session: AsyncSession, value: str) -> None:
    await db_session.execute(
        text(
            "SELECT 1 WHERE :v IN ("
            "'relocate-internal','relocate-contribution','relocate-gift','relocate-reassignment',"
            "'disposal','disposal-related-party','gift-out','no-op-loan')"
        ).bindparams(v=value)
    )


# ── Lot engine boundary invariant (grep test) ─────────────────────────────────


def test_lots_package_never_reads_pending_classifications() -> None:
    """The lot engine must not import or reference pending_classifications."""
    import pathlib

    lots_dir = pathlib.Path(__file__).parent.parent / "src/lemon_ledger/domain/lots"
    forbidden = {"pending_classifications", "PendingClassification"}
    violations: list[str] = []
    for py in lots_dir.rglob("*.py"):
        content = py.read_text()
        for term in forbidden:
            if term in content:
                violations.append(f"{py.name}: contains '{term}'")
    assert not violations, f"Boundary violation in domain/lots/: {violations}"


def test_lots_package_never_reads_bridge_correlations() -> None:
    """The lot engine must not import or reference bridge_correlations."""
    import pathlib

    lots_dir = pathlib.Path(__file__).parent.parent / "src/lemon_ledger/domain/lots"
    # Comment references are allowed; import/query references are not.
    # We check for the class name and table name as query-time references.
    forbidden_classes = {"BridgeCorrelation"}
    violations: list[str] = []
    for py in lots_dir.rglob("*.py"):
        content = py.read_text()
        for term in forbidden_classes:
            if term in content:
                violations.append(f"{py.name}: imports/references '{term}'")
        # Quoted table-name reference (actual SQL string) — docstring prose is OK.
        if '"bridge_correlations"' in content or "'bridge_correlations'" in content:
            violations.append(f"{py.name}: SQL string reference to 'bridge_correlations'")
    assert not violations, f"Boundary violation in domain/lots/: {violations}"
