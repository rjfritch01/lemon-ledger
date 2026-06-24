"""Tests for the classify orchestrator: common_transfer_events, replace_classified."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

from classify.helpers import (
    ERC20_ADDR,
    WALLET_ADDR,
    ZERO_ADDR,
    make_bundle,
    make_mock_ctx,
    make_nft_transfer,
    make_transfer,
)
from lemon_ledger.classify.orchestrator import (
    _assign_event_seq,
    common_transfer_events,
    replace_classified,
)
from lemon_ledger.classify.types import ClaimSet, ClassifiedEvent
from lemon_ledger.models.enums import ClassificationKind

# ── common_transfer_events ─────────────────────────────────────────────────────


def test_common_erc20_transfer_in() -> None:
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    transfer = make_transfer(
        log_index=0,
        from_addr="0x1234000000000000000000000000000000000000",
        to_addr=WALLET_ADDR,
        value=5_000_000_000_000_000_000,
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = common_transfer_events(bundle, ctx, claims)
    assert len(events) == 1
    assert events[0].classification == ClassificationKind.TRANSFER_IN
    assert events[0].amount == Decimal(5)


def test_common_erc20_transfer_out() -> None:
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    transfer = make_transfer(
        log_index=1,
        from_addr=WALLET_ADDR,
        to_addr="0x9999000000000000000000000000000000000000",
        value=2_000_000_000_000_000_000,
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = common_transfer_events(bundle, ctx, claims)
    assert len(events) == 1
    assert events[0].classification == ClassificationKind.TRANSFER_OUT


def test_common_skips_claimed_transfers() -> None:
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    transfer = make_transfer(
        log_index=2,
        from_addr="0xaaaa000000000000000000000000000000000001",
        to_addr=WALLET_ADDR,
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()
    claims.add(transfer)

    events = common_transfer_events(bundle, ctx, claims)
    assert len(events) == 0


def test_common_nft_cold_start_transfer_in() -> None:
    """Unclaimed ERC-721 from zero address → transfer-in (NOT mint), cold start."""
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    transfer = make_nft_transfer(log_index=3, from_addr=ZERO_ADDR, to_addr=WALLET_ADDR)
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = common_transfer_events(bundle, ctx, claims)
    assert len(events) == 1
    assert events[0].classification == ClassificationKind.TRANSFER_IN
    assert events[0].amount == Decimal(1)


def test_common_neither_direction_skipped() -> None:
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    transfer = make_transfer(
        log_index=4,
        from_addr="0x1111000000000000000000000000000000000000",
        to_addr="0x2222000000000000000000000000000000000000",
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = common_transfer_events(bundle, ctx, claims)
    assert len(events) == 0


# ── _assign_event_seq ──────────────────────────────────────────────────────────


def test_assign_event_seq_deterministic_order() -> None:
    """Mint (priority 0) appears before reward (priority 3) at same log_index."""
    wallet_id = uuid.uuid4()
    bundle = make_bundle(wallet_id)
    events = [
        ClassifiedEvent(
            classification=ClassificationKind.REWARD,
            contract_address=ERC20_ADDR,
            token_id=None,
            amount=Decimal(1),
            value_usd_at_event=None,
            _order_hint=5,
        ),
        ClassifiedEvent(
            classification=ClassificationKind.MINT,
            contract_address=ERC20_ADDR,
            token_id=None,
            amount=Decimal(1),
            value_usd_at_event=None,
            _order_hint=5,
        ),
    ]
    rows = _assign_event_seq(bundle, events)
    assert rows[0].classification == ClassificationKind.MINT
    assert rows[1].classification == ClassificationKind.REWARD
    assert rows[0].event_seq == 0
    assert rows[1].event_seq == 1


# ── replace_classified ─────────────────────────────────────────────────────────


def test_replace_classified_skips_frozen_tx() -> None:
    """If any manual_override row exists, replace_classified is a no-op."""
    session = MagicMock()
    session.scalar.return_value = 1  # pinned_count > 0

    replace_classified(session, uuid.uuid4(), "0xhash", [])

    session.execute.assert_not_called()
    session.add_all.assert_not_called()


def test_replace_classified_deletes_and_readds() -> None:
    """With no pinned rows, delete existing and add the new set."""
    session = MagicMock()
    session.scalar.return_value = 0  # no pins

    events = [MagicMock()]
    replace_classified(session, uuid.uuid4(), "0xhash", events)

    session.execute.assert_called_once()
    session.add_all.assert_called_once_with(events)
