"""PLAN31_2 PR4: tui.actions_plugin (plugin カテゴリ操作) のテスト。

test_actions_project.py のパターンを踏襲し、`menu.*` を monkeypatch して選択値を
注入、`cmd_plugin` を mock して **plan 2.3 の契約どおりの属性を持つ Namespace** で
呼ばれることを各サブコマンド (repo 系含む) で検証する。破壊的操作 (uninstall /
repo remove) の confirm 拒否で未実行になること、Esc/←/Ctrl-C の遷移も検証する。
"""

from __future__ import annotations

import pytest
import yaml

from devbase.tui import actions_plugin, flow, menu


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    """サブメニューは実行後に留まる。各テストの一時停止 (Enter 待ち) は無効化する。"""
    monkeypatch.setattr(flow, "pause_for_review", lambda: True)


def _seq(*values):
    """呼ばれるたび values を順に返し、尽きたら最後の値を返すコールバックを作る。

    操作メニューは実行後に再表示されるため、選択スタブは末尾に MENU_BACK を置く。
    """
    box = {"i": 0}

    def _next(*_a, **_k):
        i = box["i"]
        box["i"] = min(i + 1, len(values) - 1)
        return values[i]

    return _next


def _seed_registry(root, plugins=(), repos=()):
    """plugins.yml を生成して導入済み plugin / 登録済みリポジトリを注入する。"""
    data = {
        "repositories": [
            {"name": r, "url": f"https://github.com/o/{r}",
             "added_at": "2026-01-01T00:00:00+00:00", "plugins": []}
            for r in repos
        ],
        "installed_plugins": [
            {"name": p, "version": "1.0", "source": "o--r",
             "installed_at": "2026-01-01T00:00:00+00:00",
             "path": f"repos/o--r/{p}", "linked": False}
            for p in plugins
        ],
    }
    (root / "plugins.yml").write_text(yaml.safe_dump(data, allow_unicode=True))


def _capture_dispatch(monkeypatch):
    """cmd_plugin の呼び出し引数を全属性キャプチャするヘルパ。"""
    from devbase.commands import plugin as plugin_mod
    captured = {}

    def _spy(devbase_root, args):
        captured["devbase_root"] = devbase_root
        captured["subcommand"] = args.subcommand
        for k in ("available", "source", "link", "install_all", "name",
                  "repo_command", "url", "force"):
            if hasattr(args, k):
                captured[k] = getattr(args, k)
        return 0

    monkeypatch.setattr(plugin_mod, "cmd_plugin", _spy)
    return captured


def _no_dispatch(monkeypatch):
    """cmd_plugin が呼ばれないことを検証するためのスパイ (呼び出しを記録)。"""
    from devbase.commands import plugin as plugin_mod
    called = []
    monkeypatch.setattr(plugin_mod, "cmd_plugin",
                        lambda root, args: called.append(1) or 0)
    return called


# ---------------------------------------------------------------------------
# run(): plugin メニューのループと戻り値プロトコル
# ---------------------------------------------------------------------------

def _in_group(monkeypatch, group, *ops):
    """第 1 階層で ``group`` を選び、第 2 階層で ``ops`` を順に選ぶスタブを置く。"""
    monkeypatch.setattr(actions_plugin, "_select_operation", _seq(group, menu.MENU_BACK))
    seen = []
    select = _seq(*ops, menu.MENU_BACK)

    def _select_group(chosen):
        seen.append(chosen)
        return select()

    monkeypatch.setattr(actions_plugin, "_select_group_operation", _select_group)
    return seen


def test_run_executes_then_stays_in_submenu(monkeypatch, tmp_path):
    """操作を実行 → 用途のメニューに留まり、Esc/← (MENU_BACK) で 1 つずつ戻る。"""
    captured = _capture_dispatch(monkeypatch)
    seen = _in_group(monkeypatch, "maintain", "sync")
    assert actions_plugin.run(tmp_path) is menu.MENU_BACK
    assert captured["subcommand"] == "sync"
    assert captured["devbase_root"] == tmp_path
    assert seen == ["maintain", "maintain"], "実行の後は同じ用途のメニューを再表示する"


