"""停止中のプロジェクトの「プロジェクト設定変更」メニュー (tui.actions_settings)"""

from __future__ import annotations

import pytest

from devbase.project.config import load_project_config
from devbase.project.local_config import load_project_local_config
from devbase.tui import actions_settings, flow, menu

YML = """\
version: 1
scale: 2
defaults:
  owner: org
repos:
  - repo: app
  - repo: sub
"""


@pytest.fixture
def root(tmp_path):
    project = tmp_path / "projects" / "myapp"
    project.mkdir(parents=True)
    (project / "project.yml").write_text(YML, encoding="utf-8")
    (project / "env").write_text("# 設定\nexport DEVBASE_ACCOUNT_GROUP=acme\nX=1\n")
    return tmp_path


@pytest.fixture
def drive(root, monkeypatch):
    """メニュー・入力の答えを順に返す。尽きた後のメニューは Esc。渡した引数を記録して返す。"""
    def _drive(*answers):
        left, seen = list(answers), []

        def answer(kind):
            def _answer(message, *args, **kwargs):
                seen.append((kind, message, args, kwargs))
                return left.pop(0) if left else menu.MENU_BACK
            return _answer

        for kind in ("select", "text", "confirm"):
            monkeypatch.setattr(actions_settings.menu, kind, answer(kind))
        result = actions_settings.run(root, "myapp")
        assert left == []
        return result, seen

    _drive.dir = root / "projects" / "myapp"
    return _drive


def test_menu_lists_every_setting_but_scale(drive):
    result, seen = drive()
    assert result is flow.ARG_CANCEL
    titles = [title for title, _value in seen[0][2][0]]
    values = [value for _title, value in seen[0][2][0]]
    assert values == ["repos", "defaults", "open_editor", "work_dir", "group",
                      "context", "home", "gid"]
    assert not any("scale" in title for title in titles)
    assert "acme" in titles[4] and "app (primary), sub" in titles[0]
    assert set(values) == set(actions_settings._OPS)


def test_ctrl_c_aborts_everywhere(drive):
    assert drive(None)[0] is None
    assert drive("open_editor", None)[0] is None
    assert drive("repos", 0, "branch", None)[0] is None


def test_open_editor(drive):
    drive("open_editor", "false")
    assert load_project_config(drive.dir).open_editor is False
    _result, seen = drive("open_editor", "unset")
    assert load_project_config(drive.dir).open_editor is None
    # 選択の初期位置は今の値。設定した項目へカーソルが戻る
    assert seen[1][3]["default"] == "false"
    assert seen[2][3]["default"] == "open_editor"


def test_work_dir_empty_unsets(drive):
    drive("work_dir", "/work/x")
    assert load_project_config(drive.dir).work_dir == "/work/x"
    drive("work_dir", "")
    assert load_project_config(drive.dir).work_dir is None


def test_docker_settings(drive):
    drive("context", "gpu", "home", "/home/u", "gid", "999")
    docker = load_project_local_config(drive.dir).docker
    assert (docker.context, docker.home, docker.gid) == ("gpu", "/home/u", 999)


@pytest.mark.parametrize("answers", [("gid", "x"), ("gid", "-1"), ("home", "rel")])
def test_invalid_value_returns_to_the_menu(drive, answers):
    result, seen = drive(*answers)
    assert result is flow.ARG_CANCEL
    assert [kind for kind, *_ in seen] == ["select", "text", "select"]
    assert not (drive.dir / "project.local.yml").exists()


def test_group_change_is_confirmed(drive, root):
    _result, seen = drive("group", actions_settings.TYPE_GROUP, "globex", True)
    assert "acme から globex" in next(m for k, m, *_ in seen if k == "confirm")
    assert (drive.dir / "env").read_text() == (
        "# 設定\nexport DEVBASE_ACCOUNT_GROUP=globex\nX=1\n")


@pytest.mark.parametrize("answers", [
    ("group", actions_settings.TYPE_GROUP, "globex", False),
    ("group", actions_settings.TYPE_GROUP, "globex", menu.MENU_BACK),
    ("group", "acme"),
    ("group", actions_settings.TYPE_GROUP, "default", True),
    ("group", menu.MENU_BACK),
])
def test_group_is_kept(drive, answers):
    before = (drive.dir / "env").read_text()
    assert drive(*answers)[0] is flow.ARG_CANCEL
    assert (drive.dir / "env").read_text() == before


def test_group_is_declared_when_missing(drive):
    (drive.dir / "env").write_text("X=1")
    drive("group", actions_settings.TYPE_GROUP, "acme", True)
    assert (drive.dir / "env").read_text() == "X=1\nDEVBASE_ACCOUNT_GROUP=acme\n"


def test_defaults(drive):
    drive("defaults", "branch", "develop", "init", "false")
    repos = load_project_config(drive.dir).repos
    assert {(r.branch, r.init) for r in repos} == {("develop", False)}


def test_repo_fields(drive):
    drive("repos", 1, "owner", "other", "primary", "dir", "lib")
    repo = load_project_config(drive.dir).repos[1]
    assert (repo.owner, repo.primary, repo.dir) == ("other", True, "lib")


def test_add_repo_inherits_the_default_owner(drive):
    drive("repos", "add-repo", "extra", "")
    assert load_project_config(drive.dir).repos[2].owner == "org"


def test_remove_repo_returns_to_the_repo_list(drive):
    _result, seen = drive("repos", 1, "remove", True)
    assert [r.repo for r in load_project_config(drive.dir).repos] == ["app"]
    assert "のリポジトリを選択" in seen[4][1]
    # 最後の 1 件には「外す」を出さない
    _result, seen = drive("repos", 0)
    assert "remove" not in [value for _t, value in seen[2][2][0]]


def test_remove_repo_is_confirmed(drive):
    drive("repos", 1, "remove", False)
    assert len(load_project_config(drive.dir).repos) == 2


def test_broken_project_yml_returns_to_the_previous_menu(drive, caplog):
    (drive.dir / "project.yml").write_text("version: 9\n")
    result, seen = drive()
    assert result is flow.ARG_CANCEL and seen == []
