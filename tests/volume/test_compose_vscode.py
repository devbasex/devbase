"""共通の VS Code Server ボリュームと Dev Containers の共有の置き場のマウントと宣言 (#463・#507)

生成 compose に対して次を固定する。

1. どの dev インスタンスも、共通の VS Code Server ボリューム 1 本を
   ``/home/ubuntu/.vscode-server/bin`` に付ける (受け入れ条件 2・I1)
2. ``/home/ubuntu/.vscode-server`` と ``/home/ubuntu/.vscode-server/data`` には
   ボリュームを付けない (拡張と状態はコンテナの書き込みレイヤーに残す)
3. そのボリュームを ``volumes:`` に ``external: true`` で 1 回だけ宣言する
4. プロジェクトが ``~/.vscode-server`` か ``~/.vscode-server/bin`` を自分で付けて
   いれば何も足さない (受け入れ条件 5・I2)
5. 旧形式 (``devbase_vscode_<project>_<index>``) が現れない (受け入れ条件 6・I3)
"""

from __future__ import annotations

import pytest

from devbase.volume import compose
from devbase.volume.manager import VSCODE_CACHE_VOLUME, VSCODE_SERVER_VOLUME
from tests.volume.conftest import load_scaled, mount_source, write_compose

SERVER_TARGET = "/home/ubuntu/.vscode-server"
BIN_TARGET = "/home/ubuntu/.vscode-server/bin"
DATA_TARGET = "/home/ubuntu/.vscode-server/data"


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEV_SERVICE_NAME", raising=False)
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "acme")  # up が宣言から置いた後 (#315)
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "proj-a")
    return tmp_path


def _sources(service: dict) -> list[str]:
    result = []
    for vol in service.get("volumes", []):
        if isinstance(vol, str):
            result.append(vol.split(":")[0])
        elif isinstance(vol, dict):
            result.append(str(vol.get("source")))
    return result


# ---------------------------------------------------------------------------
# マウント (受け入れ条件 2・I1)
# ---------------------------------------------------------------------------

def test_every_instance_mounts_the_shared_volume_on_bin(in_tmp_cwd):
    """scale 2 でも、どのインスタンスも同じ共通のボリュームを bin に 1 つ付ける。"""
    write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=2)

    services = load_scaled(in_tmp_cwd)["services"]
    for name in ("dev-1", "dev-2"):
        dev = services[name]
        assert mount_source(dev, BIN_TARGET) == VSCODE_SERVER_VOLUME
        assert mount_source(dev, SERVER_TARGET) is None
        assert mount_source(dev, DATA_TARGET) is None
        assert _sources(dev).count(VSCODE_SERVER_VOLUME) == 1


def test_mount_does_not_depend_on_project_name(in_tmp_cwd, monkeypatch):
    """別のプロジェクトでも同じ 1 本を付ける (受け入れ条件 4 の前提)。"""
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "proj-b")
    write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1)

    dev = load_scaled(in_tmp_cwd)["services"]["dev-1"]
    assert mount_source(dev, BIN_TARGET) == VSCODE_SERVER_VOLUME


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
    assert mount_source(db, BIN_TARGET) is None


# ---------------------------------------------------------------------------
# 宣言 (external) と旧形式 (受け入れ条件 6・I3)
# ---------------------------------------------------------------------------

def test_shared_volume_is_declared_external_once(in_tmp_cwd):
    """devbase が作るボリュームなので external: true で 1 回だけ宣言する。"""
    write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=2)

    volumes = load_scaled(in_tmp_cwd)["volumes"]
    assert volumes[VSCODE_SERVER_VOLUME] == {"external": True}


def test_legacy_volumes_do_not_appear(in_tmp_cwd):
    """生成物のマウントと volumes: に旧形式の名前が無い。"""
    write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=2)

    scaled = load_scaled(in_tmp_cwd)
    assert not [n for n in scaled["volumes"] if n.startswith("devbase_vscode_")]
    for name in ("dev-1", "dev-2"):
        assert not [s for s in _sources(scaled["services"][name])
                    if s.startswith("devbase_vscode_")]


def test_project_volumes_are_kept(in_tmp_cwd):
    """プロジェクトが宣言したボリュームは残る。"""
    write_compose(
        in_tmp_cwd, {"dev": {"image": "dev:latest"}}, volumes={"mysql": None})

    compose.generate_scaled_compose(scale=1)

    assert "mysql" in load_scaled(in_tmp_cwd)["volumes"]


