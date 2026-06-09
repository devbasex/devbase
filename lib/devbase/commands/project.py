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

# questionary (prompt_toolkit ベース) は任意依存。未導入環境では番号入力に
# フォールバックするため、import 失敗を許容する。questionary は矢印キー移動 +
# 文字入力での絞り込み (use_search_filter) に対応し、prompt_toolkit が入力を
# 1 イベントずつ分解するため、旧 simple_term_menu のような ↑長押し時の入力
# 取りこぼし (連結エスケープシーケンスの破棄) が構造的に発生しない。
try:
    import questionary
    _HAVE_QUESTIONARY = True
except ImportError:  # pragma: no cover - 未導入環境のフォールバック経路
    questionary = None
    _HAVE_QUESTIONARY = False

# STATUS 色付けの有効/無効。menu entry に生 ANSI を埋め込むと prompt_toolkit の
# 表示幅計算と干渉しうるため、実機検証が完了するまではメニューでは色を付けず
# False を既定とする (機能 > 装飾)。テーブル表示 (_print_table) は端末へ直接書く
# ため影響を受けず、色付けは別途 questionary の style で検討する。
_STATUS_COLOR = False


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
    """rows を questionary メニュー用の表示文字列へ変換する。

    返り値の index は rows の index と 1:1 対応する (entry i ↔ rows[i])。
    各行に**右寄せの番号ラベル** (``1``〜``N``) を付け、全件に通し番号を振る
    (旧 ``[1]``〜``[9]`` ショートカットは先頭 9 件しかカバーできず低カバレッジ
    だったため廃止)。番号は視認用で、選択は矢印キー / 文字入力での絞り込みで行う。
    ``colorize`` が True のとき STATUS に ANSI 色を付ける (表示幅が崩れる端末向けに
    呼び出し側で False にできる)。
    """
    name_w = max(len("NAME"), *(len(r["name"]) for r in rows))
    plugin_w = max(len("PLUGIN"), *(len(r["plugin"]) for r in rows))
    num_w = len(str(len(rows)))
    entries: list[str] = []
    for i, r in enumerate(rows):
        status = _color_status(r["status"]) if colorize else r["status"]
        body = f"{r['name']:<{name_w}}  {r['plugin']:<{plugin_w}}  {status}"
        entries.append(f"{i + 1:>{num_w}}  {body}")
    return entries


def _start_project_action(name: str, action: str) -> int:
    """``project <action> <name>`` を共有ハンドラ cmd_project 経由で起動する。

    ``action`` は ``"up"`` / ``"down"`` / ``"rebuild"``。共有ハンドラ
    (_dispatch_lifecycle) が ``name`` でディレクトリ解決 (chdir) してから各
    サブコマンドを実行する。``scale`` は up のみが参照するが、常に付与しても
    他コマンドは無視するため一律 None を渡す。
    """
    import types

    from devbase.commands.container import cmd_project
    return cmd_project(types.SimpleNamespace(subcommand=action, name=name, scale=None))


def _start_project_up(name: str) -> int:
    """``project up <name>`` を起動する (後方互換の薄いラッパ)。"""
    return _start_project_action(name, "up")


# サブメニュー (_show_action_menu) で Esc を押した際の「トップメニューへ戻る」
# シグナル。``None`` (= Ctrl-C による全体中止) と区別するための番兵。
_MENU_BACK = object()


def _add_escape_binding(question, handler):
    """questionary の select に Esc 単独押下のハンドラを後付けする共通処理。

    questionary 2.x の select は Ctrl-C / Ctrl-Q しか割り当てないため、生成済み
    ``Question.application`` の key_bindings に Escape ハンドラを足す。

    Escape は矢印キー等のエスケープシーケンス (``\\x1b[A`` 等) の先頭バイトでも
    あるため、``eager=False`` で登録し prompt_toolkit のフラッシュ待ちで単独 Esc
    のみを拾う (矢印キー移動と衝突させない)。
    """
    from prompt_toolkit.keys import Keys

    question.application.key_bindings.add(Keys.Escape)(handler)
    return question


def _with_escape_cancel(question):
    """Esc 単独押下で中止する select を返す。

    Ctrl-C と同じく ``KeyboardInterrupt`` で抜けるので ``ask()`` は ``None``
    (= 中止) を返す。トップメニュー (戻り先が無い) 用。
    """
    def _cancel(event):
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    return _add_escape_binding(question, _cancel)


