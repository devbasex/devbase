"""Docker を差し替えたテストで、控えのコマンドが書くはずのファイルを置く。

控えのコマンドが書くのは ``-o /backup/<アーカイブ>`` と ``--listed-incremental=/backup/<状態>`` の
2 本である。世代の作り直しは仮の名前 (``full.tar.zst.new``・``snapshot.snar.new``) へ書く (#458)
ため、名前はコマンドから拾う。
"""

from __future__ import annotations

import re
from pathlib import Path

_ARCHIVE_RE = re.compile(r"-o /backup/(\S+)")
_SNAR_RE = re.compile(r"--listed-incremental=/backup/(\S+)")


def write_backup_outputs(snap_dir: Path, command: str) -> None:
    """``command`` が書くアーカイブと状態ファイルを ``snap_dir`` へ置く。"""
    (snap_dir / _ARCHIVE_RE.search(command).group(1)).write_text("archive")
    (snap_dir / _SNAR_RE.search(command).group(1)).write_text("snar")
