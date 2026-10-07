"""サブコマンドの振り分け (``env`` と ``env backend`` が使う)"""

import logging
from typing import Callable, Mapping, Optional


def run_subcommand(handlers: Mapping[str, Callable[[], int]], name: Optional[str], *,
                   missing_rc: int, logger: logging.Logger) -> int:
    """``name`` のハンドラを呼んで終了コードを返す。

    ``name`` が無い・知らない名前なら、選べるサブコマンドを ``logger`` (呼び出し元のもの) へ
    並べて ``missing_rc`` を返す。
    """
    handler = handlers.get(name)
    if handler is None:
        logger.error("サブコマンドを指定してください: %s", ', '.join(handlers))
        return missing_rc
    return handler()
