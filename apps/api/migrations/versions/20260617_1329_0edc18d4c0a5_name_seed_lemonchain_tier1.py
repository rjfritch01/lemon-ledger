"""name=seed_lemonchain_tier1

Revision ID: 0edc18d4c0a5
Revises: 351ece0cc2cf
Create Date: 2026-06-17 13:29:27.615667

"""

from collections.abc import Sequence
from decimal import Decimal
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, insert

revision: str = "0edc18d4c0a5"
down_revision: str | None = "351ece0cc2cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen Core snapshot — do NOT import the ORM TokenRegistry model.
_token_registry = sa.Table(
    "token_registry",
    sa.MetaData(),
    sa.Column("id", sa.Uuid()),
    sa.Column("chain", sa.Text()),
    sa.Column("contract_address", sa.Text()),
    sa.Column("symbol", sa.Text()),
    sa.Column("name", sa.Text()),
    sa.Column("decimals", sa.SmallInteger()),
    sa.Column("tier", sa.SmallInteger()),
    sa.Column("category", sa.Text()),
    sa.Column("pricing_source_primary", sa.Text()),
    sa.Column("pricing_source_fallback", sa.Text()),
    sa.Column("is_deflationary", sa.Boolean()),
    sa.Column("max_supply", sa.Numeric()),
    sa.Column("project_metadata", JSONB()),
)

_CHAIN = "lemonchain"

# Columns: (symbol, name, contract_address, decimals, category,
#            is_deflationary, max_supply_whole_tokens, pricing_primary,
#            pricing_fallback, project_metadata)
# max_supply is in whole-token units; downstream multiplies by 10**decimals.
# None means no hard cap (floating / algorithmic supply).
_TIER1: list[tuple[str, str, str, int, str, bool, int | None, str | None, str | None, dict]] = [  # noqa: E501
    ("LEMX",  "Native Lemon",       "0x0000000000000000000000000000000000000000", 18, "ecosystem-native",     False,  50_000_000,       "oracle", "coingecko", {"native_gas": True}),
    ("WLEMX", "Wrapped LEMX",       "0x84862e65ebf37af91a8b85283b58505de3352588", 18, "ecosystem-l2",         False,  None,             "oracle", "coingecko", {"wraps": "LEMX"}),
    ("LUSD",  "LemonUSD",           "0x8de60f88f19dad42dde0d9ed2eeba68269722a99", 18, "ecosystem-stablecoin", False,  None,             "oracle", None,        {}),
    ("LFLX",  "LemFlix",            "0x1bacc825fcd91971e8daca3104370380b4a981be", 18, "ecosystem-l2",         False,  833_500_000,      "oracle", None,        {}),
    ("LBNK",  "LemonBank",          "0xc17ef640d7c34a8c684073d85d815539f66da3c7", 18, "ecosystem-l2",         False,  744_000_000,      "oracle", None,        {}),
    ("LPAY",  "LemPay",             "0x708cf95b67f3dfff16e1f48313425d0cfb629ee7", 18, "ecosystem-l2",         False,  735_000_000,      "oracle", None,        {}),
    ("LMED",  "LemCare",            "0xf489e786cf6242b3c32cfe5372453b37b8f0cc13", 18, "ecosystem-l2",         False,  725_000_000,      "oracle", None,        {}),
    ("CTFZ",  "Catfiz",             "0x83d4b4db63c40846735860ce3b2adf83aa9edc8e", 18, "ecosystem-l2",         False,  1_099_000_000,    "oracle", None,        {}),
    ("LTVL",  "LemTravel",          "0x02535cbc23c045134a481cf8b6a6645e7655efb8", 18, "ecosystem-l2",         False,  1_097_000_000,    "oracle", None,        {}),
    ("LLOT",  "LemLotto",           "0xc8fa8354d6c6856de3e3f7da89f0ce4636e51710", 18, "ecosystem-l2",         False,  709_000_000,      "oracle", None,        {}),
    ("LSQZ",  "LemSqueeze",         "0xce37edd204dedbc256a7f5d3622e82f5fc031cd8", 18, "ecosystem-l2",         False,  507_000_000,      "oracle", None,        {}),
    ("HXDX",  "HexDEX",             "0x59100856dfbbb5a10bdafc894b8f82c89a0adc34", 18, "ecosystem-l2",         False,  329_000_000,      "oracle", None,        {}),
    ("HXBT",  "Hexbit",             "0xc9fd20a101f01eac20e859645e91c9998aaa509b", 18, "ecosystem-l2",         False,  332_000_000,      "oracle", None,        {}),
    ("SMART", "DNSmart",            "0x38374f0527e3320058c96adcb57c6e78afe9447e", 18, "ecosystem-l2",         False,  333_000_000,      "oracle", None,        {}),
    ("RMC",   "RubicManagement",    "0x5d59ca7460b5e0c553e62b4b7b0197bf12ac1fb5", 18, "ecosystem-l2",         False,  335_000_000,      "oracle", None,        {}),
    ("MHSA",  "MotivHSA",           "0x8f9457a8de85876951b3ac2843c09997b951c267", 18, "ecosystem-l2",         False,  334_000_000,      "oracle", None,        {}),
    ("STH",   "StartHealth",        "0x3ed3bfbac6ece65468b37abb15091f346f1b8905", 18, "ecosystem-l2",         False,  335_000_000,      "oracle", None,        {}),
    ("NXYS",  "NXCPodcast",         "0x0f4bb028eaa7f0d0545ddd24600c524c3e044962", 18, "ecosystem-l2",         False,  329_000_000,      "oracle", None,        {}),
    ("TIXA",  "TixAccess",          "0xe2677da211265c092f1bc4f018798afbc20971dc", 18, "ecosystem-l2",         False,  328_000_000,      "oracle", None,        {}),
    ("PUP",   "WasatchPup",         "0xdd84a98f9f9e0be193bfd91c123254d835cb3b32", 18, "ecosystem-l2",         False,  320_000_000,      "oracle", None,        {}),
    ("LLUX",  "LemLux",             "0x71e3a635763910bccf5f979ebbf8c69cb9704db0", 18, "ecosystem-l2",         False,  172_000_000,      "oracle", None,        {}),
    ("LMLN",  "LemLoans",           "0x6cc7ee8f2f45782cbf376b4021d41960b814f321", 18, "ecosystem-l2",         True,   182_700_000_000,  "oracle", None,        {"deflationary_burn_bps": 1000}),
]


def upgrade() -> None:
    rows = [
        {
            "id": uuid4(),
            "chain": _CHAIN,
            "contract_address": addr,
            "symbol": sym,
            "name": name,
            "decimals": dec,
            "tier": 1,
            "category": cat,
            "is_deflationary": is_def,
            "max_supply": Decimal(str(max_sup)) if max_sup is not None else None,
            "pricing_source_primary": primary,
            "pricing_source_fallback": fallback,
            "project_metadata": meta,
        }
        for sym, name, addr, dec, cat, is_def, max_sup, primary, fallback, meta in _TIER1
    ]
    op.get_bind().execute(
        insert(_token_registry)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["chain", "contract_address"])
    )


def downgrade() -> None:
    addrs = [addr for _, _, addr, *_ in _TIER1]
    op.get_bind().execute(
        sa.delete(_token_registry).where(
            sa.and_(
                _token_registry.c.chain == _CHAIN,
                _token_registry.c.contract_address.in_(addrs),
            )
        )
    )
