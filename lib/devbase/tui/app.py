"""トップ階層メニューとカテゴリ routing (`devbase list` の入口)。

``run(devbase_root, args)`` が ``cmd_project_list`` から呼ばれる新しい入口。
プロジェクト一覧の選択だけだった旧挙動を、全カテゴリ
(project / env / plugin / snapshot / status) を束ねるトップ階層メニューへ拡張する。

PR1 では **project カテゴリのみ配線**し、env/plugin/snapshot/status は後続 PR
(PR3〜PR5) で各 ``actions_*`` を ``_route`` に足すまでプレースホルダ案内を出す。

後方互換 (plan 3.2):
- ``--no-interactive`` / ``--plain`` (interactive=False) と非 TTY は従来どおり一覧
  テーブルのみ。
- questionary 不在時はトップメニューを出さず、従来の番号入力フォールバック
  (project up) へ縮退して muscle-memory を保全する。
- トップメニューでは「プロジェクト操作」を先頭に置き既定ハイライトとすることで、
  Enter 連打で従来の project 選択フローへ到達できるようにする。

ナビ規約: トップメニューは Esc / Ctrl-C で中止 (戻り先なし)。各カテゴリ内では
Esc / ← でトップメニューへ戻る (``menu.MENU_BACK``)、Ctrl-C で全体中止 (``None``)。
"""

from __future__ import annotations

import sys
from pathlib import Path

from devbase.commands.project import _print_table, list_projects
from devbase.log import get_logger
from devbase.tui import actions_project, menu

logger = get_logger(__name__)

# トップメニューのカテゴリ (表示順 = ハイライト既定順)。先頭の「プロジェクト操作」を
# 既定ハイライトにして従来フローへ Enter 連打で到達できるようにする (plan 3.2)。
TOP_CATEGORIES: list[tuple[str, str]] = [
    ("project", "プロジェクト操作"),
    ("env", "環境変数"),
    ("plugin", "プラグイン"),
    ("snapshot", "スナップショット"),
    ("status", "ステータス"),
]

_LABELS = dict(TOP_CATEGORIES)


def _route(category: str, devbase_root: Path):
    """選択カテゴリのハンドラを呼ぶ。戻り値は ``menu.MENU_BACK`` / ``None``。

    後続 PR は対応する ``actions_*`` の呼び出しをここに 1 行追加する
    (各カテゴリ別ファイルのため衝突しにくい)。
    """
    if category == "project":
        return actions_project.run(devbase_root)
    # PR3: env, PR4: plugin, PR5: snapshot/status をここに追加する。
    logger.info("「%s」は後続 PR で実装予定です。", _LABELS.get(category, category))
    return menu.MENU_BACK


def _top_menu_loop(devbase_root: Path) -> int:
    """トップ階層メニューのループ。Esc / Ctrl-C で中止 (rc=0)。"""
    while True:
        choice = menu.select(
            "操作カテゴリを選択 (↑↓ 移動 / Enter 決定 / Esc・Ctrl-C 中止):",
            list(TOP_CATEGORIES), back=False, search=False)
        if choice is None:
            logger.info("中止しました。")
            return 0

        result = _route(choice, devbase_root)
        if result is None:
            # カテゴリ内で Ctrl-C → 全体中止
            logger.info("中止しました。")
            return 0
        # MENU_BACK → トップメニューへ戻り再表示


def run(devbase_root: Path, args) -> int:
    """`devbase list` / `devbase project list` の入口。

    - interactive=False / 非 TTY: 一覧テーブルのみ (従来挙動)。
    - questionary 不在: 番号入力フォールバック (project up) へ縮退。
    - それ以外: トップ階層メニューを開く。
    """
    projects_dir = Path(devbase_root) / "projects"

    # 対話はデフォルト ON。非 TTY (パイプ / CI / リダイレクト) は表示・読取りできない
    # ため一覧表示へフォールバックする (stdin/stdout いずれかが非 TTY なら縮退)。
    interactive = (getattr(args, "interactive", True)
                   and sys.stdin.isatty() and sys.stdout.isatty())

    if not interactive:
        rows = list_projects(projects_dir)
        if not rows:
            logger.info("プロジェクトがありません (%s)。", projects_dir)
            return 0
        _print_table(rows)
        return 0

    if not menu.HAVE_QUESTIONARY:
        logger.warning(
            "questionary が未導入のため番号入力にフォールバックします "
            "(`uv sync` で導入すると階層メニューが使えます)。"
        )
        rows = list_projects(projects_dir)
        if not rows:
            logger.info("プロジェクトがありません (%s)。", projects_dir)
            return 0
        return actions_project.fallback_select_and_up(rows)

    return _top_menu_loop(devbase_root)
