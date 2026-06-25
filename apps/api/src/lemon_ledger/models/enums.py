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


# ── 1.9 cross-entity / form-generation enums ──────────────────────────────────


class TransferResolution(StrEnum):
    """Engine-signal written by the resolve service onto ClassifiedTransaction.

    The lot engine reads this field (and relocation_source_event_id for relocate-*
    values) to materialise the correct ledger treatment.  It NEVER reads
    pending_classifications or bridge_correlations directly.
    """

    RELOCATE_INTERNAL = "relocate-internal"
    RELOCATE_CONTRIBUTION = "relocate-contribution"
    RELOCATE_GIFT = "relocate-gift"
    RELOCATE_REASSIGNMENT = "relocate-reassignment"
    DISPOSAL = "disposal"
    DISPOSAL_RELATED_PARTY = "disposal-related-party"
    GIFT_OUT = "gift-out"
    NO_OP_LOAN = "no-op-loan"


class PendingClassificationKind(StrEnum):
    CROSS_ENTITY = "cross-entity"
    EXTERNAL_OUTFLOW = "external-outflow"


class PendingClassificationState(StrEnum):
    NEEDS_CLASSIFICATION = "needs_classification"
    CLASSIFIED = "classified"
    APPLIED = "applied"
    DISMISSED = "dismissed"


class ChosenClassification(StrEnum):
    """Valid choices for pending_classifications.chosen_classification.

    Validity of (kind, chosen_classification) pairs is enforced in the
    application layer (resolve service), not via a cross-column DB CHECK.
    Allowed sets:
      cross-entity   -> capital-contribution, sale, gift, loan, reassignment
      external-outflow -> sale, gift, payment
    """

    CAPITAL_CONTRIBUTION = "capital-contribution"
    SALE = "sale"
    GIFT = "gift"
    LOAN = "loan"
    REASSIGNMENT = "reassignment"
    PAYMENT = "payment"


class CoveredStatus(StrEnum):
    """Maps to Form 8949 box selection (Part I vs II columns).

    no-1099-da             -> Box C (short) or F (long)   — no 1099-DA issued
    covered-basis-reported -> Box A (short) or D (long)   — 1099-DA with basis
    covered-basis-not-reported -> Box B (short) or E (long) — 1099-DA, no basis
    """

    NO_1099_DA = "no-1099-da"
    COVERED_BASIS_REPORTED = "covered-basis-reported"
    COVERED_BASIS_NOT_REPORTED = "covered-basis-not-reported"


class AdjustmentCode(StrEnum):
    """Form 8949 column (f) adjustment codes.

    Only 'L' (§267 related-party loss disallowance) is populated in Phase 1.
    Others reserved for future phases.
    """

    L = "L"  # §267 related-party loss disallowed
    W = "W"  # wash sale loss disallowed (Phase 3+)
    D = "D"  # reserved
    E = "E"  # reserved
    OTHER = "O"  # reserved
