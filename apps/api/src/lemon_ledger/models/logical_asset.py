"""Logical asset and canonical pool membership models.

LogicalAsset    — a canonical economic identity (e.g. "LEMX" = native + WLEMX).
TokenAssetMembership — maps token_registry rows into a logical asset for pooling.

Per Rev. Proc. 2024-28 lot pooling: all token representations that are economic
equivalents share one pool. WLEMX wrap/unwrap is a no-op because WLEMX and native
LEMX are members of the same logical asset.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from lemon_ledger.db.base import Base, UUIDPrimaryKeyMixin

_ASSET_KINDS = "asset_kind IN ('fungible','nft','stablecoin')"


class LogicalAsset(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "logical_assets"
    __table_args__ = (
        UniqueConstraint("symbol", name="uq_logical_assets_symbol"),
        CheckConstraint(_ASSET_KINDS, name="ck_logical_assets_kind"),
    )

    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    asset_kind: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TokenAssetMembership(UUIDPrimaryKeyMixin, Base):
    """Maps a token_registry row into a logical asset pool."""

    __tablename__ = "token_asset_memberships"
    __table_args__ = (UniqueConstraint("token_id", name="uq_token_asset_membership_token"),)

    token_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("token_registry.id", ondelete="RESTRICT"), nullable=False
    )
    logical_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("logical_assets.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
