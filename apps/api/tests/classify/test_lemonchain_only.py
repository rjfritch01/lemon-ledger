"""Tests for Lemonchain-only decoders: registration, LQST mint-only, PENDING discipline."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from classify.helpers import (
    STAKING_ADDR,
    WALLET_ADDR,
    make_bundle,
    make_decoder_config,
    make_mock_ctx,
    make_nft_transfer,
    make_token_row,
    make_transfer,
)
from lemon_ledger.classify.decoders import lemonchain_only as _lc  # noqa: F401
from lemon_ledger.classify.decoders.base import ZERO_ADDR, L2Decoder
from lemon_ledger.classify.types import ClaimSet
from lemon_ledger.models.enums import ClassificationKind

# ── registration ──────────────────────────────────────────────────────────────

_LC_ONLY_SYMBOLS = ["HXDX", "HXBT", "MHSA", "NXYS", "PUP", "RMC", "SMART", "STH", "TIXA"]
_LC_ONLY_CLASSES = [
    "HxdxDecoder",
    "HxbtDecoder",
    "MhsaDecoder",
    "NxysDecoder",
    "PupDecoder",
    "RmcDecoder",
    "SmartDecoder",
    "SthDecoder",
    "TixaDecoder",
]


@pytest.mark.parametrize("sym", _LC_ONLY_SYMBOLS)
def test_lemonchain_only_symbol_registered(sym: str) -> None:
    assert sym in L2Decoder._registry


@pytest.mark.parametrize("cls_name", _LC_ONLY_CLASSES)
def test_lemonchain_only_class_registered(cls_name: str) -> None:
    assert cls_name in L2Decoder._registry


def test_lbst_registered() -> None:
    assert "LBST" in L2Decoder._registry
    assert "LbstDecoder" in L2Decoder._registry


def test_lqst_registered() -> None:
    assert "LQST" in L2Decoder._registry
    assert "LqstDecoder" in L2Decoder._registry


# ── PENDING discipline: staking unknown for thin stub ────────────────────────


def test_lc_only_reward_pending_when_staking_unknown() -> None:
    """Inbound ERC-20 with staking_contract=None → PENDING, not REWARD."""
    token_id = uuid.uuid4()
    wallet_id = uuid.uuid4()
    tr = make_token_row(token_id)
    cfg = make_decoder_config(token_id, staking_contract=None)
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr, fmv=Decimal("0.05"))
    transfer = make_transfer(log_index=0, from_addr=STAKING_ADDR, to_addr=WALLET_ADDR)
    bundle = make_bundle(wallet_id, transfers=[transfer])

    decoder = L2Decoder._registry["HxdxDecoder"](token_id)
    events = list(decoder._detect_reward(bundle, ctx, ClaimSet()))

    assert len(events) == 1
    assert events[0].classification == ClassificationKind.PENDING
    assert events[0].needs_review
    ctx.propose_staking_contract.assert_called_once()


def test_lc_only_reward_from_confirmed_staking() -> None:
    """Inbound ERC-20 from confirmed staking contract → REWARD."""
    token_id = uuid.uuid4()
    wallet_id = uuid.uuid4()
    tr = make_token_row(token_id)
    cfg = make_decoder_config(
        token_id,
        staking_contract=STAKING_ADDR,
        staking_contract_status="confirmed",
    )
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr, fmv=Decimal("0.10"))
    transfer = make_transfer(log_index=0, from_addr=STAKING_ADDR, to_addr=WALLET_ADDR)
    bundle = make_bundle(wallet_id, transfers=[transfer])

    decoder = L2Decoder._registry["HxdxDecoder"](token_id)
    events = list(decoder._detect_reward(bundle, ctx, ClaimSet()))

    assert len(events) == 1
    assert events[0].classification == ClassificationKind.REWARD
    assert not events[0].needs_review


def test_lc_only_sender_mismatch_not_claimed() -> None:
    """Staking known but sender doesn't match → event not claimed (falls to common)."""
    token_id = uuid.uuid4()
    wallet_id = uuid.uuid4()
    tr = make_token_row(token_id)
    cfg = make_decoder_config(
        token_id,
        staking_contract="0x" + "f" * 40,  # known but different
        staking_contract_status="confirmed",
    )
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr)
    transfer = make_transfer(log_index=0, from_addr=STAKING_ADDR, to_addr=WALLET_ADDR)
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    decoder = L2Decoder._registry["HxdxDecoder"](token_id)
    events = list(decoder._detect_reward(bundle, ctx, claims))

    # Neither REWARD nor PENDING — falls through to common layer
    assert len(events) == 0
    assert not claims.has(transfer)


