"""復元前バックアップが控えるボリューム (#255)

``restore`` が書き戻す前に作る ``pre-restore-<時刻>`` は、実行時の
``DEVBASE_ACCOUNT_GROUP`` ではなく、復元する世代の対象ボリュームの組を控える。
Docker は起動せず、``_run_docker_tar`` を差し替えてマウントの組とメタを固定する。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from devbase.errors import SnapshotCommandError, SnapshotError
from devbase.snapshot.manager import SnapshotManager

INITECH_VOLUMES = {"ai": "devbase_home_ubuntu", "group": "devbase_home_initech"}
REAL_ERROR_STDERR = "tar: ./ai/x: Cannot write: No space left on device\n"


@pytest.fixture(autouse=True)
def _no_group_env(monkeypatch):
    """CLI・TUI からの通常の実行と同じく、グループを置かない。"""
    monkeypatch.delenv("DEVBASE_ACCOUNT_GROUP", raising=False)


class RecordingManager(SnapshotManager):
    """``docker run`` を起こさず、呼び出しを記録する。指定があれば失敗させる。"""

    def __init__(self, *args, fail_backup: bool = False,
                 fail_restore: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[dict] = []
        self._fail_backup = fail_backup
        self._fail_restore = fail_restore

    def _run_docker_tar(self, snap_dir, mode, command, volumes=None):
        self.calls.append({
            "mode": mode,
            "snap_dir": snap_dir,
            "mounts": self.volume_mount_args(volumes or self.volumes, mode),
        })
        if mode == "backup":
            if self._fail_backup:
                raise SnapshotCommandError(
                    f"Dockerでのtar操作に失敗しました: {REAL_ERROR_STDERR}",
                    stderr=REAL_ERROR_STDERR)
            (snap_dir / "full.tar.zst").write_text("archive")
            (snap_dir / "snapshot.snar").write_text("snar")
            return None
        if self._fail_restore:
            raise SnapshotCommandError(
                f"Dockerでのtar操作に失敗しました: {REAL_ERROR_STDERR}",
                stderr=REAL_ERROR_STDERR)
        return None

    def backup_calls(self) -> list[dict]:
        return [c for c in self.calls if c["mode"] == "backup"]

    def restore_calls(self) -> list[dict]:
        return [c for c in self.calls if c["mode"] == "restore"]


def _write_generation(root: Path, name: str, layout: dict) -> Path:
    snap_dir = root / "backups" / name
    snap_dir.mkdir(parents=True)
    (snap_dir / "full.tar.zst").write_text("archive")
    (snap_dir / "snapshot.snar").write_text("snar")
    (snap_dir / "meta.yml").write_text(yaml.safe_dump({
        "name": name, "type": "full", "files": ["full.tar.zst"],
        "incremental_count": 0, **layout,
    }))
    return snap_dir


def _mounted(call: dict) -> list[str]:
    """``-v 名前:場所[:ro]`` からボリューム名だけを取り出す。"""
    args = call["mounts"]
    return [args[i + 1].split(":")[0] for i in range(0, len(args), 2)]


def _pre_restore_dirs(root: Path) -> list[Path]:
    return sorted((root / "backups").glob("pre-restore-*"))


def _entries(root: Path) -> list[dict]:
    path = root / "backups" / "snapshot.yml"
    if not path.exists():
        return []
    return (yaml.safe_load(path.read_text()) or {}).get("snapshots", [])


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]


# ---------------------------------------------------------------------------
# 控える組 (I1・I2)
# ---------------------------------------------------------------------------

def test_without_a_group_the_backup_takes_the_generation_volumes(tmp_path, caplog):
    """グループを置かずに復元しても、世代の組を控える (受け入れ条件 1)。"""
    _write_generation(tmp_path, "X", {"volumes": INITECH_VOLUMES})
    mgr = RecordingManager(tmp_path)

    with caplog.at_level("INFO"):
        mgr.restore("X")

    backups = mgr.backup_calls()
    assert len(backups) == 1
    assert _mounted(backups[0]) == ["devbase_home_ubuntu", "devbase_home_initech"]
    assert _warnings(caplog) == []
    assert len(mgr.restore_calls()) >= 1


def test_the_backup_records_the_generation_volumes(tmp_path):
    """控えの meta.yml と一覧のエントリに、控えた組が載る (受け入れ条件 2)。"""
    _write_generation(tmp_path, "X", {"volumes": INITECH_VOLUMES})
    mgr = RecordingManager(tmp_path)

    mgr.restore("X")

    [pre] = _pre_restore_dirs(tmp_path)
    meta = yaml.safe_load((pre / "meta.yml").read_text())
    assert meta["volumes"] == INITECH_VOLUMES
    [entry] = [e for e in _entries(tmp_path) if e["name"] == pre.name]
    assert entry["volumes"] == INITECH_VOLUMES


def test_another_group_in_the_environment_does_not_change_the_backup(
        tmp_path, monkeypatch):
    """実行時のグループが別でも、控えは世代の組 (受け入れ条件 3)。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "acme")
    _write_generation(tmp_path, "X", {"volumes": INITECH_VOLUMES})
    mgr = RecordingManager(tmp_path)

    mgr.restore("X")

    [backup] = mgr.backup_calls()
    assert "devbase_home_initech" in _mounted(backup)
    assert "devbase_home_acme" not in _mounted(backup)


