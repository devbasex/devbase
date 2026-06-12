"""PLAN31_2 PR3: tui.actions_env (env カテゴリ操作) のテスト。

test_actions_project.py のパターンを踏襲し、`menu.*` を monkeypatch して選択値を
注入、`cmd_env` を mock して **plan 2.3 の契約どおりの属性を持つ Namespace** で
呼ばれることを各サブコマンドで検証する。project スコープ操作の chdir →復帰、
破壊的操作 (delete) の confirm、Esc/←/Ctrl-C の遷移も検証する。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from devbase.tui import actions_env, menu


def _make_plugin_project(root, plugin_path, proj):
    target = root / plugin_path / "projects" / proj
    target.mkdir(parents=True, exist_ok=True)
    return target


def _link_project(root, link_name, plugin_path, proj):
    projects_dir = root / "projects"
    projects_dir.mkdir(exist_ok=True)
    (projects_dir / link_name).symlink_to(Path("..") / plugin_path / "projects" / proj)


def _capture_dispatch(monkeypatch):
    """cmd_env の呼び出しを (root, 全属性, 実行時 CWD/PWD) でキャプチャするヘルパ。"""
    from devbase.commands import env as env_mod
    captured = {}

    def _spy(devbase_root, args):
        captured["root"] = devbase_root
        captured["attrs"] = dict(vars(args))
        captured["cwd"] = os.getcwd()
        captured["pwd"] = os.environ.get("PWD")
        return 0

    monkeypatch.setattr(env_mod, "cmd_env", _spy)
    return captured


# ---------------------------------------------------------------------------
# run(): 操作選択 → 実行 / Esc / Ctrl-C / 引数収集中止
# ---------------------------------------------------------------------------

def test_run_executes_and_returns_rc(monkeypatch, tmp_path):
    """操作を選んで実行したら dispatch の rc を返す (トップへ復帰)。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_env, "_select_action", lambda: "sync")

    assert actions_env.run(tmp_path) == 0
    assert captured["root"] == tmp_path
    assert captured["attrs"] == {"subcommand": "sync"}


def test_run_propagates_nonzero_dispatch_rc(monkeypatch, tmp_path):
    """dispatch が非0 (失敗) を返したら run() もその rc を返す (終了コード伝搬)。"""
    from devbase.commands import env as env_mod
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: 1)
    monkeypatch.setattr(actions_env, "_select_action", lambda: "sync")

    assert actions_env.run(tmp_path) == 1


def test_run_back_returns_to_top(monkeypatch, tmp_path):
    """サブメニューで Esc/← (MENU_BACK) を押すとトップへ戻る (何も実行しない)。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    monkeypatch.setattr(actions_env, "_select_action", lambda: menu.MENU_BACK)

    assert actions_env.run(tmp_path) is menu.MENU_BACK
    assert called == []


def test_run_ctrl_c_aborts(monkeypatch, tmp_path):
    """サブメニューで Ctrl-C (None) を押すと全体中止 (None を返す)。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    monkeypatch.setattr(actions_env, "_select_action", lambda: None)

    assert actions_env.run(tmp_path) is None
    assert called == []


def test_run_arg_cancel_reshows_submenu(monkeypatch, tmp_path):
    """引数収集を中止 (_ARG_CANCEL) するとサブメニューを再表示し、再選択で実行する。"""
    select_calls = []
    # 1 回目: delete を選ぶ (→ 引数収集中止) / 2 回目: sync を選ぶ (→ 実行)
    monkeypatch.setattr(actions_env, "_select_action",
                        lambda: (select_calls.append(1),
                                 "delete" if len(select_calls) == 1 else "sync")[1])

    run_calls = []

    def fake_run_op(root, op):
        run_calls.append(op)
        return actions_env._ARG_CANCEL if op == "delete" else 0

    monkeypatch.setattr(actions_env, "_run_operation", fake_run_op)

    assert actions_env.run(tmp_path) == 0
    assert run_calls == ["delete", "sync"]
    assert len(select_calls) == 2, "引数中止でサブメニューが再表示される"


def test_run_propagates_ctrl_c_from_operation(monkeypatch, tmp_path):
    """引数収集中の Ctrl-C (None) はサブメニューを再表示せず全体中止を伝搬する。"""
    select_calls = []
    monkeypatch.setattr(actions_env, "_select_action",
                        lambda: select_calls.append(1) or "list")
    monkeypatch.setattr(actions_env, "_run_operation", lambda root, op: None)

    assert actions_env.run(tmp_path) is None
    assert len(select_calls) == 1, "Ctrl-C でサブメニューを再表示しない"


