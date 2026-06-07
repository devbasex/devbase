"""Project listing commands (`devbase project list` / `devbase list`).

PLAN06 Task 3。`$DEVBASE_ROOT/projects/` 配下を NAME / PLUGIN / STATUS で一覧表示し、
``--interactive`` で選択 → `project up` 起動を行う。

ライフサイクル操作 (up/down/ps/login/logs/scale/build) は引き続き
``commands/container.py`` の共有ハンドラが担当し、本モジュールは listing と
interactive 起動のみを担う。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from devbase.log import get_logger

logger = get_logger(__name__)

# simple_term_menu は Unix 専用の任意依存。未導入/非対応環境では番号入力に
# フォールバックするため、import 失敗を許容する。
try:
    from simple_term_menu import TerminalMenu
    _HAVE_TERMINAL_MENU = True
except ImportError:  # pragma: no cover - 未導入環境のフォールバック経路
    TerminalMenu = None
    _HAVE_TERMINAL_MENU = False

# STATUS 色付けの有効/無効。simple_term_menu の桁計算と ANSI が衝突する端末では
# False に倒してプレーン表示にする (機能 > 装飾)。
_STATUS_COLOR = True


def _resolve_plugin_name(entry: Path) -> str | None:
    """projects/ 配下の entry が属する plugin 名を解決する。

    entry が symlink の場合、その **リンク先** (``../<plugin.path>/projects/<proj>``)
    から plugin 名を解決する。PLAN04 の同名衝突 suffix (例 ``carmo.takemi--carmo``)
    は **リンク名のみ** に付与され、リンク先 dir 名は素の ``<proj>`` のままであるため、
    リンク名でなくリンク先を辿ることで suffix の有無に関わらず正しく解決できる。

    plugin 名はリンク先パスの ``projects`` セグメント直前の要素:
      - repos ベース: ``../repos/<owner>--<repo>/<plugin>/projects/<proj>`` → ``<plugin>``
      - --link ベース: ``../plugins/<name>/projects/<proj>``               → ``<name>``

    symlink でない実ディレクトリ (plugin に属さない) や解決不能な場合は ``None``。
    リンク先実体が存在しない (broken symlink) 場合もリンクテキストから解決する。
    """
    if not entry.is_symlink():
        return None
    try:
        target = os.readlink(entry)
    except OSError:
        return None

    parts = Path(target).parts
    # `projects` の最後の出現位置 (proj 名の直前) を採用する。
    # ただし直前要素が plugin 名として無効なパス区切り (`/` ルートや `..` 相対) の
    # 場合は解決失敗扱い (None)。例: `/projects/proj` → parts[0] が `/` になる。
    for i in range(len(parts) - 1, 0, -1):
        if parts[i] == "projects":
            candidate = parts[i - 1]
            if candidate in (os.sep, "/", "..", "."):
                return None
            return candidate
    return None


def list_projects(projects_dir: Path) -> list[dict]:
    """projects/ 配下のプロジェクトを NAME / PLUGIN / STATUS で列挙する。

    各要素は ``{"name", "plugin", "status"}``。

    - ``name``:   projects/ 内のエントリ名 (衝突 suffix 付きもそのまま)
    - ``plugin``: ``_resolve_plugin_name`` の結果。実ディレクトリ / 解決不能は ``"-"``
    - ``status``: ``status._container_status_for`` の状態文字列。
                  compose.yml 無し / docker 不在等で取得できない場合は ``"unknown"``

    symlink (broken 含む) と実ディレクトリの両方を対象とする。
    """
    # status ロジックは commands/status.py と共有する (PLAN06 リファクタで per-entry
    # 関数 _container_status_for を分離済み)。import は循環回避のため関数内で行う。
    from devbase.commands import status as status_mod

    if not projects_dir.exists():
        return []

    entries = [
        # broken symlink は is_dir() が False になるため symlink 自体も拾う。
        entry for entry in sorted(projects_dir.iterdir())
        if entry.is_symlink() or entry.is_dir()
    ]
    if not entries:
        return []

    # コンテナ状態は docker ps 1 回で全プロジェクトぶん集計し (counts)、各 entry で
    # 使い回す。プロジェクト数ぶん docker compose ps を起動していた旧実装の
    # サブプロセスコストを N→1 に削減するため、並列化 (ThreadPoolExecutor) も不要。
    counts = status_mod._running_counts_by_project()

    def _status_for(entry: Path) -> str:
        # is_dir() は symlink 先まで辿る。broken symlink は False → unknown のまま。
        if not entry.is_dir():
            return "unknown"
        st = status_mod._container_status_for(entry, counts)
        return st["status"] if st is not None else "unknown"

    return [
        {
            "name": entry.name,
            "plugin": _resolve_plugin_name(entry) or "-",
            "status": _status_for(entry),
        }
        for entry in entries
    ]


def _print_table(rows: list[dict]) -> None:
    """NAME / PLUGIN / STATUS の整列テーブルを標準出力に表示する。"""
    name_w = max(len("NAME"), *(len(r["name"]) for r in rows))
    plugin_w = max(len("PLUGIN"), *(len(r["plugin"]) for r in rows))
    print(f"{'NAME':<{name_w}}  {'PLUGIN':<{plugin_w}}  STATUS")
    for r in rows:
        print(f"{r['name']:<{name_w}}  {r['plugin']:<{plugin_w}}  {r['status']}")


# STATUS 色付け用 ANSI。実機で桁崩れ等が出る場合は _STATUS_COLOR を False にする。
_ANSI_GREEN = "\033[32m"
_ANSI_GREY = "\033[90m"
_ANSI_RESET = "\033[0m"


def _color_status(status: str) -> str:
    """STATUS 文字列に色を付ける。running 系=緑 / stopped=灰 / その他=無装飾。

    color 対象の文字列は status._container_status_for が返す
    ``running (N containers)`` / ``stopped`` と、project.list_projects が補う
    ``unknown`` を想定する。
    """
    if status.startswith("running"):
        return f"{_ANSI_GREEN}{status}{_ANSI_RESET}"
    if status == "stopped":
        return f"{_ANSI_GREY}{status}{_ANSI_RESET}"
    return status


def _build_menu_entries(rows: list[dict], colorize: bool = False) -> list[str]:
    """rows を simple_term_menu 用の表示文字列へ変換する。

    返り値の index は rows の index と 1:1 対応する (entry i ↔ rows[i])。
    先頭 9 件には simple_term_menu のショートカット記法 ``[n]`` (n=1..9) を付与し、
    数字キーで即ジャンプできるようにする。10 件目以降は ``[n] `` と同じ 4 文字幅
    (スペース) で字下げして桁を揃える。``colorize`` が True のとき STATUS に
    ANSI 色を付ける (検索/桁計算が崩れる端末向けに呼び出し側で False にできる)。
    """
    name_w = max(len("NAME"), *(len(r["name"]) for r in rows))
    plugin_w = max(len("PLUGIN"), *(len(r["plugin"]) for r in rows))
    entries: list[str] = []
    for i, r in enumerate(rows):
        status = _color_status(r["status"]) if colorize else r["status"]
        body = f"{r['name']:<{name_w}}  {r['plugin']:<{plugin_w}}  {status}"
        if i < 9:
            entries.append(f"[{i + 1}] {body}")
        else:
            entries.append(f"    {body}")  # "[n] " と同じ 4 文字幅で字下げ
    return entries


def _start_project_up(name: str) -> int:
    """``project up <name>`` を共有ハンドラ cmd_project 経由で起動する。"""
    import types

    from devbase.commands.container import cmd_project
    return cmd_project(types.SimpleNamespace(subcommand="up", name=name, scale=None))


def _show_menu(rows: list[dict]) -> int | None:
    """TerminalMenu を起動し、選択された rows の index を返す (中止時 None)。

    テストではこの関数自体を monkeypatch して TerminalMenu の実起動を避ける。
    """
    entries = _build_menu_entries(rows, colorize=_STATUS_COLOR)
    menu = TerminalMenu(
        entries,
        title=("起動するプロジェクトを選択 "
               "(↑↓ 移動 / 1-9 ジャンプ / / 検索 / Enter 決定 / Esc 中止):"),
        cycle_cursor=True,
        clear_screen=False,
        show_search_hint=True,
    )
    return menu.show()


def _tui_select_and_up(rows: list[dict]) -> int:
    """TUI メニューで 1 件選択し ``project up <name>`` を起動する。"""
    idx = _show_menu(rows)
    if idx is None:
        logger.info("中止しました。")
        return 0
    return _start_project_up(rows[idx]["name"])


def _interactive_select_and_up(rows: list[dict]) -> int:
    """一覧から 1 件選択して ``project up`` を起動する (TTY 専用)。

    simple_term_menu が利用可能なら矢印キー対応の TUI メニューを使う。未導入環境
    では現行の番号入力方式 (_fallback_select_and_up) にフォールバックする。
    """
    if _HAVE_TERMINAL_MENU:
        return _tui_select_and_up(rows)
    logger.warning(
        "simple_term_menu が未導入のため番号入力にフォールバックします "
        "(`uv sync` で導入すると矢印キー選択が使えます)。"
    )
    return _fallback_select_and_up(rows)


def _fallback_select_and_up(rows: list[dict]) -> int:
    """番号入力で 1 件選択し ``project up <name>`` を起動する (simple_term_menu 未導入時のフォールバック)。

    外部依存 (simple_term_menu 等) を増やさず stdlib の ``input()`` で実装する。
    非対話環境 (stdin が閉じている等で EOFError) ではエラー終了する。空入力は中止。
    """
    print("起動するプロジェクトを選択してください:")
    for i, r in enumerate(rows, 1):
        print(f"  [{i}] {r['name']}  ({r['plugin']}, {r['status']})")

    # 一覧取得が重い場合があるため、誤入力 (数値以外 / 範囲外) では即終了せず
    # 再入力を促す。空入力は中止、非 TTY (EOFError) はエラー終了。
    while True:
        try:
            raw = input("番号 (空で中止): ").strip()
        except EOFError:
            logger.error("対話入力ができません (非 TTY 環境)。"
                         "`devbase project up <name>` で直接指定してください。")
            return 1
        except KeyboardInterrupt:
            # Ctrl+C は traceback を出さず中止として扱う。
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

    return _start_project_up(rows[idx - 1]["name"])


def cmd_project_list(devbase_root: Path, args) -> int:
    """`devbase project list [--interactive]` / `devbase list [--interactive]`。"""
    projects_dir = Path(devbase_root) / "projects"
    rows = list_projects(projects_dir)

    if not rows:
        logger.info("プロジェクトがありません (%s)。", projects_dir)
        return 0

    # 対話選択はデフォルト ON。ただし非 TTY (パイプ / CI / リダイレクト) では
    # input() が EOFError になり実用にならないため、自動的に一覧表示へフォールバック。
    # stdin / stdout のいずれかが非 TTY (`devbase list | cat`, `> out.txt` 等) なら
    # 対話プロンプトが表示できない / 読めないため、確実に一覧表示へフォールバックする。
    if getattr(args, "interactive", True) and sys.stdin.isatty() and sys.stdout.isatty():
        return _interactive_select_and_up(rows)

    _print_table(rows)
    return 0
