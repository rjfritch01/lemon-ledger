"""Tests for WrapRecognizer and SwapCreditRedemptionRecognizer."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

from classify.helpers import BLOCK, OCCURRED_AT, WALLET_ADDR, make_bundle, make_mock_ctx
from lemon_ledger.classify.recognizers import (
    _DEPOSIT_SELECTOR,
    _WITHDRAW_SELECTOR,
    SwapCreditRedemptionRecognizer,
    WrapRecognizer,
)
from lemon_ledger.classify.types import ClaimSet, TxBundle
from lemon_ledger.domain.chains import Chain
from lemon_ledger.models.enums import ClassificationKind
from lemon_ledger.pricing.types import TokenRow

WLEMX_ADDR = "0x84862e65ebf37af91a8b85283b58505de3352588"
SCDT_ADDR = "0xe20e3c6447b024a3fbf22d4803de1d910add7776"
ZERO_ADDR = "0x" + "0" * 40


def _wlemx_row() -> TokenRow:
    return TokenRow(
        token_id=str(uuid.uuid4()),
        symbol="WLEMX",
        category="ecosystem-l2",
        contract_address=WLEMX_ADDR,
        chain="lemonchain",
        tier=2,
        decimals=18,
    )


def _scdt_row(fmv_token_id: str | None = None) -> TokenRow:
    return TokenRow(
        token_id=fmv_token_id or str(uuid.uuid4()),
        symbol="SCDT",
        category="ecosystem-l2",
        contract_address=SCDT_ADDR,
        chain="lemonchain",
        tier=2,
        decimals=0,
    )


def _make_transfer(
    *,
    from_addr: str,
    to_addr: str,
    contract: str,
    log_index: int = 0,
    value: int = 10**18,
    nft_token_id: str | None = None,
) -> MagicMock:
    t = MagicMock()
    t.contract_address = contract
    t.log_index = log_index
    t.value = Decimal(value)
    t.raw = {"from": from_addr, "to": to_addr, "value": str(value)}
    if nft_token_id is not None:
        t.raw["tokenID"] = nft_token_id
    return t


def _make_envelope(
    *,
    tx_from: str = WALLET_ADDR,
    tx_to: str = WLEMX_ADDR,
    value: int = 0,
    selector: str = "",
) -> MagicMock:
    env = MagicMock()
    env.raw = {
        "from": tx_from,
        "to": tx_to,
        "value": str(value),
        "input": selector + "0" * 56,  # pad to simulate calldata
    }
    return env


# ── WrapRecognizer ────────────────────────────────────────────────────────────


def test_wrap_recognizer_no_envelope() -> None:
    """Bundle with no envelope → no events."""
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    bundle = make_bundle(wallet_id)
    events = WrapRecognizer().recognize(bundle, ctx, ClaimSet())
    assert events == []


def test_wrap_recognizer_no_wlemx_row() -> None:
    """registry_by_symbol returns None → no events."""
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    ctx.registry_by_symbol.return_value = None
    bundle = TxBundle(
        wallet_id=wallet_id,
        chain=Chain.LEMONCHAIN,
        tx_hash="0xabc",
        block_number=BLOCK,
        occurred_at=OCCURRED_AT,
        envelope=_make_envelope(selector=_DEPOSIT_SELECTOR, value=10**18),
        transfers=[],
        internals=[],
    )
    events = WrapRecognizer().recognize(bundle, ctx, ClaimSet())
    assert events == []


def test_wrap_recognizer_deposit() -> None:
    """deposit() call → WRAP event with correct amount."""
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    ctx.registry_by_symbol.return_value = _wlemx_row()

    native_wei = 2 * 10**18
    wlemx_transfer = _make_transfer(
        from_addr=ZERO_ADDR,
        to_addr=WALLET_ADDR,
        contract=WLEMX_ADDR,
        value=native_wei,
    )
    bundle = TxBundle(
        wallet_id=wallet_id,
        chain=Chain.LEMONCHAIN,
        tx_hash="0xwrap",
        block_number=BLOCK,
        occurred_at=OCCURRED_AT,
        envelope=_make_envelope(
            tx_to=WLEMX_ADDR,
            value=native_wei,
            selector=_DEPOSIT_SELECTOR,
        ),
        transfers=[wlemx_transfer],
        internals=[],
    )
    claims = ClaimSet()

    events = WrapRecognizer().recognize(bundle, ctx, claims)
    assert len(events) == 1
    assert events[0].classification == ClassificationKind.WRAP
    assert events[0].amount == Decimal("2")
    assert claims.has(wlemx_transfer)


def test_wrap_recognizer_withdraw() -> None:
    """withdraw() call → UNWRAP event; WLEMX outflow claimed."""
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    ctx.registry_by_symbol.return_value = _wlemx_row()

    wlemx_out = _make_transfer(
        from_addr=WALLET_ADDR,
        to_addr=ZERO_ADDR,
        contract=WLEMX_ADDR,
        value=10**18,
    )
    bundle = TxBundle(
        wallet_id=wallet_id,
        chain=Chain.LEMONCHAIN,
        tx_hash="0xunwrap",
        block_number=BLOCK,
        occurred_at=OCCURRED_AT,
        envelope=_make_envelope(tx_to=WLEMX_ADDR, value=0, selector=_WITHDRAW_SELECTOR),
        transfers=[wlemx_out],
        internals=[],
    )
    claims = ClaimSet()

    events = WrapRecognizer().recognize(bundle, ctx, claims)
    assert len(events) == 1
    assert events[0].classification == ClassificationKind.UNWRAP
    assert events[0].amount == Decimal("1")
    assert claims.has(wlemx_out)


def test_wrap_recognizer_unrelated_call() -> None:
    """Call to WLEMX contract with unknown selector → no events."""
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    ctx.registry_by_symbol.return_value = _wlemx_row()

    bundle = TxBundle(
        wallet_id=wallet_id,
        chain=Chain.LEMONCHAIN,
        tx_hash="0xother",
        block_number=BLOCK,
        occurred_at=OCCURRED_AT,
        envelope=_make_envelope(tx_to=WLEMX_ADDR, selector="0xdeadbeef"),
        transfers=[],
        internals=[],
    )
    events = WrapRecognizer().recognize(bundle, ctx, ClaimSet())
    assert events == []


# ── SwapCreditRedemptionRecognizer ────────────────────────────────────────────

L2_NFT_ADDR = "0x" + "a" * 40


def test_scdt_recognizer_no_scdt_row() -> None:
    """No SCDT in registry → no events."""
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    ctx.registry_by_symbol.return_value = None
    bundle = make_bundle(wallet_id)
    events = SwapCreditRedemptionRecognizer().recognize(bundle, ctx, ClaimSet())
    assert events == []


def test_scdt_recognizer_no_scdt_outflow() -> None:
    """No SCDT NFT outflow → no events."""
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    ctx.registry_by_symbol.return_value = _scdt_row()

    bundle = make_bundle(wallet_id)
    events = SwapCreditRedemptionRecognizer().recognize(bundle, ctx, ClaimSet())
    assert events == []


def test_scdt_recognizer_paired_redemption() -> None:
    """Matching SCDT out + L2 NFT in → 2 SWAP_CREDIT_REDEMPTION events."""
    wallet_id = uuid.uuid4()
    fmv_token_id = str(uuid.uuid4())
    ctx = make_mock_ctx(wallet_id, fmv=Decimal("50.00"))
    ctx.registry_by_symbol.return_value = _scdt_row(fmv_token_id=fmv_token_id)

    scdt_out = _make_transfer(
        from_addr=WALLET_ADDR,
        to_addr="0x" + "b" * 40,
        contract=SCDT_ADDR,
        log_index=0,
        nft_token_id="1",
    )
    nft_in = _make_transfer(
        from_addr=ZERO_ADDR,
        to_addr=WALLET_ADDR,
        contract=L2_NFT_ADDR,
        log_index=1,
        nft_token_id="42",
    )

    bundle = TxBundle(
        wallet_id=wallet_id,
        chain=Chain.LEMONCHAIN,
        tx_hash="0xredeem",
        block_number=BLOCK,
        occurred_at=OCCURRED_AT,
        envelope=None,
        transfers=[scdt_out, nft_in],
        internals=[],
    )
    claims = ClaimSet()

    events = SwapCreditRedemptionRecognizer().recognize(bundle, ctx, claims)
    assert len(events) == 2
    kinds = {e.classification for e in events}
    assert kinds == {ClassificationKind.SWAP_CREDIT_REDEMPTION}
    # All FMVs populated
    assert all(e.value_usd_at_event == Decimal("50.00") for e in events)
    assert not any(e.needs_review for e in events)
    assert claims.has(scdt_out)
    assert claims.has(nft_in)


def test_scdt_recognizer_mismatch_count() -> None:
    """SCDT out count ≠ NFT in count → no events (unclaimed for common layer)."""
    wallet_id = uuid.uuid4()
    ctx = make_mock_ctx(wallet_id)
    ctx.registry_by_symbol.return_value = _scdt_row()

    scdt_out1 = _make_transfer(
        from_addr=WALLET_ADDR,
        to_addr="0x" + "b" * 40,
        contract=SCDT_ADDR,
        log_index=0,
        nft_token_id="1",
    )
    scdt_out2 = _make_transfer(
        from_addr=WALLET_ADDR,
        to_addr="0x" + "b" * 40,
        contract=SCDT_ADDR,
        log_index=1,
        nft_token_id="2",
    )
    nft_in = _make_transfer(
        from_addr=ZERO_ADDR,
        to_addr=WALLET_ADDR,
        contract=L2_NFT_ADDR,
        log_index=2,
        nft_token_id="42",
    )

    bundle = TxBundle(
        wallet_id=wallet_id,
        chain=Chain.LEMONCHAIN,
        tx_hash="0xmismatch",
        block_number=BLOCK,
        occurred_at=OCCURRED_AT,
        envelope=None,
        transfers=[scdt_out1, scdt_out2, nft_in],
        internals=[],
    )
    claims = ClaimSet()
    events = SwapCreditRedemptionRecognizer().recognize(bundle, ctx, claims)

    assert events == []
    assert not claims.has(scdt_out1)
    assert not claims.has(scdt_out2)
    assert not claims.has(nft_in)


def test_scdt_recognizer_no_fmv_sets_needs_review() -> None:
    """No SCDT FMV → both events have needs_review=True."""
    wallet_id = uuid.uuid4()
    fmv_token_id = str(uuid.uuid4())
    ctx = make_mock_ctx(wallet_id, fmv=None)  # no FMV
    ctx.registry_by_symbol.return_value = _scdt_row(fmv_token_id=fmv_token_id)

    scdt_out = _make_transfer(
        from_addr=WALLET_ADDR,
        to_addr="0x" + "b" * 40,
        contract=SCDT_ADDR,
        log_index=0,
        nft_token_id="1",
    )
    nft_in = _make_transfer(
        from_addr=ZERO_ADDR,
        to_addr=WALLET_ADDR,
        contract=L2_NFT_ADDR,
        log_index=1,
        nft_token_id="42",
    )

    bundle = TxBundle(
        wallet_id=wallet_id,
        chain=Chain.LEMONCHAIN,
        tx_hash="0xnofmv",
        block_number=BLOCK,
        occurred_at=OCCURRED_AT,
        envelope=None,
        transfers=[scdt_out, nft_in],
        internals=[],
    )
    events = SwapCreditRedemptionRecognizer().recognize(bundle, ctx, ClaimSet())
    assert all(e.needs_review for e in events)
    assert all(e.value_usd_at_event is None for e in events)
