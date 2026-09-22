"""スナップショット名の受理と拒否を固定する (PLAN66 決定 6)。

``SnapshotManager._validate_name`` は ``utils/names.is_single_segment_name`` を共有する
(決定 5)。述語を将来広げるとスナップショット名も黙って広がるため、ここで受理と拒否を
固定し、広げるときにスナップショット名をどうするかを改めて決められるようにする。
"""

from __future__ import annotations

import pytest

from devbase.errors import SnapshotError
from devbase.snapshot.manager import SnapshotManager


@pytest.mark.parametrize('name', ['ok-name', 'a.b', 'A_b', '0abc'])
def test_validate_name_accepts(tmp_path, name):
    SnapshotManager(tmp_path)._validate_name(name)


@pytest.mark.parametrize(
    'name',
    ['_foo', '.x', '-x', '', '..', 'café', 'a/b', 'abc\n'],
)
def test_validate_name_rejects(tmp_path, name):
    with pytest.raises(SnapshotError, match='無効なスナップショット名'):
        SnapshotManager(tmp_path)._validate_name(name)
