"""Tests for the reward detector in L2Decoder._detect_reward."""

from __future__ import annotations

import uuid
from decimal import Decimal

from classify.helpers import (
    STAKING_ADDR,
    WALLET_ADDR,
    make_bundle,
    make_decoder_config,
    make_mock_ctx,
    make_token_row,
    make_transfer,
)
from lemon_ledger.classify.decoders import cross_chain as _cc  # noqa: F401
from lemon_ledger.classify.decoders.base import ZERO_ADDR, L2Decoder
from lemon_ledger.classify.types import ClaimSet
from lemon_ledger.models.enums import ClassificationKind

TOKEN_ID = uuid.uuid4()


def _lflx_decoder() -> L2Decoder:
    return L2Decoder._registry["LflxDecoder"](TOKEN_ID)


def test_reward_from_known_staking_contract() -> None:
    """ERC-20 transfer from the known staking contract is classified REWARD."""
    wallet_id = uuid.uuid4()
    tr = make_token_row(TOKEN_ID)
    cfg = make_decoder_config(
        TOKEN_ID,
        staking_contract=STAKING_ADDR,
        staking_contract_status="confirmed",
    )
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr, fmv=Decimal("0.10"))
    transfer = make_transfer(log_index=0, from_addr=STAKING_ADDR, to_addr=WALLET_ADDR)
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = list(_lflx_decoder()._detect_reward(bundle, ctx, claims))
    assert len(events) == 1
    ev = events[0]
    assert ev.classification == ClassificationKind.REWARD
    assert ev.amount == Decimal("1")
    assert ev.value_usd_at_event == Decimal("0.10")
    assert not ev.needs_review
    assert claims.has(transfer)


def test_reward_option_c_discovery() -> None:
    """Unknown staking contract → Option-C proposal; needs_review=True."""
    wallet_id = uuid.uuid4()
    tr = make_token_row(TOKEN_ID)
    cfg = make_decoder_config(TOKEN_ID, staking_contract=None)
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr, fmv=Decimal("0.05"))
    transfer = make_transfer(log_index=1, from_addr=STAKING_ADDR, to_addr=WALLET_ADDR)
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = list(_lflx_decoder()._detect_reward(bundle, ctx, claims))
    assert len(events) == 1
    assert events[0].needs_review
    ctx.propose_staking_contract.assert_called_once_with(TOKEN_ID, STAKING_ADDR)


def test_reward_zero_address_skipped() -> None:
    wallet_id = uuid.uuid4()
    tr = make_token_row(TOKEN_ID)
    cfg = make_decoder_config(TOKEN_ID)
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr)
    transfer = make_transfer(log_index=2, from_addr=ZERO_ADDR, to_addr=WALLET_ADDR)
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = list(_lflx_decoder()._detect_reward(bundle, ctx, claims))
    assert len(events) == 0


def test_reward_self_transfer_skipped() -> None:
    wallet_id = uuid.uuid4()
    tr = make_token_row(TOKEN_ID)
    cfg = make_decoder_config(TOKEN_ID)
    other_wallet = "0xeeee000000000000000000000000000000000005"
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr)
    ctx.is_tracked_wallet.side_effect = lambda addr: addr == other_wallet.lower()
    transfer = make_transfer(log_index=3, from_addr=other_wallet, to_addr=WALLET_ADDR)
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = list(_lflx_decoder()._detect_reward(bundle, ctx, claims))
    assert len(events) == 0


def test_reward_no_fmv_sets_needs_review() -> None:
    wallet_id = uuid.uuid4()
    tr = make_token_row(TOKEN_ID)
    cfg = make_decoder_config(
        TOKEN_ID, staking_contract=STAKING_ADDR, staking_contract_status="confirmed"
    )
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr, fmv=None)
    transfer = make_transfer(log_index=4, from_addr=STAKING_ADDR, to_addr=WALLET_ADDR)
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = list(_lflx_decoder()._detect_reward(bundle, ctx, claims))
    assert len(events) == 1
    assert events[0].needs_review
    assert events[0].value_usd_at_event is None


def test_reward_distribution_complete_flags_review() -> None:
    wallet_id = uuid.uuid4()
    tr = make_token_row(TOKEN_ID)
    cfg = make_decoder_config(
        TOKEN_ID,
        staking_contract=STAKING_ADDR,
        staking_contract_status="confirmed",
        distribution_complete=True,
    )
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr, fmv=Decimal("0.10"))
    transfer = make_transfer(log_index=5, from_addr=STAKING_ADDR, to_addr=WALLET_ADDR)
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()

    events = list(_lflx_decoder()._detect_reward(bundle, ctx, claims))
    assert events[0].needs_review


def test_reward_already_claimed_skipped() -> None:
    wallet_id = uuid.uuid4()
    tr = make_token_row(TOKEN_ID)
    cfg = make_decoder_config(
        TOKEN_ID, staking_contract=STAKING_ADDR, staking_contract_status="confirmed"
    )
    ctx = make_mock_ctx(wallet_id, cfg=cfg, token_row=tr)
    transfer = make_transfer(log_index=6, from_addr=STAKING_ADDR, to_addr=WALLET_ADDR)
    bundle = make_bundle(wallet_id, transfers=[transfer])
    claims = ClaimSet()
    claims.add(transfer)

    events = list(_lflx_decoder()._detect_reward(bundle, ctx, claims))
    assert len(events) == 0
