"""スナップショット名の受理と拒否を固定する (PLAN66 決定 6)。

``SnapshotManager._validate_name`` は ``utils/names.is_single_segment_name`` を共有する
(決定 5)。述語を将来広げるとスナップショット名も黙って広がるため、ここで受理と拒否を
固定し、広げるときにスナップショット名をどうするかを改めて決められるようにする。
"""

from __future__ import annotations

from unittest.mock import patch

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


def test_create_rejects_invalid_name_before_side_effects(tmp_path):
    """公開入口で不正名を拒否し、副作用へ進まない現状を固定する。"""
    manager = SnapshotManager(tmp_path)

    with patch('devbase.snapshot.manager.subprocess.run') as run:
        with pytest.raises(SnapshotError, match='無効なスナップショット名'):
            manager.create(name='../evil')

        run.assert_not_called()

    assert list((tmp_path / 'backups').iterdir()) == []
    assert not (tmp_path / 'evil').exists()


def test_delete_rejects_invalid_name_before_side_effects(tmp_path):
    """公開入口で不正名を拒否し、副作用へ進まない現状を固定する。"""
    manager = SnapshotManager(tmp_path)

    with patch('devbase.snapshot.manager.shutil.rmtree') as rmtree:
        with pytest.raises(SnapshotError, match='無効なスナップショット名'):
            manager.delete(name='_foo')

        rmtree.assert_not_called()

    assert list((tmp_path / 'backups').iterdir()) == []

