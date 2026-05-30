"""Project listing commands (`devbase project list` / `devbase list`).

PLAN06 Task 3。`$DEVBASE_ROOT/projects/` 配下を NAME / PLUGIN / STATUS で一覧表示し、
``--interactive`` で選択 → `project up` 起動を行う。

ライフサイクル操作 (up/down/ps/login/logs/scale/build) は引き続き
``commands/container.py`` の共有ハンドラが担当し、本モジュールは listing と
interactive 起動のみを担う。
"""

from __future__ import annotations

import os
from pathlib import Path

from devbase.log import get_logger

logger = get_logger(__name__)


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
    from concurrent.futures import ThreadPoolExecutor

    from devbase.commands import status as status_mod

    if not projects_dir.exists():
        return []

    entries = [
        # broken symlink は is_dir() が False になるため symlink 自体も拾う。
        entry for entry in sorted(projects_dir.iterdir())
        if entry.is_symlink() or entry.is_dir()
    ]

    def _status_for(entry: Path) -> str:
        # is_dir() は symlink 先まで辿る。broken symlink は False → unknown のまま。
        # _container_status_for は cwd= 引数で完結し global chdir を行わないため
        # スレッド安全。各 `docker compose ps` は I/O バウンドで 10s timeout を
        # 持つため、プロジェクト数が増えても並列化で総待ち時間を抑える。
        if not entry.is_dir():
            return "unknown"
        st = status_mod._container_status_for(entry)
        return st["status"] if st is not None else "unknown"

    # entries が空だと max_workers=0 で ValueError になるため早期 return。
    if not entries:
        return []

    with ThreadPoolExecutor(max_workers=min(8, len(entries))) as ex:
        statuses = list(ex.map(_status_for, entries))

    return [
        {
            "name": entry.name,
            "plugin": _resolve_plugin_name(entry) or "-",
            "status": status,
        }
        for entry, status in zip(entries, statuses)
    ]


def _print_table(rows: list[dict]) -> None:
    """NAME / PLUGIN / STATUS の整列テーブルを標準出力に表示する。"""
    name_w = max(len("NAME"), *(len(r["name"]) for r in rows))
    plugin_w = max(len("PLUGIN"), *(len(r["plugin"]) for r in rows))
    print(f"{'NAME':<{name_w}}  {'PLUGIN':<{plugin_w}}  STATUS")
    for r in rows:
        print(f"{r['name']:<{name_w}}  {r['plugin']:<{plugin_w}}  {r['status']}")


def _interactive_select_and_up(rows: list[dict]) -> int:
    """一覧から番号入力で 1 件選択し ``project up <name>`` を起動する。

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

    name = rows[idx - 1]["name"]
    # name 解決 (chdir) + up は共有ハンドラ cmd_project に委譲する。
    import types

    from devbase.commands.container import cmd_project
    return cmd_project(types.SimpleNamespace(subcommand="up", name=name, scale=None))


def cmd_project_list(devbase_root: Path, args) -> int:
    """`devbase project list [--interactive]` / `devbase list [--interactive]`。"""
    projects_dir = Path(devbase_root) / "projects"
    rows = list_projects(projects_dir)

    if not rows:
        logger.info("プロジェクトがありません (%s)。", projects_dir)
        return 0

    if getattr(args, "interactive", False):
        return _interactive_select_and_up(rows)

    _print_table(rows)
    return 0
