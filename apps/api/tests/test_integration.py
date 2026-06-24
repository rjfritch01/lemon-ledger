"""Live-network integration tests.

All tests are marked @pytest.mark.integration and are EXCLUDED from the unit
suite (`just api-test` runs with -m 'not integration'). Run them explicitly
with `just api-test-integration`.

B. Testnet contract tests + capstone sync
   - Requires TEST_LEMONCHAIN_WALLET env var; skips cleanly when absent.
   - testnet URL hardcoded to Citron testnet; chain='lemonchain' everywhere.

C. Seed decimals verification (mainnet, xfail if getToken is unreliable)
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from lemon_ledger.clients.blockscout import BlockscoutClient
from lemon_ledger.clients.envelope import _EMPTY_MESSAGES
from lemon_ledger.clients.rate_limit import NullRateLimiter
from lemon_ledger.ingestion.mappers import map_internal_tx, map_token_transfer, map_transaction
from lemon_ledger.ingestion.sync import sync_wallet
from lemon_ledger.models.raw import RawTokenTransfer, RawTransaction
from lemon_ledger.models.user import User
from lemon_ledger.models.wallet import Wallet

pytestmark = pytest.mark.integration

_TESTNET_URL = "https://explorer-testnet.lemonchain.io/api"
_MAINNET_URL = "https://explorer.lemonchain.io/api"
_KNOWN_EMPTY_ADDR = "0x0000000000000000000000000000000000000001"

# 21 Tier-1 ERC-20 contracts seeded in migration 0edc18d4c0a5; expected 18 decimals each.
_TIER1_CONTRACTS: list[tuple[str, str, int]] = [
    ("WLEMX", "0x84862e65ebf37af91a8b85283b58505de3352588", 18),
    ("LUSD", "0x8de60f88f19dad42dde0d9ed2eeba68269722a99", 18),
    ("LFLX", "0x1bacc825fcd91971e8daca3104370380b4a981be", 18),
    ("LBNK", "0xc17ef640d7c34a8c684073d85d815539f66da3c7", 18),
    ("LPAY", "0x708cf95b67f3dfff16e1f48313425d0cfb629ee7", 18),
    ("LMED", "0xf489e786cf6242b3c32cfe5372453b37b8f0cc13", 18),
    ("CTFZ", "0x83d4b4db63c40846735860ce3b2adf83aa9edc8e", 18),
    ("LTVL", "0x02535cbc23c045134a481cf8b6a6645e7655efb8", 18),
    ("LLOT", "0xc8fa8354d6c6856de3e3f7da89f0ce4636e51710", 18),
    ("LSQZ", "0xce37edd204dedbc256a7f5d3622e82f5fc031cd8", 18),
    ("HXDX", "0x59100856dfbbb5a10bdafc894b8f82c89a0adc34", 18),
    ("HXBT", "0xc9fd20a101f01eac20e859645e91c9998aaa509b", 18),
    ("SMART", "0x38374f0527e3320058c96adcb57c6e78afe9447e", 18),
    ("RMC", "0x5d59ca7460b5e0c553e62b4b7b0197bf12ac1fb5", 18),
    ("MHSA", "0x8f9457a8de85876951b3ac2843c09997b951c267", 18),
    ("STH", "0x3ed3bfbac6ece65468b37abb15091f346f1b8905", 18),
    ("NXYS", "0x0f4bb028eaa7f0d0545ddd24600c524c3e044962", 18),
    ("TIXA", "0xe2677da211265c092f1bc4f018798afbc20971dc", 18),
    ("PUP", "0xdd84a98f9f9e0be193bfd91c123254d835cb3b32", 18),
    ("LLUX", "0x71e3a635763910bccf5f979ebbf8c69cb9704db0", 18),
    ("LMLN", "0x6cc7ee8f2f45782cbf376b4021d41960b814f321", 18),
]


# ── shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def testnet_wallet_address() -> str:
    addr = os.getenv("TEST_LEMONCHAIN_WALLET", "")
    if not addr:
        pytest.skip("TEST_LEMONCHAIN_WALLET env var not set — skipping testnet wallet tests")
    return addr.lower()


@pytest.fixture(scope="module")
def testnet_http() -> Generator[httpx.Client, None, None]:
    with httpx.Client(timeout=30.0) as client:
        yield client


@pytest.fixture(scope="module")
def testnet_client(testnet_http: httpx.Client) -> BlockscoutClient:
    return BlockscoutClient(_TESTNET_URL, http=testnet_http, rate_limiter=NullRateLimiter())


@pytest.fixture(scope="module")
def mainnet_http() -> Generator[httpx.Client, None, None]:
    with httpx.Client(timeout=30.0) as client:
        yield client


@pytest.fixture(scope="module")
def mainnet_client(mainnet_http: httpx.Client) -> BlockscoutClient:
    return BlockscoutClient(_MAINNET_URL, http=mainnet_http, rate_limiter=NullRateLimiter())


# ── capstone-specific fixtures ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def capstone_pg() -> Generator[str, None, None]:
    """Module-scoped Postgres container with all migrations applied."""
    with PostgresContainer("postgres:16-alpine") as pg:
        raw = pg.get_connection_url()
        asyncpg_url = (
            raw.replace("+psycopg2", "+asyncpg")
            if "+psycopg2" in raw
            else raw.replace("postgresql://", "postgresql+asyncpg://", 1)
        )
        psycopg_url = asyncpg_url.replace("+asyncpg", "+psycopg")

        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", asyncpg_url)
        command.upgrade(cfg, "head")

        yield psycopg_url


@pytest.fixture(scope="module")
def capstone_maker(capstone_pg: str) -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(capstone_pg, pool_pre_ping=True)
    maker: sessionmaker[Session] = sessionmaker(engine, expire_on_commit=False)
    yield maker
    engine.dispose()


@pytest.fixture(scope="module")
def capstone_wallet_id(
    capstone_maker: sessionmaker[Session],
    testnet_wallet_address: str,
) -> uuid.UUID:
    """Insert User + Wallet once; return the wallet UUID for capstone tests."""
    with capstone_maker() as session:
        user = User(clerk_user_id="integration_test_user")
        session.add(user)
        session.flush()

        wallet = Wallet(
            user_id=user.id,
            chain="lemonchain",
            address=testnet_wallet_address,
            role="live",
            added_via="integration-test",
        )
        session.add(wallet)
        session.commit()
        return wallet.id


# ── Part B: client contract tests ─────────────────────────────────────────────


def test_get_transactions_passes_mapper(
    testnet_wallet_address: str, testnet_client: BlockscoutClient
) -> None:
    """Transactions from the testnet wallet pass through the mapper without error."""
    records = list(testnet_client.get_transactions(testnet_wallet_address, start_block=0))
    dummy_id = uuid.uuid4()
    for rec in records:
        map_transaction(dummy_id, "lemonchain", rec)


def test_get_token_transfers_passes_mapper(
    testnet_wallet_address: str, testnet_client: BlockscoutClient
) -> None:
    """Token transfers from the testnet wallet pass through the mapper without error."""
    records = list(testnet_client.get_token_transfers(testnet_wallet_address, start_block=0))
    dummy_id = uuid.uuid4()
    for rec in records:
        map_token_transfer(dummy_id, "lemonchain", rec)


def test_get_internal_txs_passes_mapper(
    testnet_wallet_address: str, testnet_client: BlockscoutClient
) -> None:
    """Internal txs from the testnet wallet pass through the mapper without error."""
    records = list(testnet_client.get_internal_transactions(testnet_wallet_address, start_block=0))
    dummy_id = uuid.uuid4()
    for rec in records:
        map_internal_tx(dummy_id, "lemonchain", rec)


# ── Part B: empty-message allowlist hardening ─────────────────────────────────


def test_empty_message_in_allowlist(testnet_http: httpx.Client) -> None:
    """The testnet's 'no results' message must be in _EMPTY_MESSAGES.

    Makes a raw HTTP call so we can inspect the actual message returned by the
    testnet explorer and assert it is covered by the envelope parser's allowlist.
    If this test fails, add the new message to _EMPTY_MESSAGES in envelope.py.
    """
    resp = testnet_http.get(
        _TESTNET_URL,
        params={
            "module": "account",
            "action": "txlist",
            "address": _KNOWN_EMPTY_ADDR,
            "startblock": "0",
            "endblock": "99999999",
            "sort": "asc",
            "page": "1",
            "offset": "1",
        },
    )
    payload = resp.json()
    message = str(payload.get("message", "")).lower().strip()
    assert message in _EMPTY_MESSAGES, (
        f"Testnet 'no results' message {message!r} is NOT in _EMPTY_MESSAGES. "
        f"Add it to envelope.py to prevent false errors on empty wallets. "
        f"Current allowlist: {sorted(_EMPTY_MESSAGES)}"
    )


# ── Part B: traceId presence ──────────────────────────────────────────────────


def test_internal_tx_trace_id_present(
    testnet_wallet_address: str, testnet_client: BlockscoutClient
) -> None:
    """Assert internal-tx records from the testnet carry the traceId field.

    The mapper keys on `traceId` (or `trace_id` as a fallback).  If this test
    fails it means neither field is present — that is the signal to implement
    the content-hash fallback (hash[:8] + blockNumber + logIndex).
    """
    records = list(testnet_client.get_internal_transactions(testnet_wallet_address, start_block=0))
    if not records:
        pytest.skip("No internal txs found for test wallet — cannot validate traceId field")

    for rec in records:
        assert "traceId" in rec or "trace_id" in rec, (
            f"Internal tx missing traceId/trace_id. Actual keys: {sorted(rec.keys())}. "
            "This is the signal to implement the content-hash fallback."
        )


# ── Part B: capstone sync ─────────────────────────────────────────────────────


def test_capstone_sync_and_idempotency(
    capstone_maker: sessionmaker[Session],
    capstone_wallet_id: uuid.UUID,
    testnet_client: BlockscoutClient,
) -> None:
    """Capstone: sync writes raw rows, advances cursor; re-sync is idempotent.

    Pass 1 — scan from block 0 to head with confirmations=0.  Asserts cursor
    advances.

    Pass 2 — reset cursor to 0 and re-sync the same range.  Because every
    RawTransaction/RawTokenTransfer/RawInternalTx uses on_conflict_do_nothing,
    no duplicate rows should be inserted.
    """
    # Pass 1 — initial sync
    with capstone_maker() as session:
        wallet = session.get(Wallet, capstone_wallet_id)
        assert wallet is not None
        r1 = sync_wallet(session, testnet_client, wallet, confirmations=0, chunk_blocks=100_000)

    assert r1.to_block > 0, "Cursor did not advance past block 0"

    # Cursor is persisted
    with capstone_maker() as session:
        wallet = session.get(Wallet, capstone_wallet_id)
        assert wallet is not None
        assert wallet.last_synced_block == r1.to_block

    # Record raw row counts before idempotency pass
    with capstone_maker() as session:
        tx_count = session.scalar(
            select(func.count())
            .select_from(RawTransaction)
            .where(RawTransaction.wallet_id == capstone_wallet_id)
        )
        tt_count = session.scalar(
            select(func.count())
            .select_from(RawTokenTransfer)
            .where(RawTokenTransfer.wallet_id == capstone_wallet_id)
        )

    # Reset cursor — re-ingest the same block range
    with capstone_maker() as session:
        wallet = session.get(Wallet, capstone_wallet_id)
        assert wallet is not None
        wallet.last_synced_block = None
        session.commit()

    # Pass 2 — re-sync; on_conflict_do_nothing must absorb all rows
    with capstone_maker() as session:
        wallet = session.get(Wallet, capstone_wallet_id)
        assert wallet is not None
        r2 = sync_wallet(session, testnet_client, wallet, confirmations=0, chunk_blocks=100_000)

    assert r2.transactions == 0, "Re-ingestion wrote duplicate transactions (idempotency failure)"
    assert r2.token_transfers == 0, "Re-ingestion wrote duplicate token transfers"
    assert r2.internal_txs == 0, "Re-ingestion wrote duplicate internal txs"

    with capstone_maker() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(RawTransaction)
                .where(RawTransaction.wallet_id == capstone_wallet_id)
            )
            == tx_count
        ), "RawTransaction count changed on idempotent re-sync"
        assert (
            session.scalar(
                select(func.count())
                .select_from(RawTokenTransfer)
                .where(RawTokenTransfer.wallet_id == capstone_wallet_id)
            )
            == tt_count
        ), "RawTokenTransfer count changed on idempotent re-sync"


# ── Part C: seed decimals verification (mainnet) ──────────────────────────────


@pytest.mark.xfail(
    strict=False,
    reason=(
        "getToken endpoint may be unreliable on the Lemonchain mainnet explorer; "
        "deferred to Chat 1.4 where web3.py provides a reliable alternative"
    ),
)
@pytest.mark.parametrize("symbol,address,expected_decimals", _TIER1_CONTRACTS)
def test_seed_decimals_match_onchain(
    mainnet_client: BlockscoutClient, symbol: str, address: str, expected_decimals: int
) -> None:
    """Assert on-chain decimals match the seeded value for each Tier-1 ERC-20 token.

    LUSD decimals are unverified from the SOW — if this test shows 6 (USDC-style)
    rather than 18, update the seed migration accordingly.
    """
    metadata = mainnet_client.get_token_metadata(address)
    actual = int(metadata["decimals"])
    assert actual == expected_decimals, (
        f"{symbol} ({address}): seeded {expected_decimals} decimals but on-chain reports {actual}. "
        "Update the seed migration if different."
    )