def test_run_executes_nonzero_then_back(monkeypatch, tmp_path):
    """非0 を返す操作でも実行後はメニューに留まり、戻りは MENU_BACK。"""
    from devbase.commands import plugin as plugin_mod
    calls = []
    monkeypatch.setattr(plugin_mod, "cmd_plugin", lambda root, args: calls.append(1) or 1)
    _in_group(monkeypatch, "maintain", "sync")
    assert actions_plugin.run(tmp_path) is menu.MENU_BACK
    assert calls == [1], "操作は実行される (rc は終了コードへは伝搬しない)"


def test_run_back_returns_menu_back(monkeypatch, tmp_path):
    """plugin メニューで Esc/← (MENU_BACK) を押すとトップへ戻る (何も起動しない)。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_operation", lambda: menu.MENU_BACK)
    assert actions_plugin.run(tmp_path) is menu.MENU_BACK
    assert called == []


def test_run_ctrl_c_aborts(monkeypatch, tmp_path):
    """plugin メニューで Ctrl-C (None) を押すと全体中止 (None を返す)。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_operation", lambda: None)
    assert actions_plugin.run(tmp_path) is None
    assert called == []


def test_run_arg_cancel_reshows_menu(monkeypatch, tmp_path):
    """引数収集を中止 (_ARG_CANCEL) すると同じ用途のメニューを再表示し、再選択で実行する。"""
    seen = _in_group(monkeypatch, "install-update", "install", "update")
    run_calls = []

    def fake_run_op(root, op):
        run_calls.append(op)
        return actions_plugin._ARG_CANCEL if op == "install" else 0

    monkeypatch.setattr(actions_plugin, "_run_operation", fake_run_op)

    assert actions_plugin.run(tmp_path) is menu.MENU_BACK
    assert run_calls == ["install", "update"]
    assert len(seen) == 3, "引数中止と実行後に用途のメニューが再表示される"


def test_run_group_back_reshows_plugin_menu(monkeypatch, tmp_path):
    """用途のメニューで Esc/← (MENU_BACK) を押すと第 1 階層へ戻る。"""
    select_calls = []
    monkeypatch.setattr(actions_plugin, "_select_operation",
                        lambda: (select_calls.append(1),
                                 "view" if len(select_calls) == 1 else menu.MENU_BACK)[1])
    monkeypatch.setattr(actions_plugin, "_group_menu", lambda root, group: menu.MENU_BACK)

    assert actions_plugin.run(tmp_path) is menu.MENU_BACK
    assert len(select_calls) == 2, "用途のメニューから戻ると第 1 階層が再表示される"


def test_run_group_ctrl_c_aborts(monkeypatch, tmp_path):
    """用途のメニューで Ctrl-C (None) を受けたら全体中止を伝搬する。"""
    monkeypatch.setattr(actions_plugin, "_select_operation", lambda: "view")
    monkeypatch.setattr(actions_plugin, "_group_menu", lambda root, group: None)
    assert actions_plugin.run(tmp_path) is None


def test_ctrl_c_inside_an_operation_aborts_everything(monkeypatch, tmp_path):
    """引数収集の Ctrl-C は 2 階層を抜けて全体中止になる。"""
    called = _no_dispatch(monkeypatch)
    _in_group(monkeypatch, "install-update", "install")
    monkeypatch.setattr(menu, "text", lambda *a, **k: None)
    assert actions_plugin.run(tmp_path) is None
    assert called == []


# ---------------------------------------------------------------------------
# メニューの構成
# ---------------------------------------------------------------------------

def test_select_operation_lists_the_purposes(monkeypatch):
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, search=search, labels=[c[0] for c in choices])
        return choices[0][1]

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_plugin._select_operation() == "view"
    assert captured["back"] is True
    assert captured["search"] is False
    assert captured["labels"] == ["見る", "新規追加", "導入・更新", "削除", "保守"]


@pytest.mark.parametrize("group, values", [
    ("view", ["list", "list-available", "info", "repo-list"]),
    ("new", ["new-project", "new-plugin", "new-repo"]),
    ("install-update", ["install", "update", "repo-add", "repo-refresh"]),
    ("remove", ["uninstall", "repo-remove"]),
    ("maintain", ["sync", "migrate"]),
])
def test_select_group_operation_lists_the_ops(monkeypatch, group, values):
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, values=[c[1] for c in choices])
        return choices[0][1]

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_plugin._select_group_operation(group) == values[0]
    assert captured["back"] is True
    assert captured["values"] == values


