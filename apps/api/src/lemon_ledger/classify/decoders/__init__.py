# Import decoder modules so all subclasses register via __init_subclass__.
from lemon_ledger.classify.decoders import cross_chain as cross_chain  # noqa: F401
from lemon_ledger.classify.decoders import lemonchain_only as lemonchain_only  # noqa: F401