# ---------------------------------------------------------------------------
# _select_action: menu.select への委譲 (全 10 サブコマンドの提示)
# ---------------------------------------------------------------------------

def test_select_action_lists_all_ops(monkeypatch):
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, search=search,
                        values=[c[1] for c in choices])
        return "list"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_env._select_action() == "list"
    assert captured["back"] is True
    assert captured["search"] is False
    # 参照系の list を先頭にしつつ env の全 10 サブコマンドを提示する (PR3)。
    assert sorted(captured["values"]) == sorted([
        "init", "list", "set", "get", "delete",
        "edit", "sync", "project", "export", "import"])
    assert captured["values"][0] == "list", "Enter 連打で安全な一覧表示に到達できる"


# ---------------------------------------------------------------------------
# _run_operation: 引数なし系 (sync / edit / init)
# ---------------------------------------------------------------------------

def test_run_operation_sync_no_attrs(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    assert actions_env._run_operation(tmp_path, "sync") == 0
    assert captured["attrs"] == {"subcommand": "sync"}


def test_run_operation_edit_is_global_no_project_select(monkeypatch, tmp_path):
    """edit は $DEVBASE_ROOT/.env を開くグローバル操作。プロジェクト選択も chdir もしない。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_env, "_select_project",
                        lambda root: pytest.fail("edit でプロジェクト選択してはいけない"))

    before = os.getcwd()
    assert actions_env._run_operation(tmp_path, "edit") == 0
    assert captured["attrs"] == {"subcommand": "edit"}
    assert captured["cwd"] == before, "edit は chdir しない"


def test_run_operation_init_runs_without_confirm(monkeypatch, tmp_path):
    """init は確認プロンプトなしで reset=False (CLI 既定) のまま即実行する。

    セットアップ済みの環境では cmd_env_init が案内を出して安全に終了する。
    やり直しは CLI (`env init --reset`) を使う想定。
    """
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: pytest.fail("init で確認を求めない"))
    assert actions_env._run_operation(tmp_path, "init") == 0
    assert captured["attrs"] == {"subcommand": "init", "reset": False}


# ---------------------------------------------------------------------------
# _run_operation: list (表示範囲のみ収集。reveal/keys は CLI 既定の False)
# ---------------------------------------------------------------------------

def test_run_operation_list_global_scope_no_chdir(monkeypatch, tmp_path):
    """list の「グローバルのみ」は --global へ写像し、プロジェクト選択も chdir もしない。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "select", lambda *a, **k: "global")
    monkeypatch.setattr(actions_env, "_select_project",
                        lambda root: pytest.fail("global でプロジェクト選択してはいけない"))
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: pytest.fail("list で確認を求めない"))

    before = os.getcwd()
    assert actions_env._run_operation(tmp_path, "list") == 0
    assert captured["attrs"] == {
        "subcommand": "list",
        "global_only": True, "project_only": False,
        "reveal": False, "keys_only": False,
    }
    assert captured["cwd"] == before, "global スコープは chdir しない"


def test_run_operation_list_both_scope_chdirs_and_restores(monkeypatch, tmp_path):
    """list の「グローバル + プロジェクト」も対象を選ばせて chdir + PWD 切替後に実行する。

    cmd_env_list は PWD が projects/ 配下のときだけプロジェクト .env を表示する
    ため、DEVBASE_ROOT のまま global_only=False で呼んでもグローバルしか表示
    されない (codex round3 指摘の回帰テスト)。
    """
    captured = _capture_dispatch(monkeypatch)
    target = tmp_path / "projects" / "carmo"
    target.mkdir(parents=True)
    monkeypatch.setattr(menu, "select", lambda *a, **k: "both")
    monkeypatch.setattr(actions_env, "_select_project", lambda root: "carmo")
    monkeypatch.setenv("PWD", str(tmp_path))

    before = os.getcwd()
    assert actions_env._run_operation(tmp_path, "list") == 0
    assert captured["attrs"] == {
        "subcommand": "list",
        "global_only": False, "project_only": False,
        "reveal": False, "keys_only": False,
    }
    # ハンドラ実行中は projects/carmo に居る (グローバル + プロジェクト両方が出る)
    assert captured["cwd"] == str(target)
    assert captured["pwd"] == str(target)
    # 実行後は元の CWD / PWD へ復帰する (try/finally)
    assert os.getcwd() == before
    assert os.environ["PWD"] == str(tmp_path)


