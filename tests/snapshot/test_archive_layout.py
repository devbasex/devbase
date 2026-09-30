"""アーカイブ名・展開コマンド・マウント先の組み立てが 1 か所に寄っていること (#266)

作成 (``_create_full`` / ``_create_incremental``)・復元 (``restore``)・
直近日時 (``last_snapshot_time``)・消去 (``clear_command``) が、それぞれ独自に
文字列を組み立てず、同じ helper を通ることを固定する。Docker は起動しない。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from devbase.snapshot.manager import (
    ARCHIVE_MOUNT,
    BACKUP_ROOT,
    FULL_ARCHIVE,
    INCR_ARCHIVE_GLOB,
    RESTORE_ROOT,
    SNAR_FILE,
    SnapshotManager,
    incr_archive_name,
    incr_archive_number,
    is_archive_file,
)


@pytest.fixture(autouse=True)
def _group_env(monkeypatch):
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "acme")


class RecordingManager(SnapshotManager):
    """``docker run`` を実行せず、渡されたコマンドだけを記録する。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[dict] = []

    def _run_docker_tar(self, snap_dir, mode, command, volumes=None):
        self.calls.append({"mode": mode, "command": command})
        if mode == "backup":
            archive = re.search(r"-o /backup/(\S+)", command).group(1)
            (snap_dir / archive).write_text("archive")
            (snap_dir / SNAR_FILE).write_text("snar")


# ---------------------------------------------------------------------------
# 名前の helper
# ---------------------------------------------------------------------------

def test_incr_archive_name_roundtrips_with_number():
    assert incr_archive_name(1) == "incr-001.tar.zst"
    assert incr_archive_number(incr_archive_name(12)) == 12


@pytest.mark.parametrize("name", [
    FULL_ARCHIVE, SNAR_FILE, "snapshot.snar.bak", "meta.yml",
    "incr-001.tar.zst.bak", "incr-abc.tar.zst",
])
def test_incr_archive_number_rejects_non_incremental(name):
    assert incr_archive_number(name) is None


def test_is_archive_file_accepts_only_full_and_incr():
    assert is_archive_file(FULL_ARCHIVE)
    assert is_archive_file("incr-003.tar.zst")
    for noise in (SNAR_FILE, "snapshot.snar.bak", "meta.yml", "full.tar.zst.bak"):
        assert not is_archive_file(noise), noise


def test_incr_glob_matches_what_is_archive_file_accepts(tmp_path: Path):
    for name in ("incr-001.tar.zst", "incr-002.tar.zst", FULL_ARCHIVE, SNAR_FILE):
        (tmp_path / name).write_text("x")
    found = sorted(p.name for p in tmp_path.glob(INCR_ARCHIVE_GLOB))
    assert found == ["incr-001.tar.zst", "incr-002.tar.zst"]
    assert all(is_archive_file(n) for n in found)


# ---------------------------------------------------------------------------
# コマンドの helper
# ---------------------------------------------------------------------------

VOLUMES = {"ai": "devbase_home_ubuntu", "group": "devbase_home_acme"}
LEGACY = {"": "devbase_home_ubuntu"}


def test_restore_command_has_same_shape_for_full_and_incr():
    full = SnapshotManager.restore_command(FULL_ARCHIVE)
    incr = SnapshotManager.restore_command("incr-002.tar.zst")
    assert full.replace(FULL_ARCHIVE, "X") == incr.replace("incr-002.tar.zst", "X")
    assert f"zstd -d {ARCHIVE_MOUNT}/{FULL_ARCHIVE} -c" in full
    assert f"tar --listed-incremental=/dev/null -xf - -C {RESTORE_ROOT}" in full


def test_backup_command_has_same_shape_for_full_and_incr():
    full = SnapshotManager.backup_command(FULL_ARCHIVE, VOLUMES)
    incr = SnapshotManager.backup_command("incr-002.tar.zst", VOLUMES)
    assert full.replace(FULL_ARCHIVE, "X") == incr.replace("incr-002.tar.zst", "X")
    assert f"--listed-incremental={ARCHIVE_MOUNT}/{SNAR_FILE}" in full
    assert full.endswith(f"-o {ARCHIVE_MOUNT}/{FULL_ARCHIVE}")