def test_a_legacy_generation_is_backed_up_at_the_root(tmp_path):
    """volumes を持たない旧レイアウトは共通ボリューム 1 本をルートで控える (受け入れ条件 4)。"""
    _write_generation(tmp_path, "old", {"volume": "devbase_home_ubuntu"})
    mgr = RecordingManager(tmp_path)

    mgr.restore("old")

    [backup] = mgr.backup_calls()
    assert backup["mounts"] == ["-v", "devbase_home_ubuntu:/source:ro"]
    [pre] = _pre_restore_dirs(tmp_path)
    meta = yaml.safe_load((pre / "meta.yml").read_text())
    assert meta["volumes"] == {"": "devbase_home_ubuntu"}


def test_the_former_default_volume_is_backed_up_too(tmp_path):
    """旧既定のボリュームの世代でも、その組で控える (受け入れ条件 5)。"""
    layout = {"ai": "devbase_home_ubuntu", "group": "devbase_home_default"}
    _write_generation(tmp_path, "rollback", {"volumes": layout})
    mgr = RecordingManager(tmp_path)

    mgr.restore("rollback")

    [backup] = mgr.backup_calls()
    assert _mounted(backup) == ["devbase_home_ubuntu", "devbase_home_default"]


# ---------------------------------------------------------------------------
# 控えの失敗 (I3・I5)
# ---------------------------------------------------------------------------

def test_a_failed_backup_leaves_nothing_behind(tmp_path, caplog):
    """控えに失敗しても復元は続き、痕跡を残さない (受け入れ条件 6)。"""
    _write_generation(tmp_path, "X", {"volumes": INITECH_VOLUMES})
    mgr = RecordingManager(tmp_path, fail_backup=True)

    with caplog.at_level("WARNING"):
        mgr.restore("X")

    assert len(_warnings(caplog)) == 1
    assert len(mgr.restore_calls()) >= 1
    assert _pre_restore_dirs(tmp_path) == []
    assert not any(e["name"].startswith("pre-restore-") for e in _entries(tmp_path))


def test_a_failed_backup_keeps_an_existing_directory_of_the_same_name(
        tmp_path, monkeypatch):
    """同じ名前の控えが既にあれば、それは前の控えなので消さない (I3)。"""
    _write_generation(tmp_path, "X", {"volumes": INITECH_VOLUMES})
    existing = tmp_path / "backups" / "pre-restore-20260928-120000"
    existing.mkdir()
    (existing / "full.tar.zst").write_text("earlier")

    import devbase.snapshot.manager as manager_module

    class FixedDatetime(manager_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 28, 12, 0, 0)

    monkeypatch.setattr(manager_module, "datetime", FixedDatetime)
    mgr = RecordingManager(tmp_path, fail_backup=True)

    mgr.restore("X")

    assert existing.is_dir()
    assert (existing / "full.tar.zst").read_text() == "earlier"


def test_without_a_backup_the_failure_message_does_not_point_to_one(tmp_path):
    """控えも展開も失敗したら、別の世代から戻す案内になる (受け入れ条件 7)。"""
    _write_generation(tmp_path, "X", {"volumes": INITECH_VOLUMES})
    mgr = RecordingManager(tmp_path, fail_backup=True, fail_restore=True)

    with pytest.raises(SnapshotError) as e:
        mgr.restore("X")

    assert "復元前の自動バックアップは作成できていません" in str(e.value)
    assert "pre-restore-" not in str(e.value)


def test_the_failure_message_points_to_a_backup_of_the_same_volumes(tmp_path):
    """控えが成功し展開が失敗したら、その控えを示し、組は復元先と一致する (受け入れ条件 8)。"""
    _write_generation(tmp_path, "X", {"volumes": INITECH_VOLUMES})
    mgr = RecordingManager(tmp_path, fail_restore=True)

    with pytest.raises(SnapshotError) as e:
        mgr.restore("X")

    [pre] = _pre_restore_dirs(tmp_path)
    assert pre.name in str(e.value)
    meta = yaml.safe_load((pre / "meta.yml").read_text())
    [restore] = mgr.restore_calls()
    assert list(meta["volumes"].values()) == _mounted(restore)


# ---------------------------------------------------------------------------
# 不正な世代 (I4) とログ
# ---------------------------------------------------------------------------

def test_an_invalid_generation_is_refused_before_the_backup(tmp_path):
    """組が検証を通らなければ、控えも復元も行わない (受け入れ条件 9)。"""
    _write_generation(tmp_path, "bad", {
        "volumes": {"ai": "../../etc", "group": "devbase_home_initech"},
    })
    mgr = RecordingManager(tmp_path)

    with pytest.raises(SnapshotError):
        mgr.restore("bad")

    assert mgr.calls == []
    assert _pre_restore_dirs(tmp_path) == []


def test_the_backup_target_is_logged_before_the_backup(tmp_path, caplog):
    """控えの前に、控える対象ボリュームを並べた info を 1 行出す (受け入れ条件 10)。"""
    _write_generation(tmp_path, "X", {"volumes": INITECH_VOLUMES})
    logged_before_backup: list[bool] = []

    class LoggingCheckManager(RecordingManager):
        def _run_docker_tar(self, snap_dir, mode, command, volumes=None):
            if mode == "backup":
                logged_before_backup.append(any(
                    "バックアップします" in r.getMessage() for r in caplog.records))
            return super()._run_docker_tar(snap_dir, mode, command, volumes)

    mgr = LoggingCheckManager(tmp_path)
    with caplog.at_level("INFO"):
        mgr.restore("X")

    lines = [r.getMessage() for r in caplog.records
             if r.levelname == "INFO" and "バックアップします" in r.getMessage()]
    assert len(lines) == 1
    assert "devbase_home_ubuntu" in lines[0]
    assert "devbase_home_initech" in lines[0]
    assert logged_before_backup == [True]