def test_every_menu_item_has_a_handler():
    items = [value for ops in actions_plugin._GROUP_OPS.values() for _, value in ops]
    assert sorted(items) == sorted(actions_plugin._OP_HANDLERS)


@pytest.mark.parametrize("op, repo_command", [
    ("repo-list", "list"), ("repo-add", "add"), ("repo-remove", "remove"),
    ("repo-refresh", "refresh"),
])
def test_repo_items_reach_the_repo_operation(monkeypatch, tmp_path, op, repo_command):
    seen = []
    monkeypatch.setattr(actions_plugin, "_run_repo_operation",
                        lambda root, value: seen.append(value) or 0)
    assert actions_plugin._run_operation(tmp_path, op) == 0
    assert seen == [repo_command]


# ---------------------------------------------------------------------------
# 新規追加: 値を集めて CLI のハンドラへ渡す
# ---------------------------------------------------------------------------

class _Plan:
    def __init__(self, noop_reason=""):
        self.noop_reason = noop_reason

    def describe(self):
        return "作成の計画: テスト"


class _Target:
    def __init__(self, plugin):
        self.plugin = plugin
        self.label = f"{plugin} (owner/r)"


def _texts(monkeypatch, *values):
    monkeypatch.setattr(menu, "text", _seq(*values))


def _capture_project(monkeypatch):
    from devbase.commands import project as project_mod
    captured = {}

    def _spy(root, args):
        captured.update(vars(args))
        return 0

    monkeypatch.setattr(project_mod, "cmd_project_new", _spy)
    return captured


@pytest.fixture
def creation(monkeypatch):
    """gh に触らないよう creator の読むだけの関数を差し替える。"""
    from devbase.plugin import creator
    from devbase.plugin.github import GhClient

    calls = {}
    monkeypatch.setattr(GhClient, "ensure_ready", lambda self: None)
    monkeypatch.setattr(creator, "plugin_candidates", lambda *a: [_Target("one")])
    monkeypatch.setattr(creator, "suggest_group", lambda *a: "acme")

    def _plan(kind):
        def _record(registry, gh, value, **kwargs):
            calls[kind] = (value, kwargs)
            return _Plan()
        return _record

    for kind in ("repo", "plugin", "project"):
        monkeypatch.setattr(creator, f"plan_{kind}", _plan(kind))
    return calls


def test_new_repo_passes_values_with_yes(monkeypatch, tmp_path, creation, capsys):
    from devbase.commands import plugin as plugin_mod
    captured = {}
    monkeypatch.setattr(plugin_mod, "cmd_plugin",
                        lambda root, args: captured.update(vars(args)) or 0)
    _texts(monkeypatch, "owner/repo")
    answers = _seq(True, True)
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: answers())
    assert actions_plugin._run_operation(tmp_path, "new-repo") == 0
    assert captured == {"subcommand": "repo", "repo_command": "new", "repo": "owner/repo",
                        "public": True, "yes": True}
    assert creation["repo"] == ("owner/repo", {"public": True})
    assert "作成の計画" in capsys.readouterr().out


def test_new_repo_public_defaults_to_no(monkeypatch, tmp_path, creation):
    _capture_dispatch(monkeypatch)
    _texts(monkeypatch, "owner/repo")
    defaults = []

    def fake_confirm(message, *, default=False):
        defaults.append(default)
        return True

    monkeypatch.setattr(menu, "confirm", fake_confirm)
    assert actions_plugin._run_operation(tmp_path, "new-repo") == 0
    assert defaults[0] is False


def test_new_repo_noop_skips_the_confirmation(monkeypatch, tmp_path):
    from devbase.plugin import creator
    from devbase.plugin.github import GhClient
    monkeypatch.setattr(GhClient, "ensure_ready", lambda self: None)
    monkeypatch.setattr(creator, "plan_repo", lambda *a, **k: _Plan("登録済み"))
    captured = _capture_dispatch(monkeypatch)
    _texts(monkeypatch, "owner/repo")
    answers = []
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: answers.append(1) or False)
    assert actions_plugin._run_operation(tmp_path, "new-repo") == 0
    assert len(answers) == 1, "公開の確認だけで、計画の確認は出さない"
    assert captured["repo_command"] == "new"


