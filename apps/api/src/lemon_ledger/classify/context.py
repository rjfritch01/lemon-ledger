"""WalletContext — per-task read/write surface for decoders.

Constructed once per classify_wallet task run. Provides:
  - Token registry lookups (by id and by address)
  - L2DecoderConfig access (cached per token_id)
  - Option-C write-back: propose_staking_contract / propose_nft_contract
  - is_tracked_wallet: whether an address belongs to the same user
  - decoders_for_bundle: which L2Decoder instances are relevant for a TxBundle
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from lemon_ledger.models.classified import L2DecoderConfig
from lemon_ledger.models.wallet import Wallet

if TYPE_CHECKING:
    from lemon_ledger.classify.decoders.base import L2Decoder
    from lemon_ledger.classify.types import TxBundle
    from lemon_ledger.pricing.service import PricingService
    from lemon_ledger.pricing.types import TokenRow

log = logging.getLogger(__name__)


class WalletContext:
    """Read/write context for one classify_wallet task run."""

    def __init__(
        self,
        *,
        wallet: Wallet,
        user_wallet_addresses: set[str],
        session: Session,
        pricing: PricingService,
    ) -> None:
        self.wallet = wallet
        self.wallet_address = wallet.address.lower()
        self._user_addrs = {a.lower() for a in user_wallet_addresses}
        self._session = session
        self.pricing = pricing
        # Config cache: token_id → L2DecoderConfig
        self._configs: dict[uuid.UUID, L2DecoderConfig] = {}
        # Address → decoder list cache (rebuilt when configs change)
        self._addr_decoder_cache: dict[str, list[L2Decoder]] | None = None

    # ── registry (read-only) ───────────────────────────────────────────────────

    def registry_by_address(self, addr: str) -> TokenRow | None:
        """Look up a TokenRow by contract address on this wallet's chain."""
        from lemon_ledger.models.token_registry import TokenRegistry
        from lemon_ledger.pricing.types import TokenRow as _TR  # noqa: F401

        row = (
            self._session.query(TokenRegistry)
            .filter_by(chain=self.wallet.chain, contract_address=addr.lower())
            .first()
        )
        if row is None:
            return None
        from lemon_ledger.pricing.types import TokenRow

        return TokenRow(
            token_id=str(row.id),
            symbol=row.symbol,
            category=row.category,
            contract_address=row.contract_address,
            chain=row.chain,
            tier=row.tier,
            decimals=row.decimals,
        )

    def registry_by_id(self, token_id: str) -> TokenRow | None:
        from lemon_ledger.models.token_registry import TokenRegistry
        from lemon_ledger.pricing.types import TokenRow

        row = self._session.get(TokenRegistry, uuid.UUID(token_id))
        if row is None:
            return None
        return TokenRow(
            token_id=str(row.id),
            symbol=row.symbol,
            category=row.category,
            contract_address=row.contract_address,
            chain=row.chain,
            tier=row.tier,
            decimals=row.decimals,
        )

    # ── config ────────────────────────────────────────────────────────────────

    def config_for(self, token_id: uuid.UUID) -> L2DecoderConfig | None:
        if token_id not in self._configs:
            cfg = self._session.query(L2DecoderConfig).filter_by(token_id=token_id).first()
            if cfg is not None:
                self._configs[token_id] = cfg
            else:
                return None
        return self._configs.get(token_id)

    # ── decoder dispatch ──────────────────────────────────────────────────────

    def decoders_for_bundle(self, bundle: TxBundle) -> list[L2Decoder]:
        """Return L2Decoder instances whose known addresses appear in the bundle."""
        from lemon_ledger.classify.decoders.base import L2Decoder

        # Collect all contract addresses referenced in this bundle
        bundle_addrs: set[str] = set()
        for t in bundle.transfers:
            bundle_addrs.add(t.contract_address.lower())
        if bundle.envelope:
            bundle_addrs.add(bundle.envelope.raw.get("to", "").lower())
            bundle_addrs.add(bundle.envelope.raw.get("contractAddress", "").lower())

        # Load all L2DecoderConfig rows for this chain (once)
        all_cfgs = self._session.query(L2DecoderConfig).filter_by(chain=self.wallet.chain).all()

        decoders: list[L2Decoder] = []
        seen: set[str] = set()
        for cfg in all_cfgs:
            # Known addresses for this decoder
            tr_row = self.registry_by_id(str(cfg.token_id))
            known: set[str] = set()
            if tr_row and tr_row.contract_address:
                known.add(tr_row.contract_address.lower())
            for addr in (cfg.nft_contract, cfg.staking_contract, cfg.mint_contract):
                if addr:
                    known.add(addr.lower())

            if known & bundle_addrs:
                cls_name = cfg.decoder_class
                if cls_name not in seen and cls_name in L2Decoder._registry:
                    cls = L2Decoder._registry[cls_name]
                    decoders.append(cls(cfg.token_id))
                    seen.add(cls_name)

        return decoders

    # ── Option-C write-backs ──────────────────────────────────────────────────

    def propose_staking_contract(self, token_id: uuid.UUID, addr: str) -> None:
        """Set staking_contract to *addr* if not already set; mark discovered."""
        cfg = self.config_for(token_id)
        if cfg is None:
            return
        addr = addr.lower()
        if not cfg.staking_contract:
            cfg.staking_contract = addr
            cfg.staking_contract_status = "discovered"
            self._session.add(cfg)
            log.info(
                "classify: staking_contract discovered",
                extra={"token_id": str(token_id), "addr": addr},
            )
            # Invalidate decoder address cache
            self._addr_decoder_cache = None
        elif cfg.staking_contract != addr:
            log.warning(
                "classify: conflicting staking_contract proposal",
                extra={
                    "token_id": str(token_id),
                    "existing": cfg.staking_contract,
                    "proposed": addr,
                },
            )

    def propose_nft_contract(self, token_id: uuid.UUID, addr: str) -> None:
        """Set nft_contract to *addr* if not already set; mark discovered."""
        cfg = self.config_for(token_id)
        if cfg is None:
            return
        addr = addr.lower()
        if not cfg.nft_contract:
            cfg.nft_contract = addr
            cfg.nft_contract_status = "discovered"
            self._session.add(cfg)
            log.info(
                "classify: nft_contract discovered",
                extra={"token_id": str(token_id), "addr": addr},
            )
            self._addr_decoder_cache = None
        elif cfg.nft_contract != addr:
            log.warning(
                "classify: conflicting nft_contract proposal",
                extra={
                    "token_id": str(token_id),
                    "existing": cfg.nft_contract,
                    "proposed": addr,
                },
            )

    # ── helpers ───────────────────────────────────────────────────────────────

    def is_tracked_wallet(self, addr: str) -> bool:
        return addr.lower() in self._user_addrs