# ── LQST: mint-only decoder ───────────────────────────────────────────────────

LQST_TOKEN_ID = uuid.uuid4()
LQST_NFT_ADDR = "0x" + "3" * 40


def _lqst() -> L2Decoder:
    return L2Decoder._registry["LqstDecoder"](LQST_TOKEN_ID)


def _lqst_cfg(nft_status: str = "confirmed") -> MagicMock:
    return make_decoder_config(
        LQST_TOKEN_ID,
        nft_contract=LQST_NFT_ADDR,
        nft_contract_status=nft_status,
    )


def test_lqst_reward_is_noop() -> None:
    """LqstDecoder._detect_reward yields nothing."""
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    transfer = make_transfer(log_index=0, from_addr=STAKING_ADDR, to_addr=WALLET_ADDR)
    bundle = make_bundle(wallet_id, transfers=[transfer])

    events = list(_lqst()._detect_reward(bundle, ctx, ClaimSet()))
    assert events == []


def test_lqst_stake_is_noop() -> None:
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    bundle = make_bundle(wallet_id)
    assert list(_lqst()._detect_stake(bundle, ctx, ClaimSet())) == []


def test_lqst_unstake_is_noop() -> None:
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    bundle = make_bundle(wallet_id)
    assert list(_lqst()._detect_unstake(bundle, ctx, ClaimSet())) == []


