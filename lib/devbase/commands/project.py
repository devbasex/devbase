"""Project listing helpers (`devbase project list` / `devbase list`).

PLAN06 Task 3 で追加した ``$DEVBASE_ROOT/projects/`` の一覧 (NAME / PLUGIN / STATUS)
表示と整形ロジックを担う。PLAN31_2 で **対話 TUI 部分は ``devbase.tui`` パッケージへ
分離**し、本モジュールは listing と整形 (table / メニュー表示文字列) の純粋ロジックに
専念する (TUI からも CLI table からも共有される)。

ライフサイクル操作 (up/down/ps/login/logs/scale/build) は引き続き
``commands/container.py`` の共有ハンドラが担当する。``cmd_project_list`` は
``devbase.tui.run`` を入口として呼ぶだけの薄いラッパになった。
"""

from __future__ import annotations

import os
from pathlib import Path

from devbase.log import get_logger

logger = get_logger(__name__)

# STATUS 色付けの有効/無効。メニュー entry に生 ANSI を埋め込むと prompt_toolkit の
# 表示幅計算と干渉しうるため、実機検証が完了するまではメニューでは色を付けず False を
# 既定とする (機能 > 装飾)。テーブル表示 (_print_table) は端末へ直接書くため影響を
# 受けない。tui.actions_project が _build_menu_entries 呼び出し時に参照する。
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


def cmd_project_list(devbase_root: Path, args) -> int:
    """`devbase project list [--interactive]` / `devbase list [--interactive]`。

    実体は ``devbase.tui.run`` (トップ階層メニュー) へ委譲する。非 TTY /
    ``--no-interactive`` / questionary 不在時のフォールバックは tui 側で処理する。
    """
    from devbase.tui import run as tui_run

    return tui_run(Path(devbase_root), args)
