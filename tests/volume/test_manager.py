"""VolumeManager の公開互換ヘルパーの現状固定テスト。"""

from __future__ import annotations

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
