"""devbase の統合 TUI (`devbase list`) パッケージ。

`commands/project.py` に一体化していた questionary ベースのメニュー資産を
PLAN31_2 でこのパッケージに分離した。役割分担:

- ``menu``      : questionary ラッパ・``MENU_BACK`` 番兵・Esc/← バインド・引数収集ヘルパ
- ``dispatch``  : ``SimpleNamespace`` を組んで既存ハンドラを呼ぶ薄い委譲層
- ``app``       : トップ階層メニューとカテゴリ routing (`run` が入口)
- ``actions_*`` : 各カテゴリ (project/env/plugin/snapshot/status) の操作フロー

`run` を入口として再公開し、``cmd_project_list`` から ``tui.run`` で呼べるようにする。
"""

from __future__ import annotations

from devbase.tui.app import run

__all__ = ["run"]
