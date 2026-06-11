"""`devbase list` の入口: プロジェクト一覧を最上位画面とする TUI。

``run(devbase_root, args)`` が ``cmd_project_list`` から呼ばれる入口。
利用頻度が最も高い **プロジェクト一覧を起動直後のトップ画面** とし、
プロジェクト選択 → (running なら操作サブメニュー / それ以外は up) を最短経路にする。
env / plugin / snapshot / status は一覧の末尾に並ぶカテゴリ項目から遷移する。

後方互換 (plan 3.2):
- ``--no-interactive`` / ``--plain`` (interactive=False) と非 TTY は従来どおり一覧
  テーブルのみ。
- questionary 不在時は一覧メニューを出さず、従来の番号入力フォールバック
  (project up) へ縮退して muscle-memory を保全する。
- 一覧は先頭プロジェクトを既定ハイライトとし、Enter 連打で従来の
  「最初のプロジェクトを up」へ最短到達できる。

ナビ規約: トップ (プロジェクト一覧) は Esc / Ctrl-C で中止 (戻り先なし)。
各カテゴリ・サブメニュー内では Esc / ← で 1 つ前へ戻る (``menu.MENU_BACK``)、
Ctrl-C で全体中止 (``None``)。
"""

from __future__ import annotations

import sys
from pathlib import Path

from devbase.commands.project import (
    _STATUS_COLOR,
    _build_menu_entries,
    _print_table,
    list_projects,
)
from devbase.log import get_logger
from devbase.tui import (actions_env, actions_plugin, actions_project,
                         actions_snapshot, actions_status, menu)

logger = get_logger(__name__)

# プロジェクト一覧の末尾に並べるカテゴリの SSoT (表示順 / ラベル / 実装モジュール)。
# 一覧メニューには ``label (key)`` 形式で表示する (key 入力での絞り込みも効く)。
# モジュール参照を保持し ``run`` の解決を呼び出し時まで遅らせる
# (テストが ``actions_*.run`` を monkeypatch できるようにするため)。
_CATEGORIES: list[tuple[str, str, object]] = [
    ("env", "環境変数", actions_env),
    ("plugin", "プラグイン", actions_plugin),
    ("snapshot", "スナップショット", actions_snapshot),
    ("status", "ステータス", actions_status),
]

TOP_CATEGORIES: list[tuple[str, str]] = [(k, label) for k, label, _ in _CATEGORIES]
_LABELS = dict(TOP_CATEGORIES)
_CATEGORY_MODULES = {k: mod for k, _, mod in _CATEGORIES}


def _route(category: str, devbase_root: Path):
    """選択カテゴリのハンドラを呼ぶ。

    戻り値は各カテゴリの戻り値プロトコルに従う:
    - 操作実行時はその rc (``int``)
    - 操作なしで一覧へ戻るときは ``menu.MENU_BACK``
    - Ctrl-C 全体中止のときは ``None``
    """
    module = _CATEGORY_MODULES.get(category)
    if module is None:
        logger.error("未知のカテゴリです: %s", _LABELS.get(category, category))
        return menu.MENU_BACK
    return module.run(devbase_root)


def _select_top(rows: list[dict]):
    """トップ画面: プロジェクト一覧 + カテゴリ項目から 1 件選ばせる。

    戻り値: rows の index (``int`` = プロジェクト選択) / カテゴリ key (``str``) /
    ``None`` (Esc・Ctrl-C → 終了)。プロジェクトとカテゴリは値の型で判別する。
    件数が多いため文字入力での絞り込み (search=True) を有効にする。
    """
    entries = _build_menu_entries(rows, colorize=_STATUS_COLOR)
    choices: list[tuple[str, object]] = [(entry, i) for i, entry in enumerate(entries)]
    choices += [(f"{label} ({key})", key) for key, label in TOP_CATEGORIES]
    return menu.select(
        "プロジェクトまたは操作を選択 "
        "(↑↓ 移動 / 名前で絞り込み / Enter 決定 / Esc・Ctrl-C 終了):",
        choices, back=False, search=True)


def _top_menu_loop(devbase_root: Path) -> int:
    """トップ画面 (プロジェクト一覧) のループ。

    最後に実行した操作の rc (``last_rc``) を記憶し、中止時はそれを返すことで
    ``project up/down/rebuild`` の失敗が ``devbase list`` の終了コードへ伝搬する。
    操作を何もしなかった場合 (Esc/Ctrl-C のみ) は ``last_rc`` の初期値 0。

    判定は必ず ``is`` 同一性で行う (rc=0 を ``None`` / ``MENU_BACK`` と誤マッチさせない)。
    """
    last_rc = 0
    projects_dir = Path(devbase_root) / "projects"
    while True:
        rows = list_projects(projects_dir)
        if not rows:
            # プロジェクト未作成でもカテゴリ操作 (env/plugin/...) は使えるため
            # 終了せず案内だけ出して一覧 (カテゴリのみ) を表示する。
            logger.info("プロジェクトがありません (%s)。", projects_dir)

        sel = _select_top(rows)
        if sel is None:
            # トップで Esc / Ctrl-C → これまでの実行 rc を返して終了
            logger.info("中止しました。")
            return last_rc

        if isinstance(sel, str):
            result = _route(sel, devbase_root)
        else:
            result = actions_project.handle_row(devbase_root, rows[sel])

        if result is None:
            # カテゴリ・サブメニュー内で Ctrl-C → 全体中止 (直近の実行 rc を返す)
            logger.info("中止しました。")
            return last_rc
        if result is menu.MENU_BACK:
            # 操作なしで一覧へ戻り再表示 (rc は更新しない)
            continue
        # int rc: 操作を実行した → rc を記憶して一覧を再表示
        last_rc = result


def run(devbase_root: Path, args) -> int:
    """`devbase list` / `devbase project list` の入口。

    - interactive=False / 非 TTY: 一覧テーブルのみ (従来挙動)。
    - questionary 不在: 番号入力フォールバック (project up) へ縮退。
    - それ以外: プロジェクト一覧トップの階層メニューを開く。
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
