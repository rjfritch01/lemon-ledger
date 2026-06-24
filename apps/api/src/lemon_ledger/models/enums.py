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
