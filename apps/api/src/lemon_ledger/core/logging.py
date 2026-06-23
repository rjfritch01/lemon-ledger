import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Configure stdlib logging with a JSON-ish single-line format."""
    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
