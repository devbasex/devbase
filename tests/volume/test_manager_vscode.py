"""共通の VS Code Server ボリュームの名前・作成・旧形式の一覧 (#463)

VS Code Server の本体 (``~/.vscode-server/bin/<commit>``) だけを、全コンテナで 1 本の
ボリューム ``devbase_vscode-server-bin`` に置く。プロジェクト・インスタンス・
アカウントグループで分けない。#463 より前の ``devbase_vscode_<project>_<index>``
(旧形式) は、名前を読んで案内するだけで、作らない・消さない・付けない。
"""

from __future__ import annotations

import re
import subprocess

import pytest

from devbase.volume import manager


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """外部環境の COMPOSE_PROJECT_NAME に左右されないよう既定で未設定にする。"""
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "acme")  # up が宣言から置いた後 (#315)


# ---------------------------------------------------------------------------
# 名前 (受け入れ条件 3・I4)
# ---------------------------------------------------------------------------

def test_shared_name_does_not_look_like_legacy():
    """共通の名前は旧形式の接頭辞でも形でもない (旧形式の案内が選ばない)。"""
    name = manager.VSCODE_SERVER_VOLUME

    assert not name.startswith(manager.LEGACY_VSCODE_VOLUME_PREFIX)
    assert not re.match(r"^devbase_vscode_.+_[0-9]+$", name)


# ---------------------------------------------------------------------------
# 作成 (受け入れ条件 1)
# ---------------------------------------------------------------------------

def _record_docker(monkeypatch, existing: set[str] = frozenset()) -> list[str]:
    created: list[str] = []
    monkeypatch.setattr(
        manager.VolumeManager, "_volume_exists",
        lambda self, name: name in existing or name in created)
    monkeypatch.setattr(
        manager.VolumeManager, "create_volume",
        lambda self, name: created.append(name) or True)
    return created


def test_shared_volume_is_created_once_for_any_group_and_scale(monkeypatch):
    """グループ・scale が違っても、作るのは同じ名前の 1 本だけ。"""
    created = _record_docker(monkeypatch)

    manager.ensure_volumes(2)
    manager.ensure_volumes(1, group="team-b")

    vscode = [n for n in created if "vscode" in n]
    assert vscode == [manager.VSCODE_SERVER_VOLUME, manager.VSCODE_CACHE_VOLUME]


def test_existing_shared_volume_is_not_recreated(monkeypatch):
    """あれば作らない (本体を取り直さない)。"""
    created = _record_docker(
        monkeypatch, existing={manager.VSCODE_SERVER_VOLUME})

    manager.ensure_volumes(1)

    assert manager.VSCODE_SERVER_VOLUME not in created


def test_no_per_instance_vscode_volume_is_created(monkeypatch):
    """インスタンスごとの VS Code Server のボリュームと旧形式を作らない。"""
    created = _record_docker(monkeypatch)

    manager.ensure_volumes(3)

    assert not [n for n in created
                if n.startswith(manager.LEGACY_VSCODE_VOLUME_PREFIX)]


def test_failure_to_create_shared_volume_raises(monkeypatch):
    """作れなければ今のボリュームと同じく DockerError で止まる。"""
    monkeypatch.setattr(
        manager.VolumeManager, "_volume_exists", lambda self, name: False)
    monkeypatch.setattr(
        manager.VolumeManager, "create_volume",
        lambda self, name: name != manager.VSCODE_SERVER_VOLUME)

    with pytest.raises(manager.DockerError):
        manager.ensure_volumes(1)


# ---------------------------------------------------------------------------
# 旧形式の一覧 (受け入れ条件 3・11・12、I5)
# ---------------------------------------------------------------------------

def _fake_volume_ls(monkeypatch, stdout: str = "", returncode: int = 0,
                    raises: Exception | None = None) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(cmd, returncode, stdout, "error")

    monkeypatch.setattr(manager.subprocess, "run", fake_run)
    return calls


def test_find_legacy_returns_only_legacy_names_sorted(monkeypatch):
    """共通の名前やほかのボリュームが混ざっても、旧形式だけを名前の順で返す。"""
    calls = _fake_volume_ls(monkeypatch, "\n".join([
        "devbase_vscode_proj-b_1",
        manager.VSCODE_SERVER_VOLUME,
        "devbase_home_ubuntu",
        "devbase_vscode_proj-a_2",
        "devbase_vscode_proj-a_1",
        "devbase_vscode_notanindex",
    ]) + "\n")

    assert manager.find_legacy_vscode_volumes() == [
        "devbase_vscode_proj-a_1",
        "devbase_vscode_proj-a_2",
        "devbase_vscode_proj-b_1",
    ]
    # 名前の一覧を読むだけ (消す・作る・付ける呼び出しが無い)
    assert calls == [["docker", "volume", "ls", "--format", "{{.Name}}"]]


def test_find_legacy_returns_empty_when_listing_fails(monkeypatch):
    """一覧が取れなければ空を返し、例外を出さない。"""
    _fake_volume_ls(monkeypatch, returncode=1)
    assert manager.find_legacy_vscode_volumes() == []

    _fake_volume_ls(monkeypatch, raises=FileNotFoundError("docker"))
    assert manager.find_legacy_vscode_volumes() == []


# ---------------------------------------------------------------------------
# スナップショットの対象 (受け入れ条件 13・I11)
# ---------------------------------------------------------------------------

def test_snapshot_targets_exclude_shared_vscode_volume(tmp_path):
    """スナップショットの対象に共通の VS Code Server ボリュームが入らない。"""
    from devbase.snapshot.manager import SnapshotManager

    volumes = SnapshotManager(tmp_path, group="acme").volumes

    assert manager.VSCODE_SERVER_VOLUME not in volumes.values()


# ---------------------------------------------------------------------------
# Dev Containers の共有の置き場 (#507 の受け入れ条件 2)
# ---------------------------------------------------------------------------

def test_cache_volume_name_is_the_one_dev_containers_looks_for():
    """Dev Containers は名前が "vscode" のボリュームだけを共有の置き場と見なす。"""
    assert manager.VSCODE_CACHE_VOLUME == "vscode"
    assert manager.VSCODE_CACHE_TARGET == "/vscode"
    assert manager.VSCODE_CACHE_EXTENSIONS_DIR == "/vscode/vscode-server/extensionsCache"


def test_cache_volume_is_created_once_and_not_recreated(monkeypatch):
    """無ければ 1 本だけ作り、あれば作らない。"""
    created = _record_docker(monkeypatch)
    manager.ensure_volumes(2)
    manager.ensure_volumes(1, group="team-b")
    assert created.count(manager.VSCODE_CACHE_VOLUME) == 1

    created = _record_docker(monkeypatch, existing={manager.VSCODE_CACHE_VOLUME})
    manager.ensure_volumes(1)
    assert manager.VSCODE_CACHE_VOLUME not in created


def test_snapshot_targets_exclude_cache_volume(tmp_path):
    """スナップショットの対象に Dev Containers の共有の置き場が入らない。"""
    from devbase.snapshot.manager import SnapshotManager

    volumes = SnapshotManager(tmp_path, group="acme").volumes

    assert manager.VSCODE_CACHE_VOLUME not in volumes.values()
