"""スナップショットの対象ボリューム (PLAN39 Task 6)

対象が共通ボリューム 1 本から「共通 + アカウントグループ」の 2 本になる。
Docker は起動せず、``_run_docker_tar`` を差し替えて **何をどこへマウントするか**と
**旧メタデータの互換**を固定する。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from devbase.errors import SnapshotError
from devbase.snapshot.manager import SnapshotManager


@pytest.fixture(autouse=True)
def _clean_group_env(monkeypatch):
    monkeypatch.delenv("DEVBASE_ACCOUNT_GROUP", raising=False)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


class RecordingManager(SnapshotManager):
    """``docker run`` を実行せず、渡された引数だけを記録する。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[dict] = []

    def _run_docker_tar(self, snap_dir, mode, command, volumes=None):
        self.calls.append({
            "mode": mode,
            "command": command,
            "volumes": dict(volumes or self.volumes),
            "mounts": self.volume_mount_args(volumes or self.volumes, mode),
        })
        # フルバックアップの実体が無いと restore が止まるので、印だけ作る
        if mode == "backup":
            (snap_dir / "full.tar.zst").write_text("archive")
            (snap_dir / "snapshot.snar").write_text("snar")


# ---------------------------------------------------------------------------
# 対象ボリューム (AC9)
# ---------------------------------------------------------------------------

def test_both_volumes_are_targeted(root):
    mgr = RecordingManager(root)

    assert mgr.volumes == {
        "ai": "devbase_home_ubuntu",
        "group": "devbase_home_default",
    }


def test_group_volume_follows_the_account_group(root, monkeypatch):
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")
    mgr = RecordingManager(root)

    assert mgr.volumes["group"] == "devbase_home_kkg"


def test_explicit_group_wins(root, monkeypatch):
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")
    mgr = RecordingManager(root, group="with")

    assert mgr.volumes["group"] == "devbase_home_with"


def test_backup_mounts_both_volumes_read_only(root):
    mgr = RecordingManager(root)

    mgr.create(name="snap1")

    mounts = mgr.calls[0]["mounts"]
    assert mounts == [
        "-v", "devbase_home_ubuntu:/source/ai:ro",
        "-v", "devbase_home_default:/source/group:ro",
    ]


def test_restore_mounts_both_volumes_writable(root):
    mgr = RecordingManager(root)
    mgr.create(name="snap1")
    mgr.calls.clear()

    mgr.restore("snap1")

    restore_calls = [c for c in mgr.calls if c["mode"] == "restore"]
    assert restore_calls[0]["mounts"] == [
        "-v", "devbase_home_ubuntu:/target/ai",
        "-v", "devbase_home_default:/target/group",
    ]


def test_restore_clears_each_mount_not_the_mount_points(root):
    """マウントポイント自身は消せない (busy)。各マウントの直下を消す。"""
    mgr = RecordingManager(root)
    mgr.create(name="snap1")
    mgr.calls.clear()

    mgr.restore("snap1")

    command = [c for c in mgr.calls if c["mode"] == "restore"][0]["command"]
    assert "for d in /target/ai /target/group;" in command
    assert "-C /target" in command


# ---------------------------------------------------------------------------
# メタデータ
# ---------------------------------------------------------------------------

def test_metadata_records_the_target_volumes(root):
    mgr = RecordingManager(root)

    mgr.create(name="snap1")

    meta = yaml.safe_load((root / "backups" / "snap1" / "meta.yml").read_text())
    assert meta["volumes"] == {
        "ai": "devbase_home_ubuntu",
        "group": "devbase_home_default",
    }


def test_global_metadata_records_the_target_volumes(root):
    mgr = RecordingManager(root)

    mgr.create(name="snap1")

    meta = yaml.safe_load((root / "backups" / "snapshot.yml").read_text())
    assert meta["snapshots"][0]["volumes"]["group"] == "devbase_home_default"


# ---------------------------------------------------------------------------
# 旧スナップショットの互換 (AC9)
# ---------------------------------------------------------------------------

def _write_legacy_snapshot(root: Path, name: str = "old") -> Path:
    """PLAN39 以前のスナップショット (共通ボリューム 1 本) を作る。"""
    snap_dir = root / "backups" / name
    snap_dir.mkdir(parents=True)
    (snap_dir / "full.tar.zst").write_text("archive")
    (snap_dir / "snapshot.snar").write_text("snar")
    (snap_dir / "meta.yml").write_text(yaml.safe_dump({
        "name": name,
        "type": "full",
        "volume": "devbase_home_ubuntu",
        "files": ["full.tar.zst"],
        "incremental_count": 0,
    }))
    (root / "backups" / "snapshot.yml").write_text(yaml.safe_dump({
        "max_generations": 3,
        "snapshots": [{"name": name, "created_at": "2026-01-01T00:00:00",
                       "updated_at": "2026-01-01T00:00:00",
                       "incremental_count": 0}],
    }))
    return snap_dir


