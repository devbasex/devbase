"""devbase CLI ログ設定"""

import logging
import sys


class _Formatter(logging.Formatter):
    _FORMATS = {
        logging.ERROR: "Error: %(message)s",
        logging.WARNING: "Warning: %(message)s",
        logging.INFO: "%(message)s",
        logging.DEBUG: "[DEBUG] %(message)s",
    }

    def format(self, record):
        fmt = self._FORMATS.get(record.levelno, "%(message)s")
        return logging.Formatter(fmt).format(record)


def setup(verbose: bool = False) -> None:
    root = logging.getLogger("devbase")
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Formatter())
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
