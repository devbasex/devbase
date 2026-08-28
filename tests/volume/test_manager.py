"""VolumeManager の公開互換ヘルパーの現状固定テスト。"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from devbase.volume import manager as manager_module
from devbase.volume.manager import (
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
# _volume_exists: docker CLI の結果と失敗経路の現状固定
# ---------------------------------------------------------------------------


def _fake_run(monkeypatch, side_effect=None, returncode=0):
    """subprocess.run を差し替える (side_effect があれば raise する)。"""
    def fake(cmd, **kwargs):
        if side_effect is not None:
            raise side_effect
        return SimpleNamespace(returncode=returncode)
    monkeypatch.setattr(manager_module.subprocess, "run", fake)


def test_volume_exists_true_when_inspect_succeeds(monkeypatch):
    _fake_run(monkeypatch, returncode=0)
    assert VolumeManager()._volume_exists("devbase_work_1") is True


def test_volume_exists_false_when_inspect_fails(monkeypatch):
    # returncode != 0 は「存在しない」の通常経路
    _fake_run(monkeypatch, returncode=1)
    assert VolumeManager()._volume_exists("devbase_work_1") is False


def test_volume_exists_false_when_docker_is_missing(monkeypatch):
    # docker バイナリ不在 (FileNotFoundError) は警告つきで False
    _fake_run(monkeypatch, side_effect=FileNotFoundError("docker"))
    assert VolumeManager()._volume_exists("devbase_work_1") is False


def test_volume_exists_false_on_subprocess_error(monkeypatch):
    _fake_run(monkeypatch, side_effect=subprocess.SubprocessError("boom"))
    assert VolumeManager()._volume_exists("devbase_work_1") is False


def test_volume_exists_propagates_unexpected_errors(monkeypatch):
    # docker CLI 呼び出しに関係しない想定外の例外は False へ丸めず伝播させる
    _fake_run(monkeypatch, side_effect=ValueError("unexpected"))
    with pytest.raises(ValueError):
        VolumeManager()._volume_exists("devbase_work_1")
