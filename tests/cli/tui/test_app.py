"""PLAN31_2 PR1: tui.app (トップ階層メニュー & 入口 run) のテスト。

旧 commands/project.py の cmd_project_list (非 TTY / questionary 不在フォールバック)
の非回帰検証を tui.app へ移送し、トップ階層メニューの routing を追加検証する。
"""

from __future__ import annotations

import types

from devbase.tui import actions_project, app, menu


def _make_plugin_project(root, plugin_path, proj):
    target = root / plugin_path / "projects" / proj
    target.mkdir(parents=True, exist_ok=True)


def _link_project(root, link_name, plugin_path, proj):
    from pathlib import Path
    projects_dir = root / "projects"
    projects_dir.mkdir(exist_ok=True)
    (projects_dir / link_name).symlink_to(Path("..") / plugin_path / "projects" / proj)


def _seed(root, monkeypatch, status=None):
    from devbase.commands import status as status_mod
    _make_plugin_project(root, "repos/o--r/alpha", "alpha-proj")
    _link_project(root, "alpha-proj", "repos/o--r/alpha", "alpha-proj")
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: status)


# ---------------------------------------------------------------------------
# run(): 非対話 / 非 TTY → table フォールバック
# ---------------------------------------------------------------------------

def test_run_non_interactive_prints_table(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch, status={"name": "x", "status": "stopped", "count": 0})
    rc = app.run(tmp_path, types.SimpleNamespace(interactive=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "alpha-proj" in out and "NAME" in out


def test_run_stdin_non_tty_falls_back_to_table(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch, status={"name": "x", "status": "stopped", "count": 0})
    monkeypatch.setattr(app.sys.stdin, "isatty", lambda: False)
    called = []
    monkeypatch.setattr(app, "_top_menu_loop", lambda root: called.append(1) or 0)

    rc = app.run(tmp_path, types.SimpleNamespace(interactive=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert called == [], "非 TTY ではトップメニューを開かない"
    assert "alpha-proj" in out


def test_run_stdout_non_tty_falls_back_to_table(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, monkeypatch, status={"name": "x", "status": "stopped", "count": 0})
    monkeypatch.setattr(app.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(app.sys.stdout, "isatty", lambda: False)
    called = []
    monkeypatch.setattr(app, "_top_menu_loop", lambda root: called.append(1) or 0)

    rc = app.run(tmp_path, types.SimpleNamespace(interactive=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert called == []
    assert "alpha-proj" in out


def test_run_non_interactive_empty(tmp_path, monkeypatch, capsys):
    from devbase.commands import status as status_mod
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)
    rc = app.run(tmp_path, types.SimpleNamespace(interactive=False))
    assert rc == 0


# ---------------------------------------------------------------------------
# run(): questionary 不在 → 番号入力フォールバック (project up)
# ---------------------------------------------------------------------------

def test_run_no_questionary_falls_back_to_number_input(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, status=None)
    monkeypatch.setattr(app.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(app.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", False)

    called = {}
    monkeypatch.setattr(actions_project, "fallback_select_and_up",
                        lambda rows: called.update(n=len(rows)) or 0)
    top_called = []
    monkeypatch.setattr(app, "_top_menu_loop", lambda root: top_called.append(1) or 0)

    rc = app.run(tmp_path, types.SimpleNamespace(interactive=True))
    assert rc == 0
    assert called == {"n": 1}, "questionary 不在時は番号入力フォールバックへ"
    assert top_called == [], "トップメニューは開かない"


def test_run_no_questionary_empty_projects(tmp_path, monkeypatch):
    from devbase.commands import status as status_mod
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)
    monkeypatch.setattr(app.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(app.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", False)

    called = []
    monkeypatch.setattr(actions_project, "fallback_select_and_up",
                        lambda rows: called.append(1) or 0)
    rc = app.run(tmp_path, types.SimpleNamespace(interactive=True))
    assert rc == 0
    assert called == [], "プロジェクトが無ければフォールバックも呼ばない"


# ---------------------------------------------------------------------------
# run(): questionary 利用可 → トップ階層メニュー
# ---------------------------------------------------------------------------

def test_run_interactive_opens_top_menu(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, status=None)
    monkeypatch.setattr(app.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(app.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", True)

    called = {}
    monkeypatch.setattr(app, "_top_menu_loop", lambda root: called.update(root=root) or 0)
    rc = app.run(tmp_path, types.SimpleNamespace(interactive=True))
    assert rc == 0
    assert called["root"] == tmp_path


# ---------------------------------------------------------------------------
# トップ階層メニュー: routing
# ---------------------------------------------------------------------------

def test_top_menu_project_first_highlighted():
    """「プロジェクト操作」が先頭 (既定ハイライト) で従来フローへ Enter 連打到達できる。"""
    assert app.TOP_CATEGORIES[0] == ("project", "プロジェクト操作")


def test_top_menu_routes_project_then_back_to_top(monkeypatch, tmp_path):
    """カテゴリ選択 → project 実行 (MENU_BACK) → トップ再表示 → Esc (None) で終了。"""
    selects = iter(["project", None])  # 1 回目 project、2 回目 Esc 中止
    monkeypatch.setattr(menu, "select", lambda *a, **k: next(selects))

    routed = []
    monkeypatch.setattr(actions_project, "run",
                        lambda root: routed.append(root) or menu.MENU_BACK)

    rc = app._top_menu_loop(tmp_path)
    assert rc == 0
    assert routed == [tmp_path], "project カテゴリへ 1 回 routing される"


def test_top_menu_propagates_executed_rc(monkeypatch, tmp_path):
    """カテゴリ実行で非0 rc が返ると、その後トップで中止しても rc がループ戻り値へ伝搬する。"""
    selects = iter(["project", None])  # 1 回目 project 実行、2 回目 Esc 中止
    monkeypatch.setattr(menu, "select", lambda *a, **k: next(selects))
    # actions_project.run が rc=1 (実行・失敗) を返す
    monkeypatch.setattr(actions_project, "run", lambda root: 1)

    assert app._top_menu_loop(tmp_path) == 1


def test_top_menu_back_does_not_overwrite_last_rc(monkeypatch, tmp_path):
    """実行 rc を記憶後、別カテゴリが MENU_BACK を返しても last_rc は上書きされない。"""
    selects = iter(["project", "env", None])
    monkeypatch.setattr(menu, "select", lambda *a, **k: next(selects))
    runs = iter([1])  # project 実行 → rc=1
    monkeypatch.setattr(actions_project, "run", lambda root: next(runs))
    # env は未実装カテゴリ (_route が MENU_BACK) → last_rc を維持

    assert app._top_menu_loop(tmp_path) == 1


def test_top_menu_zero_rc_propagates(monkeypatch, tmp_path):
    """rc=0 が int として正しく扱われる (None/MENU_BACK と誤マッチしない)。"""
    selects = iter(["project", None])
    monkeypatch.setattr(menu, "select", lambda *a, **k: next(selects))
    monkeypatch.setattr(actions_project, "run", lambda root: 0)

    assert app._top_menu_loop(tmp_path) == 0


def test_top_menu_escape_aborts(monkeypatch, tmp_path):
    """トップメニューで Esc/Ctrl-C (None) を押すと即終了 (rc=0)。"""
    monkeypatch.setattr(menu, "select", lambda *a, **k: None)
    routed = []
    monkeypatch.setattr(actions_project, "run", lambda root: routed.append(1) or menu.MENU_BACK)
    assert app._top_menu_loop(tmp_path) == 0
    assert routed == []


def test_top_menu_category_ctrl_c_aborts_whole_app(monkeypatch, tmp_path):
    """カテゴリ内で Ctrl-C (None) を受けたら全体中止する。"""
    monkeypatch.setattr(menu, "select", lambda *a, **k: "project")
    monkeypatch.setattr(actions_project, "run", lambda root: None)  # Ctrl-C
    assert app._top_menu_loop(tmp_path) == 0


def test_top_menu_unimplemented_category_returns_to_top(monkeypatch, tmp_path):
    """未実装カテゴリ (env 等) はプレースホルダ案内を出してトップへ戻る。"""
    selects = iter(["env", None])
    monkeypatch.setattr(menu, "select", lambda *a, **k: next(selects))
    # _route が MENU_BACK を返してループ継続 → 2 回目 None で終了
    rc = app._top_menu_loop(tmp_path)
    assert rc == 0


def test_route_project_delegates(monkeypatch, tmp_path):
    monkeypatch.setattr(actions_project, "run", lambda root: "RESULT")
    assert app._route("project", tmp_path) == "RESULT"


def test_route_unimplemented_returns_menu_back(tmp_path):
    assert app._route("plugin", tmp_path) is menu.MENU_BACK
