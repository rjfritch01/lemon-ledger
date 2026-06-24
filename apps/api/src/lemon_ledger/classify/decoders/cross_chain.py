"""Cross-chain L2 decoder subclasses.

Each of the 10 cross-chain ecosystem tokens gets a thin subclass whose only
job is to set `symbol` and trigger auto-registration via __init_subclass__.

LMLN is a straight-mint stub here; buy/burn override lands in Chat 1.6.
"""

from lemon_ledger.classify.decoders.base import L2Decoder


class LflxDecoder(L2Decoder):
    symbol = "LFLX"


class LbnkDecoder(L2Decoder):
    symbol = "LBNK"


class LpayDecoder(L2Decoder):
    symbol = "LPAY"


class LmedDecoder(L2Decoder):
    symbol = "LMED"


class CtfzDecoder(L2Decoder):
    symbol = "CTFZ"


class LlotDecoder(L2Decoder):
    symbol = "LLOT"


class LluxDecoder(L2Decoder):
    symbol = "LLUX"


class LmlnDecoder(L2Decoder):
    symbol = "LMLN"


class LsqzDecoder(L2Decoder):
    symbol = "LSQZ"


class LtvlDecoder(L2Decoder):
    symbol = "LTVL"