def test_new_plugin_passes_values(monkeypatch, tmp_path, creation):
    from devbase.commands import plugin as plugin_mod
    captured = {}
    monkeypatch.setattr(plugin_mod, "cmd_plugin",
                        lambda root, args: captured.update(vars(args)) or 0)
    _texts(monkeypatch, "owner/repo", "team-a")
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: True)
    assert actions_plugin._run_operation(tmp_path, "new-plugin") == 0
    assert captured == {"subcommand": "new", "repo": "owner/repo", "name": "team-a",
                        "public": False, "yes": True}


def test_new_plugin_empty_name_is_none(monkeypatch, tmp_path, creation):
    captured = _capture_dispatch(monkeypatch)
    _texts(monkeypatch, "owner/repo", "")
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: True)
    assert actions_plugin._run_operation(tmp_path, "new-plugin") == 0
    assert captured["name"] is None
    assert creation["plugin"][1]["name"] is None


def test_new_project_single_candidate_shows_no_select(monkeypatch, tmp_path, creation):
    captured = _capture_project(monkeypatch)
    prompts = []

    def fake_text(message, *, default=None, allow_empty=True):
        prompts.append((message, default))
        return {0: "owner/app", 1: "web", 2: "typed"}[len(prompts) - 1]

    monkeypatch.setattr(menu, "text", fake_text)
    monkeypatch.setattr(menu, "select", lambda *a, **k: pytest.fail("候補 1 件で選択を出した"))
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: True)
    assert actions_plugin._run_operation(tmp_path, "new-project") == 0
    assert captured == {"subcommand": "new", "repo": "owner/app", "name": "web",
                        "plugin": "one", "group": "typed", "yes": True}
    assert prompts[2][1] == "acme", "グループは案を既定値にする"
    assert creation["project"] == ("owner/app", {"plugin": "one", "group": "typed",
                                                 "name": "web"})


def test_new_project_multiple_candidates_select(monkeypatch, tmp_path, creation):
    from devbase.plugin import creator
    monkeypatch.setattr(creator, "plugin_candidates",
                        lambda *a: [_Target("one"), _Target("two")])
    captured = _capture_project(monkeypatch)
    _texts(monkeypatch, "owner/app", "", "acme")
    offered = {}

    def fake_select(message, choices, *, back, search):
        offered.update(values=[c[1] for c in choices], back=back)
        return "two"

    monkeypatch.setattr(menu, "select", fake_select)
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: True)
    assert actions_plugin._run_operation(tmp_path, "new-project") == 0
    assert offered == {"values": ["one", "two"], "back": True}
    assert captured["plugin"] == "two"
    assert captured["name"] is None


def test_new_project_without_candidates_reports(monkeypatch, tmp_path, creation, caplog):
    from devbase.plugin import creator
    monkeypatch.setattr(creator, "plugin_candidates", lambda *a: [])
    captured = _capture_project(monkeypatch)
    _texts(monkeypatch, "owner/app", "")
    assert actions_plugin._run_operation(tmp_path, "new-project") == 1
    assert captured == {}
    assert "入れ先" in caplog.text


@pytest.mark.parametrize("op", ["new-project", "new-plugin", "new-repo"])
def test_new_esc_at_the_repository_goes_back(monkeypatch, tmp_path, creation, op):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "text", lambda *a, **k: menu.MENU_BACK)
    assert actions_plugin._run_operation(tmp_path, op) is actions_plugin._ARG_CANCEL
    assert called == []


@pytest.mark.parametrize("op", ["new-project", "new-plugin", "new-repo"])
def test_new_ctrl_c_at_the_repository_aborts(monkeypatch, tmp_path, creation, op):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "text", lambda *a, **k: None)
    assert actions_plugin._run_operation(tmp_path, op) is None
    assert called == []


