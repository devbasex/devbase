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
    encode_workspace_folders,
    hook_env,
    read_scale,
    workspace_path,
    write_scale,
)


def config_of(*repos, **top):
    return parse_project_config(
        {"version": 1, "defaults": {"owner": "example-org"},
         "repos": [dict(repo=r) if isinstance(r, str) else r for r in repos],
         **top},
        source="project.yml")


# ---------------------------------------------------------------------------
# コンテナへ渡す環境変数
# ---------------------------------------------------------------------------

def test_container_env_carries_the_clone_plan_and_primary_dir():
    env = container_env(config_of("myapp", "myapp-batch"), project_name="myapp")

    entries = decode_repo_plan(env["DEVBASE_REPOS"])
    assert [(e.url, e.dir) for e in entries] == [
        ("https://github.com/example-org/myapp.git", "myapp"),
        ("https://github.com/example-org/myapp-batch.git", "myapp-batch"),
    ]
    assert env["DEVBASE_PRIMARY_DIR"] == "myapp"


def test_multi_repo_projects_get_a_workspace_file():
    env = container_env(config_of("myapp", "myapp-batch"), project_name="myapp")

    assert env["DEVBASE_WORKSPACE"] == "/work/myapp.code-workspace"
    document = json.loads(base64.b64decode(env["DEVBASE_WORKSPACE_B64"]).decode())
    assert document["folders"] == [
        {"name": "myapp", "path": "/work/myapp"},
        {"name": "myapp-batch", "path": "/work/myapp-batch"},
    ]


def test_single_repo_projects_open_a_plain_folder():
    """repo が 1 件なら従来どおりフォルダを開く (workspace ファイルを作らない)"""
    env = container_env(config_of("myapp"), project_name="myapp")

    assert "DEVBASE_WORKSPACE" not in env
    assert "DEVBASE_WORKSPACE_B64" not in env
    assert "DEVBASE_WORKSPACE_FOLDERS" not in env


# ---------------------------------------------------------------------------
# workspace の folder レコード (PLAN37)
# ---------------------------------------------------------------------------

def decode_folder_records(encoded: str):
    """``<dir><US><JSON>`` の行を (dir, folder) の列へ戻す。"""
    text = base64.b64decode(encoded).decode()
    assert text.endswith("\n"), "行区切りは末尾にも付ける契約"
    return [(line.split("\x1f")[0], json.loads(line.split("\x1f")[1]))
            for line in text.splitlines() if line]


def test_workspace_folders_pair_each_dir_with_its_serialized_folder():
    """entrypoint が dir で存在確認できるよう、dir と folder JSON が組で並ぶ。"""
    env = container_env(config_of("myapp", "myapp-batch"), project_name="myapp")

    assert decode_folder_records(env["DEVBASE_WORKSPACE_FOLDERS"]) == [
        ("myapp", {"name": "myapp", "path": "/work/myapp"}),
        ("myapp-batch", {"name": "myapp-batch", "path": "/work/myapp-batch"}),
    ]


def test_workspace_folders_follow_the_document_order():
    """primary 先頭の並びは workspace 本体と揃える (エクスプローラの並び)。"""
    config = config_of("myapp-doc", {"repo": "myapp", "primary": True})

    records = decode_folder_records(encode_workspace_folders(config))

    assert [dir_ for dir_, _ in records] == ["myapp", "myapp-doc"]
    assert [folder for _, folder in records] == build_workspace_document(config)["folders"]


def test_workspace_folder_records_stay_on_one_line_with_special_characters():
    """dir に引用符が入っても、直列化はホスト側で済ませてあるので行が割れない。"""
    config = config_of({"repo": "myapp", "dir": 'we"ird'}, "myapp-batch")

    text = base64.b64decode(encode_workspace_folders(config)).decode()

    assert len(text.splitlines()) == 2
    assert decode_folder_records(encode_workspace_folders(config))[0] == (
        'we"ird', {"name": 'we"ird', "path": '/work/we"ird'})


def test_workspace_path_is_derived_from_the_project_name():
    assert workspace_path("myapp") == "/work/myapp.code-workspace"


