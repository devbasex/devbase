"""status カテゴリの TUI 操作フロー (PLAN31_2 PR5)。

status は閲覧のみでサブコマンドも引数も持たない (``cmd_status(devbase_root)`` /
plan 2.2)。そのためメニューや引数収集を介さず、表示してそのまま rc を返す
薄い委譲に留める。rc (``int``) を返すことで「操作を実行した → トップへ復帰」
の戻り値プロトコル (actions_project と同じ) に従い、トップループが rc を記憶する。
"""

from __future__ import annotations

from pathlib import Path

from devbase.commands.status import cmd_status


def run(devbase_root: Path) -> int:
    """ステータスを表示し、rc を返してトップメニューへ復帰する。"""
    return cmd_status(Path(devbase_root))
