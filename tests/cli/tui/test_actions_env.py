"""tui.actions_env (env カテゴリ操作) のテスト (PLAN31_2 PR3 → メニュー再構成)。

test_actions_project.py のパターンを踏襲し、`menu.*` を monkeypatch して選択値を
注入、`cmd_env` を mock して契約どおりの属性を持つ Namespace で呼ばれることを
検証する。TUI はキーの一覧と編集・sync・init・OpenBao の接続設定の 4 つを提供する
(#273 / #312)。一覧・edit・project と export/import は CLI 専用 (メニューに出さない)。
プロジェクトの置き場への委譲の chdir → 復帰、Esc/←/Ctrl-C の遷移も検証する。
"""

from __future__ import annotations

import os

import pytest

from devbase.tui import actions_env, flow, menu


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    """サブメニューは実行後に留まる。各テストの一時停止 (Enter 待ち) は無効化する。"""
    monkeypatch.setattr(flow, "pause_for_review", lambda: True)


def _seq(*values):
    """呼ばれるたび values を順に返し、尽きたら最後の値を返すコールバックを作る。

    サブメニューは操作実行後に再表示されるため、選択スタブは「操作 → … →
    MENU_BACK」のように最後に MENU_BACK を置いてループを終わらせる。
    """
    box = {"i": 0}

    def _next(*_a, **_k):
        i = box["i"]
        box["i"] = min(i + 1, len(values) - 1)
        return values[i]

    return _next


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

def test_run_executes_and_stays_in_submenu(monkeypatch, tmp_path):
    """操作を選んで実行 → サブメニューに留まり、Esc/← で初めてトップへ戻る。"""
    captured = _capture_dispatch(monkeypatch)
    # sync を実行 → サブメニュー再表示 → MENU_BACK でトップへ。
    monkeypatch.setattr(actions_env, "_select_action", _seq("sync", menu.MENU_BACK))

    assert actions_env.run(tmp_path) is menu.MENU_BACK
    assert captured["root"] == tmp_path
    assert captured["attrs"] == {"subcommand": "sync"}


def test_run_executes_then_back_runs_operation(monkeypatch, tmp_path):
    """非0 を返す操作でも実行後はサブメニューに留まり、戻りは MENU_BACK。"""
    from devbase.commands import env as env_mod
    calls = []
    monkeypatch.setattr(env_mod, "cmd_env", lambda root, args: calls.append(1) or 1)
    monkeypatch.setattr(actions_env, "_select_action", _seq("sync", menu.MENU_BACK))

    assert actions_env.run(tmp_path) is menu.MENU_BACK
    assert calls == [1], "操作は実行される (rc は終了コードへは伝搬しない)"


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
    # 1 回目: keys (→ 引数収集中止) / 2 回目: sync (→ 実行) / 3 回目: MENU_BACK
    select = _seq("keys", "sync", menu.MENU_BACK)
    select_calls = []
    monkeypatch.setattr(actions_env, "_select_action",
                        lambda: select_calls.append(1) or select())

    run_calls = []

    def fake_run_op(root, op):
        run_calls.append(op)
        return actions_env._ARG_CANCEL if op == "keys" else 0

    monkeypatch.setattr(actions_env, "_run_operation", fake_run_op)

    assert actions_env.run(tmp_path) is menu.MENU_BACK
    assert run_calls == ["keys", "sync"]
    assert len(select_calls) == 3, "引数中止と実行後にサブメニューが再表示される"


def test_run_propagates_ctrl_c_from_operation(monkeypatch, tmp_path):
    """引数収集中の Ctrl-C (None) はサブメニューを再表示せず全体中止を伝搬する。"""
    select_calls = []
    monkeypatch.setattr(actions_env, "_select_action",
                        lambda: select_calls.append(1) or "keys")
    monkeypatch.setattr(actions_env, "_run_operation", lambda root, op: None)

    assert actions_env.run(tmp_path) is None
    assert len(select_calls) == 1, "Ctrl-C でサブメニューを再表示しない"


# ---------------------------------------------------------------------------
# _select_action: menu.select への委譲 (参照・対話系のみの提示)
# ---------------------------------------------------------------------------

def test_select_action_lists_all_ops(monkeypatch):
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, search=search,
                        values=[c[1] for c in choices])
        return "keys"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_env._select_action() == "keys"
    assert captured["back"] is True
    assert captured["search"] is False
    # 一覧・edit・project はキーの一覧と編集と役割が重なるため出さない (#312)。
    # export/import は CLI 専用でメニューに出さない。
    assert captured["values"] == ["keys", "sync", "init", "openbao"]
    assert captured["values"][0] == "keys", "Enter 連打で書き込みの操作へ到達しない"


# ---------------------------------------------------------------------------
# _run_operation: 引数なし系 (sync / init)
# ---------------------------------------------------------------------------

def test_run_operation_sync_no_attrs(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    assert actions_env._run_operation(tmp_path, "sync") == 0
    assert captured["attrs"] == {"subcommand": "sync"}


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
# _run_in_project (プロジェクトの置き場への委譲の chdir + 復帰)
# ---------------------------------------------------------------------------

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
# #273: キーの一覧と編集・OpenBao の接続設定
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("op", ["list-global", "edit", "project"])
def test_ops_overlapping_the_key_screen_are_gone(op):
    """一覧・edit・project はキーの一覧と編集と役割が重なるため TUI から外した (#312)。"""
    assert op not in dict((v, k) for k, v in actions_env._ENV_OPS)
    assert op not in actions_env._OP_HANDLERS


@pytest.mark.parametrize("op, module", [("keys", "actions_env_keys"),
                                        ("openbao", "actions_env_openbao")])
def test_new_ops_run_their_screens(monkeypatch, tmp_path, op, module):
    import importlib

    screen = importlib.import_module(f"devbase.tui.{module}")
    seen = []
    monkeypatch.setattr(screen, "run", lambda root: seen.append(root) or 0)

    assert actions_env._run_operation(tmp_path, op) == 0
    assert seen == [tmp_path]
