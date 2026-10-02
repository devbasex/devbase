"""entrypoint の完了の印の寿命 (起動 1 回ごと)

docker start で再起動したコンテナには前回の ``/tmp/entrypoint-ready`` が残る。
entrypoint が冒頭で消さないと、ホスト側の post-start が今回の初期化の途中でも
完了と読み、token 配布などを先に走らせてしまう。
"""

from __future__ import annotations

import re
from pathlib import Path

from devbase.utils.docker import ENTRYPOINT_READY_FILE

ENTRYPOINT = Path(__file__).resolve().parents[2] / "containers" / "base" / "entrypoint.sh"


def _lines() -> list[str]:
    return ENTRYPOINT.read_text().splitlines()


def _index(pattern: str) -> int:
    for i, line in enumerate(_lines()):
        if re.search(pattern, line):
            return i
    raise AssertionError(f"pattern not found: {pattern}")


def test_marker_path_matches_host_side():
    assert f"ENTRYPOINT_READY_FILE={ENTRYPOINT_READY_FILE}" in _lines()


def test_stale_marker_removed_before_setup_and_touched_last():
    guard = _index(r'DEVBASE_ENTRYPOINT_LIB_ONLY:-')
    remove = _index(r'^rm -f "\$ENTRYPOINT_READY_FILE"$')
    first_setup = _index(r'^devbase_setup_cloud_config_dirs ')
    touch = _index(r'^touch "\$ENTRYPOINT_READY_FILE"$')
    exec_line = _index(r'^exec "\$@"$')
    assert guard < remove < first_setup < touch < exec_line
