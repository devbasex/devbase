"""PLAN06 Task 3: `project list` 一覧表示 + `--interactive` 選択起動のテスト

検証対象:
- `lib/devbase/commands/project.py`
  - `_resolve_plugin_name`: symlink 先から plugin 名を解決する (衝突 suffix 耐性)
  - `list_projects`: projects/ 配下を NAME/PLUGIN/STATUS で列挙する
  - `cmd_project_list`: table 表示 / `--interactive` での選択起動
- `lib/devbase/commands/status.py`
  - `_container_status_for`: per-entry status 抽出後の回帰
- `lib/devbase/cli.py`
  - `project list` parser / dispatch ルーティング / トップレベル `list` シノニム / prefix 解決
"""

from __future__ import annotations

import os
import types
from pathlib import Path

import pytest

from devbase import cli


# ---------------------------------------------------------------------------
# 補助: projects/ 配下に plugin project への symlink を作る
# ---------------------------------------------------------------------------

def _make_plugin_project(devbase_root: Path, plugin_path: str, proj: str) -> Path:
    """repos/ or plugins/ 配下に plugin の projects/<proj> 実体を作って返す。"""
    target_dir = devbase_root / plugin_path / "projects" / proj
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def _link_project(devbase_root: Path, link_name: str, plugin_path: str, proj: str) -> Path:
    """projects/<link_name> -> ../<plugin_path>/projects/<proj> の相対 symlink を作る。

    syncer.sync_projects と同じ相対ターゲット形式 (衝突時は link_name に suffix が
    付くが、ターゲット dir 名は素の proj のまま) を再現する。
    """
    projects_dir = devbase_root / "projects"
    projects_dir.mkdir(exist_ok=True)
    target = Path("..") / plugin_path / "projects" / proj
    link = projects_dir / link_name
    link.symlink_to(target)
    return link


# ---------------------------------------------------------------------------
# _resolve_plugin_name
# ---------------------------------------------------------------------------

def test_resolve_plugin_name_repos_based(tmp_path):
    from devbase.commands.project import _resolve_plugin_name

    _make_plugin_project(tmp_path, "repos/owner--repo/myplugin", "carmo")
    link = _link_project(tmp_path, "carmo", "repos/owner--repo/myplugin", "carmo")

    assert _resolve_plugin_name(link) == "myplugin"


def test_resolve_plugin_name_linked(tmp_path):
    from devbase.commands.project import _resolve_plugin_name

    _make_plugin_project(tmp_path, "plugins/foo", "carmo")
    link = _link_project(tmp_path, "carmo", "plugins/foo", "carmo")

    assert _resolve_plugin_name(link) == "foo"


def test_resolve_plugin_name_collision_suffix_uses_target_not_linkname(tmp_path):
    """衝突 suffix (carmo.takemi) はリンク名のみに付き、ターゲット dir は素の carmo。

    PLUGIN 解決は link 名でなく symlink 先から行うため suffix で壊れてはならない。
    """
    from devbase.commands.project import _resolve_plugin_name

    _make_plugin_project(tmp_path, "repos/takemi--carmo/carmo-plugin", "carmo")
    link = _link_project(tmp_path, "carmo.takemi--carmo",
                         "repos/takemi--carmo/carmo-plugin", "carmo")

    assert _resolve_plugin_name(link) == "carmo-plugin"


def test_resolve_plugin_name_real_dir_returns_none(tmp_path):
    """symlink でない実ディレクトリは plugin に属さないため None。"""
    from devbase.commands.project import _resolve_plugin_name

    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    real = projects_dir / "standalone"
    real.mkdir()

    assert _resolve_plugin_name(real) is None


def test_resolve_plugin_name_absolute_root_target_returns_none(tmp_path):
    """symlink 先が `/projects/proj` のような絶対パスだと parts[0] が '/' になる。

    plugin 名として無効な root 区切りを返さず None にする (堅牢性指摘 #36)。
    """
    from devbase.commands.project import _resolve_plugin_name

    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    link = projects_dir / "rooted"
    link.symlink_to("/projects/proj")  # 先頭 '/' で parts[0] == '/'
    assert _resolve_plugin_name(link) is None


