"""PLAN31_2 PR1: tui.actions_project (project カテゴリ操作) のテスト。

旧 commands/project.py の _tui_select_and_up / 番号入力フォールバックの非回帰検証を
tui.actions_project へ移送したもの。`menu.select` を monkeypatch して選択値を注入する。
"""

from __future__ import annotations

import pytest

from devbase.tui import actions_project, menu


def _make_plugin_project(root, plugin_path, proj):
    target = root / plugin_path / "projects" / proj
    target.mkdir(parents=True, exist_ok=True)
    return target


def _link_project(root, link_name, plugin_path, proj):
    from pathlib import Path
    projects_dir = root / "projects"
    projects_dir.mkdir(exist_ok=True)
    (projects_dir / link_name).symlink_to(Path("..") / plugin_path / "projects" / proj)


# ---------------------------------------------------------------------------
# run(): 一覧選択 → up/rebuild/down
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", ["up", "rebuild", "down"])
def test_run_running_row_shows_action_menu(monkeypatch, tmp_path, action):
    """running 行を選ぶとサブメニューで up/rebuild/down を選び、その subcommand で起動する。"""
    from devbase.commands import container as container_mod
    from devbase.commands import status as status_mod

    _make_plugin_project(tmp_path, "repos/o--r/p", "carmo")
    _link_project(tmp_path, "carmo", "repos/o--r/p", "carmo")
    monkeypatch.setattr(status_mod, "_container_status_for",
                        lambda entry, counts=None: {"name": entry.name,
                                                    "status": "running (2 containers)", "count": 2})

    # _select_project → index 0、_select_action → action
    monkeypatch.setattr(actions_project, "_select_project", lambda rows: 0)
    seen = {}
    monkeypatch.setattr(actions_project, "_select_action",
                        lambda name: seen.update(name=name) or action)

    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(
                            subcommand=args.subcommand, name=args.name) or 0)

    result = actions_project.run(tmp_path)
    assert result is menu.MENU_BACK          # 操作完了でトップへ戻る
    assert seen["name"] == "carmo"
    assert captured == {"subcommand": action, "name": "carmo"}


@pytest.mark.parametrize("status", ["stopped", "unknown"])
def test_run_non_running_row_direct_up(monkeypatch, tmp_path, status):
    """非 running 行はサブメニューを出さず直接 up する。"""
    from devbase.commands import container as container_mod
    from devbase.commands import status as status_mod

    _make_plugin_project(tmp_path, "repos/o--r/p", "carmo")
    _link_project(tmp_path, "carmo", "repos/o--r/p", "carmo")
    monkeypatch.setattr(status_mod, "_container_status_for",
                        lambda entry, counts=None: ({"name": entry.name, "status": status, "count": 0}
                                                    if status == "stopped" else None))

    monkeypatch.setattr(actions_project, "_select_project", lambda rows: 0)
    action_calls = []
    monkeypatch.setattr(actions_project, "_select_action",
                        lambda name: action_calls.append(name) or "down")

    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(
                            subcommand=args.subcommand, name=args.name) or 0)

    result = actions_project.run(tmp_path)
    assert result is menu.MENU_BACK
    assert action_calls == [], "非 running ではサブメニューを出さない"
    assert captured == {"subcommand": "up", "name": "carmo"}


def test_run_select_back_returns_to_top(monkeypatch, tmp_path):
    """一覧で Esc/← (MENU_BACK) を押すとトップメニューへ戻る (何も起動しない)。"""
    from devbase.commands import container as container_mod
    from devbase.commands import status as status_mod

    _make_plugin_project(tmp_path, "repos/o--r/p", "carmo")
    _link_project(tmp_path, "carmo", "repos/o--r/p", "carmo")
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)
    monkeypatch.setattr(actions_project, "_select_project", lambda rows: menu.MENU_BACK)

    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)

    assert actions_project.run(tmp_path) is menu.MENU_BACK
    assert called == []


def test_run_select_ctrl_c_aborts(monkeypatch, tmp_path):
    """一覧で Ctrl-C (None) を押すと全体中止 (None を返す)。"""
    from devbase.commands import container as container_mod
    from devbase.commands import status as status_mod

    _make_plugin_project(tmp_path, "repos/o--r/p", "carmo")
    _link_project(tmp_path, "carmo", "repos/o--r/p", "carmo")
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)
    monkeypatch.setattr(actions_project, "_select_project", lambda rows: None)

    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)

    assert actions_project.run(tmp_path) is None
    assert called == []


def test_run_action_menu_back_returns_to_list(monkeypatch, tmp_path):
    """running 行のサブメニューで Esc/← (MENU_BACK) を押すと一覧へ戻る。"""
    from devbase.commands import container as container_mod
    from devbase.commands import status as status_mod

    _make_plugin_project(tmp_path, "repos/o--r/p", "carmo")
    _link_project(tmp_path, "carmo", "repos/o--r/p", "carmo")
    _make_plugin_project(tmp_path, "repos/o--r/q", "beta")
    _link_project(tmp_path, "beta", "repos/o--r/q", "beta")

    def fake_status(entry, counts=None):
        st = "running (1 containers)" if entry.name == "carmo" else "stopped"
        return {"name": entry.name, "status": st, "count": 1}

    monkeypatch.setattr(status_mod, "_container_status_for", fake_status)

    # sorted 順: beta(stopped)=idx0, carmo(running)=idx1
    # 1 回目: carmo(running, idx1) → action menu で MENU_BACK → 一覧へ戻る
    # 2 回目: beta(stopped, idx0) → 直接 up
    select_calls = []
    monkeypatch.setattr(actions_project, "_select_project",
                        lambda rows: (select_calls.append(1),
                                      1 if len(select_calls) == 1 else 0)[1])
    monkeypatch.setattr(actions_project, "_select_action", lambda name: menu.MENU_BACK)

    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(
                            subcommand=args.subcommand, name=args.name) or 0)

    result = actions_project.run(tmp_path)
    assert result is menu.MENU_BACK
    assert len(select_calls) == 2, "MENU_BACK で一覧が再表示される"
    assert captured == {"subcommand": "up", "name": "beta"}


