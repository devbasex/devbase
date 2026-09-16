"""PLAN58 決定 8: `devbase list` の起動中の行から、プロファイルを起動・停止する

2 項目はプロファイルを持つプロジェクトにだけ出す。選んだ後はプロファイル名を選ばせ
(1 件でも選択を出す)、共有のハンドラ ``cmd_project`` へ委譲する。TUI はコマンドの
中身を持たない。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from devbase.commands import container
from devbase.errors import DevbaseError
from devbase.tui import actions_project, flow, menu

PROFILE_ITEMS = [("テスト用サーバ起動 (profile up)", "profile-up"),
                 ("テスト用サーバ停止 (profile down)", "profile-down")]


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(flow, "pause_for_review", lambda: True)


@pytest.fixture
def root(tmp_path, monkeypatch):
    """``$DEVBASE_ROOT`` を tmp へ向け、機密の注入は実 backend に触れないよう差し替える。

    解決は対象プロジェクトへ chdir してから行う。テストの CWD は ``elsewhere`` に置き、
    別ディレクトリから一覧を開いた状況にする。
    """
    (tmp_path / "projects" / "carmo").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("DEVBASE_ROOT", str(tmp_path))
    monkeypatch.setenv("PWD", str(elsewhere))
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(container, "_inject_secrets", lambda *, required: None)
    return tmp_path


def generated(root):
    path = root / "projects" / "carmo" / ".docker-compose.scale.yml"
    path.write_text("services: {}\n")
    return path


def resolve_to(monkeypatch, result):
    asked = []

    def fake(compose_file, environ=None):
        asked.append(Path(compose_file).resolve())
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(container, "profile_services", fake)
    return asked


def test_profile_items_follow_running_ops_when_profiles_exist(root, monkeypatch):
    path = generated(root)
    asked = resolve_to(monkeypatch, {"test": ["app"]})

    ops = actions_project._running_ops(root, "carmo")

    assert ops == list(actions_project._RUNNING_OPS) + PROFILE_ITEMS
    assert asked == [path.resolve()]


@pytest.mark.parametrize("result", [{}, DevbaseError("config failed")])
def test_no_profile_items_without_profiles(root, monkeypatch, result):
    generated(root)
    resolve_to(monkeypatch, result)

    assert actions_project._running_ops(root, "carmo") == list(actions_project._RUNNING_OPS)


def test_no_profile_items_before_first_up(root, monkeypatch):
    asked = resolve_to(monkeypatch, {"test": ["app"]})

    assert actions_project._running_ops(root, "carmo") == list(actions_project._RUNNING_OPS)
    assert asked == []


@pytest.mark.parametrize("op, sub", [("profile-up", "up"), ("profile-down", "down")])
def test_profile_item_asks_profile_name_even_for_one_and_delegates(root, monkeypatch, op, sub):
    generated(root)
    resolve_to(monkeypatch, {"test": ["app", "mysql"]})
    prompts = []

    def fake_select(message, choices, **kwargs):
        prompts.append(list(choices))
        return "test"

    monkeypatch.setattr(menu, "select", fake_select)
    delegated = []
    monkeypatch.setattr(container, "cmd_project",
                        lambda args: delegated.append(vars(args).copy()) or 0)

    assert actions_project._run_operation(root, "carmo", op) == 0

    assert prompts == [[("test", "test")]]
    assert delegated == [{"subcommand": "profile", "name": "carmo",
                          "profile_subcommand": sub, "profile": "test"}]


def test_profile_selection_back_returns_to_submenu(root, monkeypatch):
    generated(root)
    resolve_to(monkeypatch, {"test": ["app"]})
    monkeypatch.setattr(menu, "select", lambda *a, **k: menu.MENU_BACK)
    delegated = []
    monkeypatch.setattr(container, "cmd_project", lambda args: delegated.append(args) or 0)

    assert actions_project._run_operation(root, "carmo", "profile-up") is flow.ARG_CANCEL
    assert delegated == []


@pytest.mark.parametrize("op", ["profile-up", "profile-down"])
def test_profile_ops_return_to_project_list(root, monkeypatch, op):
    generated(root)
    resolve_to(monkeypatch, {"test": ["app"]})
    shown = []

    def fake_action(name, ops=None):
        shown.append(ops)
        return op if len(shown) == 1 else menu.MENU_BACK

    monkeypatch.setattr(actions_project, "_select_action", fake_action)
    monkeypatch.setattr(menu, "select", lambda *a, **k: "test")
    monkeypatch.setattr(container, "cmd_project", lambda args: 0)

    result = actions_project.handle_row(root, {"name": "carmo", "status": "running (2 containers)"})

    assert result is menu.MENU_BACK        # 1 回の実行で一覧へ戻る (2 回目の選択が無い)
    assert len(shown) == 1 and PROFILE_ITEMS[0] in shown[0]


def test_profiles_resolve_inside_target_project_and_restore_session(root, monkeypatch):
    """別ディレクトリから開いても、対象プロジェクトの env と機密を載せた上で解決する。

    生成物が ``${REVIEW_APP_IMAGE:?required}`` のように対象の env にだけある変数を
    参照しても解決できるようにするため。終わった後は CWD と ``os.environ`` を戻し、
    TUI セッションへ残さない。
    """
    project = root / "projects" / "carmo"
    (project / "env").write_text("REVIEW_APP_IMAGE=review:latest\n")
    generated(root)
    injected = []
    monkeypatch.setattr(container, "_inject_secrets",
                        lambda *, required: injected.append(
                            (required, Path.cwd().resolve())))
    seen = {}

    def fake(compose_file, environ=None):
        seen["cwd"] = Path.cwd().resolve()
        seen["image"] = os.environ.get("REVIEW_APP_IMAGE")
        seen["project"] = os.environ.get("COMPOSE_PROJECT_NAME")
        return {"test": ["app"]}

    monkeypatch.setattr(container, "profile_services", fake)
    monkeypatch.delenv("REVIEW_APP_IMAGE", raising=False)
    before_cwd = Path.cwd()
    before_env = os.environ.copy()

    ops = actions_project._running_ops(root, "carmo")

    assert ops == list(actions_project._RUNNING_OPS) + PROFILE_ITEMS
    assert seen == {"cwd": project.resolve(), "image": "review:latest", "project": "carmo"}
    assert injected == [(False, project.resolve())]
    assert Path.cwd() == before_cwd
    assert os.environ.copy() == before_env