@pytest.mark.parametrize("answer, expected", [
    (False, actions_plugin._ARG_CANCEL),
    (menu.MENU_BACK, actions_plugin._ARG_CANCEL),
    (None, None),
])
def test_new_plan_confirmation_refused(monkeypatch, tmp_path, creation, answer, expected):
    called = _no_dispatch(monkeypatch)
    captured = _capture_project(monkeypatch)
    _texts(monkeypatch, "owner/app", "", "acme")
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: answer)
    assert actions_plugin._run_operation(tmp_path, "new-project") is expected
    assert called == []
    assert captured == {}


@pytest.mark.parametrize("step", ["select", "group"])
def test_new_project_esc_in_the_middle_goes_back(monkeypatch, tmp_path, creation, step):
    from devbase.plugin import creator
    monkeypatch.setattr(creator, "plugin_candidates",
                        lambda *a: [_Target("one"), _Target("two")])
    captured = _capture_project(monkeypatch)
    if step == "select":
        _texts(monkeypatch, "owner/app", "")
        monkeypatch.setattr(menu, "select", lambda *a, **k: menu.MENU_BACK)
    else:
        _texts(monkeypatch, "owner/app", "", menu.MENU_BACK)
        monkeypatch.setattr(menu, "select", lambda *a, **k: "one")
    assert actions_plugin._run_operation(tmp_path, "new-project") is actions_plugin._ARG_CANCEL
    assert captured == {}


def test_new_plan_error_is_reported_with_rc_1(monkeypatch, tmp_path, creation, caplog):
    from devbase.errors import RepositoryError
    from devbase.plugin import creator

    def _boom(*_a, **_k):
        raise RepositoryError("push する権限がありません")

    monkeypatch.setattr(creator, "plan_plugin", _boom)
    called = _no_dispatch(monkeypatch)
    _texts(monkeypatch, "owner/repo", "")
    assert actions_plugin._run_operation(tmp_path, "new-plugin") == 1
    assert called == []
    assert "push する権限がありません" in caplog.text


# ---------------------------------------------------------------------------
# _run_operation: 各操作の引数収集 + dispatch 契約 (plan 2.3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("op,available", [("list", False), ("list-available", True)])
def test_run_operation_list_available_flag(monkeypatch, tmp_path, op, available):
    """list 系は確認プロンプトなしで即実行する (--available はメニュー項目で分岐)。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: pytest.fail("list で確認を求めない"))
    assert actions_plugin._run_operation(tmp_path, op) == 0
    assert captured["subcommand"] == "list"
    assert captured["available"] is available


def test_run_operation_install_collects_source_only(monkeypatch, tmp_path):
    """install は source のみ収集し、--link/--all は CLI 既定 (False) で実行する。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(menu, "text", lambda *a, **k: "owner/repo")
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: pytest.fail("install で確認を求めない"))
    assert actions_plugin._run_operation(tmp_path, "install") == 0
    assert captured["subcommand"] == "install"
    assert captured["source"] == "owner/repo"
    assert captured["link"] is False and captured["install_all"] is False


@pytest.mark.parametrize("text_ret", ["BACK", None])
def test_run_operation_install_source_cancel(monkeypatch, tmp_path, text_ret):
    """source 入力で Esc は再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。"""
    called = _no_dispatch(monkeypatch)
    ret = menu.MENU_BACK if text_ret == "BACK" else None
    monkeypatch.setattr(menu, "text", lambda *a, **k: ret)
    expected = actions_plugin._ARG_CANCEL if text_ret == "BACK" else None
    assert actions_plugin._run_operation(tmp_path, "install") is expected
    assert called == []


def test_run_operation_uninstall_confirmed(monkeypatch, tmp_path):
    """uninstall は一覧から選んだ name で confirm=True のとき実行する (plan 3.4)。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: "ndf")
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: True)
    assert actions_plugin._run_operation(tmp_path, "uninstall") == 0
    assert captured["subcommand"] == "uninstall" and captured["name"] == "ndf"


@pytest.mark.parametrize("confirm_ret", [False, "BACK", None])
def test_run_operation_uninstall_cancelled_does_not_dispatch(
        monkeypatch, tmp_path, confirm_ret):
    """uninstall の confirm を拒否 (False) / Esc / Ctrl-C したら実行しない。

    拒否と Esc はサブメニュー再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。
    """
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: "ndf")
    ret = menu.MENU_BACK if confirm_ret == "BACK" else confirm_ret
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: ret)
    expected = None if confirm_ret is None else actions_plugin._ARG_CANCEL
    assert actions_plugin._run_operation(tmp_path, "uninstall") is expected
    assert called == [], "確認を拒否/中止したら uninstall しない"


def test_run_operation_uninstall_name_cancel(monkeypatch, tmp_path):
    """name 選択を中止したら confirm も dispatch もしない。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: actions_plugin._ARG_CANCEL)
    confirms = []
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: confirms.append(1) or True)
    assert actions_plugin._run_operation(tmp_path, "uninstall") is actions_plugin._ARG_CANCEL
    assert called == [] and confirms == []


