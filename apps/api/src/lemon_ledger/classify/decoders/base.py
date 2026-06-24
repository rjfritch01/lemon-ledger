"""L2Decoder base class.

Template-method engine for per-L2 token classification.  Four detectors run in
dependency order:

  reward    → bootstraps staking_contract discovery (Option C)
  stake     → discovers nft_contract from the NFT's contract address
  unstake   → mirrors stake (inbound NFT from staking_contract)
  mint      → needs nft_contract known; otherwise defers to common layer

Subclasses register automatically via __init_subclass__ keyed on `symbol`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from lemon_ledger.models.enums import ClassificationKind
from lemon_ledger.pricing.units import from_token_units

if TYPE_CHECKING:
    from lemon_ledger.classify.context import WalletContext
    from lemon_ledger.classify.types import ClaimSet, ClassifiedEvent, TxBundle
    from lemon_ledger.models.raw import RawTokenTransfer

log = logging.getLogger(__name__)

ZERO_ADDR = "0x" + "0" * 40

# ABI keccak selectors (4 bytes) for ERC-20 / ERC-721 Transfer events.
# We don't need them for detection since the raw rows carry typed data,
# but they're referenced in comments for auditability.
_ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class L2Decoder:
    """Base class for per-L2 token decoders.

    Subclasses set `symbol` and are auto-registered on definition.
    """

    _registry: ClassVar[dict[str, type[L2Decoder]]] = {}
    symbol: ClassVar[str] = ""

    def __init_subclass__(cls, **kw: object) -> None:
        super().__init_subclass__(**kw)
        sym = getattr(cls, "symbol", "")
        if sym:
            L2Decoder._registry[sym] = cls
        # Also register by class name so context.py can look up via
        # l2_decoder_config.decoder_class (e.g. "LflxDecoder").
        L2Decoder._registry[cls.__name__] = cls

    def __init__(self, token_id: uuid.UUID) -> None:
        self.token_id = token_id

    @classmethod
    def for_symbol(cls, symbol: str) -> type[L2Decoder]:
        return cls._registry[symbol]

    # ── detector pipeline ─────────────────────────────────────────────────────

    def decode(
        self,
        b: TxBundle,
        ctx: WalletContext,
        claims: ClaimSet,
    ) -> list[ClassifiedEvent]:
        out: list[ClassifiedEvent] = []
        out += list(self._detect_reward(b, ctx, claims))
        out += list(self._detect_stake(b, ctx, claims))
        out += list(self._detect_unstake(b, ctx, claims))
        out += list(self._detect_mint(b, ctx, claims))
        return out

    # ── reward (bootstraps staking discovery) ─────────────────────────────────

    def _detect_reward(
        self,
        b: TxBundle,
        ctx: WalletContext,
        claims: ClaimSet,
    ) -> Iterator[ClassifiedEvent]:
        from lemon_ledger.classify.types import ClassifiedEvent

        cfg = ctx.config_for(self.token_id)
        for t in b.transfers:
            if claims.has(t):
                continue
            if not self._is_erc20_of(t, ctx):
                continue
            if t.raw.get("to", "").lower() != ctx.wallet_address:
                continue
            sender = t.raw.get("from", "").lower()
            # Skip: minted-to-user (from ZERO_ADDR) or self-transfer
            if sender == ZERO_ADDR or ctx.is_tracked_wallet(sender):
                continue

            review = False
            if not (cfg and cfg.staking_contract and cfg.staking_contract == sender):
                # Option-C: propose the sender as the staking contract
                ctx.propose_staking_contract(self.token_id, sender)
                review = True  # unconfirmed sender → flag for human review

            if cfg and cfg.distribution_complete:
                review = True  # reward after the supply cap is anomalous

            amount = self._to_decimal(str(t.value), self._decimals(ctx))
            # FMV via PricingService: already the tax-safe path (no stale LKG,
            # returns None for same-day rewards before daily average finalizes).
            fmv = ctx.pricing.get_historical_price(
                str(b.chain), str(self.token_id), b.occurred_at.timestamp()
            )
            claims.add(t)
            yield ClassifiedEvent(
                classification=ClassificationKind.REWARD,
                contract_address=t.contract_address,
                token_id=self.token_id,
                amount=amount,
                value_usd_at_event=fmv,
                needs_review=review or fmv is None,
                _order_hint=t.log_index,
            )

    # ── stake / unstake (ERC-721 to/from staking contract) ───────────────────

    def _detect_stake(
        self,
        b: TxBundle,
        ctx: WalletContext,
        claims: ClaimSet,
    ) -> Iterator[ClassifiedEvent]:
        from lemon_ledger.classify.types import ClassifiedEvent

        cfg = ctx.config_for(self.token_id)
        for t in b.transfers:
            if claims.has(t):
                continue
            if not self._is_nft_of(t, ctx):
                continue
            if t.raw.get("from", "").lower() != ctx.wallet_address:
                continue
            to_addr = t.raw.get("to", "").lower()
            if ctx.is_tracked_wallet(to_addr):
                continue

            # Discover staking contract from the NFT destination
            if not (cfg and cfg.staking_contract):
                ctx.propose_staking_contract(self.token_id, to_addr)
                cfg = ctx.config_for(self.token_id)  # reload after write-back

            # Discover NFT contract from the transfer's contract_address
            if not (cfg and cfg.nft_contract):
                ctx.propose_nft_contract(self.token_id, t.contract_address)

            claims.add(t)
            yield ClassifiedEvent(
                classification=ClassificationKind.STAKE,
                contract_address=t.contract_address,
                token_id=self.token_id,
                amount=Decimal(1),
                value_usd_at_event=None,
                _order_hint=t.log_index,
            )

    def _detect_unstake(
        self,
        b: TxBundle,
        ctx: WalletContext,
        claims: ClaimSet,
    ) -> Iterator[ClassifiedEvent]:
        from lemon_ledger.classify.types import ClassifiedEvent

        cfg = ctx.config_for(self.token_id)
        for t in b.transfers:
            if claims.has(t):
                continue
            if not self._is_nft_of(t, ctx):
                continue
            if t.raw.get("to", "").lower() != ctx.wallet_address:
                continue
            sender = t.raw.get("from", "").lower()
            # Only claim if the sender is the known/discovered staking contract
            if not (cfg and cfg.staking_contract and cfg.staking_contract == sender):
                continue

            claims.add(t)
            yield ClassifiedEvent(
                classification=ClassificationKind.UNSTAKE,
                contract_address=t.contract_address,
                token_id=self.token_id,
                amount=Decimal(1),
                value_usd_at_event=None,
                _order_hint=t.log_index,
            )

    # ── mint (needs nft_contract known) ───────────────────────────────────────

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
            # Cold start: nft_contract unknown → defer to common layer (transfer-in)
            return

        for t in b.transfers:
            if claims.has(t):
                continue
            if not self._is_nft_of(t, ctx):
                continue
            if t.contract_address.lower() != cfg.nft_contract.lower():
                continue
            if t.raw.get("from", "").lower() != ZERO_ADDR:
                continue
            if t.raw.get("to", "").lower() != ctx.wallet_address:
                continue

            # Mint-fee USD: price the LEMX fee via PricingService for the LEMX
            # token (zero address on Lemonchain, priced via CoinGecko lemon-2).
            mint_fee_usd = self._mint_fee_usd(b, ctx)
            claims.add(t)
            yield ClassifiedEvent(
                classification=ClassificationKind.MINT,
                contract_address=t.contract_address,
                token_id=self.token_id,
                amount=Decimal(1),
                value_usd_at_event=mint_fee_usd,
                needs_review=mint_fee_usd is None,
                _order_hint=t.log_index,
            )

    # ── template hooks ────────────────────────────────────────────────────────

    def mint_fee(self, b: TxBundle, ctx: WalletContext) -> Decimal:
        """Return the raw LEMX wei amount paid as mint fee."""
        cfg = ctx.config_for(self.token_id)
        if cfg and cfg.mint_fee_wei is not None:
            return Decimal(cfg.mint_fee_wei)
        return self._derive_native_fee(b)

    def _mint_fee_usd(self, b: TxBundle, ctx: WalletContext) -> Decimal | None:
        """Convert the LEMX mint fee to USD using PricingService (TAX path)."""
        fee_wei = self.mint_fee(b, ctx)
        if fee_wei == 0:
            return Decimal(0)
        # Resolve LEMX token_id from the registry (zero address on Lemonchain).
        lemx_row = ctx.registry_by_address(ZERO_ADDR)
        if lemx_row is None:
            return None
        lemx_price = ctx.pricing.get_historical_price(
            str(b.chain), lemx_row.token_id, b.occurred_at.timestamp()
        )
        if lemx_price is None:
            return None
        fee_lemx = from_token_units(int(fee_wei), lemx_row.decimals)
        return fee_lemx * lemx_price

    def matches_asset(self, address: str, ctx: WalletContext) -> bool:
        """True if *address* is any known address for this decoder's token."""
        cfg = ctx.config_for(self.token_id)
        known: set[str] = {self._erc20_address(ctx)}
        if cfg:
            for a in (cfg.nft_contract, cfg.staking_contract, cfg.mint_contract):
                if a:
                    known.add(a.lower())
        return address.lower() in known

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_decimal(raw_value: str, decimals: int) -> Decimal:
        return from_token_units(int(raw_value), decimals)

    def _is_erc20_of(self, transfer: RawTokenTransfer, ctx: WalletContext) -> bool:
        """True if *transfer* is an ERC-20 transfer for this decoder's token.

        ERC-20: has a numeric value field, no tokenID in raw, contract matches.
        """
        if transfer.contract_address.lower() != self._erc20_address(ctx):
            return False
        return "tokenID" not in transfer.raw

    def _is_nft_of(self, transfer: RawTokenTransfer, ctx: WalletContext) -> bool:
        """True if *transfer* is an ERC-721 transfer for this decoder's token.

        ERC-721: tokenID present in raw JSONB (set by Blockscout tokennfttx).
        Contract must match nft_contract if known; otherwise accept any NFT
        with a matching tokenID (the staking detector uses counterparty alone).
        """
        if "tokenID" not in transfer.raw:
            return False
        cfg = ctx.config_for(self.token_id)
        if cfg and cfg.nft_contract:
            return transfer.contract_address.lower() == cfg.nft_contract.lower()
        # nft_contract unknown: match any NFT (stake discovery)
        # Guard: symbol in project_metadata or token name prefix as heuristic.
        # At this stage we rely on the orchestrator calling us only when the
        # bundle address matches — so returning True here is safe.
        return True

    def _decimals(self, ctx: WalletContext) -> int:
        tr = ctx.registry_by_id(str(self.token_id))
        return tr.decimals if tr else 18

    def _erc20_address(self, ctx: WalletContext) -> str:
        tr = ctx.registry_by_id(str(self.token_id))
        if tr and tr.contract_address:
            return tr.contract_address.lower()
        return ""

    def _derive_native_fee(self, b: TxBundle) -> Decimal:
        """Sum of outbound native value in the tx envelope (mint fee proxy)."""
        if b.envelope is None:
            return Decimal(0)
        gas_used = int(b.envelope.raw.get("gasUsed", 0))
        gas_price = int(b.envelope.raw.get("gasPrice", 0))
        native_out = int(b.envelope.raw.get("value", 0))
        return Decimal(native_out + gas_used * gas_price)
