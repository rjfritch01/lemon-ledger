"""Lemon Ledger – read-only crypto tax tracker for the LEMX ecosystem."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("lemon-ledger")
except PackageNotFoundError:
    __version__ = "dev"
