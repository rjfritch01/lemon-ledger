"""Tests for WalletContext (no Docker; mocked session)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

from classify.helpers import ERC20_ADDR, WALLET_ADDR, make_bundle, make_transfer
from lemon_ledger.classify.context import WalletContext
from lemon_ledger.classify.decoders import cross_chain as _cc  # noqa: F401
from lemon_ledger.models.classified import L2DecoderConfig


def _wallet(chain: str = "lemonchain") -> MagicMock:
    w = MagicMock()
    w.address = WALLET_ADDR
    w.chain = chain
    w.user_id = uuid.uuid4()
    return w


def _pricing() -> MagicMock:
    p = MagicMock()
    p.get_historical_price.return_value = Decimal("0.05")
    return p


def _session_with_config(cfg: MagicMock | None) -> MagicMock:
    sess = MagicMock()
    query_mock = MagicMock()
    query_mock.filter_by.return_value.first.return_value = cfg
    query_mock.all.return_value = [cfg] if cfg else []
    sess.query.return_value = query_mock
    sess.get.return_value = None
    return sess


# ── is_tracked_wallet ──────────────────────────────────────────────────────────


def test_is_tracked_wallet_match() -> None:
    ctx = WalletContext(
        wallet=_wallet(),
        user_wallet_addresses={WALLET_ADDR, "0xother"},
        session=MagicMock(),
        pricing=_pricing(),
    )
    assert ctx.is_tracked_wallet(WALLET_ADDR)
    assert ctx.is_tracked_wallet(WALLET_ADDR.upper())  # case-insensitive
    assert not ctx.is_tracked_wallet("0xunknown")


# ── config_for (cache) ─────────────────────────────────────────────────────────


def test_config_for_caches_result() -> None:
    token_id = uuid.uuid4()
    cfg = MagicMock(spec=L2DecoderConfig)
    cfg.token_id = token_id
    sess = _session_with_config(cfg)

    ctx = WalletContext(
        wallet=_wallet(),
        user_wallet_addresses=set(),
        session=sess,
        pricing=_pricing(),
    )
    result1 = ctx.config_for(token_id)
    result2 = ctx.config_for(token_id)
    assert result1 is result2
    # Should only hit DB once despite two calls
    assert sess.query.call_count == 1


def test_config_for_returns_none_when_missing() -> None:
    sess = _session_with_config(None)
    ctx = WalletContext(
        wallet=_wallet(),
        user_wallet_addresses=set(),
        session=sess,
        pricing=_pricing(),
    )
    assert ctx.config_for(uuid.uuid4()) is None


# ── propose_staking_contract ───────────────────────────────────────────────────


def test_propose_staking_contract_writes_when_unknown() -> None:
    token_id = uuid.uuid4()
    cfg = MagicMock(spec=L2DecoderConfig)
    cfg.token_id = token_id
    cfg.staking_contract = None
    sess = _session_with_config(cfg)

    ctx = WalletContext(
        wallet=_wallet(),
        user_wallet_addresses=set(),
        session=sess,
        pricing=_pricing(),
    )
    ctx.config_for(token_id)  # prime cache

    ctx.propose_staking_contract(token_id, "0xSomeContract")
    assert cfg.staking_contract == "0xsomecontract"
    assert cfg.staking_contract_status == "discovered"
    sess.add.assert_called_once_with(cfg)


def test_propose_staking_contract_logs_conflict() -> None:
    token_id = uuid.uuid4()
    cfg = MagicMock(spec=L2DecoderConfig)
    cfg.token_id = token_id
    cfg.staking_contract = "0xexisting"
    sess = _session_with_config(cfg)

    ctx = WalletContext(
        wallet=_wallet(),
        user_wallet_addresses=set(),
        session=sess,
        pricing=_pricing(),
    )
    ctx.config_for(token_id)

    # Different address → conflict; no DB write
    ctx.propose_staking_contract(token_id, "0xConflict")
    sess.add.assert_not_called()


def test_propose_staking_contract_idempotent() -> None:
    token_id = uuid.uuid4()
    cfg = MagicMock(spec=L2DecoderConfig)
    cfg.token_id = token_id
    cfg.staking_contract = "0xsame"
    sess = _session_with_config(cfg)

    ctx = WalletContext(
        wallet=_wallet(),
        user_wallet_addresses=set(),
        session=sess,
        pricing=_pricing(),
    )
    ctx.config_for(token_id)
    ctx.propose_staking_contract(token_id, "0xsame")
    # No write since it's already set to the same value
    sess.add.assert_not_called()


# ── propose_nft_contract ───────────────────────────────────────────────────────


def test_propose_nft_contract_writes_when_unknown() -> None:
    token_id = uuid.uuid4()
    cfg = MagicMock(spec=L2DecoderConfig)
    cfg.token_id = token_id
    cfg.nft_contract = None
    sess = _session_with_config(cfg)

    ctx = WalletContext(
        wallet=_wallet(),
        user_wallet_addresses=set(),
        session=sess,
        pricing=_pricing(),
    )
    ctx.config_for(token_id)
    ctx.propose_nft_contract(token_id, ERC20_ADDR)

    assert cfg.nft_contract == ERC20_ADDR.lower()
    assert cfg.nft_contract_status == "discovered"
    sess.add.assert_called_once_with(cfg)


def test_propose_nft_contract_no_op_when_same() -> None:
    token_id = uuid.uuid4()
    cfg = MagicMock(spec=L2DecoderConfig)
    cfg.token_id = token_id
    cfg.nft_contract = ERC20_ADDR.lower()
    sess = _session_with_config(cfg)

    ctx = WalletContext(
        wallet=_wallet(),
        user_wallet_addresses=set(),
        session=sess,
        pricing=_pricing(),
    )
    ctx.config_for(token_id)
    ctx.propose_nft_contract(token_id, ERC20_ADDR)
    sess.add.assert_not_called()


# ── decoders_for_bundle ────────────────────────────────────────────────────────


def test_decoders_for_bundle_matches_contract_address() -> None:
    """A bundle whose token transfer matches the known ERC-20 address returns a decoder."""
    from lemon_ledger.classify.decoders.base import L2Decoder
    from lemon_ledger.models.token_registry import TokenRegistry

    token_id = uuid.uuid4()
    cfg = MagicMock(spec=L2DecoderConfig)
    cfg.token_id = token_id
    cfg.chain = "lemonchain"
    cfg.decoder_class = "LflxDecoder"
    cfg.nft_contract = None
    cfg.staking_contract = None
    cfg.mint_contract = None

    tr = MagicMock(spec=TokenRegistry)
    tr.id = token_id
    tr.contract_address = ERC20_ADDR.lower()
    tr.symbol = "LFLX"
    tr.category = "l2_token"
    tr.chain = "lemonchain"
    tr.tier = 2
    tr.decimals = 18

    # Session: query(L2DecoderConfig).filter_by(chain=...).all() → [cfg]
    # session.get(TokenRegistry, token_id) → tr
    sess = MagicMock()
    query_mock = MagicMock()
    query_mock.filter_by.return_value.all.return_value = [cfg]
    query_mock.filter_by.return_value.first.return_value = cfg
    sess.query.return_value = query_mock
    sess.get.return_value = tr

    wallet_id = uuid.uuid4()
    ctx = WalletContext(
        wallet=_wallet(),
        user_wallet_addresses=set(),
        session=sess,
        pricing=_pricing(),
    )

    # Bundle with a transfer to/from ERC20_ADDR
    transfer = make_transfer(
        log_index=0,
        from_addr="0x" + "0" * 40,
        to_addr=WALLET_ADDR,
        contract=ERC20_ADDR,
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    decoders = ctx.decoders_for_bundle(bundle)

    assert len(decoders) == 1
    assert isinstance(decoders[0], L2Decoder._registry["LflxDecoder"])


def test_decoders_for_bundle_empty_when_no_match() -> None:
    """Bundle with an unrelated contract address yields no decoders."""
    sess = MagicMock()
    query_mock = MagicMock()
    query_mock.filter_by.return_value.all.return_value = []  # no configs
    sess.query.return_value = query_mock
    sess.get.return_value = None

    wallet_id = uuid.uuid4()
    ctx = WalletContext(
        wallet=_wallet(),
        user_wallet_addresses=set(),
        session=sess,
        pricing=_pricing(),
    )
    transfer = make_transfer(
        log_index=0,
        from_addr=WALLET_ADDR,
        to_addr="0x" + "1" * 40,
        contract="0x" + "9" * 40,  # completely unrelated
    )
    bundle = make_bundle(wallet_id, transfers=[transfer])
    assert ctx.decoders_for_bundle(bundle) == []
