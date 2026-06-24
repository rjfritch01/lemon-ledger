"""Lemonchain-only L2 decoder subclasses.

Nine thin stubs that inherit all base detectors (reward/stake/unstake/mint).
LBST is BSC-only (parked — config-only, no tests).
LQST is mint-only: overrides reward/stake/unstake to no-ops; custom mint fee
resolution (native LEMX in tx.value → ERC-20 fee transfer → internal tx).
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import TYPE_CHECKING

from lemon_ledger.classify.decoders.base import ZERO_ADDR, L2Decoder
from lemon_ledger.models.enums import ClassificationKind

if TYPE_CHECKING:
    from lemon_ledger.classify.context import WalletContext
    from lemon_ledger.classify.types import ClaimSet, ClassifiedEvent, TxBundle


# ── 9 Lemonchain-only thin stubs ─────────────────────────────────────────────


class HxdxDecoder(L2Decoder):
    symbol = "HXDX"


class HxbtDecoder(L2Decoder):
    symbol = "HXBT"


class MhsaDecoder(L2Decoder):
    symbol = "MHSA"


class NxysDecoder(L2Decoder):
    symbol = "NXYS"


class PupDecoder(L2Decoder):
    symbol = "PUP"


class RmcDecoder(L2Decoder):
    symbol = "RMC"


class SmartDecoder(L2Decoder):
    symbol = "SMART"


class SthDecoder(L2Decoder):
    symbol = "STH"


class TixaDecoder(L2Decoder):
    symbol = "TIXA"


# ── LBST — BSC-only, parked (config row exists; stub triggers registration) ──


class LbstDecoder(L2Decoder):
    """LemBoost (BSC-only). Parked: no active detection logic in 1.6."""

    symbol = "LBST"


# ── LQST — LemQuest mint-only NFT ────────────────────────────────────────────


class LqstDecoder(L2Decoder):
    """LemQuest mint-only decoder.

    LemQuest NFTs are user-mintable collectibles. There is no staking or reward
    mechanism. Cost basis = mint_fee (native LEMX in tx.value, ERC-20 LEMX fee
    transfer, or internal tx forward) + gas.

    If fee cannot be resolved from any source → PENDING, never $0 basis.

    FLAG(1.7): Collectible asset_class carried in notes until tax_lots.asset_class
    is available.
    """

    symbol = "LQST"

    # ── no-op overrides for unused detectors ─────────────────────────────────

    def _detect_reward(
        self, b: TxBundle, ctx: WalletContext, claims: ClaimSet
    ) -> Iterator[ClassifiedEvent]:
        return iter([])

    def _detect_stake(
        self, b: TxBundle, ctx: WalletContext, claims: ClaimSet
    ) -> Iterator[ClassifiedEvent]:
        return iter([])

    def _detect_unstake(
        self, b: TxBundle, ctx: WalletContext, claims: ClaimSet
    ) -> Iterator[ClassifiedEvent]:
        return iter([])

    # ── mint-only detection ───────────────────────────────────────────────────

    def _detect_mint(
        self,
        b: TxBundle,
        ctx: WalletContext,
        claims: ClaimSet,
    ) -> Iterator[ClassifiedEvent]:
        from lemon_ledger.classify.types import ClassifiedEvent

        cfg = ctx.config_for(self.token_id)
        if not (
            cfg and cfg.nft_contract and cfg.nft_contract_status in ("discovered", "confirmed")
        ):
            return

        for t in b.transfers:
            if claims.has(t):
                continue
            if "tokenID" not in t.raw:
                continue
            if t.contract_address.lower() != cfg.nft_contract.lower():
                continue
            if t.raw.get("from", "").lower() != ZERO_ADDR:
                continue
            if t.raw.get("to", "").lower() != ctx.wallet_address:
                continue

            # Resolve mint fee from 3 sources in priority order.
            mint_fee_wei = self._resolve_lqst_fee(b, ctx)
            if mint_fee_wei is None:
                # Unresolved fee → never $0; flag pending
                claims.add(t)
                yield ClassifiedEvent(
                    classification=ClassificationKind.PENDING,
                    contract_address=t.contract_address,
                    token_id=self.token_id,
                    amount=Decimal(1),
                    value_usd_at_event=None,
                    needs_review=True,
                    notes="lqst-mint: fee unresolved; basis pending operator input",
                    _order_hint=t.raw.get("logIndex", t.log_index),
                )
                continue

            mint_fee_usd = self._fee_wei_to_usd(mint_fee_wei, b, ctx)
            claims.add(t)
            yield ClassifiedEvent(
                classification=ClassificationKind.MINT,
                contract_address=t.contract_address,
                token_id=self.token_id,
                amount=Decimal(1),
                value_usd_at_event=mint_fee_usd,
                needs_review=mint_fee_usd is None,
                # FLAG(1.7): collectible for 28% capital gains rate;
                # use tax_lots.asset_class when that column lands.
                notes="collectible; basis=fee+gas; see 1.7 tax_lots.asset_class",
                _order_hint=t.raw.get("logIndex", t.log_index),
            )

    def _resolve_lqst_fee(self, b: TxBundle, ctx: WalletContext) -> Decimal | None:
        """Return raw LEMX wei for the mint fee, or None if unresolvable.

        Priority:
          1. Native LEMX in tx.value (envelope)
          2. ERC-20 LEMX outflow from wallet in the same tx
          3. Internal tx native outflow from wallet
        """
        # Priority 1: native LEMX in envelope.value
        if b.envelope:
            native_val = int(b.envelope.raw.get("value", 0))
            if native_val > 0:
                return Decimal(native_val)

        # Priority 2: ERC-20 LEMX outflow from wallet (LEMX = zero address on LC)
        lemx_row = ctx.registry_by_address(ZERO_ADDR)
        if lemx_row and lemx_row.contract_address:
            lemx_addr = lemx_row.contract_address.lower()
            for t in b.transfers:
                if t.contract_address.lower() != lemx_addr:
                    continue
                if t.raw.get("from", "").lower() != ctx.wallet_address:
                    continue
                if "tokenID" in t.raw:
                    continue
                fee_raw = int(str(t.value))
                if fee_raw > 0:
                    return Decimal(fee_raw)

        # Priority 3: internal tx outflow from wallet
        for itx in b.internals:
            if itx.raw.get("from", "").lower() != ctx.wallet_address:
                continue
            itx_val = int(itx.raw.get("value", 0))
            if itx_val > 0:
                return Decimal(itx_val)

        return None

    def _fee_wei_to_usd(self, fee_wei: Decimal, b: TxBundle, ctx: WalletContext) -> Decimal | None:
        """Convert raw LEMX wei fee to USD using PricingService (tax path)."""
        from lemon_ledger.pricing.units import from_token_units

        lemx_row = ctx.registry_by_address(ZERO_ADDR)
        if lemx_row is None:
            return None
        price = ctx.pricing.get_historical_price(
            str(b.chain), lemx_row.token_id, b.occurred_at.timestamp()
        )
        if price is None:
            return None
        fee_lemx = from_token_units(int(fee_wei), lemx_row.decimals)
        return fee_lemx * price
