"""devbase userns status / apply / revert (#533)

判定と変更は :mod:`devbase.utils.userns` が持ち、ここは出力と終了コードだけを決める。
"""

from __future__ import annotations

from pathlib import Path

from devbase.log import get_logger
from devbase.utils import userns

logger = get_logger("devbase.commands.userns")


def _print_emit(level: str, line: str) -> None:
    if level == 'warning':
        logger.warning('%s', line)
    else:
        print(line, flush=True)


def cmd_userns(devbase_root: Path, args) -> int:
    """``args.subcommand`` で status / apply / revert を打つ

    ``devbase_root`` は使わない。``_ROOT_COMMANDS`` から ``func(devbase_root, args)`` の形で
    呼ばれるため、他のコマンドと同じ引数で受ける。
    """
    sub = getattr(args, 'subcommand', None)
    if sub == 'status':
        userns.status(emit=_print_emit)
        return 0
    if sub == 'apply':
        report = userns.apply(emit=userns.logger_emit(logger))
        return 0 if report.ok else 1
    if sub == 'revert':
        report = userns.revert(emit=userns.logger_emit(logger))
        return 0 if report.ok else 1
    logger.error("使い方: devbase userns {status|apply|revert}")
    return 2
