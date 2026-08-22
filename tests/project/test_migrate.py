"""env → project.yml の移行 (PLAN32 Task 4)"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbase.project.config import load_project_config
from devbase.project.migrate import migrate_project, migrate_projects

LEGACY_ENV = """GIT_USER=volareinc
GIT_REPO=carmo
WORK_DIR=/work/$GIT_REPO
CONTAINER_SCALE=1
# up/list 完了後に dev コンテナへ接続した VS Code を自動で開く (PLAN31_3)
DEVBASE_OPEN_EDITOR=1
"""


def project(tmp_path: Path, name: str = "carmo", env: str = LEGACY_ENV) -> Path:
    directory = tmp_path / name
    directory.mkdir()
    (directory / "env").write_text(env, encoding="utf-8")
    return directory


def test_creates_project_yml_from_env(tmp_path):
    directory = project(tmp_path)

    result = migrate_project(directory)

    assert result.status == "migrated"
    config = load_project_config(directory)
    assert [(r.host, r.owner, r.repo, r.dir) for r in config.repos] == [
        ("github.com", "volareinc", "carmo", "carmo")]
    assert config.scale == 1
    assert config.open_editor is True
    assert config.work_dir is None  # 既定 (/work/<repo>) と同じなら書かない


def test_removes_migrated_keys_from_env(tmp_path):
    directory = project(tmp_path, env=LEGACY_ENV + "ENABLE_SSH=true\n")

    migrate_project(directory)

    env_text = (directory / "env").read_text(encoding="utf-8")
    assert "ENABLE_SSH=true" in env_text
    for key in ("GIT_USER", "GIT_REPO", "WORK_DIR", "CONTAINER_SCALE",
                "DEVBASE_OPEN_EDITOR"):
        assert key not in env_text


def test_drops_the_comment_that_documented_a_removed_key(tmp_path):
    directory = project(tmp_path)

    migrate_project(directory)

    assert "PLAN31_3" not in (directory / "env").read_text(encoding="utf-8")


def test_keeps_the_env_file_even_when_it_becomes_empty(tmp_path):
    """compose が env_file で参照するため、空になってもファイルは残す"""
    directory = project(tmp_path)

    migrate_project(directory)

    env_file = directory / "env"
    assert env_file.is_file()
    assert env_file.read_text(encoding="utf-8").strip().startswith("#")


def test_non_default_host_and_work_dir_are_kept(tmp_path):
    directory = project(tmp_path, env=(
        "GIT_USER=uttaro_dev\nGIT_REPO=uttarov2\nGIT_HOST=gitlab.com\n"
        "WORK_DIR=/work/$GIT_REPO/src\nCONTAINER_SCALE=1\n"))

    migrate_project(directory)

    config = load_project_config(directory)
    assert config.repos[0].host == "gitlab.com"
    assert config.work_dir == "/work/uttarov2/src"


def test_open_editor_off_is_preserved(tmp_path):
    directory = project(tmp_path, env=(
        "GIT_USER=volareinc\nGIT_REPO=carmo\nDEVBASE_OPEN_EDITOR=0\n"))

    migrate_project(directory)

    assert load_project_config(directory).open_editor is False


def test_absent_optional_keys_are_not_written(tmp_path):
    directory = project(tmp_path, env="GIT_USER=volareinc\nGIT_REPO=carmo\n")

    migrate_project(directory)

    config = load_project_config(directory)
    assert config.scale is None
    assert config.open_editor is None


def test_dry_run_changes_nothing(tmp_path):
    directory = project(tmp_path)

    result = migrate_project(directory, dry_run=True)

    assert result.status == "migrated"
    assert result.project_yml.startswith("#") or "version: 1" in result.project_yml
    assert not (directory / "project.yml").exists()
    assert (directory / "env").read_text(encoding="utf-8") == LEGACY_ENV


def test_running_twice_is_a_no_op(tmp_path):
    directory = project(tmp_path)
    migrate_project(directory)
    first = (directory / "project.yml").read_text(encoding="utf-8")

    result = migrate_project(directory)

    assert result.status == "already"
    assert (directory / "project.yml").read_text(encoding="utf-8") == first


def test_existing_project_yml_is_never_overwritten(tmp_path):
    """手で整えた設定 (複数 repo 等) を移行が壊さない"""
    directory = project(tmp_path)
    (directory / "project.yml").write_text(
        "version: 1\nrepos:\n  - owner: volareinc\n    repo: carmo\n"
        "  - owner: volareinc\n    repo: carmo-batch\n", encoding="utf-8")

    result = migrate_project(directory)

    assert result.status == "already"
    assert len(load_project_config(directory).repos) == 2
    # env に残っていた旧キーは掃除される
    assert "GIT_REPO" not in (directory / "env").read_text(encoding="utf-8")


def test_project_without_repo_keys_is_skipped(tmp_path):
    directory = project(tmp_path, env="ENABLE_SSH=true\n")

    result = migrate_project(directory)

    assert result.status == "skipped"
    assert "GIT_USER" in result.reason
    assert not (directory / "project.yml").exists()


def test_missing_env_file_is_skipped(tmp_path):
    directory = tmp_path / "carmo"
    directory.mkdir()

    result = migrate_project(directory)

    assert result.status == "skipped"


def test_generated_config_is_validated(tmp_path):
    """検証に通らない値 (空白混じり等) は書き出さずに失敗として報告する"""
    directory = project(tmp_path, env="GIT_USER=vol areinc\nGIT_REPO=carmo\n")

    result = migrate_project(directory)

    assert result.status == "failed"
    assert not (directory / "project.yml").exists()


def test_migrate_projects_walks_every_project(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    for name in ("a", "b"):
        project(projects, name)
    (projects / "c").mkdir()  # env なし → skipped

    results = migrate_projects(projects)

    assert {r.name: r.status for r in results} == {
        "a": "migrated", "b": "migrated", "c": "skipped"}
    assert load_project_config(projects / "a").repos[0].repo == "carmo"


def test_migrate_projects_follows_symlinks_to_the_plugin_repo(tmp_path):
    """projects/<name> は plugin repo への symlink。実体側を書き換える"""
    plugin_repo = tmp_path / "plugin-repo"
    plugin_repo.mkdir()
    real = project(plugin_repo)
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "carmo").symlink_to(real, target_is_directory=True)

    results = migrate_projects(projects)

    assert [r.status for r in results] == ["migrated"]
    assert (real / "project.yml").is_file()
    assert not (projects / "carmo" / "project.yml").is_symlink()


def test_broken_yaml_from_env_fails_only_that_project(tmp_path):
    """閉じられていない引用符などで生成 YAML が壊れても一括移行は止まらない"""
    projects = tmp_path / "projects"
    projects.mkdir()
    project(projects, "broken", env='GIT_USER=volareinc\nGIT_REPO="carmo\n')
    project(projects, "sound")

    results = migrate_projects(projects)

    assert {r.name: r.status for r in results} == {
        "broken": "failed", "sound": "migrated"}
    assert not (projects / "broken" / "project.yml").exists()
    # 変換できなかった側の env は旧キーを保持する (復旧元を残す)
    assert "GIT_REPO" in (projects / "broken" / "env").read_text(encoding="utf-8")


def test_unreadable_env_is_reported_as_failed(tmp_path):
    """不正な UTF-8 の env が例外のまま伝播して残りの移行を止めない"""
    projects = tmp_path / "projects"
    projects.mkdir()
    bad = projects / "bad"
    bad.mkdir()
    (bad / "env").write_bytes(b"GIT_USER=vol\xffareinc\nGIT_REPO=carmo\n")
    project(projects, "sound")

    results = migrate_projects(projects)

    assert {r.name: r.status for r in results} == {
        "bad": "failed", "sound": "migrated"}


def test_broken_existing_project_yml_keeps_env_untouched(tmp_path):
    """既存 project.yml が壊れているとき env の旧キー (復旧元) は消さない"""
    directory = project(tmp_path)
    (directory / "project.yml").write_text(
        "version: 1\nrepos:\n  - owner: volareinc\n", encoding="utf-8")

    result = migrate_project(directory)

    assert result.status == "failed"
    assert "GIT_REPO=carmo" in (directory / "env").read_text(encoding="utf-8")


def test_writes_are_atomic(tmp_path, monkeypatch):
    """書き込み中に落ちても既存ファイルが truncate されない"""
    directory = project(tmp_path)
    original = (directory / "env").read_text(encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr("devbase.project.migrate.os.replace", boom)

    result = migrate_project(directory)

    assert result.status == "failed"
    assert not (directory / "project.yml").exists()
    assert (directory / "env").read_text(encoding="utf-8") == original
    # 一時ファイルは後始末される
    assert sorted(p.name for p in directory.iterdir()) == ["env"]
