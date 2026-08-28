"""自動スナップショットの最小間隔判定まわりの単体テスト。

カバー対象:
  - ``_snapshot_min_interval_minutes`` (commands/container.py): 環境変数の
    パースと不正値フォールバック。
  - ``SnapshotManager.last_snapshot_time`` (snapshot/manager.py): アーカイブ実体
    (full.tar.zst / incr-*.tar.zst) の mtime のみを集計し、meta.yml /
    snapshot.snar 等は除外することの確認。
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone

import pytest

from devbase.commands.container import (
    _SNAPSHOT_MIN_INTERVAL_MINUTES_DEFAULT,
    _snapshot_min_interval_minutes,
)
from devbase.snapshot.manager import SNAPSHOT_META_FILE, SnapshotManager
from devbase.errors import SnapshotError


# ---------------------------------------------------------------------------
# _snapshot_min_interval_minutes
# ---------------------------------------------------------------------------

_ENV = 'DEVBASE_SNAPSHOT_MIN_INTERVAL_MINUTES'


def test_min_interval_unset_returns_default(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    assert _snapshot_min_interval_minutes() == _SNAPSHOT_MIN_INTERVAL_MINUTES_DEFAULT
    assert _SNAPSHOT_MIN_INTERVAL_MINUTES_DEFAULT == 60


def test_min_interval_valid_value(monkeypatch):
    monkeypatch.setenv(_ENV, '30')
    assert _snapshot_min_interval_minutes() == 30


def test_min_interval_zero_disables(monkeypatch):
    monkeypatch.setenv(_ENV, '0')
    assert _snapshot_min_interval_minutes() == 0


def test_min_interval_negative_falls_back_with_warning(monkeypatch, caplog):
    monkeypatch.setenv(_ENV, '-5')
    with caplog.at_level('WARNING'):
        result = _snapshot_min_interval_minutes()
    assert result == _SNAPSHOT_MIN_INTERVAL_MINUTES_DEFAULT
    assert any('Invalid' in r.getMessage() for r in caplog.records)


def test_min_interval_non_numeric_falls_back_with_warning(monkeypatch, caplog):
    monkeypatch.setenv(_ENV, 'abc')
    with caplog.at_level('WARNING'):
        result = _snapshot_min_interval_minutes()
    assert result == _SNAPSHOT_MIN_INTERVAL_MINUTES_DEFAULT
    assert any('Invalid' in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# SnapshotManager.last_snapshot_time
# ---------------------------------------------------------------------------

def _touch(path, mtime: float) -> None:
    path.write_bytes(b'x')
    os.utime(path, (mtime, mtime))


def test_last_snapshot_time_empty_backups(tmp_path):
    mgr = SnapshotManager(tmp_path)
    # __init__ で backups ディレクトリは作成されるが中身は空。
    assert mgr.last_snapshot_time() is None


def test_last_snapshot_time_missing_backups_dir(tmp_path):
    mgr = SnapshotManager(tmp_path)
    # backups ディレクトリ自体を消した場合も None。
    import shutil
    shutil.rmtree(mgr.backups_dir)
    assert mgr.last_snapshot_time() is None


def test_last_snapshot_time_uses_newest_archive(tmp_path):
    mgr = SnapshotManager(tmp_path)
    snap_dir = mgr.backups_dir / '20240101-000000'
    snap_dir.mkdir()

    old = 1_700_000_000.0
    new = 1_700_086_400.0  # +1 day
    _touch(snap_dir / 'full.tar.zst', old)
    _touch(snap_dir / 'incr-001.tar.zst', new)

    result = mgr.last_snapshot_time()
    # last_snapshot_time は aware な UTC を返す (DST ズレ回避)。tz 非依存に
    # 比較するため timestamp で突き合わせる。
    assert result is not None
    assert result.tzinfo is not None
    assert result == datetime.fromtimestamp(new, tz=timezone.utc)
    assert result.timestamp() == new


def test_last_snapshot_time_ignores_meta_and_snar(tmp_path):
    """meta.yml / snapshot.snar が新しくても、アーカイブ実体の mtime を返す。"""
    mgr = SnapshotManager(tmp_path)
    snap_dir = mgr.backups_dir / '20240101-000000'
    snap_dir.mkdir()

    archive_old = 1_700_000_000.0
    noise_new = 1_700_200_000.0  # アーカイブより新しい付随ファイル

    _touch(snap_dir / 'full.tar.zst', archive_old)
    _touch(snap_dir / SNAPSHOT_META_FILE, noise_new)
    _touch(snap_dir / 'snapshot.snar', noise_new)
    _touch(snap_dir / 'snapshot.snar.bak', noise_new)

    result = mgr.last_snapshot_time()
    # 付随ファイルは除外され、アーカイブ実体の古い mtime が返るはず。
    assert result is not None
    assert result == datetime.fromtimestamp(archive_old, tz=timezone.utc)
    assert result.timestamp() == archive_old


def test_last_snapshot_time_only_noise_returns_none(tmp_path):
    """アーカイブ実体が一切無く付随ファイルだけの場合は None。"""
    mgr = SnapshotManager(tmp_path)
    snap_dir = mgr.backups_dir / '20240101-000000'
    snap_dir.mkdir()
    _touch(snap_dir / SNAPSHOT_META_FILE, 1_700_200_000.0)
    _touch(snap_dir / 'snapshot.snar', 1_700_200_000.0)

    assert mgr.last_snapshot_time() is None


# ---------------------------------------------------------------------------
# SnapshotManager.restore
# ---------------------------------------------------------------------------

def _snapshot_with_archives(mgr: SnapshotManager, name: str, archives: list[str]):
    snap_dir = mgr.backups_dir / name
    snap_dir.mkdir()
    for archive in archives:
        (snap_dir / archive).write_bytes(b'archive')
    return snap_dir


def test_restore_applies_full_and_all_incrementals(tmp_path, monkeypatch):
    mgr = SnapshotManager(tmp_path)
    snap_dir = _snapshot_with_archives(
        mgr,
        'snap1',
        ['full.tar.zst', 'incr-001.tar.zst', 'incr-002.tar.zst'],
    )
    created: list[tuple[str, bool]] = []
    commands: list[tuple[object, str, str]] = []

    monkeypatch.setattr(
        mgr,
        'create',
        lambda name, full: created.append((name, full)),
    )
    monkeypatch.setattr(
        mgr,
        '_run_docker_tar',
        lambda snap_dir, mode, command: commands.append((snap_dir, mode, command)),
    )

    mgr.restore('snap1')

    assert len(created) == 1
    assert created[0][0].startswith('pre-restore-')
    assert created[0][1] is True
    assert commands == [
        (
            snap_dir,
            'restore',
            "cd /target && find . -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + 2>/dev/null; "
            "zstd -d /backup/full.tar.zst -c | tar --listed-incremental=/dev/null -xf -",
        ),
        (
            snap_dir,
            'restore',
            "cd /target && zstd -d /backup/incr-001.tar.zst -c | tar --listed-incremental=/dev/null -xf -",
        ),
        (
            snap_dir,
            'restore',
            "cd /target && zstd -d /backup/incr-002.tar.zst -c | tar --listed-incremental=/dev/null -xf -",
        ),
    ]


def test_restore_applies_incrementals_up_to_point(tmp_path, monkeypatch):
    mgr = SnapshotManager(tmp_path)
    snap_dir = _snapshot_with_archives(
        mgr,
        'snap1',
        [
            'full.tar.zst',
            'incr-001.tar.zst',
            'incr-002.tar.zst',
            'incr-003.tar.zst',
        ],
    )
    commands: list[tuple[object, str, str]] = []

    monkeypatch.setattr(mgr, 'create', lambda name, full: None)
    monkeypatch.setattr(
        mgr,
        '_run_docker_tar',
        lambda snap_dir, mode, command: commands.append((snap_dir, mode, command)),
    )

    mgr.restore('snap1', point=2)

    assert commands == [
        (
            snap_dir,
            'restore',
            "cd /target && find . -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + 2>/dev/null; "
            "zstd -d /backup/full.tar.zst -c | tar --listed-incremental=/dev/null -xf -",
        ),
        (
            snap_dir,
            'restore',
            "cd /target && zstd -d /backup/incr-001.tar.zst -c | tar --listed-incremental=/dev/null -xf -",
        ),
        (
            snap_dir,
            'restore',
            "cd /target && zstd -d /backup/incr-002.tar.zst -c | tar --listed-incremental=/dev/null -xf -",
        ),
    ]


def test_restore_rejects_invalid_point(tmp_path):
    mgr = SnapshotManager(tmp_path)

    with pytest.raises(SnapshotError, match="--point は正の整数"):
        mgr.restore('snap1', point=0)


def test_restore_rejects_missing_full_archive(tmp_path):
    mgr = SnapshotManager(tmp_path)
    _snapshot_with_archives(mgr, 'snap1', ['incr-001.tar.zst'])

    with pytest.raises(SnapshotError, match="フルバックアップが見つかりません"):
        mgr.restore('snap1')


# ---------------------------------------------------------------------------
# SnapshotManager._run_docker_tar (mount 方向の現状固定)
# ---------------------------------------------------------------------------

def _capture_docker_run_command(mgr, monkeypatch):
    """_run_docker_tar が組み立てる docker run のコマンドを捕捉する"""
    monkeypatch.setattr(mgr, '_ensure_snapshot_image', lambda: 'devbase-snapshot:latest')
    captured: list[list[str]] = []

    class _Result:
        stdout = ''

    def fake_run(cmd, capture_output, text, check):
        captured.append(cmd)
        return _Result()

    monkeypatch.setattr(subprocess, 'run', fake_run)
    return captured


def test_run_docker_tar_backup_mode_mounts_volume_readonly(tmp_path, monkeypatch):
    """backup: volume は :ro (読み取り専用)、backup 先は書き込み可能でマウントされる。"""
    mgr = SnapshotManager(tmp_path)
    snap_dir = mgr.backups_dir / 'snap1'
    snap_dir.mkdir()
    captured = _capture_docker_run_command(mgr, monkeypatch)

    mgr._run_docker_tar(snap_dir, 'backup', 'echo hi')

    assert len(captured) == 1
    cmd = captured[0]
    assert f'devbase_home_ubuntu:/source:ro' in cmd
    assert f'{snap_dir.resolve()}:/backup' in cmd
    assert f'{snap_dir.resolve()}:/backup:ro' not in cmd


def test_run_docker_tar_restore_mode_mounts_backup_readonly(tmp_path, monkeypatch):
    """restore: volume は書き込み可能、backup 元は :ro でマウントされる。"""
    mgr = SnapshotManager(tmp_path)
    snap_dir = mgr.backups_dir / 'snap1'
    snap_dir.mkdir()
    captured = _capture_docker_run_command(mgr, monkeypatch)

    mgr._run_docker_tar(snap_dir, 'restore', 'echo hi')

    assert len(captured) == 1
    cmd = captured[0]
    assert 'devbase_home_ubuntu:/target' in cmd
    assert 'devbase_home_ubuntu:/target:ro' not in cmd
    assert f'{snap_dir.resolve()}:/backup:ro' in cmd


def test_run_docker_tar_unknown_mode_mixes_writable_mounts(tmp_path, monkeypatch):
    """NOTE: 現状固定。'backup' でも 'restore' でもない未検証の文字列を渡すと、
    volume 側は mode == 'backup' でないため restore 用 (書き込み可能・/target) の
    マウントになり、backup 側は mode == 'restore' でないため backup 用 (書き込み
    可能) のマウントになる。つまり volume も backup 先も両方書き込み可能になる
    不具合を含む (2 つの三項演算子がそれぞれ別の条件で判定しているため)。
    修正は別の変更で行う。
    """
    mgr = SnapshotManager(tmp_path)
    snap_dir = mgr.backups_dir / 'snap1'
    snap_dir.mkdir()
    captured = _capture_docker_run_command(mgr, monkeypatch)

    mgr._run_docker_tar(snap_dir, 'not-a-real-mode', 'echo hi')

    cmd = captured[0]
    assert 'devbase_home_ubuntu:/target' in cmd
    assert f'{snap_dir.resolve()}:/backup' in cmd
    assert f'{snap_dir.resolve()}:/backup:ro' not in cmd
