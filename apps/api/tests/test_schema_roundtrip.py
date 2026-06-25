import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lemon_ledger.models import Entity, User, Wallet, WalletEntityAssignment


async def test_user_entity_wallet_assignment_roundtrip(db_session: AsyncSession) -> None:
    user = User(clerk_user_id=f"clerk_{uuid.uuid4().hex}")
    db_session.add(user)
    await db_session.flush()

    entity = Entity(user_id=user.id, name="Personal Holdings", type="personal")
    db_session.add(entity)
    await db_session.flush()

    wallet = Wallet(
        user_id=user.id,
        chain="lemonchain",
        address="0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        role="live",
    )
    db_session.add(wallet)
    await db_session.flush()

    assignment = WalletEntityAssignment(
        wallet_id=wallet.id,
        entity_id=entity.id,
        effective_from=date(2024, 1, 1),
        classification="initial-assignment",
    )
    db_session.add(assignment)
    await db_session.flush()

    # Read back each object
    fetched_user = await db_session.get(User, user.id)
    assert fetched_user is not None
    assert fetched_user.clerk_user_id == user.clerk_user_id

    fetched_entity = await db_session.get(Entity, entity.id)
    assert fetched_entity is not None
    assert fetched_entity.name == "Personal Holdings"
    assert fetched_entity.user_id == user.id
    assert fetched_entity.default_basis_method == "fifo"

    fetched_wallet = await db_session.get(Wallet, wallet.id)
    assert fetched_wallet is not None
    assert fetched_wallet.chain == "lemonchain"
    assert fetched_wallet.address == "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

    result = await db_session.execute(
        select(WalletEntityAssignment).where(
            WalletEntityAssignment.wallet_id == wallet.id,
            WalletEntityAssignment.effective_to.is_(None),
        )
    )
    row = result.scalar_one()
    assert row.entity_id == entity.id
    assert row.classification == "initial-assignment"


@pytest.mark.parametrize(
    "field,value",
    [
        ("type", "s-corp"),
        ("type", "llc-passthrough"),
        ("default_basis_method", "specific_id"),
    ],
)
async def test_entity_check_constraint_values(
    db_session: AsyncSession, field: str, value: str
) -> None:
    user = User(clerk_user_id=f"clerk_{uuid.uuid4().hex}")
    db_session.add(user)
    await db_session.flush()

    kwargs: dict[str, object] = {
        "user_id": user.id,
        "name": "Test",
        "type": "personal",
        field: value,
    }
    entity = Entity(**kwargs)
    db_session.add(entity)
    await db_session.flush()
    assert getattr(entity, field) == value


async def test_pk_default_is_uuidv7(db_session: AsyncSession) -> None:
    user = User(clerk_user_id=f"clerk_{uuid.uuid4().hex}")
    db_session.add(user)
    await db_session.flush()
    assert user.id.version == 7
