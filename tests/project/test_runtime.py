"""project.yml をコンテナ・エディタへ渡す形へ変換する層 (PLAN32 Task 2)"""

from __future__ import annotations

import base64
import json

import pytest

from devbase.errors import ConfigError
from devbase.project.config import decode_repo_plan, parse_project_config
from devbase.project.runtime import (
    build_workspace_document,
    container_env,
    read_scale,
    workspace_path,
    write_scale,
)


def config_of(*repos, **top):
    return parse_project_config(
        {"version": 1, "defaults": {"owner": "volareinc"},
         "repos": [dict(repo=r) if isinstance(r, str) else r for r in repos],
         **top},
        source="project.yml")


# ---------------------------------------------------------------------------
# コンテナへ渡す環境変数
# ---------------------------------------------------------------------------

def test_container_env_carries_the_clone_plan_and_primary_dir():
    env = container_env(config_of("carmo", "carmo-batch"), project_name="carmo")

    entries = decode_repo_plan(env["DEVBASE_REPOS"])
    assert [(e.url, e.dir) for e in entries] == [
        ("https://github.com/volareinc/carmo.git", "carmo"),
        ("https://github.com/volareinc/carmo-batch.git", "carmo-batch"),
    ]
    assert env["DEVBASE_PRIMARY_DIR"] == "carmo"


def test_multi_repo_projects_get_a_workspace_file():
    env = container_env(config_of("carmo", "carmo-batch"), project_name="carmo")

    assert env["DEVBASE_WORKSPACE"] == "/work/carmo.code-workspace"
    document = json.loads(base64.b64decode(env["DEVBASE_WORKSPACE_B64"]).decode())
    assert document["folders"] == [
        {"name": "carmo", "path": "/work/carmo"},
        {"name": "carmo-batch", "path": "/work/carmo-batch"},
    ]


def test_single_repo_projects_open_a_plain_folder():
    """repo が 1 件なら従来どおりフォルダを開く (workspace ファイルを作らない)"""
    env = container_env(config_of("carmo"), project_name="carmo")

    assert "DEVBASE_WORKSPACE" not in env
    assert "DEVBASE_WORKSPACE_B64" not in env


def test_workspace_path_is_derived_from_the_project_name():
    assert workspace_path("carmo") == "/work/carmo.code-workspace"


def test_workspace_document_lists_the_primary_repo_first():
    config = config_of("carmo-doc", {"repo": "carmo", "primary": True})

    document = build_workspace_document(config)

    assert [f["name"] for f in document["folders"]] == ["carmo", "carmo-doc"]


def test_container_env_values_are_safe_for_compose():
    """base64 と単純な名前だけなので、compose の変数展開に食われない"""
    env = container_env(config_of("carmo", "carmo-batch"), project_name="carmo")

    assert all("$" not in value and "\n" not in value for value in env.values())


# ---------------------------------------------------------------------------
# scale の読み書き (旧 CONTAINER_SCALE)
# ---------------------------------------------------------------------------

def test_read_scale_uses_the_project_config(tmp_path):
    (tmp_path / "project.yml").write_text(
        "version: 1\nscale: 3\nrepos:\n  - owner: volareinc\n    repo: carmo\n")

    assert read_scale(tmp_path) == 3


def test_read_scale_falls_back_to_the_default(tmp_path):
    (tmp_path / "project.yml").write_text(
        "version: 1\nrepos:\n  - owner: volareinc\n    repo: carmo\n")

    assert read_scale(tmp_path) == 2


def test_read_scale_reports_a_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="project.yml"):
        read_scale(tmp_path)


def test_write_scale_updates_the_existing_key_and_keeps_comments(tmp_path):
    (tmp_path / "project.yml").write_text(
        "version: 1\n# 並行開発用のコンテナ数\nscale: 1\nrepos:\n"
        "  - owner: volareinc\n    repo: carmo\n")

    write_scale(tmp_path, 4)

    text = (tmp_path / "project.yml").read_text()
    assert "scale: 4" in text
    assert "# 並行開発用のコンテナ数" in text
    assert read_scale(tmp_path) == 4


def test_write_scale_keeps_an_inline_comment(tmp_path):
    (tmp_path / "project.yml").write_text(
        "version: 1\nscale: 1  # 並列数\nrepos:\n"
        "  - owner: volareinc\n    repo: carmo\n")

    write_scale(tmp_path, 4)

    # 値だけが差し替わり、行内コメントと間隔がそのまま残ること
    assert (tmp_path / "project.yml").read_text().splitlines()[1] == (
        "scale: 4  # 並列数")
    assert read_scale(tmp_path) == 4


def test_write_scale_adds_the_key_when_absent(tmp_path):
    (tmp_path / "project.yml").write_text(
        "version: 1\nrepos:\n  - owner: volareinc\n    repo: carmo\n")

    write_scale(tmp_path, 2)

    assert read_scale(tmp_path) == 2
    # repos の配下ではなく最上位に書かれること
    assert (tmp_path / "project.yml").read_text().splitlines()[1] == "scale: 2"


def test_write_scale_rejects_a_broken_result(tmp_path):
    (tmp_path / "project.yml").write_text(
        "version: 1\nrepos:\n  - owner: volareinc\n    repo: carmo\n")

    with pytest.raises(ConfigError, match="scale"):
        write_scale(tmp_path, 0)
    assert "scale" not in (tmp_path / "project.yml").read_text()
