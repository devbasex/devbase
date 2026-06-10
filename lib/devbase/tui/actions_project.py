"""project カテゴリの TUI 操作フロー (PLAN31_2 PR1: 既存挙動の非回帰移送)。

旧 ``commands/project.py`` の ``_tui_select_and_up`` / ``_show_menu`` /
``_show_action_menu`` / ``_fallback_select_and_up`` をこのモジュールへ移送し、
メニュー部品は ``tui.menu`` に、ハンドラ委譲は ``tui.dispatch`` に一般化した。

PR1 で扱うのは既存の **一覧選択 → (running なら up/rebuild/down サブメニュー) →
それ以外は直接 up** までで、login/ps/logs/scale/build の追加は PR2 で行う。

一覧表示・整形 (``list_projects`` / ``_build_menu_entries``) は ``commands/project``
の純粋ロジックを再利用する (TUI からも CLI(table) からも共有)。
"""

from __future__ import annotations

from pathlib import Path

from devbase.commands.project import (
    _STATUS_COLOR,
    _build_menu_entries,
    list_projects,
)
from devbase.log import get_logger
from devbase.tui import menu
from devbase.tui.dispatch import dispatch_lifecycle

logger = get_logger(__name__)


def _select_project(rows: list[dict]):
    """一覧から 1 件選ばせ rows の index を返す。Esc → ``MENU_BACK`` / Ctrl-C → ``None``。

    件数が多いため文字入力での絞り込み (search=True) を有効にする。search 有効時は
    ← が入力カーソル移動と衝突するため戻る操作は Esc のみ (menu.select が調整する)。
    """
    entries = _build_menu_entries(rows, colorize=_STATUS_COLOR)
    choices = [(entry, i) for i, entry in enumerate(entries)]
    return menu.select(
        "操作するプロジェクトを選択 "
        "(↑↓ 移動 / 名前で絞り込み / Enter 決定 / Esc 戻る / Ctrl-C 中止):",
        choices, back=True, search=True)


def _select_action(name: str):
    """running 中プロジェクトの操作 (up/rebuild/down) を選ぶサブメニュー。

    戻り値: action 文字列 / ``MENU_BACK`` (Esc・← → 一覧へ戻る) / ``None`` (Ctrl-C 中止)。
    """
    choices = [
        ("再起動 (up)", "up"),
        ("再ビルド (rebuild --no-cache)", "rebuild"),
        ("停止 (down)", "down"),
    ]
    return menu.select(
        f"'{name}' は起動中です。操作を選択 "
        "(↑↓ 移動 / Enter 決定 / ←・Esc 戻る / Ctrl-C 中止):",
        choices, back=True, search=False)


def run(devbase_root: Path):
    """プロジェクト操作カテゴリ。一覧選択 → up/rebuild/down を起動する。

    戻り値:
    - ``menu.MENU_BACK``: Esc/← または操作完了でトップメニューへ戻る
    - ``None``: Ctrl-C による全体中止

    選択行が running 中なら ``_select_action`` で up/rebuild/down を選ばせ、それ以外
    (stopped / unknown 等) は従来どおり直接 ``project up`` を起動する。サブメニューで
    Esc/← を押すと (``MENU_BACK``) 一覧へ戻る。操作完了後はトップメニューへ復帰する
    (plan 3.5 状態遷移: Exec → Top)。
    """
    projects_dir = Path(devbase_root) / "projects"
    while True:
        rows = list_projects(projects_dir)
        if not rows:
            logger.info("プロジェクトがありません (%s)。", projects_dir)
            return menu.MENU_BACK

        idx = _select_project(rows)
        if idx is menu.MENU_BACK:
            return menu.MENU_BACK
        if idx is None:
            return None  # Ctrl-C → 全体中止

        row = rows[idx]
        name = row["name"]
        if str(row.get("status", "")).startswith("running"):
            action = _select_action(name)
            if action is menu.MENU_BACK:
                continue          # 一覧へ戻る
            if action is None:
                return None       # Ctrl-C → 全体中止
            dispatch_lifecycle(action, name, scale=None)
        else:
            dispatch_lifecycle("up", name, scale=None)

        return menu.MENU_BACK     # 操作完了 → トップメニューへ復帰


def fallback_select_and_up(rows: list[dict]) -> int:
    """番号入力で 1 件選択し ``project up <name>`` を起動する (questionary 未導入時)。

    旧 ``project.py:_fallback_select_and_up`` の非回帰移送。questionary 不在環境では
    トップ階層メニューを出さず、この従来フロー (番号入力 → up) に縮退して muscle-memory
    を保全する。外部依存を増やさず stdlib ``input()`` で実装する。空入力は中止、非 TTY
    (EOFError) はエラー終了 (rc=1)、Ctrl-C は中止 (rc=0)。
    """
    print("起動するプロジェクトを選択してください:")
    for i, r in enumerate(rows, 1):
        print(f"  [{i}] {r['name']}  ({r['plugin']}, {r['status']})")

    # 一覧取得が重い場合があるため、誤入力 (数値以外 / 範囲外) では即終了せず再入力を促す。
    while True:
        try:
            raw = input("番号 (空で中止): ").strip()
        except EOFError:
            logger.error("対話入力ができません (非 TTY 環境)。"
                         "`devbase project up <name>` で直接指定してください。")
            return 1
        except KeyboardInterrupt:
            print()
            logger.info("中止しました。")
            return 0

        if not raw:
            logger.info("中止しました。")
            return 0

        try:
            idx = int(raw)
        except ValueError:
            logger.error("番号で指定してください: %r", raw)
            continue

        if not (1 <= idx <= len(rows)):
            logger.error("範囲外の番号です: %d (1〜%d)", idx, len(rows))
            continue

        break

    return dispatch_lifecycle("up", rows[idx - 1]["name"], scale=None)