def _with_escape_back(question):
    """Esc 単独押下で ``_MENU_BACK`` を返す select を返す。

    Ctrl-C は questionary 既定どおり中止 (``ask()`` が ``None``) のまま残し、Esc
    だけを「1 つ前のメニューへ戻る」シグナルに割り当てる。サブメニュー用。
    """
    def _back(event):
        event.app.exit(result=_MENU_BACK)

    return _add_escape_binding(question, _back)


def _show_menu(rows: list[dict]) -> int | None:
    """questionary の select を起動し、選択された rows の index を返す (中止時 None)。

    テストではこの関数自体を monkeypatch して questionary の実起動を避ける。
    """
    entries = _build_menu_entries(rows, colorize=_STATUS_COLOR)
    choices = [questionary.Choice(title=entry, value=i)
               for i, entry in enumerate(entries)]
    question = questionary.select(
        "起動するプロジェクトを選択 (↑↓ 移動 / 名前で絞り込み / Enter 決定 / Esc・Ctrl-C 中止):",
        choices=choices,
        use_arrow_keys=True,
        use_jk_keys=False,        # use_search_filter と併用不可のため False
        use_search_filter=True,   # 文字入力でプロジェクト名等を部分一致絞り込み
        use_shortcuts=False,      # 単一キーショートカットは使わない
    )
    return _with_escape_cancel(question).ask()  # value (= rows index) / 中止時 None


def _show_action_menu(name: str):
    """running 中プロジェクトの操作 (up/rebuild/down) を選ぶサブメニュー。

    戻り値:
    - action 文字列 (``"up"`` / ``"rebuild"`` / ``"down"``): 操作を選択
    - ``_MENU_BACK``: Esc 押下 → トップメニューへ戻る
    - ``None``: Ctrl-C 押下 → 全体中止

    テストではこの関数を monkeypatch する。
    """
    choices = [
        questionary.Choice(title="再起動 (up)", value="up"),
        questionary.Choice(title="再ビルド (rebuild --no-cache)", value="rebuild"),
        questionary.Choice(title="停止 (down)", value="down"),
    ]
    question = questionary.select(
        f"'{name}' は起動中です。操作を選択 "
        "(↑↓ 移動 / Enter 決定 / Esc 戻る / Ctrl-C 中止):",
        choices=choices,
        use_arrow_keys=True,
        use_shortcuts=False,
    )
    return _with_escape_back(question).ask()


def _tui_select_and_up(rows: list[dict]) -> int:
    """TUI メニューで 1 件選択して操作を起動する。

    選択行が running 中なら ``_show_action_menu`` で up/rebuild/down を選ばせ、
    それ以外 (stopped / unknown 等) は従来どおり直接 ``project up`` を起動する。
    サブメニューで Esc を押すと (``_MENU_BACK``) トップメニューへ戻る。
    """
    while True:
        idx = _show_menu(rows)
        if idx is None:
            logger.info("中止しました。")
            return 0

        row = rows[idx]
        name = row["name"]
        if str(row.get("status", "")).startswith("running"):
            action = _show_action_menu(name)
            if action is _MENU_BACK:
                continue                      # Esc → トップメニューへ戻る
            if action is None:
                logger.info("中止しました。")   # Ctrl-C → 全体中止
                return 0
            return _start_project_action(name, action)

        return _start_project_action(name, "up")


def _interactive_select_and_up(rows: list[dict]) -> int:
    """一覧から 1 件選択して ``project up`` を起動する (TTY 専用)。

    questionary が利用可能なら矢印キー + 絞り込み対応の TUI メニューを使う。未導入
    環境では現行の番号入力方式 (_fallback_select_and_up) にフォールバックする。
    """
    if _HAVE_QUESTIONARY:
        return _tui_select_and_up(rows)
    logger.warning(
        "questionary が未導入のため番号入力にフォールバックします "
        "(`uv sync` で導入すると矢印キー選択が使えます)。"
    )
    return _fallback_select_and_up(rows)


def _fallback_select_and_up(rows: list[dict]) -> int:
    """番号入力で 1 件選択し ``project up <name>`` を起動する (questionary 未導入時のフォールバック)。

    外部依存 (questionary 等) を増やさず stdlib の ``input()`` で実装する。
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
