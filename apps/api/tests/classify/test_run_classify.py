"""Tests for classify/tasks.py _run_classify and _classify_chunk (no Docker)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from classify.helpers import (
    BLOCK,
    OCCURRED_AT,
    WALLET_ADDR,
    make_transfer,
)
from lemon_ledger.classify.tasks import _classify_chunk, _run_classify
from lemon_ledger.classify.types import TxBundle


def _wallet(
    wid: uuid.UUID,
    *,
    last_classified_block: int | None = None,
    last_synced_block: int = 100,
    chain: str = "lemonchain",
    is_active: bool = True,
) -> MagicMock:
    w = MagicMock()
    w.id = wid
    w.address = WALLET_ADDR
    w.chain = chain
    w.is_active = is_active
    w.last_classified_block = last_classified_block
    w.last_synced_block = last_synced_block
    w.user_id = uuid.uuid4()
    return w


def _session_for(wallet: MagicMock, user_addrs: list[str] | None = None) -> MagicMock:
    sess = MagicMock()
    sess.get.return_value = wallet

    # scalars().all() for user wallets
    user_addrs_result = user_addrs or [WALLET_ADDR]
    sess.scalars.return_value.all.return_value = user_addrs_result
    return sess


# ── _run_classify ──────────────────────────────────────────────────────────────


def test_run_classify_nothing_to_do() -> None:
    """When last_classified_block >= last_synced_block, return early."""
    wid = uuid.uuid4()
    wallet = _wallet(wid, last_classified_block=100, last_synced_block=100)
    sess = _session_for(wallet)
    pricing = MagicMock()

    result = _run_classify(str(wid), pricing, sess, MagicMock())
    assert result["classified"] == 0
    assert result["msg"] == "nothing_to_classify"


def test_run_classify_inactive_wallet_raises() -> None:
    wid = uuid.uuid4()
    wallet = _wallet(wid, is_active=False)
    sess = _session_for(wallet)

    with pytest.raises(ValueError, match="not found or inactive"):
        _run_classify(str(wid), MagicMock(), sess, MagicMock())


def test_run_classify_not_found_raises() -> None:
    wid = uuid.uuid4()
    sess = MagicMock()
    sess.get.return_value = None  # wallet not found

    with pytest.raises(ValueError, match="not found or inactive"):
        _run_classify(str(wid), MagicMock(), sess, MagicMock())


def test_run_classify_advances_cursor() -> None:
    """Cursor advances to last_synced_block and commit is called once per chunk."""
    wid = uuid.uuid4()
    wallet = _wallet(wid, last_classified_block=0, last_synced_block=5)
    sess = _session_for(wallet, [WALLET_ADDR])

    # Each scalars().all() for chunk queries returns empty lists
    sess.scalars.return_value.all.side_effect = [
        [WALLET_ADDR],  # user wallets query
        [],  # RawTransaction
        [],  # RawTokenTransfer
        [],  # RawInternalTx
    ]

    pricing = MagicMock()

    with patch("lemon_ledger.classify.tasks.WalletContext") as MockCtx:
        ctx_inst = MagicMock()
        ctx_inst.decoders_for_bundle.return_value = []
        MockCtx.return_value = ctx_inst

        result = _run_classify(str(wid), pricing, sess, MagicMock())

    assert result["from_block"] == 1
    assert result["to_block"] == 5
    assert wallet.last_classified_block == 5
    sess.commit.assert_called()


# ── _classify_chunk ────────────────────────────────────────────────────────────


def test_classify_chunk_empty_returns_zero() -> None:
    wid = uuid.uuid4()
    wallet = _wallet(wid)
    sess = MagicMock()
    # All three queries return empty
    sess.scalars.return_value.all.return_value = []

    ctx = MagicMock()
    ctx.decoders_for_bundle.return_value = []

    count = _classify_chunk(wid, wallet, ctx, sess, 0, 100)
    assert count == 0


def test_classify_chunk_processes_transfer_only_tx() -> None:
    """A tx with only a token transfer (no RawTransaction envelope) is processed."""
    wid = uuid.uuid4()
    wallet = _wallet(wid)

    transfer = make_transfer(
        log_index=0,
        from_addr="0x" + "1" * 40,
        to_addr=WALLET_ADDR,
        wallet_id=wid,
        tx_hash="0xabc",
    )
    transfer.block_number = 10
    transfer.occurred_at = OCCURRED_AT

    sess = MagicMock()
    # txs = [], transfers = [transfer], internals = []
    sess.scalars.return_value.all.side_effect = [
        [],  # RawTransaction
        [transfer],  # RawTokenTransfer
        [],  # RawInternalTx
    ]

    ctx = MagicMock()
    ctx.decoders_for_bundle.return_value = []
    ctx.wallet_address = WALLET_ADDR

    with (
        patch("lemon_ledger.classify.tasks.classify_bundle", return_value=[MagicMock()]) as mock_cb,
        patch("lemon_ledger.classify.tasks.replace_classified") as mock_rc,
    ):
        count = _classify_chunk(wid, wallet, ctx, sess, 0, 100)

    assert count == 1
    mock_cb.assert_called_once()
    mock_rc.assert_called_once()


# ── orchestrator envelope/internal paths ───────────────────────────────────────


def test_common_native_transfer_in_via_envelope() -> None:
    """Native value in the envelope (to=wallet) yields TRANSFER_IN."""
    from classify.helpers import make_mock_ctx
    from lemon_ledger.classify.orchestrator import common_transfer_events
    from lemon_ledger.classify.types import ClaimSet
    from lemon_ledger.domain.chains import Chain
    from lemon_ledger.models.enums import ClassificationKind

    wid = uuid.uuid4()
    ctx = make_mock_ctx(wid)

    envelope = MagicMock()
    envelope.raw = {
        "from": "0x" + "f" * 40,
        "to": WALLET_ADDR,
        "value": str(1 * 10**18),
    }

    bundle = TxBundle(
        wallet_id=wid,
        chain=Chain.LEMONCHAIN,
        tx_hash="0xnative",
        block_number=BLOCK,
        occurred_at=OCCURRED_AT,
        envelope=envelope,
        transfers=[],
        internals=[],
    )
    events = common_transfer_events(bundle, ctx, ClaimSet())
    assert any(e.classification == ClassificationKind.TRANSFER_IN for e in events)
    native_in = next(e for e in events if e.classification == ClassificationKind.TRANSFER_IN)
    assert native_in.amount == Decimal("1")


def test_common_native_transfer_out_via_envelope() -> None:
    """Native value out from wallet via envelope yields TRANSFER_OUT."""
    from classify.helpers import make_mock_ctx
    from lemon_ledger.classify.orchestrator import common_transfer_events
    from lemon_ledger.classify.types import ClaimSet
    from lemon_ledger.domain.chains import Chain
    from lemon_ledger.models.enums import ClassificationKind

    wid = uuid.uuid4()
    ctx = make_mock_ctx(wid)

    envelope = MagicMock()
    envelope.raw = {
        "from": WALLET_ADDR,
        "to": "0x" + "2" * 40,
        "value": str(2 * 10**18),
    }

    bundle = TxBundle(
        wallet_id=wid,
        chain=Chain.LEMONCHAIN,
        tx_hash="0xnative2",
        block_number=BLOCK,
        occurred_at=OCCURRED_AT,
        envelope=envelope,
        transfers=[],
        internals=[],
    )
    events = common_transfer_events(bundle, ctx, ClaimSet())
    out_events = [e for e in events if e.classification == ClassificationKind.TRANSFER_OUT]
    assert len(out_events) == 1
    assert out_events[0].amount == Decimal("2")


def test_common_native_transfer_via_internal_tx() -> None:
    """Native value in internal tx (to=wallet) yields TRANSFER_IN."""
    from classify.helpers import make_mock_ctx
    from lemon_ledger.classify.orchestrator import common_transfer_events
    from lemon_ledger.classify.types import ClaimSet
    from lemon_ledger.domain.chains import Chain
    from lemon_ledger.models.enums import ClassificationKind

    wid = uuid.uuid4()
    ctx = make_mock_ctx(wid)

    itx = MagicMock()
    itx.raw = {
        "from": "0x" + "a" * 40,
        "to": WALLET_ADDR,
        "value": str(5 * 10**17),  # 0.5 native
    }

    bundle = TxBundle(
        wallet_id=wid,
        chain=Chain.LEMONCHAIN,
        tx_hash="0xinternal",
        block_number=BLOCK,
        occurred_at=OCCURRED_AT,
        envelope=None,
        transfers=[],
        internals=[itx],
    )
    events = common_transfer_events(bundle, ctx, ClaimSet())
    in_events = [e for e in events if e.classification == ClassificationKind.TRANSFER_IN]
    assert len(in_events) == 1
    assert in_events[0].amount == Decimal("0.5")
