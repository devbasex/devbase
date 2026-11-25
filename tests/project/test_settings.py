"""project.yml / project.local.yml の書き換え (devbase.project.settings)"""

from __future__ import annotations

import pytest

from devbase.errors import ConfigError
from devbase.project import settings
from devbase.project.config import load_project_config
from devbase.project.local_config import load_project_local_config

YML = """\
version: 1  # 版
scale: 2

defaults:
  owner: org

repos:
  - repo: app
  - repo: sub
    owner: other
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "project.yml").write_text(YML, encoding="utf-8")
    return tmp_path


def _text(project):
    return (project / "project.yml").read_text(encoding="utf-8")


def test_top_level_value(project):
    assert settings.set_project_value(project, "open_editor", False) is True
    assert "scale: 2\nopen_editor: false\n" in _text(project)
    assert settings.set_project_value(project, "open_editor", False) is False
    assert settings.set_project_value(project, "open_editor", None) is True
    assert _text(project) == YML


def test_default_value(project):
    settings.set_default_value(project, "branch", "develop")
    assert [r.branch for r in load_project_config(project).repos] == ["develop"] * 2


def test_primary_is_moved(project):
    settings.set_primary(project, 1)
    assert load_project_config(project).primary.repo == "sub"
    settings.set_primary(project, 0)
    assert load_project_config(project).primary.repo == "app"
    assert _text(project).count("primary") == 1


def test_add_and_remove_repo(project):
    settings.add_repo(project, "extra", None)
    assert load_project_config(project).repos[-1].owner == "org"
    settings.remove_repo(project, 0)
    settings.remove_repo(project, 0)
    with pytest.raises(ConfigError, match="1 件以上"):
        settings.remove_repo(project, 0)
    assert [r.repo for r in load_project_config(project).repos] == ["extra"]


@pytest.mark.parametrize("edit", [
    lambda p: settings.set_repo_value(p, 0, "dir", "sub"),         # 重複
    lambda p: settings.set_repo_value(p, 0, "dir", "../x"),
    lambda p: settings.set_repo_value(p, 0, "repo", None),         # 必須
    lambda p: settings.set_default_value(p, "owner", None),        # 継承元が消える
    lambda p: settings.add_repo(p, "app", None),                   # dir の重複
    lambda p: settings.set_repo_value(p, 0, "branch", "a b"),
])
def test_invalid_result_is_not_written(project, edit):
    with pytest.raises(ConfigError):
        edit(project)
    assert _text(project) == YML


def test_missing_project_yml(tmp_path):
    with pytest.raises(ConfigError):
        settings.set_project_value(tmp_path, "work_dir", "/work/x")
    assert list(tmp_path.iterdir()) == []


def test_docker_value_creates_and_empties_local_file(project):
    settings.set_docker_value(project, "context", "gpu")
    settings.set_docker_value(project, "gid", 999)
    docker = load_project_local_config(project).docker
    assert (docker.context, docker.gid, docker.home) == ("gpu", 999, None)
    settings.set_docker_value(project, "gid", None)
    settings.set_docker_value(project, "context", None)
    assert (project / "project.local.yml").read_text() == ""
    assert load_project_local_config(project).docker.context is None


def test_unset_docker_value_does_not_create_the_file(project):
    assert settings.set_docker_value(project, "context", None) is False
    assert not (project / "project.local.yml").exists()


def test_invalid_docker_value_is_not_written(project):
    with pytest.raises(ConfigError):
        settings.set_docker_value(project, "home", "relative")
    assert not (project / "project.local.yml").exists()