def test_run_operation_update_named(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: "ndf")
    assert actions_plugin._run_operation(tmp_path, "update") == 0
    assert captured["subcommand"] == "update" and captured["name"] == "ndf"


def test_run_operation_update_all_is_name_none(monkeypatch, tmp_path):
    """「全 plugin を更新」('') は name=None で委譲する (CLI の引数省略と同じ)。"""
    captured = _capture_dispatch(monkeypatch)
    seen = {}
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: seen.update(k) or "")
    assert actions_plugin._run_operation(tmp_path, "update") == 0
    assert captured["subcommand"] == "update" and captured["name"] is None
    assert seen.get("all_label"), "update では全 plugin 選択肢を提示する"


def test_run_operation_update_cancel(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: actions_plugin._ARG_CANCEL)
    assert actions_plugin._run_operation(tmp_path, "update") is actions_plugin._ARG_CANCEL
    assert called == []


def test_run_operation_update_ctrl_c_aborts(monkeypatch, tmp_path):
    """name 選択中の Ctrl-C は None を伝搬して全体中止する (codex round2 指摘)。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: None)
    assert actions_plugin._run_operation(tmp_path, "update") is None
    assert called == []


def test_run_operation_info(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: "ndf")
    assert actions_plugin._run_operation(tmp_path, "info") == 0
    assert captured["subcommand"] == "info" and captured["name"] == "ndf"


def test_run_operation_info_cancel(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_installed_plugin",
                        lambda root, msg, **k: actions_plugin._ARG_CANCEL)
    assert actions_plugin._run_operation(tmp_path, "info") is actions_plugin._ARG_CANCEL
    assert called == []


@pytest.mark.parametrize("op", ["sync", "migrate"])
def test_run_operation_sync_migrate_no_attrs(monkeypatch, tmp_path, op):
    """sync/migrate は引数収集なしで即委譲する (plan 2.3: 属性なし)。"""
    captured = _capture_dispatch(monkeypatch)
    assert actions_plugin._run_operation(tmp_path, op) == 0
    assert captured == {"devbase_root": tmp_path, "subcommand": op}, \
        "subcommand 以外の属性を載せない"


def test_run_operation_unknown_is_noop(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    assert actions_plugin._run_operation(tmp_path, "bogus") is actions_plugin._ARG_CANCEL
    assert called == []


# ---------------------------------------------------------------------------
# _run_repo_operation: repo 系の引数収集 + dispatch 契約 (plan 2.3)
# ---------------------------------------------------------------------------

def test_run_repo_operation_list(monkeypatch, tmp_path):
    """repo list は repo_command='list' のみで委譲する。"""
    captured = _capture_dispatch(monkeypatch)
    assert actions_plugin._run_repo_operation(tmp_path, "list") == 0
    assert captured == {"devbase_root": tmp_path, "subcommand": "repo",
                        "repo_command": "list"}


def test_run_repo_operation_add_with_custom_name(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    texts = iter(["https://github.com/o/r", "myrepo"])  # url, name
    monkeypatch.setattr(menu, "text", lambda *a, **k: next(texts))
    assert actions_plugin._run_repo_operation(tmp_path, "add") == 0
    assert captured["subcommand"] == "repo"
    assert captured["repo_command"] == "add"
    assert captured["url"] == "https://github.com/o/r"
    assert captured["name"] == "myrepo"


def test_run_repo_operation_add_empty_name_is_none(monkeypatch, tmp_path):
    """カスタム名を空にすると name=None (URL から自動命名) で委譲する。"""
    captured = _capture_dispatch(monkeypatch)
    texts = iter(["o/r", ""])                # url, name(空)
    monkeypatch.setattr(menu, "text", lambda *a, **k: next(texts))
    assert actions_plugin._run_repo_operation(tmp_path, "add") == 0
    assert captured["url"] == "o/r" and captured["name"] is None


@pytest.mark.parametrize("text_ret", ["BACK", None])
def test_run_repo_operation_add_url_cancel(monkeypatch, tmp_path, text_ret):
    """url 入力で Esc は再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。"""
    called = _no_dispatch(monkeypatch)
    ret = menu.MENU_BACK if text_ret == "BACK" else None
    monkeypatch.setattr(menu, "text", lambda *a, **k: ret)
    expected = actions_plugin._ARG_CANCEL if text_ret == "BACK" else None
    assert actions_plugin._run_repo_operation(tmp_path, "add") is expected
    assert called == []


@pytest.mark.parametrize("force", [True, False])
def test_run_repo_operation_remove_confirmed(monkeypatch, tmp_path, force):
    """repo remove は confirm=True のとき force フラグ付きで実行する (plan 3.4)。"""
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: "r1")
    confirms = iter([True, force])           # 削除確認=True, force
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: next(confirms))
    assert actions_plugin._run_repo_operation(tmp_path, "remove") == 0
    assert captured["subcommand"] == "repo"
    assert captured["repo_command"] == "remove"
    assert captured["name"] == "r1" and captured["force"] is force


@pytest.mark.parametrize("confirm_ret", [False, "BACK", None])
def test_run_repo_operation_remove_cancelled_does_not_dispatch(
        monkeypatch, tmp_path, confirm_ret):
    """repo remove の confirm を拒否 (False) / Esc / Ctrl-C したら実行しない。

    拒否と Esc はサブメニュー再表示 (_ARG_CANCEL)、Ctrl-C は全体中止 (None)。
    """
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: "r1")
    ret = menu.MENU_BACK if confirm_ret == "BACK" else confirm_ret
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: ret)
    expected = None if confirm_ret is None else actions_plugin._ARG_CANCEL
    assert actions_plugin._run_repo_operation(tmp_path, "remove") is expected
    assert called == [], "確認を拒否/中止したら remove しない"


@pytest.mark.parametrize("confirm_ret", ["BACK", None])
def test_run_repo_operation_remove_force_cancel(monkeypatch, tmp_path, confirm_ret):
    """force の確認で Esc / Ctrl-C したら実行しない。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: "r1")
    ret = menu.MENU_BACK if confirm_ret == "BACK" else None
    confirms = iter([True, ret])             # 削除確認=True, force で中止
    monkeypatch.setattr(menu, "confirm", lambda *a, **k: next(confirms))
    expected = actions_plugin._ARG_CANCEL if confirm_ret == "BACK" else None
    assert actions_plugin._run_repo_operation(tmp_path, "remove") is expected
    assert called == []