def test_run_operation_list_project_chdirs_and_restores(monkeypatch, tmp_path):
    """list の「プロジェクトのみ」は対象を選ばせて chdir + PWD 切替後に実行し、復帰する。

    cmd_env_list は PWD が projects/ 配下のときだけプロジェクト .env を表示する
    ため、切替なしでは何も表示されない (codex round1 指摘の回帰テスト)。
    """
    captured = _capture_dispatch(monkeypatch)
    target = tmp_path / "projects" / "carmo"
    target.mkdir(parents=True)
    monkeypatch.setattr(menu, "select", lambda *a, **k: "project")
    monkeypatch.setattr(actions_env, "_select_project", lambda root: "carmo")
    monkeypatch.setenv("PWD", str(tmp_path))

    before = os.getcwd()
    assert actions_env._run_operation(tmp_path, "list") == 0
    assert captured["attrs"] == {
        "subcommand": "list",
        "global_only": False, "project_only": True,
        "reveal": False, "keys_only": False,
    }
    # ハンドラ実行中は projects/carmo に居る (CWD と PWD の両方を切り替える)
    assert captured["cwd"] == str(target)
    assert captured["pwd"] == str(target)
    # 実行後は元の CWD / PWD へ復帰する (try/finally)
    assert os.getcwd() == before
    assert os.environ["PWD"] == str(tmp_path)


def test_run_operation_list_project_select_cancel(monkeypatch, tmp_path):
    """list のプロジェクト選択を中止したら実行しない。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    monkeypatch.setattr(menu, "select", lambda *a, **k: "project")
    monkeypatch.setattr(actions_env, "_select_project",
                        lambda root: actions_env._ARG_CANCEL)
    assert actions_env._run_operation(tmp_path, "list") is actions_env._ARG_CANCEL
    assert called == []


@pytest.mark.parametrize("scope_ret", ["BACK", None])
def test_run_operation_list_scope_cancel(monkeypatch, tmp_path, scope_ret):
    """表示範囲選択で Esc/← は再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    ret = menu.MENU_BACK if scope_ret == "BACK" else None
    monkeypatch.setattr(menu, "select", lambda *a, **k: ret)
    expected = actions_env._ARG_CANCEL if scope_ret == "BACK" else None
    assert actions_env._run_operation(tmp_path, "list") is expected
    assert called == []


# ---------------------------------------------------------------------------
# _run_operation: get / delete
# ---------------------------------------------------------------------------

def test_run_operation_get_global_collects_key(monkeypatch, tmp_path):
    """グローバル取得は chdir せず key のみ渡して委譲する。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "select", lambda *a, **k: "global")
    monkeypatch.setattr(menu, "text", lambda *a, **k: "MY_KEY")

    before = os.getcwd()
    assert actions_env._run_operation(tmp_path, "get") == 0
    assert captured["attrs"] == {"subcommand": "get", "key": "MY_KEY"}
    assert captured["cwd"] == before, "グローバル取得は chdir しない"


def test_run_operation_get_project_chdirs_and_restores(monkeypatch, tmp_path):
    """get のプロジェクト取得は対象へ chdir + PWD 切替後に実行し、復帰する。

    cmd_env_get はグローバル .env に無いキーを CWD (PWD) のプロジェクト .env へ
    フォールバックして探すため、切替なしではプロジェクト固有キーを取得できない
    (codex round2 指摘の回帰テスト)。
    """
    captured = _capture_dispatch(monkeypatch)
    target = tmp_path / "projects" / "carmo"
    target.mkdir(parents=True)
    monkeypatch.setattr(menu, "select", lambda *a, **k: "project")
    monkeypatch.setattr(actions_env, "_select_project", lambda root: "carmo")
    monkeypatch.setattr(menu, "text", lambda *a, **k: "DB_HOST")
    monkeypatch.setenv("PWD", str(tmp_path))

    before = os.getcwd()
    assert actions_env._run_operation(tmp_path, "get") == 0
    assert captured["attrs"] == {"subcommand": "get", "key": "DB_HOST"}
    # ハンドラ実行中は projects/carmo に居る (CWD と PWD の両方を切り替える)
    assert captured["cwd"] == str(target)
    assert captured["pwd"] == str(target)
    # 実行後は元の CWD / PWD へ復帰する (try/finally)
    assert os.getcwd() == before
    assert os.environ["PWD"] == str(tmp_path)


def test_run_operation_get_project_select_cancel(monkeypatch, tmp_path):
    """get のプロジェクト選択を中止したらキー入力にも進まない。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    monkeypatch.setattr(menu, "select", lambda *a, **k: "project")
    monkeypatch.setattr(actions_env, "_select_project",
                        lambda root: actions_env._ARG_CANCEL)
    monkeypatch.setattr(menu, "text",
                        lambda *a, **k: pytest.fail("選択中止後に入力を求めない"))
    assert actions_env._run_operation(tmp_path, "get") is actions_env._ARG_CANCEL
    assert called == []