def test_backup_command_starts_from_the_mounted_volumes():
    """起点は各ボリュームのマウント先で、``/source`` そのものではない。

    ``/source`` はコンテナを起動するたびに作られ inode が変わる。起点にすると GNU tar が
    毎回「新しいディレクトリ」とみなし、差分がボリューム全体になる。
    """
    cmd = SnapshotManager.backup_command(FULL_ARCHIVE, VOLUMES)
    assert f"-C {BACKUP_ROOT} ai group |" in cmd
    assert f"-C {BACKUP_ROOT} . " not in cmd


# ---------------------------------------------------------------------------
# マウント先の helper
# ---------------------------------------------------------------------------

def test_mount_points_follow_mode_and_layout():
    assert SnapshotManager.mount_points(VOLUMES, "backup") == [
        f"{BACKUP_ROOT}/ai", f"{BACKUP_ROOT}/group"]
    assert SnapshotManager.mount_points(VOLUMES, "restore") == [
        f"{RESTORE_ROOT}/ai", f"{RESTORE_ROOT}/group"]
    assert SnapshotManager.mount_points(LEGACY, "restore") == [RESTORE_ROOT]


@pytest.mark.parametrize("volumes", [VOLUMES, LEGACY])
def test_clear_command_clears_exactly_the_restore_mount_points(volumes):
    points = SnapshotManager.mount_points(volumes, "restore")
    mounts = SnapshotManager.volume_mount_args(volumes, "restore")
    mounted_to = [m.split(":", 1)[1] for m in mounts[1::2]]
    assert mounted_to == points
    assert f"for d in {' '.join(points)};" in SnapshotManager.clear_command(volumes)


def test_backup_mounts_are_read_only_under_source():
    assert SnapshotManager.volume_mount_args(VOLUMES, "backup") == [
        "-v", f"devbase_home_ubuntu:{BACKUP_ROOT}/ai:ro",
        "-v", f"devbase_home_acme:{BACKUP_ROOT}/group:ro",
    ]


# ---------------------------------------------------------------------------
# 呼び出し側が helper を通ること
# ---------------------------------------------------------------------------

def test_create_and_restore_go_through_the_helpers(tmp_path: Path):
    mgr = RecordingManager(tmp_path)
    mgr.create(name="snap1")
    mgr.create(name="snap1")  # 差分
    backup_cmds = [c["command"] for c in mgr.calls if c["mode"] == "backup"]
    assert backup_cmds[0] == SnapshotManager.backup_command(FULL_ARCHIVE, mgr.volumes)
    assert backup_cmds[1].endswith(
        SnapshotManager.backup_command(incr_archive_name(1), mgr.volumes))
    assert backup_cmds[1].startswith(
        f"cp {ARCHIVE_MOUNT}/{SNAR_FILE} {ARCHIVE_MOUNT}/{SNAR_FILE}.bak && ")

    mgr.calls.clear()
    mgr.restore("snap1")
    restore_cmds = [c["command"] for c in mgr.calls if c["mode"] == "restore"]
    # 先頭は自動バックアップ (backup) の後の full。消去 + 展開の形。
    assert restore_cmds[0] == (
        SnapshotManager.clear_command(mgr.volumes)
        + SnapshotManager.restore_command(FULL_ARCHIVE))
    assert restore_cmds[1] == SnapshotManager.restore_command(incr_archive_name(1))


def test_restore_point_uses_incr_archive_number(tmp_path: Path):
    mgr = RecordingManager(tmp_path)
    mgr.create(name="snap1")
    for _ in range(3):
        mgr.create(name="snap1")
    mgr.calls.clear()

    mgr.restore("snap1", point=2)

    applied = [re.search(r"/backup/(\S+) -c", c["command"]).group(1)
               for c in mgr.calls if c["mode"] == "restore"]
    assert applied == [FULL_ARCHIVE, incr_archive_name(1), incr_archive_name(2)]


def test_last_snapshot_time_uses_is_archive_file(tmp_path: Path):
    mgr = RecordingManager(tmp_path)
    mgr.create(name="snap1")
    snap_dir = mgr.backups_dir / "snap1"
    now = 1_700_000_000.0
    os.utime(snap_dir / FULL_ARCHIVE, (now, now))
    os.utime(snap_dir / SNAR_FILE, (now + 1000, now + 1000))
    (snap_dir / f"{SNAR_FILE}.bak").write_text("bak")
    os.utime(snap_dir / f"{SNAR_FILE}.bak", (now + 1000, now + 1000))

    assert mgr.last_snapshot_time().timestamp() == now