def test_run_repo_operation_remove_name_cancel(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: actions_plugin._ARG_CANCEL)
    assert actions_plugin._run_repo_operation(tmp_path, "remove") is actions_plugin._ARG_CANCEL
    assert called == []


def test_run_repo_operation_refresh_named(monkeypatch, tmp_path):
    captured = _capture_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: "r1")
    assert actions_plugin._run_repo_operation(tmp_path, "refresh") == 0
    assert captured["subcommand"] == "repo"
    assert captured["repo_command"] == "refresh" and captured["name"] == "r1"


def test_run_repo_operation_refresh_all_is_name_none(monkeypatch, tmp_path):
    """「全リポジトリを更新」('') は name=None で委譲する (CLI の引数省略と同じ)。"""
    captured = _capture_dispatch(monkeypatch)
    seen = {}
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: seen.update(k) or "")
    assert actions_plugin._run_repo_operation(tmp_path, "refresh") == 0
    assert captured["repo_command"] == "refresh" and captured["name"] is None
    assert seen.get("all_label"), "refresh では全リポジトリ選択肢を提示する"


def test_run_repo_operation_refresh_cancel(monkeypatch, tmp_path):
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: actions_plugin._ARG_CANCEL)
    assert actions_plugin._run_repo_operation(tmp_path, "refresh") is actions_plugin._ARG_CANCEL
    assert called == []


