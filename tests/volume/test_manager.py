"""VolumeManager の公開互換ヘルパーの現状固定テスト。"""

from __future__ import annotations

import pytest

from devbase.errors import DockerError
from devbase.volume.manager import (
    HOME_UBUNTU_VOLUME,
    VolumeManager, get_volume_for_index, get_work_volume_for_index,
)


def test_work_volume_helpers_use_the_same_name_rule():
    assert get_work_volume_for_index(3) == "devbase_work_3"
    assert VolumeManager().get_work_volume_for_index(3) == "devbase_work_3"


def test_shared_volume_helpers_use_the_same_name_rule():
    # module-level helper と VolumeManager のメソッドは同じ命名規則
    # (SHARED_VOLUME_PREFIX + index) を返す現状の振る舞いを固定する。
    assert get_volume_for_index(3) == "devbase_home_3"
    assert VolumeManager().get_volume_for_index(3) == "devbase_home_3"


# ---------------------------------------------------------------------------
# VolumeManager.ensure_volumes の現状固定テスト
# ---------------------------------------------------------------------------

def test_ensure_volumes_all_exist_creates_nothing(monkeypatch, caplog):
    mgr = VolumeManager()
    monkeypatch.setattr(mgr, '_volume_exists', lambda name: True)
    created = []
    monkeypatch.setattr(
        mgr, '_create_volume', lambda name: created.append(name) or True)

    with caplog.at_level('INFO'):
        mgr.ensure_volumes(2)

    assert created == []
    messages = [r.getMessage() for r in caplog.records]
    assert f"  {HOME_UBUNTU_VOLUME} (shared home, exists)" in messages
    assert "  devbase_work_1 (exists)" in messages
    assert "  devbase_work_2 (exists)" in messages


def test_ensure_volumes_creates_missing_volumes(monkeypatch, caplog):
    mgr = VolumeManager()
    monkeypatch.setattr(mgr, '_volume_exists', lambda name: False)
    created = []
    monkeypatch.setattr(
        mgr, '_create_volume', lambda name: created.append(name) or True)

    with caplog.at_level('INFO'):
        mgr.ensure_volumes(2)

    assert created == [HOME_UBUNTU_VOLUME, 'devbase_work_1', 'devbase_work_2']
    messages = [r.getMessage() for r in caplog.records]
    assert f"  Creating {HOME_UBUNTU_VOLUME} (shared home)..." in messages
    assert "  Creating devbase_work_1..." in messages
    assert "  Creating devbase_work_2..." in messages


def test_ensure_volumes_raises_when_shared_home_creation_fails(monkeypatch):
    mgr = VolumeManager()
    monkeypatch.setattr(mgr, '_volume_exists', lambda name: False)
    monkeypatch.setattr(mgr, '_create_volume', lambda name: False)

    with pytest.raises(DockerError, match=f"Failed to create volume {HOME_UBUNTU_VOLUME}"):
        mgr.ensure_volumes(1)


def test_ensure_volumes_raises_when_work_volume_creation_fails(monkeypatch):
    mgr = VolumeManager()
    monkeypatch.setattr(mgr, '_volume_exists', lambda name: False)

    def fake_create(name):
        return name == HOME_UBUNTU_VOLUME

    monkeypatch.setattr(mgr, '_create_volume', fake_create)

    with pytest.raises(DockerError, match="Failed to create volume devbase_work_1"):
        mgr.ensure_volumes(1)


def test_ensure_volumes_zero_scale_only_ensures_shared_home(monkeypatch):
    mgr = VolumeManager()
    monkeypatch.setattr(mgr, '_volume_exists', lambda name: True)
    checked = []
    monkeypatch.setattr(
        mgr, '_volume_exists',
        lambda name: checked.append(name) or True)

    mgr.ensure_volumes(0)

    assert checked == [HOME_UBUNTU_VOLUME]