# ---------------------------------------------------------------------------
# プロジェクトの指定を尊重する (受け入れ条件 5・I2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", [SERVER_TARGET, BIN_TARGET])
def test_declared_vscode_mount_is_not_overridden(in_tmp_cwd, target):
    """~/.vscode-server か bin を書いていたら、マウントも宣言も足さない。"""
    write_compose(in_tmp_cwd, {
        "dev": {"image": "dev:latest",
                "volumes": [f"./vscode-server:{target}"]},
    })

    compose.generate_scaled_compose(scale=1)

    scaled = load_scaled(in_tmp_cwd)
    dev = scaled["services"]["dev-1"]
    assert mount_source(dev, target) == "./vscode-server"
    assert VSCODE_SERVER_VOLUME not in _sources(dev)
    # 使わないボリュームを宣言しない (external は実体を要求する)
    assert VSCODE_SERVER_VOLUME not in scaled["volumes"]


def test_data_only_declaration_still_gets_shared_bin(in_tmp_cwd):
    """~/.vscode-server/data だけの指定なら、共通のボリュームを bin に足す。"""
    write_compose(in_tmp_cwd, {
        "dev": {"image": "dev:latest",
                "volumes": [f"./vscode-data:{DATA_TARGET}"]},
    })

    compose.generate_scaled_compose(scale=1)

    dev = load_scaled(in_tmp_cwd)["services"]["dev-1"]
    assert mount_source(dev, DATA_TARGET) == "./vscode-data"
    assert mount_source(dev, BIN_TARGET) == VSCODE_SERVER_VOLUME


# ---------------------------------------------------------------------------
# Dev Containers の共有の置き場 (#507 の受け入れ条件 1)
# ---------------------------------------------------------------------------

CACHE_TARGET = "/vscode"


def test_every_instance_mounts_the_cache_volume(in_tmp_cwd):
    """どのインスタンスにも名前 "vscode" のボリュームを /vscode に 1 つ付ける。"""
    write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=2)

    scaled = load_scaled(in_tmp_cwd)
    for name in ("dev-1", "dev-2"):
        dev = scaled["services"][name]
        assert mount_source(dev, CACHE_TARGET) == VSCODE_CACHE_VOLUME
        assert _sources(dev).count(VSCODE_CACHE_VOLUME) == 1
        # #463 の本体のマウントはそのまま
        assert mount_source(dev, BIN_TARGET) == VSCODE_SERVER_VOLUME
    assert scaled["volumes"][VSCODE_CACHE_VOLUME] == {"external": True}


def test_non_dev_services_do_not_get_the_cache_volume(in_tmp_cwd):
    write_compose(in_tmp_cwd, {
        "dev": {"image": "dev:latest"},
        "db": {"image": "mysql:8"},
    })

    compose.generate_scaled_compose(scale=1)

    db = load_scaled(in_tmp_cwd)["services"]["db"]
    assert mount_source(db, CACHE_TARGET) is None


def test_declared_cache_mount_is_not_overridden(in_tmp_cwd):
    """プロジェクトが /vscode を自分で付けていれば、マウントも宣言も足さない。"""
    write_compose(in_tmp_cwd, {
        "dev": {"image": "dev:latest", "volumes": ["./own:/vscode"]},
    })

    compose.generate_scaled_compose(scale=1)

    scaled = load_scaled(in_tmp_cwd)
    dev = scaled["services"]["dev-1"]
    assert mount_source(dev, CACHE_TARGET) == "./own"
    assert VSCODE_CACHE_VOLUME not in _sources(dev)
    assert VSCODE_CACHE_VOLUME not in scaled["volumes"]
    # 本体のボリュームは /vscode の指定に左右されない
    assert mount_source(dev, BIN_TARGET) == VSCODE_SERVER_VOLUME


def test_declared_vscode_server_mount_still_gets_cache_volume(in_tmp_cwd):
    """~/.vscode-server を自分で付けていても、/vscode は別のパスなので足す。"""
    write_compose(in_tmp_cwd, {
        "dev": {"image": "dev:latest",
                "volumes": [f"./vscode-server:{SERVER_TARGET}"]},
    })

    compose.generate_scaled_compose(scale=1)

    dev = load_scaled(in_tmp_cwd)["services"]["dev-1"]
    assert mount_source(dev, CACHE_TARGET) == VSCODE_CACHE_VOLUME