def test_run_action_menu_ctrl_c_aborts(monkeypatch, tmp_path):
    """running 行のサブメニューで Ctrl-C (None) を押すと全体中止。"""
    from devbase.commands import container as container_mod
    from devbase.commands import status as status_mod

    _make_plugin_project(tmp_path, "repos/o--r/p", "carmo")
    _link_project(tmp_path, "carmo", "repos/o--r/p", "carmo")
    monkeypatch.setattr(status_mod, "_container_status_for",
                        lambda entry, counts=None: {"name": entry.name,
                                                    "status": "running (1 containers)", "count": 1})
    monkeypatch.setattr(actions_project, "_select_project", lambda rows: 0)
    monkeypatch.setattr(actions_project, "_select_action", lambda name: None)

    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)

    assert actions_project.run(tmp_path) is None
    assert called == []


def test_run_empty_projects_returns_back(monkeypatch, tmp_path):
    """プロジェクトが無いときはトップメニューへ戻る (MENU_BACK)。"""
    from devbase.commands import status as status_mod
    monkeypatch.setattr(status_mod, "_container_status_for", lambda entry, counts=None: None)
    # projects/ ディレクトリ無し
    assert actions_project.run(tmp_path) is menu.MENU_BACK


# ---------------------------------------------------------------------------
# _select_project / _select_action: menu.select への委譲
# ---------------------------------------------------------------------------

def test_select_project_uses_search_back_menu(monkeypatch):
    rows = [{"name": "carmo", "plugin": "-", "status": "stopped"}]
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, search=search, n=len(choices))
        return 0

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_project._select_project(rows) == 0
    assert captured == {"back": True, "search": True, "n": 1}


def test_select_action_lists_three_ops(monkeypatch):
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, search=search,
                        values=[c[1] for c in choices])
        return "rebuild"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_project._select_action("carmo") == "rebuild"
    assert captured["back"] is True
    assert captured["search"] is False
    assert captured["values"] == ["up", "rebuild", "down"]


# ---------------------------------------------------------------------------
# fallback_select_and_up: 番号入力 (questionary 不在) の非回帰
# ---------------------------------------------------------------------------

def test_fallback_selects_and_ups(monkeypatch):
    from devbase.commands import container as container_mod
    rows = [{"name": "alpha", "plugin": "-", "status": "stopped"},
            {"name": "beta", "plugin": "-", "status": "stopped"}]
    monkeypatch.setattr("builtins.input", lambda *a, **k: "2")

    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(
                            subcommand=args.subcommand, name=args.name) or 0)

    assert actions_project.fallback_select_and_up(rows) == 0
    assert captured == {"subcommand": "up", "name": "beta"}


def test_fallback_empty_input_aborts(monkeypatch):
    from devbase.commands import container as container_mod
    rows = [{"name": "alpha", "plugin": "-", "status": "stopped"}]
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)
    assert actions_project.fallback_select_and_up(rows) == 0
    assert called == []


def test_fallback_non_tty_eof(monkeypatch):
    from devbase.commands import container as container_mod
    rows = [{"name": "alpha", "plugin": "-", "status": "stopped"}]

    def _eof(*a, **k):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)
    assert actions_project.fallback_select_and_up(rows) == 1
    assert called == []


def test_fallback_keyboard_interrupt_aborts(monkeypatch):
    from devbase.commands import container as container_mod
    rows = [{"name": "alpha", "plugin": "-", "status": "stopped"}]

    def _interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _interrupt)
    called = []
    monkeypatch.setattr(container_mod, "cmd_project", lambda args: called.append(1) or 0)
    assert actions_project.fallback_select_and_up(rows) == 0
    assert called == []


def test_fallback_out_of_range_then_valid(monkeypatch):
    from devbase.commands import container as container_mod
    rows = [{"name": "alpha", "plugin": "-", "status": "stopped"}]
    inputs = iter(["99", "1"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(name=args.name) or 0)
    assert actions_project.fallback_select_and_up(rows) == 0
    assert captured == {"name": "alpha"}


def test_fallback_non_numeric_then_valid(monkeypatch):
    from devbase.commands import container as container_mod
    rows = [{"name": "alpha", "plugin": "-", "status": "stopped"}]
    inputs = iter(["abc", "1"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    captured = {}
    monkeypatch.setattr(container_mod, "cmd_project",
                        lambda args: captured.update(name=args.name) or 0)
    assert actions_project.fallback_select_and_up(rows) == 0
    assert captured == {"name": "alpha"}