def test_resolve_plugin_name_relative_dotdot_target_returns_none(tmp_path):
    """`../projects/proj` だと直前要素が '..' になり plugin 名として無効 → None。"""
    from devbase.commands.project import _resolve_plugin_name

    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    link = projects_dir / "dotdot"
    link.symlink_to(Path("..") / "projects" / "proj")
    assert _resolve_plugin_name(link) is None


def test_resolve_plugin_name_broken_symlink(tmp_path):
    """ターゲットが存在しない symlink でも link テキストから plugin を解決できる。"""
    from devbase.commands.project import _resolve_plugin_name

    link = _link_project(tmp_path, "ghost", "repos/o--r/ghostplugin", "ghost")
    # ターゲット実体は作らない (broken)
    assert not link.exists()
    assert _resolve_plugin_name(link) == "ghostplugin"


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------

def test_list_projects_enumerates_name_plugin_status(tmp_path, monkeypatch):
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod

    _make_plugin_project(tmp_path, "repos/o--r/alpha", "alpha-proj")
    _link_project(tmp_path, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    _make_plugin_project(tmp_path, "plugins/beta", "beta-proj")
    _link_project(tmp_path, "beta-proj", "plugins/beta", "beta-proj")

    # status は docker に依存させず固定値を返す
    def fake_status(entry, counts=None):
        return {"name": entry.name, "status": "running (2 containers)", "count": 2}

    monkeypatch.setattr(status_mod, "_container_status_for", fake_status)

    rows = project_mod.list_projects(tmp_path / "projects")
    by_name = {r["name"]: r for r in rows}

    assert by_name["alpha-proj"]["plugin"] == "alpha"
    assert by_name["alpha-proj"]["status"] == "running (2 containers)"
    assert by_name["beta-proj"]["plugin"] == "beta"


def test_list_projects_unknown_status_when_none(tmp_path, monkeypatch):
    """_container_status_for が None (compose.yml 無し/docker 不在) なら 'unknown'。"""
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod

    _make_plugin_project(tmp_path, "repos/o--r/alpha", "alpha-proj")
    _link_project(tmp_path, "alpha-proj", "repos/o--r/alpha", "alpha-proj")

    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)

    rows = project_mod.list_projects(tmp_path / "projects")
    assert rows[0]["status"] == "unknown"


def test_list_projects_real_dir_plugin_dash(tmp_path, monkeypatch):
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod

    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "standalone").mkdir()

    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)

    rows = project_mod.list_projects(projects_dir)
    assert rows[0]["name"] == "standalone"
    assert rows[0]["plugin"] == "-"


def test_list_projects_empty_when_no_projects_dir(tmp_path):
    from devbase.commands import project as project_mod
    assert project_mod.list_projects(tmp_path / "projects") == []


# ---------------------------------------------------------------------------
# cmd_project_list: table 出力
# ---------------------------------------------------------------------------