def test_legacy_snapshot_layout_is_recognised(root):
    snap_dir = _write_legacy_snapshot(root)
    mgr = RecordingManager(root)

    assert mgr.snapshot_volumes(snap_dir) == {"": "devbase_home_ubuntu"}


def test_legacy_snapshot_restores_into_the_shared_volume(root):
    """旧世代は共通ボリュームをルートへ直接マウントして復元する。"""
    _write_legacy_snapshot(root)
    mgr = RecordingManager(root)

    mgr.restore("old")

    restore_calls = [c for c in mgr.calls if c["mode"] == "restore"]
    assert restore_calls[0]["mounts"] == ["-v", "devbase_home_ubuntu:/target"]
    assert "for d in /target;" in restore_calls[0]["command"]


def test_snapshot_without_metadata_falls_back_to_the_shared_volume(root):
    """meta.yml が壊れている / 無い世代でも復元先を見失わない。"""
    snap_dir = root / "backups" / "broken"
    snap_dir.mkdir(parents=True)
    mgr = RecordingManager(root)

    assert mgr.snapshot_volumes(snap_dir) == {"": "devbase_home_ubuntu"}


# ---------------------------------------------------------------------------
# レイアウト変更時の世代分割
# ---------------------------------------------------------------------------

def test_layout_change_starts_a_new_generation(root):
    """旧世代へ差分を積むと snar のレイアウトが違うため差分が壊れる。"""
    _write_legacy_snapshot(root)
    mgr = RecordingManager(root)

    assert mgr.should_start_new_generation() is True


def test_group_change_starts_a_new_generation(root, monkeypatch):
    mgr = RecordingManager(root)
    mgr.create(name="snap1")

    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")
    other = RecordingManager(root)

    assert other.should_start_new_generation() is True


def test_same_layout_keeps_appending_increments(root):
    mgr = RecordingManager(root)
    mgr.create(name="snap1")

    assert mgr.should_start_new_generation() is False


def test_incremental_on_a_different_layout_is_refused(root):
    """明示的に古い世代を指定されたときは、壊れた差分を積まず理由を出す。"""
    _write_legacy_snapshot(root)
    mgr = RecordingManager(root)

    with pytest.raises(SnapshotError) as excinfo:
        mgr.create(name="old", full=False)

    message = str(excinfo.value)
    assert "devbase_home_ubuntu" in message
    assert "devbase_home_default" in message


def test_invalid_group_does_not_break_read_only_operations(root, monkeypatch):
    """一覧のように対象ボリュームを要さない操作は、グループ名が不正でも通る。"""
    _write_legacy_snapshot(root)
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "ubuntu")

    mgr = SnapshotManager(root)

    assert [s["name"] for s in mgr.list()] == ["old"]


def test_invalid_group_is_rejected_when_volumes_are_needed(root, monkeypatch):
    from devbase.errors import DevbaseError

    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "ubuntu")
    mgr = SnapshotManager(root)

    with pytest.raises(DevbaseError):
        _ = mgr.volumes


# ---------------------------------------------------------------------------
# メタデータの検証 (改変・持ち込みスナップショット対策)
# ---------------------------------------------------------------------------

def _write_meta(root: Path, name: str, meta: dict) -> Path:
    snap_dir = root / "backups" / name
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "meta.yml").write_text(yaml.safe_dump(meta))
    (snap_dir / "full.tar.zst").write_text("archive")
    return snap_dir


def test_absolute_path_as_volume_is_rejected(root):
    """絶対パスを許すと任意のホストディレクトリを bind mount して消せてしまう。"""
    from devbase.errors import SnapshotError

    snap_dir = _write_meta(root, "tampered", {
        "name": "tampered", "type": "full",
        "volumes": {"ai": "/Users/someone", "group": "devbase_home_default"},
    })
    mgr = SnapshotManager(root)

    with pytest.raises(SnapshotError) as excinfo:
        mgr.snapshot_volumes(snap_dir)
    assert "/Users/someone" in str(excinfo.value)


