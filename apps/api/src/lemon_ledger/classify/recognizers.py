"""Bundle-level recognizers that run before per-L2 decoders.

WrapRecognizer             — WLEMX wrap/unwrap (WETH9-style; tax-neutral)
SwapCreditRedemptionRecognizer — SCDT ERC-721 outflow + L2 NFT inflow
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from lemon_ledger.models.enums import ClassificationKind

if TYPE_CHECKING:
    from lemon_ledger.classify.context import WalletContext
    from lemon_ledger.classify.types import ClaimSet, ClassifiedEvent, TxBundle
    from lemon_ledger.models.raw import RawTokenTransfer

log = logging.getLogger(__name__)

# WETH9-style function selectors (4-byte ABI selectors).
# FLAG(1.6): Confirm these against the deployed WLEMX contract ABI.
# WLEMX address sourced from token_registry (symbol='WLEMX') at runtime.
_DEPOSIT_SELECTOR = "0xd0e30db0"  # deposit()
_WITHDRAW_SELECTOR = "0x2e1a7d4d"  # withdraw(uint256)

_ZERO_ADDR = "0x" + "0" * 40


class WrapRecognizer:
    """Detects WLEMX wrap and unwrap operations in a transaction bundle.

    Wrap:   native LEMX → WLEMX  (tx.to == WLEMX, input[:10] == deposit selector)
    Unwrap: WLEMX → native LEMX  (tx.to == WLEMX, input[:10] == withdraw selector)

    Both are tax-neutral relocations (no gain/loss); classified WRAP/UNWRAP.
    Anchor address resolved from token_registry(chain, symbol='WLEMX') at runtime.
    """

    def recognize(
        self,
        bundle: TxBundle,
        ctx: WalletContext,
        claims: ClaimSet,
    ) -> list[ClassifiedEvent]:
        from lemon_ledger.classify.types import ClassifiedEvent

        if bundle.envelope is None:
            return []

        # Resolve WLEMX contract address from token_registry
        wlemx_row = ctx.registry_by_symbol("WLEMX")
        if wlemx_row is None or not wlemx_row.contract_address:
            return []

        wlemx_addr = wlemx_row.contract_address.lower()
        tx_to = bundle.envelope.raw.get("to", "").lower()
        if tx_to != wlemx_addr:
            return []

        tx_input = bundle.envelope.raw.get("input", "")
        selector = tx_input[:10].lower() if len(tx_input) >= 10 else ""

        if selector == _DEPOSIT_SELECTOR:
            kind = ClassificationKind.WRAP
            native_wei = int(bundle.envelope.raw.get("value", 0))
            if native_wei <= 0:
                return []
            amount = Decimal(native_wei).scaleb(-18)
            # Claim the matching WLEMX ERC-20 Transfer (from 0x0 to wallet)
            for t in bundle.transfers:
                if claims.has(t):
                    continue
                if t.contract_address.lower() != wlemx_addr:
                    continue
                if t.raw.get("from", "").lower() != _ZERO_ADDR:
                    continue
                if t.raw.get("to", "").lower() != ctx.wallet_address:
                    continue
                claims.add(t)

            return [
                ClassifiedEvent(
                    classification=kind,
                    contract_address=wlemx_addr,
                    token_id=None,
                    amount=amount,
                    value_usd_at_event=None,
                    needs_review=False,
                    notes="wrap: native LEMX → WLEMX; tax-neutral",
                    _order_hint=-1,
                )
            ]

        if selector == _WITHDRAW_SELECTOR:
            kind = ClassificationKind.UNWRAP
            # Amount is in the calldata; approximate from Transfer value
            wlemx_out: Decimal | None = None
            for t in bundle.transfers:
                if claims.has(t):
                    continue
                if t.contract_address.lower() != wlemx_addr:
                    continue
                if t.raw.get("from", "").lower() != ctx.wallet_address:
                    continue
                if t.raw.get("to", "").lower() != _ZERO_ADDR:
                    continue
                wlemx_out = Decimal(str(t.value)).scaleb(-18)
                claims.add(t)
                break

            if wlemx_out is None:
                return []

            return [
                ClassifiedEvent(
                    classification=kind,
                    contract_address=wlemx_addr,
                    token_id=None,
                    amount=wlemx_out,
                    value_usd_at_event=None,
                    needs_review=False,
                    notes="unwrap: WLEMX → native LEMX; tax-neutral",
                    _order_hint=-1,
                )
            ]

        return []


class SwapCreditRedemptionRecognizer:
    """Detects SCDT NFT redemption → L2 NFT acquisition in one transaction.

    Pattern:
      - SCDT ERC-721 outflow from wallet (any quantity)
      - L2 NFT ERC-721 inflow to wallet from a known L2 NFT contract

    Both sides are classified SWAP_CREDIT_REDEMPTION with FMV from the SCDT
    NFT at event time. Mismatch (unequal counts, no FMV) → PENDING.

    Anchor address resolved from token_registry(chain, symbol='SCDT') at runtime.
    """

    def recognize(
        self,
        bundle: TxBundle,
        ctx: WalletContext,
        claims: ClaimSet,
    ) -> list[ClassifiedEvent]:
        from lemon_ledger.classify.types import ClassifiedEvent

        # Resolve SCDT contract address on this chain
        scdt_row = ctx.registry_by_symbol("SCDT")
        if scdt_row is None or not scdt_row.contract_address:
            return []

        scdt_addr = scdt_row.contract_address.lower()

        # Find SCDT ERC-721 outflows from wallet
        scdt_out: list[RawTokenTransfer] = []
        for t in bundle.transfers:
            if claims.has(t):
                continue
            if t.contract_address.lower() != scdt_addr:
                continue
            if "tokenID" not in t.raw:
                continue
            if t.raw.get("from", "").lower() != ctx.wallet_address:
                continue
            scdt_out.append(t)

        if not scdt_out:
            return []

        # Find L2 NFT inflows to wallet in the same tx
        nft_in: list[RawTokenTransfer] = []
        for t in bundle.transfers:
            if claims.has(t):
                continue
            if "tokenID" not in t.raw:
                continue
            if t.raw.get("to", "").lower() != ctx.wallet_address:
                continue
            if t.contract_address.lower() == scdt_addr:
                continue
            nft_in.append(t)

        if len(nft_in) != len(scdt_out):
            # Mismatch — can't pair; leave unclaimed for common layer
            log.warning(
                "classify: SCDT redemption count mismatch",
                extra={
                    "tx_hash": bundle.tx_hash,
                    "scdt_out": len(scdt_out),
                    "nft_in": len(nft_in),
                },
            )
            return []

        # Price SCDT NFT at event time via PricingService
        scdt_fmv: Decimal | None = None
        if scdt_row.token_id:
            scdt_fmv = ctx.pricing.get_historical_price(
                str(bundle.chain), scdt_row.token_id, bundle.occurred_at.timestamp()
            )

        events: list[ClassifiedEvent] = []

        for t in scdt_out:
            claims.add(t)
            events.append(
                ClassifiedEvent(
                    classification=ClassificationKind.SWAP_CREDIT_REDEMPTION,
                    contract_address=t.contract_address,
                    token_id=None,
                    amount=Decimal(1),
                    value_usd_at_event=scdt_fmv,
                    needs_review=scdt_fmv is None,
                    notes="scdt-out: SCDT NFT redeemed",
                    _order_hint=t.log_index,
                )
            )

        for t in nft_in:
            claims.add(t)
            events.append(
                ClassifiedEvent(
                    classification=ClassificationKind.SWAP_CREDIT_REDEMPTION,
                    contract_address=t.contract_address,
                    token_id=None,
                    amount=Decimal(1),
                    value_usd_at_event=scdt_fmv,
                    needs_review=scdt_fmv is None,
                    notes="l2-nft-in: acquired via SCDT redemption",
                    _order_hint=t.log_index,
                )
            )

        return events
