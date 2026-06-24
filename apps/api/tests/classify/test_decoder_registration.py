"""Verify __init_subclass__ auto-registration."""

from lemon_ledger.classify.decoders import cross_chain as _cc  # noqa: F401
from lemon_ledger.classify.decoders.base import L2Decoder

EXPECTED_SYMBOLS = {"LFLX", "LBNK", "LPAY", "LMED", "CTFZ", "LLOT", "LLUX", "LMLN", "LSQZ", "LTVL"}
EXPECTED_CLASS_NAMES = {
    "LflxDecoder",
    "LbnkDecoder",
    "LpayDecoder",
    "LmedDecoder",
    "CtfzDecoder",
    "LlotDecoder",
    "LluxDecoder",
    "LmlnDecoder",
    "LsqzDecoder",
    "LtvlDecoder",
}


def test_symbols_registered() -> None:
    for sym in EXPECTED_SYMBOLS:
        assert sym in L2Decoder._registry, f"{sym} not in registry"


def test_class_names_registered() -> None:
    for name in EXPECTED_CLASS_NAMES:
        assert name in L2Decoder._registry, f"{name} not in registry"


def test_for_symbol_roundtrip() -> None:
    import uuid

    for sym in EXPECTED_SYMBOLS:
        cls = L2Decoder.for_symbol(sym)
        assert cls.symbol == sym
        decoder = cls(uuid.uuid4())
        assert decoder.symbol == sym


def test_all_subclasses_are_l2decoder() -> None:
    for sym in EXPECTED_SYMBOLS:
        assert issubclass(L2Decoder._registry[sym], L2Decoder)