@pytest.mark.parametrize("scope_ret", ["BACK", None])
def test_run_operation_get_scope_cancel(monkeypatch, tmp_path, scope_ret):
    """取得元選択で Esc/← は再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    ret = menu.MENU_BACK if scope_ret == "BACK" else None
    monkeypatch.setattr(menu, "select", lambda *a, **k: ret)
    expected = actions_env._ARG_CANCEL if scope_ret == "BACK" else None
    assert actions_env._run_operation(tmp_path, "get") is expected
    assert called == []


@pytest.mark.parametrize("text_ret", ["BACK", None])
def test_run_operation_get_key_cancel(monkeypatch, tmp_path, text_ret):
    """キー入力で Esc は再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    monkeypatch.setattr(menu, "select", lambda *a, **k: "global")
    ret = menu.MENU_BACK if text_ret == "BACK" else None
    monkeypatch.setattr(menu, "text", lambda *a, **k: ret)
    expected = actions_env._ARG_CANCEL if text_ret == "BACK" else None
    assert actions_env._run_operation(tmp_path, "get") is expected
    assert called == []


def test_run_operation_delete_confirmed(monkeypatch, tmp_path):
    """delete は confirm=True で削除を実行する (plan 3.4 破壊的操作)。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "text", lambda *a, **k: "OLD_KEY")
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: True)
    assert actions_env._run_operation(tmp_path, "delete") == 0
    assert captured["attrs"] == {"subcommand": "delete", "key": "OLD_KEY"}


@pytest.mark.parametrize("confirm_ret", [False, "BACK", None])
def test_run_operation_delete_cancelled_does_not_dispatch(monkeypatch, tmp_path,
                                                          confirm_ret):
    """delete の confirm を拒否 (False) / Esc / Ctrl-C したら削除しない。

    拒否と Esc はサブメニュー再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。
    """
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    monkeypatch.setattr(menu, "text", lambda *a, **k: "OLD_KEY")
    ret = menu.MENU_BACK if confirm_ret == "BACK" else confirm_ret
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: ret)
    expected = None if confirm_ret is None else actions_env._ARG_CANCEL
    assert actions_env._run_operation(tmp_path, "delete") is expected
    assert called == [], "確認を拒否/中止したら delete しない"


@pytest.mark.parametrize("text_ret", ["BACK", None])
def test_run_operation_delete_key_cancel(monkeypatch, tmp_path, text_ret):
    """delete のキー入力を中止したら confirm にも進まない。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    ret = menu.MENU_BACK if text_ret == "BACK" else None
    monkeypatch.setattr(menu, "text", lambda *a, **k: ret)
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: pytest.fail("キー未入力で confirm しない"))
    expected = actions_env._ARG_CANCEL if text_ret == "BACK" else None
    assert actions_env._run_operation(tmp_path, "delete") is expected
    assert called == []


# ---------------------------------------------------------------------------
# _run_operation: set (グローバル / プロジェクト + chdir)
# ---------------------------------------------------------------------------

