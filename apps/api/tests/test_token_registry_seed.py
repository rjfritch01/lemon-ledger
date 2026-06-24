"""Offline validation tests for the seed_lemonchain_tier1 Alembic data migration.

Cross-checks the seeded token_registry rows against Appendix B of the SOW.
All tests run against a real Postgres via Testcontainers; no network calls are made.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lemon_ledger.models.token_registry import TokenRegistry

_SOW_PATH = (
    Path(__file__).parent.parent.parent.parent / "docs" / "reference" / "lemon-ledger-sow.md"
)

_CHAIN = "lemonchain"
_NFT_SYMBOLS = frozenset({"LQST", "SCDT"})


def _parse_sow_lemonchain_contracts() -> dict[str, str]:
    """Return {symbol: lowercased_address} for the 21 ERC-20 rows in Appendix B.

    Skips LEMX (native, no valid 40-hex address) and the 2 NFT collections.
    """
    text = _SOW_PATH.read_text()
    lc_section = re.search(r"### Lemonchain mainnet.*?\n(.*?)### BSC chain", text, re.DOTALL)
    if not lc_section:
        raise ValueError("Lemonchain mainnet section not found in SOW Appendix B")
    rows = re.findall(
        r"\|\s*(\w+)\s*\|[^|]+\|\s*`(0x[0-9a-fA-F]{40})`\s*\|",
        lc_section.group(1),
    )
    return {sym: addr.lower() for sym, addr in rows if sym not in _NFT_SYMBOLS}


async def test_tier1_row_count(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        select(func.count())
        .select_from(TokenRegistry)
        .where(TokenRegistry.chain == _CHAIN, TokenRegistry.tier == 1)
    )
    assert result.scalar_one() == 22


async def test_native_lemx_seeded(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        select(TokenRegistry).where(
            TokenRegistry.chain == _CHAIN,
            TokenRegistry.symbol == "LEMX",
            TokenRegistry.contract_address == "0x0000000000000000000000000000000000000000",
        )
    )
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.category == "ecosystem-native"
    assert row.tier == 1
    assert row.max_supply == Decimal("50000000")
    assert isinstance(row.max_supply, Decimal)


async def test_contract_addresses_match_sow(db_session: AsyncSession) -> None:
    sow_contracts = _parse_sow_lemonchain_contracts()
    assert len(sow_contracts) == 21, f"Expected 21 SOW ERC-20 rows, got {len(sow_contracts)}"

    result = await db_session.execute(
        select(TokenRegistry).where(
            TokenRegistry.chain == _CHAIN,
            TokenRegistry.tier == 1,
            TokenRegistry.symbol.in_(list(sow_contracts)),
        )
    )
    seeded: dict[str, str | None] = {
        row.symbol: row.contract_address for row in result.scalars().all()
    }
    for sym, expected_addr in sow_contracts.items():
        assert sym in seeded, f"Token {sym!r} not found in seed"
        assert seeded[sym] == expected_addr, f"{sym}: seeded={seeded[sym]!r} sow={expected_addr!r}"


async def test_all_addresses_lowercase(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        select(TokenRegistry).where(
            TokenRegistry.chain == _CHAIN,
            TokenRegistry.tier == 1,
            TokenRegistry.contract_address.isnot(None),
        )
    )
    for row in result.scalars().all():
        assert row.contract_address == row.contract_address.lower(), (  # type: ignore[union-attr]
            f"{row.symbol} contract_address is not lowercase: {row.contract_address!r}"
        )


async def test_all_decimals_are_18(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        select(TokenRegistry).where(TokenRegistry.chain == _CHAIN, TokenRegistry.tier == 1)
    )
    for row in result.scalars().all():
        assert row.decimals == 18, f"{row.symbol} has unexpected decimals={row.decimals}"


async def test_lmln_is_deflationary(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        select(TokenRegistry).where(TokenRegistry.chain == _CHAIN, TokenRegistry.symbol == "LMLN")
    )
    lmln = result.scalar_one()
    assert lmln.is_deflationary is True
    assert lmln.max_supply == Decimal("182700000000")
    assert isinstance(lmln.max_supply, Decimal)


async def test_wlemx_references_lemx(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        select(TokenRegistry).where(TokenRegistry.chain == _CHAIN, TokenRegistry.symbol == "WLEMX")
    )
    wlemx = result.scalar_one()
    assert wlemx.project_metadata.get("wraps") == "LEMX"


async def test_no_float_max_supply(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        select(TokenRegistry).where(
            TokenRegistry.chain == _CHAIN,
            TokenRegistry.tier == 1,
            TokenRegistry.max_supply.isnot(None),
        )
    )
    for row in result.scalars().all():
        assert isinstance(row.max_supply, Decimal), (
            f"{row.symbol} max_supply is {type(row.max_supply)!r}, expected Decimal"
        )
        assert not isinstance(row.max_supply, float), f"{row.symbol} max_supply is a float"


async def test_nft_symbols_seeded_by_1_6(db_session: AsyncSession) -> None:
    """1.6 migration adds LQST/SCDT as tier-2 ecosystem-l2 entries on Lemonchain."""
    result = await db_session.execute(
        select(func.count())
        .select_from(TokenRegistry)
        .where(
            TokenRegistry.chain == _CHAIN,
            TokenRegistry.symbol.in_(list(_NFT_SYMBOLS)),
        )
    )
    assert result.scalar_one() == 2, "LQST/SCDT must be seeded by the 1.6 migration"


@pytest.mark.parametrize(
    "symbol,category",
    [
        ("LEMX", "ecosystem-native"),
        ("LUSD", "ecosystem-stablecoin"),
        ("WLEMX", "ecosystem-l2"),
        ("LFLX", "ecosystem-l2"),
        ("LMLN", "ecosystem-l2"),
    ],
)
async def test_spot_check_categories(db_session: AsyncSession, symbol: str, category: str) -> None:
    result = await db_session.execute(
        select(TokenRegistry).where(TokenRegistry.chain == _CHAIN, TokenRegistry.symbol == symbol)
    )
    row = result.scalar_one()
    assert row.category == category, f"{symbol}: expected {category!r}, got {row.category!r}"
