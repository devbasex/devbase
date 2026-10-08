"""VS Code Server ボリュームのマウントと宣言 (PLAN36 Task 2)

生成 compose に対して次を固定する。

1. dev インスタンスごとに ``/home/ubuntu/.vscode-server`` が
   ``devbase_vscode_<project>_<index>`` としてマウントされる (AC1 / AC2)
2. インスタンスごとに別のボリュームになる (AC3)
3. そのボリュームが ``volumes:`` セクションへ ``external: true`` で宣言される
4. プロジェクトが同じマウント先を書いている場合は上書きしない
"""

from __future__ import annotations

import pytest

from devbase.volume import compose
from tests.volume.conftest import load_scaled, mount_source, write_compose

VSCODE_TARGET = "/home/ubuntu/.vscode-server"


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEV_SERVICE_NAME", raising=False)
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "acme")  # up が宣言から置いた後 (#315)
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "myapp-ai")
    return tmp_path


# ---------------------------------------------------------------------------
# マウント (AC1 / AC2 / AC3)
# ---------------------------------------------------------------------------

def test_vscode_mount_is_added_when_absent(in_tmp_cwd):
    """プロジェクト compose が書いていなくても自動で足される。"""
    write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1)

    dev = load_scaled(in_tmp_cwd)["services"]["dev-1"]
    assert mount_source(dev, VSCODE_TARGET) == "devbase_vscode_myapp-ai_1"


def test_each_instance_mounts_its_own_volume(in_tmp_cwd):
    """scale > 1 でインスタンスごとに別のボリュームを掴む (AC3)。"""
    write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=2)

    services = load_scaled(in_tmp_cwd)["services"]
    assert mount_source(services["dev-1"], VSCODE_TARGET) == \
        "devbase_vscode_myapp-ai_1"
    assert mount_source(services["dev-2"], VSCODE_TARGET) == \
        "devbase_vscode_myapp-ai_2"


def test_mount_follows_project_name(in_tmp_cwd, monkeypatch):
    """別プロジェクトでは別のボリュームになる (AC4)。"""
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "bi-tools")
    write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1)

    dev = load_scaled(in_tmp_cwd)["services"]["dev-1"]
    assert mount_source(dev, VSCODE_TARGET) == "devbase_vscode_bi-tools_1"


def test_existing_mounts_are_kept(in_tmp_cwd):
    """既存のマウント (/work 等) を壊さない。"""
    write_compose(in_tmp_cwd, {
        "dev": {"image": "dev:latest", "volumes": ["./src:/src"]},
    })

    compose.generate_scaled_compose(scale=1)

    dev = load_scaled(in_tmp_cwd)["services"]["dev-1"]
    assert "./src:/src" in dev["volumes"]
    assert mount_source(dev, "/work") == "devbase_work_1"


def test_non_dev_services_do_not_get_the_mount(in_tmp_cwd):
    """VS Code が入るのは dev だけ。db 等へは足さない。"""
    write_compose(in_tmp_cwd, {
        "dev": {"image": "dev:latest"},
        "db": {"image": "mysql:8"},
    })

    compose.generate_scaled_compose(scale=1)

    db = load_scaled(in_tmp_cwd)["services"]["db"]
    assert mount_source(db, VSCODE_TARGET) is None


# ---------------------------------------------------------------------------
# 宣言 (external)
# ---------------------------------------------------------------------------

def test_vscode_volumes_are_declared_external(in_tmp_cwd):
    """devbase が作るボリュームなので external: true で宣言する。"""
    write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=2)

    volumes = load_scaled(in_tmp_cwd)["volumes"]
    assert volumes["devbase_vscode_myapp-ai_1"] == {"external": True}
    assert volumes["devbase_vscode_myapp-ai_2"] == {"external": True}


def test_project_volumes_are_kept(in_tmp_cwd):
    """プロジェクトが宣言したボリュームは残る。"""
    write_compose(
        in_tmp_cwd, {"dev": {"image": "dev:latest"}}, volumes={"mysql": None})

    compose.generate_scaled_compose(scale=1)

    assert "mysql" in load_scaled(in_tmp_cwd)["volumes"]


# ---------------------------------------------------------------------------
# プロジェクトの指定を尊重する
# ---------------------------------------------------------------------------

def test_declared_vscode_mount_is_not_overridden(in_tmp_cwd):
    """プロジェクトが同じマウント先を書いていたらそのまま残す。

    ホストのディレクトリを bind したい構成を devbase が奪わないため。
    """
    write_compose(in_tmp_cwd, {
        "dev": {"image": "dev:latest",
                "volumes": [f"./vscode-server:{VSCODE_TARGET}"]},
    })

    compose.generate_scaled_compose(scale=1)

    scaled = load_scaled(in_tmp_cwd)
    dev = scaled["services"]["dev-1"]
    assert mount_source(dev, VSCODE_TARGET) == "./vscode-server"
    # 使わないボリュームを宣言しない (external は実体を要求する)
    assert "devbase_vscode_myapp-ai_1" not in scaled["volumes"]
