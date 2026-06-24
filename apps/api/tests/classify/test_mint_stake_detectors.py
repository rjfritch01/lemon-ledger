"""Tests for stake, unstake, and mint detectors."""

from __future__ import annotations

import uuid
from decimal import Decimal

from classify.helpers import (
    NFT_ADDR,
    STAKING_ADDR,
    WALLET_ADDR,
    make_bundle,
    make_decoder_config,
    make_mock_ctx,
    make_nft_transfer,
    make_token_row,
)
from lemon_ledger.classify.decoders import cross_chain as _cc  # noqa: F401
from lemon_ledger.classify.decoders.base import ZERO_ADDR, L2Decoder
from lemon_ledger.classify.types import ClaimSet
from lemon_ledger.models.enums import ClassificationKind

TOKEN_ID = uuid.uuid4()


def _lflx() -> L2Decoder:
    return L2Decoder._registry["LflxDecoder"](TOKEN_ID)


# ── stake ──────────────────────────────────────────────────────────────────────


def test_stake_nft_to_contract() -> None:
    """Outbound ERC-721 to non-tracked address is classified STAKE."""
    wallet_id = uuid.uuid4()
    tr = make_token_row(TOKEN_ID)
    cfg = make_decoder_config(TOKEN_ID, nft_contract=NFT_ADDR, nft_contract_status="confirmed")
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr)
    transfer = make_nft_transfer(
        log_index=0, from_addr=WALLET_ADDR, to_addr=STAKING_ADDR, contract=NFT_ADDR
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = list(_lflx()._detect_stake(bundle, ctx, claims))
    assert len(events) == 1
    assert events[0].classification == ClassificationKind.STAKE
    assert events[0].amount == Decimal(1)
    assert claims.has(transfer)


def test_stake_discovers_staking_contract() -> None:
    """Stake to unknown address → propose_staking_contract called."""
    wallet_id = uuid.uuid4()
    tr = make_token_row(TOKEN_ID)
    cfg = make_decoder_config(TOKEN_ID, nft_contract=NFT_ADDR, nft_contract_status="discovered")
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr)
    transfer = make_nft_transfer(
        log_index=1, from_addr=WALLET_ADDR, to_addr=STAKING_ADDR, contract=NFT_ADDR
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    list(_lflx()._detect_stake(bundle, ctx, claims))
    ctx.propose_staking_contract.assert_called_once()


# ── unstake ────────────────────────────────────────────────────────────────────


def test_unstake_from_known_staking_contract() -> None:
    wallet_id = uuid.uuid4()
    tr = make_token_row(TOKEN_ID)
    cfg = make_decoder_config(
        TOKEN_ID,
        staking_contract=STAKING_ADDR,
        staking_contract_status="confirmed",
        nft_contract=NFT_ADDR,
        nft_contract_status="confirmed",
    )
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr)
    transfer = make_nft_transfer(
        log_index=2, from_addr=STAKING_ADDR, to_addr=WALLET_ADDR, contract=NFT_ADDR
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = list(_lflx()._detect_unstake(bundle, ctx, claims))
    assert len(events) == 1
    assert events[0].classification == ClassificationKind.UNSTAKE


def test_unstake_skipped_when_sender_unknown() -> None:
    wallet_id = uuid.uuid4()
    tr = make_token_row(TOKEN_ID)
    cfg = make_decoder_config(TOKEN_ID, nft_contract=NFT_ADDR, nft_contract_status="confirmed")
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr)
    transfer = make_nft_transfer(
        log_index=3, from_addr=STAKING_ADDR, to_addr=WALLET_ADDR, contract=NFT_ADDR
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = list(_lflx()._detect_unstake(bundle, ctx, claims))
    assert len(events) == 0


# ── mint ───────────────────────────────────────────────────────────────────────


def test_mint_from_zero_address() -> None:
    """ERC-721 from zero to wallet, with known nft_contract → MINT.

    No envelope and no mint_fee_wei: _derive_native_fee returns 0, so
    _mint_fee_usd returns Decimal(0) — mint was free.  needs_review stays False.
    """
    wallet_id = uuid.uuid4()
    tr = make_token_row(TOKEN_ID)
    cfg = make_decoder_config(TOKEN_ID, nft_contract=NFT_ADDR, nft_contract_status="confirmed")
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr, fmv=None)
    transfer = make_nft_transfer(
        log_index=4, from_addr=ZERO_ADDR, to_addr=WALLET_ADDR, contract=NFT_ADDR
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = list(_lflx()._detect_mint(bundle, ctx, claims))
    assert len(events) == 1
    ev = events[0]
    assert ev.classification == ClassificationKind.MINT
    assert ev.amount == Decimal(1)
    assert ev.value_usd_at_event == Decimal(0)
    assert not ev.needs_review
    assert claims.has(transfer)


def test_mint_needs_review_when_fee_unpriceable() -> None:
    """When mint_fee_wei > 0 but LEMX has no price, needs_review=True."""
    from decimal import Decimal as D

    wallet_id = uuid.uuid4()
    tr = make_token_row(TOKEN_ID)
    cfg = make_decoder_config(
        TOKEN_ID,
        nft_contract=NFT_ADDR,
        nft_contract_status="confirmed",
        mint_fee_wei=D("100000000000000000"),  # 0.1 LEMX in wei
    )
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr, fmv=None)
    ctx.registry_by_address.return_value = None  # no LEMX token row → price lookup fails
    transfer = make_nft_transfer(
        log_index=10, from_addr=ZERO_ADDR, to_addr=WALLET_ADDR, contract=NFT_ADDR
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = list(_lflx()._detect_mint(bundle, ctx, claims))
    assert len(events) == 1
    assert events[0].needs_review
    assert events[0].value_usd_at_event is None


def test_mint_deferred_when_nft_contract_unknown() -> None:
    """When nft_contract is unknown, _detect_mint yields nothing (cold start)."""
    wallet_id = uuid.uuid4()
    tr = make_token_row(TOKEN_ID)
    cfg = make_decoder_config(TOKEN_ID)
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr)
    transfer = make_nft_transfer(
        log_index=5, from_addr=ZERO_ADDR, to_addr=WALLET_ADDR, contract=NFT_ADDR
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = list(_lflx()._detect_mint(bundle, ctx, claims))
    assert len(events) == 0
    assert not claims.has(transfer)