def test_lqst_mint_pending_when_fee_unresolved() -> None:
    """LQST mint with no resolvable fee → PENDING (never $0 basis)."""
    wallet_id = uuid.uuid4()
    cfg = _lqst_cfg()
    ctx = make_mock_ctx(wallet_id, cfg=cfg)
    # No envelope, no LEMX row, no internals → unresolvable
    ctx.registry_by_address.return_value = None
    transfer = make_nft_transfer(
        log_index=0, from_addr=ZERO_ADDR, to_addr=WALLET_ADDR, contract=LQST_NFT_ADDR
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = list(_lqst()._detect_mint(bundle, ctx, claims))
    assert len(events) == 1
    assert events[0].classification == ClassificationKind.PENDING
    assert events[0].needs_review
    assert events[0].value_usd_at_event is None
    assert claims.has(transfer)


def test_lqst_mint_fee_from_envelope_value() -> None:
    """LQST mint fee resolved from envelope.value → MINT with basis."""
    from unittest.mock import MagicMock

    from lemon_ledger.pricing.types import TokenRow

    wallet_id = uuid.uuid4()
    cfg = _lqst_cfg()
    lemx_row = TokenRow(
        token_id=str(uuid.uuid4()),
        symbol="LEMX",
        category="ecosystem-native",
        contract_address=ZERO_ADDR,
        chain="lemonchain",
        tier=1,
        decimals=18,
    )
    ctx = make_mock_ctx(wallet_id, cfg=cfg, fmv=Decimal("0.50"))
    ctx.registry_by_address.return_value = lemx_row

    envelope = MagicMock()
    envelope.raw = {
        "from": WALLET_ADDR,
        "to": LQST_NFT_ADDR,
        "value": str(10 * 10**18),  # 10 LEMX
        "gasUsed": "21000",
        "gasPrice": "1000000000",
    }
    transfer = make_nft_transfer(
        log_index=0, from_addr=ZERO_ADDR, to_addr=WALLET_ADDR, contract=LQST_NFT_ADDR
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    # Attach envelope
    from classify.helpers import BLOCK, OCCURRED_AT
    from lemon_ledger.classify.types import TxBundle
    from lemon_ledger.domain.chains import Chain

    bundle = TxBundle(
        wallet_id=wallet_id,
        chain=Chain.LEMONCHAIN,
        tx_hash="0xabc",
        block_number=BLOCK,
        occurred_at=OCCURRED_AT,
        envelope=envelope,
        transfers=[transfer],
        internals=[],
    )
    claims = ClaimSet()

    events = list(_lqst()._detect_mint(bundle, ctx, claims))
    assert len(events) == 1
    ev = events[0]
    assert ev.classification == ClassificationKind.MINT
    # fee = 10 LEMX * $0.50 = $5.00
    assert ev.value_usd_at_event == Decimal("5.0")
    assert not ev.needs_review
    assert "collectible" in (ev.notes or "")
    assert claims.has(transfer)


def test_lqst_mint_deferred_when_nft_contract_unknown() -> None:
    """LQST with unknown nft_contract yields nothing (cold start)."""
    wallet_id = uuid.uuid4()
    cfg = make_decoder_config(LQST_TOKEN_ID)  # nft_contract=None
    ctx = make_mock_ctx(wallet_id, cfg=cfg)
    transfer = make_nft_transfer(
        log_index=0, from_addr=ZERO_ADDR, to_addr=WALLET_ADDR, contract=LQST_NFT_ADDR
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = list(_lqst()._detect_mint(bundle, ctx, claims))
    assert events == []
    assert not claims.has(transfer)


def test_lqst_mint_fee_from_internal_tx() -> None:
    """LQST fee resolution falls through to internal tx when no envelope value."""
    from classify.helpers import BLOCK, OCCURRED_AT
    from lemon_ledger.classify.types import TxBundle
    from lemon_ledger.domain.chains import Chain
    from lemon_ledger.pricing.types import TokenRow

    wallet_id = uuid.uuid4()
    cfg = _lqst_cfg()
    lemx_row = TokenRow(
        token_id=str(uuid.uuid4()),
        symbol="LEMX",
        category="ecosystem-native",
        contract_address=ZERO_ADDR,
        chain="lemonchain",
        tier=1,
        decimals=18,
    )
    ctx = make_mock_ctx(wallet_id, cfg=cfg, fmv=Decimal("1.00"))
    ctx.registry_by_address.return_value = lemx_row

    envelope = MagicMock()
    envelope.raw = {
        "from": WALLET_ADDR,
        "to": LQST_NFT_ADDR,
        "value": "0",  # no native value
        "gasUsed": "21000",
        "gasPrice": "1000000000",
    }

    itx = MagicMock()
    itx.raw = {
        "from": WALLET_ADDR,
        "to": LQST_NFT_ADDR,
        "value": str(5 * 10**18),  # 5 LEMX internal forward
    }

    transfer = make_nft_transfer(
        log_index=0, from_addr=ZERO_ADDR, to_addr=WALLET_ADDR, contract=LQST_NFT_ADDR
    )
    bundle = TxBundle(
        wallet_id=wallet_id,
        chain=Chain.LEMONCHAIN,
        tx_hash="0xdef",
        block_number=BLOCK,
        occurred_at=OCCURRED_AT,
        envelope=envelope,
        transfers=[transfer],
        internals=[itx],
    )
    claims = ClaimSet()

    events = list(_lqst()._detect_mint(bundle, ctx, claims))
    assert len(events) == 1
    ev = events[0]
    assert ev.classification == ClassificationKind.MINT
    # fee = 5 LEMX * $1.00 = $5.00
    assert ev.value_usd_at_event == Decimal("5.0")