def test_workspace_document_lists_the_primary_repo_first():
    config = config_of("myapp-doc", {"repo": "myapp", "primary": True})

    document = build_workspace_document(config)

    assert [f["name"] for f in document["folders"]] == ["myapp", "myapp-doc"]


def test_container_env_values_are_safe_for_compose():
    """base64 と単純な名前だけなので、compose の変数展開に食われない"""
    env = container_env(config_of("myapp", "myapp-batch"), project_name="myapp")

    assert all("$" not in value and "\n" not in value for value in env.values())


# ---------------------------------------------------------------------------
# scale の読み書き (旧 CONTAINER_SCALE)
# ---------------------------------------------------------------------------

def test_read_scale_uses_the_project_config(tmp_path):
    (tmp_path / "project.yml").write_text(
        "version: 1\nscale: 3\nrepos:\n  - owner: example-org\n    repo: myapp\n")

    assert read_scale(tmp_path) == 3


def test_read_scale_falls_back_to_the_default(tmp_path):
    (tmp_path / "project.yml").write_text(
        "version: 1\nrepos:\n  - owner: example-org\n    repo: myapp\n")

    assert read_scale(tmp_path) == 2


def test_read_scale_reports_a_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="project.yml"):
        read_scale(tmp_path)


def test_write_scale_updates_the_existing_key_and_keeps_comments(tmp_path):
    (tmp_path / "project.yml").write_text(
        "version: 1\n# 並行開発用のコンテナ数\nscale: 1\nrepos:\n"
        "  - owner: example-org\n    repo: myapp\n")

    write_scale(tmp_path, 4)

    text = (tmp_path / "project.yml").read_text()
    assert "scale: 4" in text
    assert "# 並行開発用のコンテナ数" in text
    assert read_scale(tmp_path) == 4


def test_write_scale_keeps_an_inline_comment(tmp_path):
    (tmp_path / "project.yml").write_text(
        "version: 1\nscale: 1  # 並列数\nrepos:\n"
        "  - owner: example-org\n    repo: myapp\n")

    write_scale(tmp_path, 4)

    # 値だけが差し替わり、行内コメントと間隔がそのまま残ること
    assert (tmp_path / "project.yml").read_text().splitlines()[1] == (
        "scale: 4  # 並列数")
    assert read_scale(tmp_path) == 4


def test_write_scale_adds_the_key_when_absent(tmp_path):
    (tmp_path / "project.yml").write_text(
        "version: 1\nrepos:\n  - owner: example-org\n    repo: myapp\n")

    write_scale(tmp_path, 2)

    assert read_scale(tmp_path) == 2
    # repos の配下ではなく最上位に書かれること
    assert (tmp_path / "project.yml").read_text().splitlines()[1] == "scale: 2"


def test_write_scale_rejects_a_broken_result(tmp_path):
    (tmp_path / "project.yml").write_text(
        "version: 1\nrepos:\n  - owner: example-org\n    repo: myapp\n")

    with pytest.raises(ConfigError, match="scale"):
        write_scale(tmp_path, 0)
    assert "scale" not in (tmp_path / "project.yml").read_text()


# ---------------------------------------------------------------------------
# ライフサイクルフックへ渡す環境変数
# ---------------------------------------------------------------------------

def test_hook_env_exposes_the_primary_repo_and_work_dir():
    """`pre-up` / `deploy` は clone 先を知る必要がある (旧 WORK_DIR / GIT_REPO の代替)"""
    env = hook_env(config_of("myapp", "myapp-batch"))

    assert env["DEVBASE_PRIMARY_DIR"] == "myapp"
    assert env["DEVBASE_PRIMARY_URL"] == "https://github.com/example-org/myapp.git"
    assert env["DEVBASE_WORK_DIR"] == "/work/myapp"
    assert env["DEVBASE_REPO_DIRS"] == "myapp myapp-batch"


def test_hook_env_follows_explicit_work_dir_and_primary():
    config = config_of({"repo": "myapp-doc"}, {"repo": "myapp", "primary": True},
                       work_dir="/work/myapp/app")

    env = hook_env(config)

    assert env["DEVBASE_PRIMARY_DIR"] == "myapp"
    assert env["DEVBASE_WORK_DIR"] == "/work/myapp/app"