def test_run_operation_set_global(monkeypatch, tmp_path):
    """グローバル設定は chdir せず project=False で委譲する (plan 2.3)。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "select", lambda *a, **k: "global")
    monkeypatch.setattr(menu, "text", lambda *a, **k: "API_KEY=secret")

    before = os.getcwd()
    assert actions_env._run_operation(tmp_path, "set") == 0
    assert captured["attrs"] == {"subcommand": "set",
                                 "assignment": "API_KEY=secret", "project": False}
    assert captured["cwd"] == before, "グローバル設定は chdir しない"


def test_run_operation_set_project_chdirs_and_restores(monkeypatch, tmp_path):
    """プロジェクト設定は対象へ chdir + PWD 切替後に project=True で委譲し、復帰する。"""
    captured = _capture_dispatch(monkeypatch)
    target = tmp_path / "projects" / "carmo"
    target.mkdir(parents=True)
    monkeypatch.setattr(menu, "select", lambda *a, **k: "project")
    monkeypatch.setattr(actions_env, "_select_project", lambda root: "carmo")
    monkeypatch.setattr(menu, "text", lambda *a, **k: "DB_HOST=localhost")
    monkeypatch.setenv("PWD", str(tmp_path))

    before = os.getcwd()
    assert actions_env._run_operation(tmp_path, "set") == 0
    assert captured["attrs"] == {"subcommand": "set",
                                 "assignment": "DB_HOST=localhost", "project": True}
    # ハンドラ実行中は projects/carmo に居る (CWD と PWD の両方を切り替える)
    assert captured["cwd"] == str(target)
    assert captured["pwd"] == str(target)
    # 実行後は元の CWD / PWD へ復帰する (try/finally)
    assert os.getcwd() == before
    assert os.environ["PWD"] == str(tmp_path)


def test_run_operation_set_project_select_cancel(monkeypatch, tmp_path):
    """プロジェクト選択を中止したら assignment 入力にも進まない。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    monkeypatch.setattr(menu, "select", lambda *a, **k: "project")
    monkeypatch.setattr(actions_env, "_select_project",
                        lambda root: actions_env._ARG_CANCEL)
    monkeypatch.setattr(menu, "text",
                        lambda *a, **k: pytest.fail("選択中止後に入力を求めない"))
    assert actions_env._run_operation(tmp_path, "set") is actions_env._ARG_CANCEL
    assert called == []


@pytest.mark.parametrize("scope_ret", ["BACK", None])
def test_run_operation_set_scope_cancel(monkeypatch, tmp_path, scope_ret):
    """設定先選択で Esc/← は再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    ret = menu.MENU_BACK if scope_ret == "BACK" else None
    monkeypatch.setattr(menu, "select", lambda *a, **k: ret)
    expected = actions_env._ARG_CANCEL if scope_ret == "BACK" else None
    assert actions_env._run_operation(tmp_path, "set") is expected
    assert called == []


@pytest.mark.parametrize("text_ret", ["BACK", None])
def test_run_operation_set_assignment_cancel(monkeypatch, tmp_path, text_ret):
    """assignment 入力で Esc は再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    monkeypatch.setattr(menu, "select", lambda *a, **k: "global")
    ret = menu.MENU_BACK if text_ret == "BACK" else None
    monkeypatch.setattr(menu, "text", lambda *a, **k: ret)
    expected = actions_env._ARG_CANCEL if text_ret == "BACK" else None
    assert actions_env._run_operation(tmp_path, "set") is expected
    assert called == []


# ---------------------------------------------------------------------------
# _run_operation: project (chdir + 復帰)
# ---------------------------------------------------------------------------

def test_run_operation_project_chdirs_and_restores(monkeypatch, tmp_path):
    """env project は対象プロジェクトへ chdir + PWD 切替後に実行し、復帰する (plan 3.3)。"""
    captured = _capture_dispatch(monkeypatch)
    target = tmp_path / "projects" / "carmo"
    target.mkdir(parents=True)
    monkeypatch.setattr(actions_env, "_select_project", lambda root: "carmo")
    monkeypatch.setenv("PWD", str(tmp_path))

    before = os.getcwd()
    assert actions_env._run_operation(tmp_path, "project") == 0
    assert captured["attrs"] == {"subcommand": "project"}
    assert captured["cwd"] == str(target)
    assert captured["pwd"] == str(target)
    assert os.getcwd() == before
    assert os.environ["PWD"] == str(tmp_path)


def test_run_operation_project_select_cancel(monkeypatch, tmp_path):
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    monkeypatch.setattr(actions_env, "_select_project",
                        lambda root: actions_env._ARG_CANCEL)
    assert actions_env._run_operation(tmp_path, "project") is actions_env._ARG_CANCEL
    assert called == []