def test_cmd_project_list_prints_table(tmp_path, monkeypatch, capsys):
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod

    _make_plugin_project(tmp_path, "repos/o--r/alpha", "alpha-proj")
    _link_project(tmp_path, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    monkeypatch.setattr(status_mod, "_container_status_for",
                        lambda entry, counts=None: {"name": entry.name, "status": "stopped", "count": 0})

    args = types.SimpleNamespace(interactive=False)
    rc = project_mod.cmd_project_list(tmp_path, args)
    out = capsys.readouterr().out

    assert rc == 0
    assert "NAME" in out and "PLUGIN" in out and "STATUS" in out
    assert "alpha-proj" in out
    assert "alpha" in out
    assert "stopped" in out


def test_cmd_project_list_empty(tmp_path, capsys):
    from devbase.commands import project as project_mod
    args = types.SimpleNamespace(interactive=False)
    rc = project_mod.cmd_project_list(tmp_path, args)
    assert rc == 0


def test_cmd_project_list_non_tty_falls_back_to_table(tmp_path, monkeypatch, capsys):
    """interactive=True (デフォルト) でも非 TTY では一覧表示にフォールバックする。"""
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod
    from devbase.commands import container as container_mod

    _make_plugin_project(tmp_path, "repos/o--r/alpha", "alpha-proj")
    _link_project(tmp_path, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    monkeypatch.setattr(status_mod, "_container_status_for",
                        lambda entry, counts=None: {"name": entry.name, "status": "stopped", "count": 0})
    monkeypatch.setattr(project_mod.sys.stdin, "isatty", lambda: False)

    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)

    args = types.SimpleNamespace(interactive=True)
    rc = project_mod.cmd_project_list(tmp_path, args)
    out = capsys.readouterr().out

    assert rc == 0
    assert called == [], "非 TTY では対話起動しない"
    assert "alpha-proj" in out


def test_cmd_project_list_stdout_non_tty_falls_back_to_table(tmp_path, monkeypatch, capsys):
    """stdin が TTY でも stdout が非 TTY (`devbase list | cat` / `> out.txt`) なら
    対話起動せず一覧表示へフォールバックする。"""
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod
    from devbase.commands import container as container_mod

    _make_plugin_project(tmp_path, "repos/o--r/alpha", "alpha-proj")
    _link_project(tmp_path, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    monkeypatch.setattr(status_mod, "_container_status_for",
                        lambda entry, counts=None: {"name": entry.name, "status": "stopped", "count": 0})
    monkeypatch.setattr(project_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(project_mod.sys.stdout, "isatty", lambda: False)

    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)

    args = types.SimpleNamespace(interactive=True)
    rc = project_mod.cmd_project_list(tmp_path, args)
    out = capsys.readouterr().out

    assert rc == 0
    assert called == [], "stdout 非 TTY では対話起動しない"
    assert "alpha-proj" in out


# ---------------------------------------------------------------------------
# cmd_project_list: --interactive
# ---------------------------------------------------------------------------

def test_cmd_project_list_interactive_selects_and_ups(tmp_path, monkeypatch):
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod
    from devbase.commands import container as container_mod

    _make_plugin_project(tmp_path, "repos/o--r/alpha", "alpha-proj")
    _link_project(tmp_path, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    _make_plugin_project(tmp_path, "plugins/beta", "beta-proj")
    _link_project(tmp_path, "beta-proj", "plugins/beta", "beta-proj")
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)

    # 対話選択は TTY 環境でのみ起動するため isatty を True に固定する。
    monkeypatch.setattr(project_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(project_mod.sys.stdout, "isatty", lambda: True)
    # 番号 "2" を選択 (sorted: alpha-proj=1, beta-proj=2)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "2")

    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(
                            subcommand=args.subcommand, name=args.name) or 0)

    args = types.SimpleNamespace(interactive=True)
    rc = project_mod.cmd_project_list(tmp_path, args)

    assert rc == 0
    assert captured["subcommand"] == "up"
    assert captured["name"] == "beta-proj"


def test_cmd_project_list_interactive_empty_input_aborts(tmp_path, monkeypatch):
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod
    from devbase.commands import container as container_mod

    _make_plugin_project(tmp_path, "repos/o--r/alpha", "alpha-proj")
    _link_project(tmp_path, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)
    monkeypatch.setattr(project_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(project_mod.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")

    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)

    args = types.SimpleNamespace(interactive=True)
    rc = project_mod.cmd_project_list(tmp_path, args)
    assert rc == 0
    assert called == [], "空入力では up を起動しない"


def test_cmd_project_list_interactive_non_tty_eof(tmp_path, monkeypatch):
    """非対話環境 (input が EOFError) では up を起動せずエラー終了する。"""
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod
    from devbase.commands import container as container_mod

    _make_plugin_project(tmp_path, "repos/o--r/alpha", "alpha-proj")
    _link_project(tmp_path, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)

    def raise_eof(*a, **k):
        raise EOFError

    monkeypatch.setattr(project_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(project_mod.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", raise_eof)
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)

    args = types.SimpleNamespace(interactive=True)
    rc = project_mod.cmd_project_list(tmp_path, args)
    assert rc == 1
    assert called == []


def test_cmd_project_list_interactive_keyboard_interrupt_aborts(tmp_path, monkeypatch):
    """Ctrl+C (KeyboardInterrupt) は traceback を出さず中止 (rc=0) として扱う。"""
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod
    from devbase.commands import container as container_mod

    _make_plugin_project(tmp_path, "repos/o--r/alpha", "alpha-proj")
    _link_project(tmp_path, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)

    def raise_interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(project_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(project_mod.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", raise_interrupt)
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)

    args = types.SimpleNamespace(interactive=True)
    rc = project_mod.cmd_project_list(tmp_path, args)
    assert rc == 0
    assert called == []


def test_cmd_project_list_interactive_out_of_range_reprompts(tmp_path, monkeypatch):
    """範囲外の番号では即終了せず再入力を促す。有効入力で最終的に up する。"""
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod
    from devbase.commands import container as container_mod

    _make_plugin_project(tmp_path, "repos/o--r/alpha", "alpha-proj")
    _link_project(tmp_path, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)

    monkeypatch.setattr(project_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(project_mod.sys.stdout, "isatty", lambda: True)
    # "99" (範囲外) → "1" (有効) の順に入力 → 再入力後に up が起動する
    inputs = iter(["99", "1"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(name=args.name) or 0)

    args = types.SimpleNamespace(interactive=True)
    rc = project_mod.cmd_project_list(tmp_path, args)
    assert rc == 0
    assert captured["name"] == "alpha-proj"


def test_cmd_project_list_interactive_non_numeric_reprompts(tmp_path, monkeypatch):
    """数値以外の入力では即終了せず再入力を促す。"""
    from devbase.commands import project as project_mod
    from devbase.commands import status as status_mod
    from devbase.commands import container as container_mod

    _make_plugin_project(tmp_path, "repos/o--r/alpha", "alpha-proj")
    _link_project(tmp_path, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)

    monkeypatch.setattr(project_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(project_mod.sys.stdout, "isatty", lambda: True)
    # "abc" (数値以外) → "1" (有効)
    inputs = iter(["abc", "1"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(name=args.name) or 0)

    args = types.SimpleNamespace(interactive=True)
    rc = project_mod.cmd_project_list(tmp_path, args)
    assert rc == 0
    assert captured["name"] == "alpha-proj"


# ---------------------------------------------------------------------------
# parser: project list / --interactive
# ---------------------------------------------------------------------------

def test_parser_project_list():
    # 対話選択はデフォルト ON (フラグ無しで interactive=True)。
    parser = cli._create_parser()
    args = parser.parse_args(["project", "list"])
    assert args.command == "project"
    assert args.subcommand == "list"
    assert args.interactive is True


def test_parser_project_list_interactive_flag():
    # `-i` / `--interactive` は後方互換で受け付ける (実質 no-op、True のまま)。
    parser = cli._create_parser()
    for flag in ("--interactive", "-i"):
        args = parser.parse_args(["project", "list", flag])
        assert args.interactive is True


def test_parser_project_list_no_interactive_flag():
    # `--no-interactive` / `--plain` / `-P` で一覧表示のみ (interactive=False)。
    parser = cli._create_parser()
    for flag in ("--no-interactive", "--plain", "-P"):
        args = parser.parse_args(["project", "list", flag])
        assert args.interactive is False


def test_parser_top_level_list_synonym():
    parser = cli._create_parser()
    args = parser.parse_args(["list", "-i"])
    assert args.command == "list"
    assert args.interactive is True


# ---------------------------------------------------------------------------
# prefix 解決: project list / 単独 list
# ---------------------------------------------------------------------------

def test_expand_argv_project_list_prefix(monkeypatch):
    """`devbase project li` は `list` に解決される。"""
    import sys
    monkeypatch.setattr(sys, "argv", ["devbase", "project", "li"])
    cli._expand_argv()
    assert sys.argv == ["devbase", "project", "list"]


def test_expand_argv_top_level_list_prefix(monkeypatch):
    """`devbase li` は一意に `list` へ解決される (login とは li/lo で分離)。"""
    import sys
    monkeypatch.setattr(sys, "argv", ["devbase", "li"])
    cli._expand_argv()
    assert sys.argv[1] == "list"


def test_expand_argv_top_level_l_resolves_to_login(monkeypatch):
    """後方互換: `list` 追加で ambiguous になった `devbase l` を `login` に維持する。

    `l` は `login` / `list` の両方にマッチするが TOP_PREFIX_PREFERENCES で
    既存挙動 (`l` → `login`) を保つ (互換性指摘 #36)。
    """
    import sys
    monkeypatch.setattr(sys, "argv", ["devbase", "l"])
    cli._expand_argv()
    assert sys.argv[1] == "login"


def test_expand_argv_top_level_lo_resolves_to_login(monkeypatch):
    """`devbase lo` は一意に `login` へ解決される (回帰確認)。"""
    import sys
    monkeypatch.setattr(sys, "argv", ["devbase", "lo"])
    cli._expand_argv()
    assert sys.argv[1] == "login"


# ---------------------------------------------------------------------------
# dispatch ルーティング
# ---------------------------------------------------------------------------

def test_dispatch_project_list_routes_to_cmd_project_list(monkeypatch):
    from devbase.commands import project as project_mod
    monkeypatch.setenv("DEVBASE_ROOT", "/tmp/devbase-root-test")
    calls = []
    monkeypatch.setattr(project_mod, "cmd_project_list",
                        lambda root, args: calls.append(str(root)) or 0)
    args = types.SimpleNamespace(command="project", subcommand="list", interactive=False)
    assert cli._dispatch("project", args) == 0
    assert calls == ["/tmp/devbase-root-test"]


def test_dispatch_project_up_still_routes_to_lifecycle(monkeypatch):
    """project list 追加後も up 等は従来通り cmd_project (lifecycle) へ。"""
    from devbase.commands import container as container_mod
    calls = []
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: calls.append(args.subcommand) or 0)
    args = types.SimpleNamespace(command="project", subcommand="up", name=None, scale=None)
    assert cli._dispatch("project", args) == 0
    assert calls == ["up"]


def test_dispatch_top_level_list_routes_to_cmd_project_list(monkeypatch):
    from devbase.commands import project as project_mod
    monkeypatch.setenv("DEVBASE_ROOT", "/tmp/devbase-root-test")
    calls = []
    monkeypatch.setattr(project_mod, "cmd_project_list",
                        lambda root, args: calls.append("list") or 0)
    args = types.SimpleNamespace(command="list", interactive=False)
    assert cli._dispatch("list", args) == 0
    assert calls == ["list"]


# ---------------------------------------------------------------------------
# status.py リファクタ回帰: _container_status_for / _get_container_status
# ---------------------------------------------------------------------------

def test_container_status_for_none_without_compose(tmp_path):
    from devbase.commands.status import _container_status_for
    entry = tmp_path / "proj"
    entry.mkdir()
    assert _container_status_for(entry) is None


def test_get_container_status_uses_per_entry(tmp_path, monkeypatch):
    from devbase.commands import status as status_mod
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "a").mkdir()
    (projects_dir / "b").mkdir()

    monkeypatch.setattr(status_mod, "_container_status_for",
                        lambda entry, counts=None: {"name": entry.name, "status": "stopped", "count": 0})
    results = status_mod._get_container_status(projects_dir)
    names = sorted(r["name"] for r in results)
    assert names == ["a", "b"]


# ---------------------------------------------------------------------------
# TUI: _build_menu_entries / _color_status
# ---------------------------------------------------------------------------

def test_build_menu_entries_shortcuts_and_mapping():
    from devbase.commands.project import _build_menu_entries

    rows = [{"name": f"p{i}", "plugin": "-", "status": "stopped"} for i in range(11)]
    entries = _build_menu_entries(rows)

    assert len(entries) == 11
    # 先頭 9 件は [1]..[9] ショートカット付き (entry index と rows index は 1:1)
    for i in range(9):
        assert entries[i].startswith(f"[{i + 1}] ")
        assert f"p{i}" in entries[i]
    # 10 件目以降はショートカット無し (4 スペース始まりで桁を揃える)
    assert entries[9].startswith("    ")
    assert not entries[9].lstrip().startswith("[")
    assert "p9" in entries[9]
    assert "p10" in entries[10]


def test_build_menu_entries_colorize_wraps_status():
    from devbase.commands.project import _build_menu_entries

    rows = [
        {"name": "a", "plugin": "-", "status": "running (1 containers)"},
        {"name": "b", "plugin": "-", "status": "stopped"},
        {"name": "c", "plugin": "-", "status": "unknown"},
    ]
    entries = _build_menu_entries(rows, colorize=True)

    assert "\033[32m" in entries[0] and "\033[0m" in entries[0]   # running=緑
    assert "\033[90m" in entries[1]                                # stopped=灰
    assert "\033[" not in entries[2]                               # unknown=無装飾


def test_build_menu_entries_plain_has_no_ansi():
    from devbase.commands.project import _build_menu_entries

    rows = [{"name": "a", "plugin": "-", "status": "running (1 containers)"}]
    entries = _build_menu_entries(rows, colorize=False)

    assert "\033[" not in entries[0]
