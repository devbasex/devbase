"""`devbase project migrate-config` (PLAN32 Task 4)"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from devbase.commands.project import cmd_project_migrate_config

LEGACY_ENV = ("GIT_USER=volareinc\nGIT_REPO=carmo\nWORK_DIR=/work/$GIT_REPO\n"
              "CONTAINER_SCALE=1\nDEVBASE_OPEN_EDITOR=1\n")


@pytest.fixture
def devbase_root(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    for name in ("carmo", "adminer"):
        directory = projects / name
        directory.mkdir()
        (directory / "env").write_text(LEGACY_ENV, encoding="utf-8")
    return tmp_path


def args(**kw):
    return SimpleNamespace(**{"dry_run": False, "names": [], "projects_dir": None, **kw})


def test_migrates_every_project(devbase_root, capsys):
    assert cmd_project_migrate_config(devbase_root, args()) == 0

    for name in ("carmo", "adminer"):
        assert (devbase_root / "projects" / name / "project.yml").is_file()
    out = capsys.readouterr().out
    assert "migrated" in out


def test_dry_run_writes_nothing(devbase_root, capsys):
    assert cmd_project_migrate_config(devbase_root, args(dry_run=True)) == 0

    assert not (devbase_root / "projects" / "carmo" / "project.yml").exists()
    # 生成される内容を確認できること
    assert "version: 1" in capsys.readouterr().out


def test_named_projects_only(devbase_root):
    cmd_project_migrate_config(devbase_root, args(names=["carmo"]))

    assert (devbase_root / "projects" / "carmo" / "project.yml").is_file()
    assert not (devbase_root / "projects" / "adminer" / "project.yml").exists()


def test_unknown_project_is_an_error(devbase_root, capsys):
    assert cmd_project_migrate_config(devbase_root, args(names=["nope"])) == 1
    assert "nope" in capsys.readouterr().err


def test_failed_conversion_is_reported_as_an_error(devbase_root, capsys):
    (devbase_root / "projects" / "carmo" / "env").write_text(
        "GIT_USER=vol areinc\nGIT_REPO=carmo\n", encoding="utf-8")

    assert cmd_project_migrate_config(devbase_root, args()) == 1
    assert "failed" in capsys.readouterr().out.lower()


def test_projects_dir_override(tmp_path, devbase_root):
    """plugin リポジトリ内の未リンク projects も直接変換できる"""
    plugin_projects = tmp_path / "plugin" / "projects"
    (plugin_projects / "appliv").mkdir(parents=True)
    (plugin_projects / "appliv" / "env").write_text(LEGACY_ENV, encoding="utf-8")

    assert cmd_project_migrate_config(
        devbase_root, args(projects_dir=str(plugin_projects))) == 0

    assert (plugin_projects / "appliv" / "project.yml").is_file()
    assert not (devbase_root / "projects" / "carmo" / "project.yml").exists()


def test_missing_projects_dir_is_an_error(devbase_root, tmp_path, capsys):
    assert cmd_project_migrate_config(
        devbase_root, args(projects_dir=str(tmp_path / "nope"))) == 1
    assert "projects" in capsys.readouterr().err