def test_run_operation_project_select_ctrl_c_aborts(monkeypatch, tmp_path):
    """プロジェクト選択中の Ctrl-C は None を伝搬して全体中止する (codex round2 指摘)。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    monkeypatch.setattr(actions_env, "_select_project", lambda root: None)
    assert actions_env._run_operation(tmp_path, "project") is None
    assert called == []


def test_run_in_project_restores_cwd_on_exception(monkeypatch, tmp_path):
    """ハンドラが例外を投げても CWD / PWD は復帰する (try/finally)。"""
    target = tmp_path / "projects" / "carmo"
    target.mkdir(parents=True)
    monkeypatch.setenv("PWD", "/original/pwd")

    def _boom():
        raise RuntimeError("handler failed")

    before = os.getcwd()
    with pytest.raises(RuntimeError):
        actions_env._run_in_project(tmp_path, "carmo", _boom)
    assert os.getcwd() == before
    assert os.environ["PWD"] == "/original/pwd"


def test_run_in_project_restores_unset_pwd(monkeypatch, tmp_path):
    """元の環境に PWD が無い場合は復帰時に PWD を残さない。"""
    target = tmp_path / "projects" / "carmo"
    target.mkdir(parents=True)
    monkeypatch.delenv("PWD", raising=False)

    seen = {}

    def _probe():
        seen["pwd"] = os.environ.get("PWD")
        return 0

    assert actions_env._run_in_project(tmp_path, "carmo", _probe) == 0
    assert seen["pwd"] == str(target)
    assert "PWD" not in os.environ


def test_run_in_project_missing_dir_cancels(monkeypatch, tmp_path):
    """対象ディレクトリへ移動できない場合は実行せず _ARG_CANCEL (メニューへ戻る)。"""
    called = []
    result = actions_env._run_in_project(tmp_path, "ghost",
                                         lambda: called.append(1) or 0)
    assert result is actions_env._ARG_CANCEL
    assert called == []


# ---------------------------------------------------------------------------
# _run_operation: export / import (parser 既定値との同期)
# ---------------------------------------------------------------------------

def test_run_operation_export_default_dest(monkeypatch, tmp_path):
    """export は空入力で dest=None、残り属性は CLI parser 既定値と一致する。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "path", lambda *a, **k: "")
    assert actions_env._run_operation(tmp_path, "export") == 0
    assert captured["attrs"] == {
        "subcommand": "export", "dest": None,
        "include_projects": None, "exclude_projects": [],
        "no_global": False, "no_metadata": False, "recipients": [],
        "passphrase_env": None, "passphrase_stdin": False,
        "force_unencrypted": False, "unsafe_allow_unencrypted_bucket": False,
    }


def test_run_operation_export_explicit_dest(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "path", lambda *a, **k: "/tmp/bundle.dbenv")
    assert actions_env._run_operation(tmp_path, "export") == 0
    assert captured["attrs"]["dest"] == "/tmp/bundle.dbenv"


@pytest.mark.parametrize("path_ret", ["BACK", None])
def test_run_operation_export_cancel(monkeypatch, tmp_path, path_ret):
    """dest 入力で Esc は再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    ret = menu.MENU_BACK if path_ret == "BACK" else None
    monkeypatch.setattr(menu, "path", lambda *a, **k: ret)
    expected = actions_env._ARG_CANCEL if path_ret == "BACK" else None
    assert actions_env._run_operation(tmp_path, "export") is expected
    assert called == []


def test_run_operation_import_collects_source(monkeypatch, tmp_path):
    """import は source を収集し、残り属性は CLI parser 既定値と一致する。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "path", lambda *a, **k: "/tmp/bundle.dbenv")
    assert actions_env._run_operation(tmp_path, "import") == 0
    assert captured["attrs"] == {
        "subcommand": "import", "source": "/tmp/bundle.dbenv",
        "merge": "keep-existing", "replace_keys": "", "replace": False,
        "dry_run": False, "identities": [],
        "passphrase_env": None, "passphrase_stdin": False,
        "include_projects": None, "exclude_projects": [],
        "no_global": False, "no_metadata": False, "merge_metadata": False,
        "backup_dir": None, "keep_last": 10,
    }


