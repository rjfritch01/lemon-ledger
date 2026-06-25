"""Central enum definitions for the Lemon Ledger domain.

Chain lives in domain.chains and is re-exported here so models that need
both can import from a single location.
"""

from enum import StrEnum

from lemon_ledger.domain.chains import Chain as Chain  # re-export


class ClassificationKind(StrEnum):
    REWARD = "reward"
    MINT = "mint"
    STAKE = "stake"
    UNSTAKE = "unstake"
    TRANSFER_IN = "transfer-in"
    TRANSFER_OUT = "transfer-out"
    UNCLASSIFIED = "unclassified"
    # 1.6 additions
    PENDING = "pending"
    WRAP = "wrap"
    UNWRAP = "unwrap"
    SWAP_CREDIT_REDEMPTION = "swap-credit-redemption"
    BURN = "burn"
    # 1.8: bridge signal classifications (set by bridge module; read by lot engine)
    BRIDGE_IN = "bridge-in"
    BRIDGE_OUT = "bridge-out"


# ── 1.7 lot-engine enums ──────────────────────────────────────────────────────


class AssetClass(StrEnum):
    FUNGIBLE = "fungible"
    COLLECTIBLE = "collectible"


class HoldingPeriod(StrEnum):
    SHORT = "short"
    LONG = "long"


class AcquisitionType(StrEnum):
    BUY = "buy"
    MINT = "mint"
    REWARD = "reward"
    BRIDGE_IN = "bridge-in"
    GIFT = "gift"
    CAP_CONTRIBUTION = "cap-contribution"


class BasisMethod(StrEnum):
    """Allowable values for entities.default_basis_method.

    Average Cost is intentionally absent: it is not permitted for US crypto
    holdings under current IRS guidance (Rev. Proc. 2024-28).
    """

    FIFO = "fifo"
    SPECIFIC_ID = "specific_id"


class SelectionStrategy(StrEnum):
    """Sub-strategy used for specific-identification disposals (audit record)."""

    FIFO = "fifo"
    HIFO = "hifo"
    LIFO = "lifo"
    MANUAL = "manual"


class LotTreatment(StrEnum):
    """Engine-internal treatment derived from ClassificationKind. Never persisted."""

    ACQUIRE = "acquire"
    DISPOSE = "dispose"
    RELOCATE = "relocate"
    NONE = "none"
    PENDING = "pending"


class LotExceptionReason(StrEnum):
    INSUFFICIENT_LOTS = "insufficient_lots"
    MISSING_BASIS = "missing_basis"
    UNRESOLVED_FEE = "unresolved_fee"
