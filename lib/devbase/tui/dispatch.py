"""既存コマンドハンドラへの薄い委譲層。

TUI は CLI のロジックを再実装せず、``types.SimpleNamespace`` を組んで既存ハンドラ
(``cmd_project`` / ``cmd_env`` / ``cmd_plugin`` / ``cmd_snapshot`` 等) をそのまま呼ぶ。
旧 ``project.py:_start_project_action`` を一般化したもの:

- ``dispatch_lifecycle``: project ライフサイクル (up/down/login/.../rebuild)。
  ``cmd_project`` は ``args`` 1 つだけを取り、``name`` が真なら ``_dispatch_lifecycle``
  が ``projects/<name>`` へ chdir してからサブコマンドを実行する (PLAN06 機構)。
- ``dispatch_group``: env / plugin / snapshot 等の ``handler(devbase_root, args)``
  シグネチャを持つグループハンドラ向け (PR3 以降で使用)。

属性契約は ``issues/PLAN31_2_list-tui-unified.md`` 2.3 の表に従う。CLI 実行と差異を
出さないため、呼び出し側が CLI parser の既定値どおりの属性を ``**attrs`` で渡す。
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Callable


def dispatch_lifecycle(subcommand: str, name: str | None = None, **attrs) -> int:
    """``project <subcommand> [name]`` を共有ハンドラ ``cmd_project`` 経由で起動する。

    ``name`` が指定されると ``_dispatch_lifecycle`` が対象ディレクトリへ chdir して
    から実行する。``up`` の ``scale`` など各サブコマンド固有の属性は ``attrs`` で渡す
    (未指定でも getattr の既定で吸収される)。
    """
    from devbase.commands.container import cmd_project

    ns = types.SimpleNamespace(subcommand=subcommand, name=name, **attrs)
    return cmd_project(ns)


def dispatch_group(handler: Callable[[Path, object], int], devbase_root: Path,
                   subcommand: str, **attrs) -> int:
    """``handler(devbase_root, args)`` 形式のグループハンドラを起動する。

    env / plugin / snapshot の各 ``cmd_*`` は ``(devbase_root, args)`` を取り、
    ``args.subcommand`` で分岐する。TUI はサブコマンドと属性を ``SimpleNamespace`` に
    詰めてそのまま委譲する (PR3 以降の actions_* が利用)。
    """
    ns = types.SimpleNamespace(subcommand=subcommand, **attrs)
    return handler(devbase_root, ns)