@pytest.mark.parametrize("path_ret", ["BACK", None])
def test_run_operation_import_cancel(monkeypatch, tmp_path, path_ret):
    """source 入力で Esc は再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。"""
    from devbase.commands import env as env_mod
    called = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: called.append(1) or 0)
    ret = menu.MENU_BACK if path_ret == "BACK" else None
    monkeypatch.setattr(menu, "path", lambda *a, **k: ret)
    expected = actions_env._ARG_CANCEL if path_ret == "BACK" else None
    assert actions_env._run_operation(tmp_path, "import") is expected
    assert called == []


def test_export_defaults_match_cli_parser(tmp_path):
    """TUI が補う export 既定値が cli.py parser の parse 結果と一致する (plan 6 同期)。"""
    from devbase import cli
    parsed = vars(cli._create_parser().parse_args(["env", "export"]))
    tui_attrs = {"dest": None, **actions_env._export_default_attrs()}
    for key, value in tui_attrs.items():
        assert parsed[key] == value, f"export 属性 {key} が parser 既定値と乖離"


def test_import_defaults_match_cli_parser(tmp_path):
    """TUI が補う import 既定値が cli.py parser の parse 結果と一致する (plan 6 同期)。"""
    from devbase import cli
    parsed = vars(cli._create_parser().parse_args(["env", "import", "b.dbenv"]))
    tui_attrs = {"source": "b.dbenv", **actions_env._import_default_attrs()}
    for key, value in tui_attrs.items():
        assert parsed[key] == value, f"import 属性 {key} が parser 既定値と乖離"


# ---------------------------------------------------------------------------
# _collect_assignment / _select_project
# ---------------------------------------------------------------------------

def test_collect_assignment_valid(monkeypatch):
    monkeypatch.setattr(menu, "text", lambda *a, **k: "K=V")
    assert actions_env._collect_assignment() == "K=V"


def test_collect_assignment_reprompts_invalid(monkeypatch):
    """`=` 無し / キー名空は再入力を促す (cmd_env_set 到達前に弾く)。"""
    vals = iter(["NOEQUAL", "=value", "K=V"])
    monkeypatch.setattr(menu, "text", lambda *a, **k: next(vals))
    assert actions_env._collect_assignment() == "K=V"


def test_collect_assignment_cancel(monkeypatch):
    """Ctrl-C (None) は None、Esc (MENU_BACK) は MENU_BACK をそのまま返す。"""
    monkeypatch.setattr(menu, "text", lambda *a, **k: None)
    assert actions_env._collect_assignment() is None

    monkeypatch.setattr(menu, "text", lambda *a, **k: menu.MENU_BACK)
    assert actions_env._collect_assignment() is menu.MENU_BACK


def test_select_project_returns_name(monkeypatch, tmp_path):
    """一覧 (actions_project と同じ取得方法) から選んだ行の name を返す。"""
    from devbase.commands import status as status_mod
    _make_plugin_project(tmp_path, "repos/o--r/p", "carmo")
    _link_project(tmp_path, "carmo", "repos/o--r/p", "carmo")
    monkeypatch.setattr(status_mod, "_container_status_for",
                        lambda entry, counts=None: None)

    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, search=search, n=len(choices))
        return 0

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_env._select_project(tmp_path) == "carmo"
    assert captured == {"back": True, "search": True, "n": 1}


@pytest.mark.parametrize("sel_ret", ["BACK", None])
def test_select_project_cancel(monkeypatch, tmp_path, sel_ret):
    """Esc (MENU_BACK) は _ARG_CANCEL、Ctrl-C (None) は None (全体中止) を返す。"""
    from devbase.commands import status as status_mod
    _make_plugin_project(tmp_path, "repos/o--r/p", "carmo")
    _link_project(tmp_path, "carmo", "repos/o--r/p", "carmo")
    monkeypatch.setattr(status_mod, "_container_status_for",
                        lambda entry, counts=None: None)
    ret = menu.MENU_BACK if sel_ret == "BACK" else None
    monkeypatch.setattr(menu, "select", lambda *a, **k: ret)
    expected = actions_env._ARG_CANCEL if sel_ret == "BACK" else None
    assert actions_env._select_project(tmp_path) is expected


def test_select_project_empty_cancels(monkeypatch, tmp_path):
    """プロジェクトが無いときは選択メニューを出さず _ARG_CANCEL。"""
    monkeypatch.setattr(menu, "select",
                        lambda *a, **k: pytest.fail("空一覧でメニューを出さない"))
    assert actions_env._select_project(tmp_path) is actions_env._ARG_CANCEL