def test_run_repo_operation_remove_ctrl_c_aborts(monkeypatch, tmp_path):
    """リポジトリ選択中の Ctrl-C は None を伝搬して全体中止する (codex round2 指摘)。"""
    called = _no_dispatch(monkeypatch)
    monkeypatch.setattr(actions_plugin, "_select_repository",
                        lambda root, msg, **k: None)
    monkeypatch.setattr(menu, "confirm",
                        lambda *a, **k: pytest.fail("Ctrl-C 後に確認を求めない"))
    assert actions_plugin._run_repo_operation(tmp_path, "remove") is None
    assert called == []


# ---------------------------------------------------------------------------
# 名前選択ヘルパ (_select_name / _select_installed_plugin / _select_repository)
# ---------------------------------------------------------------------------

def test_select_name_lists_names(monkeypatch):
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured.update(back=back, search=search,
                        values=[c[1] for c in choices])
        return "b"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_plugin._select_name("選択", ["a", "b"]) == "b"
    assert captured["back"] is True
    assert captured["values"] == ["a", "b"]


def test_select_name_all_label_first_and_returns_empty(monkeypatch):
    """all_label は value='' で先頭に置き、選択時は '' を返す (呼び出し側で None へ変換)。"""
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured["values"] = [c[1] for c in choices]
        return ""

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_plugin._select_name("選択", ["a"], all_label="全対象") == ""
    assert captured["values"] == ["", "a"]


@pytest.mark.parametrize("sel", [None, "BACK"])
def test_select_name_back_or_ctrl_c(monkeypatch, sel):
    """Esc/← (MENU_BACK) は _ARG_CANCEL、Ctrl-C (None) は None (全体中止) を返す。"""
    ret = menu.MENU_BACK if sel == "BACK" else None
    monkeypatch.setattr(menu, "select", lambda *a, **k: ret)
    expected = actions_plugin._ARG_CANCEL if sel == "BACK" else None
    assert actions_plugin._select_name("選択", ["a"]) is expected


def test_select_installed_plugin_reads_registry(monkeypatch, tmp_path):
    """plugins.yml の導入済み plugin が選択肢に並ぶ (registry 結合)。"""
    _seed_registry(tmp_path, plugins=("ndf", "myapp"))
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured["values"] = [c[1] for c in choices]
        return "ndf"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_plugin._select_installed_plugin(tmp_path, "選択") == "ndf"
    assert captured["values"] == ["ndf", "myapp"]


def test_select_installed_plugin_empty_is_cancel(monkeypatch, tmp_path):
    """導入済み plugin が無ければ選択メニューを出さず中止する。"""
    selects = []
    monkeypatch.setattr(menu, "select", lambda *a, **k: selects.append(1) or None)
    assert actions_plugin._select_installed_plugin(tmp_path, "選択") \
        is actions_plugin._ARG_CANCEL
    assert selects == []


def test_select_repository_reads_registry(monkeypatch, tmp_path):
    """plugins.yml の登録済みリポジトリが選択肢に並ぶ (registry 結合)。"""
    _seed_registry(tmp_path, repos=("r1", "r2"))
    captured = {}

    def fake_select(message, choices, *, back, search):
        captured["values"] = [c[1] for c in choices]
        return "r2"

    monkeypatch.setattr(menu, "select", fake_select)
    assert actions_plugin._select_repository(tmp_path, "選択") == "r2"
    assert captured["values"] == ["r1", "r2"]


def test_select_repository_empty_is_cancel(monkeypatch, tmp_path):
    selects = []
    monkeypatch.setattr(menu, "select", lambda *a, **k: selects.append(1) or None)
    assert actions_plugin._select_repository(tmp_path, "選択") \
        is actions_plugin._ARG_CANCEL
    assert selects == []


def test_creation_drops_injected_secrets_before_gh(monkeypatch, tmp_path):
    """TUI は起動時の注入が載ったまま動くので、gh を呼ぶ前に落とす (#514 の決定 11)。"""
    from devbase.env import runtime

    cleared = []
    monkeypatch.setattr(runtime, "clear_injected", lambda: cleared.append(True))
    actions_plugin._creation_context(tmp_path)
    assert cleared == [True]