def test_unknown_mount_name_is_rejected(root):
    """マウント名は消去コマンドのシェル文字列に入るため、既知の名前だけ許す。"""
    from devbase.errors import SnapshotError

    snap_dir = _write_meta(root, "tampered2", {
        "name": "tampered2", "type": "full",
        "volumes": {"; rm -rf /": "devbase_home_default"},
    })
    mgr = SnapshotManager(root)

    with pytest.raises(SnapshotError):
        mgr.snapshot_volumes(snap_dir)


def test_legacy_volume_value_is_validated_too(root):
    """旧形式の `volume:` も同じ検証を通す。"""
    from devbase.errors import SnapshotError

    snap_dir = _write_meta(root, "tampered3", {
        "name": "tampered3", "type": "full", "volume": "/etc",
    })
    mgr = SnapshotManager(root)

    with pytest.raises(SnapshotError):
        mgr.snapshot_volumes(snap_dir)


def test_restore_refuses_before_touching_the_volumes(root):
    """検証は消去コマンドを流す**前**に効く。"""
    from devbase.errors import SnapshotError

    _write_meta(root, "tampered4", {
        "name": "tampered4", "type": "full",
        "volumes": {"ai": "../../etc", "group": "devbase_home_default"},
    })
    mgr = RecordingManager(root)

    with pytest.raises(SnapshotError):
        mgr.restore("tampered4")
    assert [c for c in mgr.calls if c["mode"] == "restore"] == []


@pytest.mark.parametrize("group_volume", [
    "devbase_home_default", "devbase_home_kkg", "devbase_home_with",
    "devbase_home_a-b_c.d",
])
def test_devbase_owned_volume_names_pass(root, group_volume):
    snap_dir = _write_meta(root, f"ok-{group_volume}", {
        "name": "ok", "type": "full",
        "volumes": {"ai": "devbase_home_ubuntu", "group": group_volume},
    })
    mgr = SnapshotManager(root)

    assert mgr.snapshot_volumes(snap_dir) == {
        "ai": "devbase_home_ubuntu", "group": group_volume}


@pytest.mark.parametrize("name", [
    "mysql_data",              # 同じ Docker 上の無関係なボリューム
    "devbase_work_1",          # devbase の作業ボリューム (スナップショット対象外)
    "devbase_home_ubuntu",     # group 側に共通ボリュームを書く
    "devbase_home_1",          # 数字のみのグループ名 (index と衝突)
    "home_kkg",                # プレフィックスが違う
])
def test_unrelated_volume_names_are_rejected_for_the_group_mount(root, name):
    """named volume の形をしているだけでは通さない。

    無関係なボリューム名を書けると、復元前の消去でその中身を失わせられる。
    """
    from devbase.errors import SnapshotError

    snap_dir = _write_meta(root, f"bad-{name}", {
        "name": "bad", "type": "full",
        "volumes": {"ai": "devbase_home_ubuntu", "group": name},
    })
    mgr = SnapshotManager(root)

    with pytest.raises(SnapshotError):
        mgr.snapshot_volumes(snap_dir)


@pytest.mark.parametrize("name", ["mysql_data", "devbase_home_kkg", "devbase_work_1"])
def test_shared_mount_only_accepts_the_shared_volume(root, name):
    from devbase.errors import SnapshotError

    snap_dir = _write_meta(root, f"bad-shared-{name}", {
        "name": "bad", "type": "full",
        "volumes": {"ai": name, "group": "devbase_home_default"},
    })
    mgr = SnapshotManager(root)

    with pytest.raises(SnapshotError):
        mgr.snapshot_volumes(snap_dir)


@pytest.mark.parametrize("name", [
    "devbase_home_",           # 空のグループ名 (resolve は default に正規化してしまう)
    "devbase_home_  kkg  ",    # 前後空白 (resolve は空白を落としてしまう)
    "devbase_home_ KKG",
])
def test_unnormalised_group_volume_names_are_rejected(root, name):
    """検証を通るかどうかだけでは足りない。

    ``resolve_account_group`` は空文字を ``default`` に、前後空白を落とした名前に
    **正規化する**ため、通ること自体は不正な名前を許してしまう。実際にマウント
    されるのは正規化前の生の名前なので、一致まで確認する。
    """
    from devbase.errors import SnapshotError

    snap_dir = _write_meta(root, f"unnorm-{abs(hash(name))}", {
        "name": "unnorm", "type": "full",
        "volumes": {"ai": "devbase_home_ubuntu", "group": name},
    })
    mgr = SnapshotManager(root)

    with pytest.raises(SnapshotError):
        mgr.snapshot_volumes(snap_dir)
