"""Tests for deflationary burn detection in common_transfer_events."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

from classify.helpers import BLOCK, OCCURRED_AT, WALLET_ADDR, make_mock_ctx
from lemon_ledger.classify.orchestrator import common_transfer_events
from lemon_ledger.classify.types import ClaimSet, TxBundle
from lemon_ledger.domain.chains import Chain
from lemon_ledger.models.enums import ClassificationKind

ZERO_ADDR = "0x" + "0" * 40
BURN_ADDR = "0x000000000000000000000000000000000000dead"
ERC20_ADDR = "0x" + "c" * 40


def _make_erc20_out(*, to_addr: str) -> MagicMock:
    t = MagicMock()
    t.contract_address = ERC20_ADDR
    t.log_index = 0
    t.value = Decimal(10**18)
    t.raw = {
        "from": WALLET_ADDR,
        "to": to_addr,
        "value": str(10**18),
        "tokenDecimal": "18",
    }
    return t


def _bundle(transfer: MagicMock) -> TxBundle:
    return TxBundle(
        wallet_id=uuid.uuid4(),
        chain=Chain.LEMONCHAIN,
        tx_hash="0xburn",
        block_number=BLOCK,
        occurred_at=OCCURRED_AT,
        envelope=None,
        transfers=[transfer],
        internals=[],
    )


def test_burn_detection_confirmed_burn_address() -> None:
    """Outflow to confirmed burn address for a deflationary token → BURN."""
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    ctx.is_confirmed_burn_address.return_value = True

    transfer = _make_erc20_out(to_addr=BURN_ADDR)
    bundle = _bundle(transfer)

    with patch(
        "lemon_ledger.classify.orchestrator._is_deflationary_in_config",
        return_value=True,
    ):
        events = common_transfer_events(bundle, ctx, ClaimSet())

    assert len(events) == 1
    assert events[0].classification == ClassificationKind.BURN


def test_no_burn_when_not_deflationary() -> None:
    """Outflow to confirmed burn address but token is NOT deflationary → TRANSFER_OUT."""
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    ctx.is_confirmed_burn_address.return_value = True

    transfer = _make_erc20_out(to_addr=BURN_ADDR)
    bundle = _bundle(transfer)

    with patch(
        "lemon_ledger.classify.orchestrator._is_deflationary_in_config",
        return_value=False,  # NOT deflationary
    ):
        events = common_transfer_events(bundle, ctx, ClaimSet())

    assert len(events) == 1
    assert events[0].classification == ClassificationKind.TRANSFER_OUT


def test_no_burn_when_address_not_confirmed() -> None:
    """Outflow to non-confirmed address for deflationary token → TRANSFER_OUT."""
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    ctx.is_confirmed_burn_address.return_value = False  # discovered, not confirmed

    transfer = _make_erc20_out(to_addr="0x" + "5" * 40)
    bundle = _bundle(transfer)

    with patch(
        "lemon_ledger.classify.orchestrator._is_deflationary_in_config",
        return_value=True,
    ):
        events = common_transfer_events(bundle, ctx, ClaimSet())

    assert len(events) == 1
    assert events[0].classification == ClassificationKind.TRANSFER_OUT


def test_nft_outflow_to_burn_addr_is_not_burn() -> None:
    """ERC-721 outflow to burn address is TRANSFER_OUT, not BURN (burn is ERC-20 only)."""
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    ctx.is_confirmed_burn_address.return_value = True

    nft_t = MagicMock()
    nft_t.contract_address = ERC20_ADDR
    nft_t.log_index = 0
    nft_t.value = Decimal(0)
    nft_t.raw = {
        "from": WALLET_ADDR,
        "to": BURN_ADDR,
        "value": "0",
        "tokenID": "99",  # marks it as NFT
        "tokenDecimal": "0",
    }
    bundle = _bundle(nft_t)

    with patch(
        "lemon_ledger.classify.orchestrator._is_deflationary_in_config",
        return_value=True,
    ):
        events = common_transfer_events(bundle, ctx, ClaimSet())

    assert len(events) == 1
    assert events[0].classification == ClassificationKind.TRANSFER_OUT
