import uuid

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from uuid_utils.compat import uuid7


class Base(DeclarativeBase):
    pass


class UUIDPrimaryKeyMixin:
    """Single canonical source for UUIDv7 app-side primary keys."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
