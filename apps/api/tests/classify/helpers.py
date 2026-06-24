"""Shared builder helpers for classify unit tests.

Not a conftest — import directly: from tests.classify.helpers import ...
(tests/ is on sys.path via pytest rootdir, but we need an __init__.py at
 the test root level for this to resolve cleanly.  We add pythonpath = ["tests"]
 in a pytest.ini if needed, but simpler: use relative imports from each test.)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

from lemon_ledger.classify.context import WalletContext
from lemon_ledger.domain.chains import Chain
from lemon_ledger.models.classified import L2DecoderConfig
from lemon_ledger.pricing.types import TokenRow

WALLET_ADDR = "0xaaaa000000000000000000000000000000000001"
STAKING_ADDR = "0xbbbb000000000000000000000000000000000002"
NFT_ADDR = "0xcccc000000000000000000000000000000000003"
ERC20_ADDR = "0xdddd000000000000000000000000000000000004"
ZERO_ADDR = "0x" + "0" * 40

OCCURRED_AT = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
BLOCK = 10_000


def make_transfer(
    *,
    log_index: int,
    from_addr: str,
    to_addr: str,
    contract: str = ERC20_ADDR,
    value: int = 1_000_000_000_000_000_000,
    raw_extra: dict[str, Any] | None = None,
    wallet_id: uuid.UUID | None = None,
    tx_hash: str = "0xdeadbeef",
) -> Any:
    from lemon_ledger.models.raw import RawTokenTransfer

    obj = MagicMock(spec=RawTokenTransfer)
    obj.log_index = log_index
    obj.contract_address = contract
    obj.value = Decimal(value)
    obj.tx_hash = tx_hash
    obj.block_number = BLOCK
    obj.occurred_at = OCCURRED_AT
    obj.wallet_id = wallet_id or uuid.uuid4()
    obj.raw = {"from": from_addr, "to": to_addr, "value": str(value), "tokenDecimal": "18"}
    if raw_extra:
        obj.raw.update(raw_extra)
    return obj


def make_nft_transfer(
    *,
    log_index: int,
    from_addr: str,
    to_addr: str,
    contract: str = NFT_ADDR,
    token_id_on_chain: str = "42",
    wallet_id: uuid.UUID | None = None,
    tx_hash: str = "0xdeadbeef",
) -> Any:
    t = make_transfer(
        log_index=log_index,
        from_addr=from_addr,
        to_addr=to_addr,
        contract=contract,
        value=0,
        wallet_id=wallet_id,
        tx_hash=tx_hash,
    )
    t.raw["tokenID"] = token_id_on_chain
    return t


def make_bundle(
    wallet_id: uuid.UUID,
    *,
    transfers: list[Any] | None = None,
    internals: list[Any] | None = None,
) -> Any:
    from lemon_ledger.classify.types import TxBundle

    return TxBundle(
        wallet_id=wallet_id,
        chain=Chain.LEMONCHAIN,
        tx_hash="0xdeadbeef",
        block_number=BLOCK,
        occurred_at=OCCURRED_AT,
        envelope=None,
        transfers=transfers or [],
        internals=internals or [],
    )


def make_decoder_config(
    token_id: uuid.UUID,
    *,
    staking_contract: str | None = None,
    staking_contract_status: str = "unknown",
    nft_contract: str | None = None,
    nft_contract_status: str = "unknown",
    distribution_complete: bool = False,
    mint_fee_wei: Decimal | None = None,
    deflationary: bool = False,
    buy_burn_wallet: str | None = None,
) -> MagicMock:
    cfg = MagicMock(spec=L2DecoderConfig)
    cfg.token_id = token_id
    cfg.chain = "lemonchain"
    cfg.decoder_class = "LflxDecoder"
    cfg.staking_contract = staking_contract
    cfg.staking_contract_status = staking_contract_status
    cfg.nft_contract = nft_contract
    cfg.nft_contract_status = nft_contract_status
    cfg.distribution_complete = distribution_complete
    cfg.mint_fee_wei = mint_fee_wei
    cfg.mint_contract = None
    cfg.deflationary = deflationary
    cfg.buy_burn_wallet = buy_burn_wallet
    return cfg


def make_token_row(
    token_id: uuid.UUID,
    address: str = ERC20_ADDR,
    decimals: int = 18,
) -> TokenRow:
    return TokenRow(
        token_id=str(token_id),
        symbol="LFLX",
        category="l2_token",
        contract_address=address,
        chain="lemonchain",
        tier=2,
        decimals=decimals,
    )


def make_mock_ctx(
    wallet_id: uuid.UUID,
    *,
    cfg: Any = None,
    token_row: TokenRow | None = None,
    fmv: Decimal | None = Decimal("0.05"),
) -> MagicMock:
    ctx = MagicMock(spec=WalletContext)
    ctx.wallet_address = WALLET_ADDR
    ctx.wallet = MagicMock()
    ctx.wallet.chain = "lemonchain"
    ctx.pricing = MagicMock()
    ctx.pricing.get_historical_price.return_value = fmv
    ctx.config_for.return_value = cfg
    ctx.registry_by_id.return_value = token_row
    ctx.registry_by_address.return_value = None
    ctx.registry_by_symbol.return_value = None
    ctx.is_tracked_wallet.return_value = False
    ctx.is_confirmed_burn_address.return_value = False
    ctx.propose_staking_contract.return_value = None
    ctx.propose_nft_contract.return_value = None
    ctx.decoders_for_bundle.return_value = []
    return ctx
