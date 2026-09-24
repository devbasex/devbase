"""スナップショットの世代を、ボリュームの組ごとの系列で持つ (PLAN68)

系列の解決・差分の積み先・系列ごとの最小間隔・系列ごとの保持と全体の上限、
および世代の場所の検証 (決定 7) を固定する。Docker は起動しない
(``_run_docker_tar`` を差し替える。``test_manager_volumes.py`` と同じ流儀)。
"""

from __future__ import annotations

import logging
import os
import re
import time
import types
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from devbase.commands.snapshot import cmd_snapshot
from devbase.errors import SnapshotError
from devbase.snapshot.manager import SnapshotManager


@pytest.fixture(autouse=True)
def _clean_group_env(monkeypatch):
    monkeypatch.delenv("DEVBASE_ACCOUNT_GROUP", raising=False)


class RecordingManager(SnapshotManager):
    """``docker run`` を実行せず、渡された引数だけを記録する。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[dict] = []

    def _run_docker_tar(self, snap_dir, mode, command, volumes=None):
        self.calls.append({"mode": mode, "command": command})
        if mode == "backup":
            (snap_dir / "full.tar.zst").write_text("archive")
            (snap_dir / "snapshot.snar").write_text("snar")


def vols(group: str) -> dict:
    return {"ai": "devbase_home_ubuntu", "group": f"devbase_home_{group}"}


def write_state(root: Path, entries: list) -> Path:
    """``snapshot.yml`` と各世代のディレクトリを直接書く。

    entries は ``(名前, グループ | None, 差分数)`` の並び。``None`` は旧レイアウト
    (``volumes`` を持たないエントリ)。並びの順に ``created_at`` を古い方から振る。
    """
    backups = root / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    snaps = []
    for i, (name, group, incr) in enumerate(entries):
        created = f"2026-09-01T00:{i:02d}:00"
        snap_dir = backups / name
        if not snap_dir.exists() and not snap_dir.is_symlink() and "/" not in name:
            snap_dir.mkdir()
            (snap_dir / "full.tar.zst").write_text("archive")
            (snap_dir / "snapshot.snar").write_text("snar")
            meta = {"name": name, "type": "full", "files": ["full.tar.zst"],
                    "incremental_count": incr}
            if group is None:
                meta["volume"] = "devbase_home_ubuntu"
            else:
                meta["volumes"] = vols(group)
            (snap_dir / "meta.yml").write_text(yaml.safe_dump(meta))
        entry = {"name": name, "created_at": created, "updated_at": created,
                 "incremental_count": incr}
        if group is not None:
            entry["volumes"] = vols(group)
        snaps.append(entry)
    (backups / "snapshot.yml").write_text(yaml.safe_dump(
        {"max_generations": 3, "snapshots": snaps}))
    return backups


def names(root: Path) -> list:
    data = yaml.safe_load((root / "backups" / "snapshot.yml").read_text())
    return [s["name"] for s in data["snapshots"]]


# ---------------------------------------------------------------------------
# 系列の解決
# ---------------------------------------------------------------------------

def test_series_label_names_the_group():
    assert SnapshotManager.series_label(vols("with")) == "グループ with"
    assert SnapshotManager.series_label({"": "devbase_home_ubuntu"}) == \
        "旧レイアウト（共通ボリュームのみ）"


def test_series_key_ignores_order():
    a = {"ai": "devbase_home_ubuntu", "group": "devbase_home_x"}
    b = {"group": "devbase_home_x", "ai": "devbase_home_ubuntu"}
    assert SnapshotManager.series_key(a) == SnapshotManager.series_key(b)


# ---------------------------------------------------------------------------
# 差分の積み先 (受け入れ条件 1〜5)
# ---------------------------------------------------------------------------

def test_returning_group_appends_to_its_latest_generation(tmp_path):
    """1: default → with → default で、default の世代へ差分を積む。"""
    RecordingManager(tmp_path, group="default").create(name="A")
    RecordingManager(tmp_path, group="with").create(name="B")

    mgr = RecordingManager(tmp_path, group="default")
    assert mgr.auto_snapshot_target() == "A"
    assert (mgr.auto_snapshot_target() is None) is False


def test_group_without_generation_starts_a_new_one(tmp_path, caplog):
    """2: 系列に世代が無ければ新しい世代にする。理由を出す。"""
    write_state(tmp_path, [("A", "default", 0)])
    mgr = RecordingManager(tmp_path, group="with")

    with caplog.at_level(logging.INFO, logger="devbase"):
        assert mgr.auto_snapshot_target() is None
    assert any("グループ with の世代がまだ無いため" in r.getMessage()
               for r in caplog.records)


def test_incremental_limit_is_counted_per_series(tmp_path, caplog):
    """3: 差分の上限は系列ごと。他の系列の差分数を見ない。"""
    write_state(tmp_path, [("W", "with", 0), ("D", "default", 10)])

    with caplog.at_level(logging.INFO, logger="devbase"):
        assert RecordingManager(tmp_path, group="default").auto_snapshot_target() is None
    assert any("上限 (10) に達した" in r.getMessage() and "グループ default" in r.getMessage()
               for r in caplog.records)
    assert RecordingManager(tmp_path, group="with").auto_snapshot_target() == "W"


def test_legacy_generation_is_not_appended_to(tmp_path):
    """4: 旧レイアウトの世代だけなら新しい世代にする。"""
    write_state(tmp_path, [("old", None, 0)])
    assert RecordingManager(tmp_path).auto_snapshot_target() is None


def test_meta_mismatch_starts_a_new_generation(tmp_path, caplog):
    """snapshot.yml と meta.yml の組が食い違えば積まない (判定の順 2)。"""
    backups = write_state(tmp_path, [("D", "default", 0)])
    meta = yaml.safe_load((backups / "D" / "meta.yml").read_text())
    meta["volumes"] = vols("kkg")
    (backups / "D" / "meta.yml").write_text(yaml.safe_dump(meta))

    with caplog.at_level(logging.INFO, logger="devbase"):
        assert RecordingManager(tmp_path, group="default").auto_snapshot_target() is None
    assert any("meta.yml の対象ボリューム" in r.getMessage() for r in caplog.records)


def test_series_latest_uses_created_at(tmp_path):
    write_state(tmp_path, [("D1", "default", 0), ("W", "with", 0), ("D2", "default", 0)])
    mgr = RecordingManager(tmp_path, group="default")
    assert mgr.series_latest()["name"] == "D2"
    assert mgr.series_latest(vols("with"))["name"] == "W"
    assert mgr.series_latest(vols("kkg")) is None


def _unquote_created_at(backups: Path, *targets: str) -> None:
    """指定した世代の ``created_at`` を引用符なしの YAML timestamp で書き直す。"""
    path = backups / "snapshot.yml"
    data = yaml.safe_load(path.read_text())
    for snap in data["snapshots"]:
        if snap["name"] in targets:
            snap["created_at"] = datetime.fromisoformat(snap["created_at"])
    path.write_text(yaml.safe_dump(data))


def test_series_latest_accepts_yaml_timestamp(tmp_path):
    """手で書いた snapshot.yml の引用符なしの日時が混ざっても比べられる。"""
    backups = write_state(tmp_path, [("D1", "default", 0), ("D2", "default", 0)])
    _unquote_created_at(backups, "D1")
    assert RecordingManager(tmp_path, group="default").series_latest()["name"] == "D2"


def test_rotate_accepts_yaml_timestamp(tmp_path):
    backups = write_state(tmp_path, [
        ("D1", "default", 0), ("D2", "default", 0), ("D3", "default", 0),
        ("D4", "default", 0)])
    _unquote_created_at(backups, "D2", "D4")
    assert SnapshotManager(tmp_path).rotate() == 1
    assert names(tmp_path) == ["D2", "D3", "D4"]


class ArchiveRecordingManager(RecordingManager):
    """書き込むアーカイブ名を command から拾う (full / incr-NNN を区別する)。"""

    def _run_docker_tar(self, snap_dir, mode, command, volumes=None):
        self.calls.append({"mode": mode, "command": command})
        if mode == "backup":
            archive = re.search(r"/backup/(full\.tar\.zst|incr-\d+\.tar\.zst)", command)
            (snap_dir / archive.group(1)).write_text("archive")
            (snap_dir / "snapshot.snar").write_text("snar")


def _entry(root: Path, name: str) -> dict:
    data = yaml.safe_load((root / "backups" / "snapshot.yml").read_text())
    return next(s for s in data["snapshots"] if s["name"] == name)


def test_create_numbers_incrementals_in_order(tmp_path):
    """現状固定: 2 本目以降の差分は incr-002 と番号を進め、差分数を両方の台帳へ書く。"""
    mgr = ArchiveRecordingManager(tmp_path, group="default")
    mgr.create(name="g")
    mgr.create(name="g", full=False)
    mgr.create(name="g", full=False)

    snap_dir = tmp_path / "backups" / "g"
    assert sorted(p.name for p in snap_dir.glob("*.tar.zst")) == [
        "full.tar.zst", "incr-001.tar.zst", "incr-002.tar.zst"]
    meta = yaml.safe_load((snap_dir / "meta.yml").read_text())
    assert meta["type"] == "incremental"
    assert meta["incremental_count"] == 2
    assert meta["files"] == ["full.tar.zst", "incr-001.tar.zst", "incr-002.tar.zst"]
    assert _entry(tmp_path, "g")["incremental_count"] == 2


def test_create_without_snar_falls_back_to_full(tmp_path):
    """現状固定: 既存世代に snapshot.snar が無ければ差分でなく full を作り直す。"""
    mgr = ArchiveRecordingManager(tmp_path, group="default")
    mgr.create(name="h")
    mgr.create(name="h", full=False)
    snap_dir = tmp_path / "backups" / "h"
    (snap_dir / "snapshot.snar").unlink()

    mgr.create(name="h", full=False)

    assert "incr-002.tar.zst" not in [p.name for p in snap_dir.iterdir()]
    assert "incr-002" not in mgr.calls[-1]["command"]
    assert "/backup/full.tar.zst" in mgr.calls[-1]["command"]
    meta = yaml.safe_load((snap_dir / "meta.yml").read_text())
    assert meta["type"] == "full"
    assert meta["incremental_count"] == 0
    assert meta["files"] == ["full.tar.zst"]
    assert _entry(tmp_path, "h")["incremental_count"] == 0


# ---------------------------------------------------------------------------
# 系列ごとの最小間隔 (受け入れ条件 6)
# ---------------------------------------------------------------------------

def test_last_snapshot_time_per_series(tmp_path):
    backups = write_state(tmp_path, [("W", "with", 0), ("D", "default", 0)])
    now = time.time()
    os.utime(backups / "D" / "full.tar.zst", (now - 600, now - 600))
    os.utime(backups / "W" / "full.tar.zst", (now - 7200, now - 7200))

    mgr = SnapshotManager(tmp_path)
    d = mgr.last_snapshot_time(vols("default")).timestamp()
    w = mgr.last_snapshot_time(vols("with")).timestamp()
    assert abs(d - (now - 600)) < 2
    assert abs(w - (now - 7200)) < 2
    assert mgr.last_snapshot_time(vols("kkg")) is None
    # 省けば全体 (現行どおり)
    assert abs(mgr.last_snapshot_time().timestamp() - (now - 600)) < 2


# ---------------------------------------------------------------------------
# 保持 (受け入れ条件 8〜14・17・22)
# ---------------------------------------------------------------------------

def test_rotate_keeps_per_series(tmp_path, caplog):
    """8・17: default 4・with 1 で default の最古だけを消す。"""
    backups = write_state(tmp_path, [
        ("D1", "default", 0), ("D2", "default", 0), ("W1", "with", 0),
        ("D3", "default", 0), ("D4", "default", 0)])

    with caplog.at_level(logging.INFO, logger="devbase"):
        assert SnapshotManager(tmp_path).rotate() == 1
    assert names(tmp_path) == ["D2", "W1", "D3", "D4"]
    assert not (backups / "D1").exists()
    assert (backups / "W1").exists()
    assert any("グループ default の 1 世代を削除しました" in r.getMessage()
               for r in caplog.records)


def test_alternating_groups_keep_three_each(tmp_path):
    """9: 交互に 4 つずつ作っても各 3 世代が残る。"""
    entries = []
    for i in range(4):
        entries += [(f"D{i}", "default", 0), (f"W{i}", "with", 0)]
    write_state(tmp_path, entries)

    assert SnapshotManager(tmp_path).rotate() == 2
    assert names(tmp_path) == ["D1", "W1", "D2", "W2", "D3", "W3"]


def test_total_limit_removes_oldest_across_series(tmp_path, caplog):
    """10・17: 4 系列 × 3 世代 (交互) で A1・B1・C1 を消す。"""
    entries = []
    for i in (1, 2, 3):
        for g in "abcd":
            entries.append((f"{g.upper()}{i}", f"g{g}", 0))
    write_state(tmp_path, entries)

    with caplog.at_level(logging.INFO, logger="devbase"):
        assert SnapshotManager(tmp_path).rotate() == 3
    remaining = names(tmp_path)
    assert len(remaining) == 9
    assert not {"A1", "B1", "C1"} & set(remaining)
    assert any("全体の上限 9 世代を超えたため、グループ ga の A1 を削除しました"
               in r.getMessage() for r in caplog.records)


def test_total_limit_keeps_latest_of_each_series(tmp_path):
    """11: A の 3 世代が最古でも、A の最新は残す。"""
    entries = []
    for g in "abcd":
        for i in (1, 2, 3):
            entries.append((f"{g.upper()}{i}", f"g{g}", 0))
    write_state(tmp_path, entries)

    assert SnapshotManager(tmp_path).rotate() == 3
    remaining = names(tmp_path)
    assert "A3" in remaining
    assert not {"A1", "A2", "B1"} & set(remaining)


def test_total_limit_cannot_remove_series_latest(tmp_path, caplog):
    """12: 10 系列 × 1 世代は消さず、警告を 1 行出す。"""
    write_state(tmp_path, [(f"S{i}", f"g{i}x", 0) for i in range(10)])
    before = (tmp_path / "backups" / "snapshot.yml").read_bytes()

    with caplog.at_level(logging.INFO, logger="devbase"):
        assert SnapshotManager(tmp_path).rotate(keep=3, max_total=9) == 0
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "全体の上限 9 世代" in warnings[0].getMessage()
    assert "10 世代を残します" in warnings[0].getMessage()
    assert (tmp_path / "backups" / "snapshot.yml").read_bytes() == before


def test_legacy_series_is_counted_separately(tmp_path):
    """13: 旧レイアウト 3 と default 3 は、どちらも消さない。"""
    write_state(tmp_path, [("L1", None, 0), ("L2", None, 0), ("L3", None, 0),
                           ("D1", "default", 0), ("D2", "default", 0), ("D3", "default", 0)])
    assert SnapshotManager(tmp_path).rotate() == 0


@pytest.mark.parametrize("kwargs", [{"keep": 0}, {"max_total": 0}, {"keep": -1}])
def test_rotate_rejects_non_positive(tmp_path, kwargs):
    """14: 0 以下は SnapshotError。何も消さない。"""
    backups = write_state(tmp_path, [(f"D{i}", "default", 0) for i in range(5)])
    with pytest.raises(SnapshotError):
        SnapshotManager(tmp_path).rotate(**kwargs)
    assert all((backups / f"D{i}").exists() for i in range(5))


@pytest.mark.parametrize("attrs", [{"keep": 0}, {"keep": 3, "max_total": 0}])
def test_cli_rotate_rejects_non_positive(tmp_path, attrs):
    write_state(tmp_path, [("D1", "default", 0)])
    ns = types.SimpleNamespace(subcommand="rotate", **attrs)
    assert cmd_snapshot(tmp_path, ns) == 1


def test_cli_rotate_passes_max_total(tmp_path):
    """15: --max-total が manager へ渡る (既定の 6 なら 0 件になる)。"""
    write_state(tmp_path, [("D1", "default", 0), ("W1", "with", 0),
                           ("D2", "default", 0), ("W2", "with", 0)])
    ns = types.SimpleNamespace(subcommand="rotate", keep=2, max_total=2)
    assert cmd_snapshot(tmp_path, ns) == 0
    assert names(tmp_path) == ["D2", "W2"]


def test_cli_rotate_without_max_total_uses_keep_times_three(tmp_path, caplog):
    """15・18: TUI と同じ keep だけの引数で動き、全体の上限は keep × 3。"""
    write_state(tmp_path, [(f"S{i}", f"g{i}x", 0) for i in range(4)])
    ns = types.SimpleNamespace(subcommand="rotate", keep=1)
    with caplog.at_level(logging.INFO, logger="devbase"):
        assert cmd_snapshot(tmp_path, ns) == 0
    assert any("全体の上限 3 世代" in r.getMessage() for r in caplog.records
               if r.levelno == logging.WARNING)


def test_existing_state_is_left_untouched(tmp_path):
    """22: この端末と同じ 3 エントリで何も消さず、snapshot.yml を書かない。"""
    backups = tmp_path / "backups"
    backups.mkdir()
    real = {
        "max_generations": 3,
        "snapshots": [
            {"name": "20260915-231738", "created_at": "2026-09-15T23:17:38.1",
             "updated_at": "2026-09-22T10:00:00", "incremental_count": 9,
             "volumes": vols("default")},
            {"name": "20260920-212546", "created_at": "2026-09-20T21:25:46.1",
             "updated_at": "2026-09-20T21:25:46.1", "incremental_count": 0,
             "volumes": vols("with")},
            {"name": "20260923-081407", "created_at": "2026-09-23T08:14:07.1",
             "updated_at": "2026-09-23T08:14:07.1", "incremental_count": 0,
             "volumes": vols("default")},
        ],
    }
    (backups / "snapshot.yml").write_text(yaml.safe_dump(real))
    before = (backups / "snapshot.yml").read_bytes()

    assert SnapshotManager(tmp_path).rotate() == 0
    assert (backups / "snapshot.yml").read_bytes() == before


# ---------------------------------------------------------------------------
# 世代の場所の検証 (受け入れ条件 25〜28、決定 7)
# ---------------------------------------------------------------------------

def test_rotate_does_not_remove_outside_backups(tmp_path, caplog):
    """25: ``../outside`` のエントリは一覧から外すだけで、外のディレクトリは残す。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    write_state(tmp_path, [("../outside", "default", 0), ("D1", "default", 0),
                           ("D2", "default", 0), ("D3", "default", 0)])

    with caplog.at_level(logging.INFO, logger="devbase"):
        SnapshotManager(tmp_path).rotate()
    assert (outside / "keep.txt").read_text() == "keep"
    assert names(tmp_path) == ["D1", "D2", "D3"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "'../outside'" in warnings[0].getMessage()


def _link_outside(tmp_path: Path) -> Path:
    outside = tmp_path / "backups-outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (tmp_path / "backups").mkdir(exist_ok=True)
    (tmp_path / "backups" / "old").symlink_to(outside, target_is_directory=True)
    return outside


def test_rotate_does_not_follow_symlink_outside(tmp_path, caplog):
    """26: 兄弟の backups-outside/ を指すリンクの世代は、リンク先を消さない。"""
    outside = _link_outside(tmp_path)
    write_state(tmp_path, [("old", "default", 0), ("D1", "default", 0),
                           ("D2", "default", 0), ("D3", "default", 0)])

    with caplog.at_level(logging.INFO, logger="devbase"):
        SnapshotManager(tmp_path).rotate()
    assert (outside / "keep.txt").read_text() == "keep"
    assert names(tmp_path) == ["D1", "D2", "D3"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "'old'" in warnings[0].getMessage()
    with pytest.raises(SnapshotError):
        SnapshotManager(tmp_path)._safe_snap_dir("old")


def test_rotate_does_not_follow_symlink_inside(tmp_path, caplog):
    """26: backups/ の中の系列の最新を指すリンクでも、その中身を消さない。"""
    backups = write_state(tmp_path, [("D1", "default", 0), ("D2", "default", 0),
                                     ("new", "default", 0)])
    (backups / "old").symlink_to(backups / "new", target_is_directory=True)
    data = yaml.safe_load((backups / "snapshot.yml").read_text())
    data["snapshots"].insert(0, {"name": "old", "created_at": "2026-08-01T00:00:00",
                                 "incremental_count": 0, "volumes": vols("default")})
    (backups / "snapshot.yml").write_text(yaml.safe_dump(data))

    with caplog.at_level(logging.INFO, logger="devbase"):
        SnapshotManager(tmp_path).rotate()
    assert (backups / "new" / "full.tar.zst").read_text() == "archive"
    assert names(tmp_path) == ["D1", "D2", "new"]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_safe_snap_dir_uses_path_components(tmp_path):
    """決定 7: 兄弟の backups-outside/ は文字列の前方一致でも通さない。"""
    _link_outside(tmp_path)
    mgr = SnapshotManager(tmp_path)
    with pytest.raises(SnapshotError):
        mgr._safe_snap_dir("old")


def test_cli_delete_refuses_symlink(tmp_path):
    """27: delete はリンクの世代を止め、リンク先を消さない。"""
    outside = _link_outside(tmp_path)
    write_state(tmp_path, [("old", "default", 0)])
    ns = types.SimpleNamespace(subcommand="delete", name="old")
    assert cmd_snapshot(tmp_path, ns) == 1
    assert (outside / "keep.txt").read_text() == "keep"


@pytest.mark.parametrize("op", ["restore", "copy", "create"])
def test_other_operations_refuse_symlink(tmp_path, op):
    """28: restore / copy / create もリンクの世代で止まり、何も書かない。"""
    outside = _link_outside(tmp_path)
    write_state(tmp_path, [("old", "default", 0)])
    mgr = RecordingManager(tmp_path)

    with pytest.raises(SnapshotError):
        if op == "restore":
            mgr.restore("old")
        elif op == "copy":
            mgr.copy("old", "new")
        else:
            mgr.create(name="old")
    assert mgr.calls == []
    assert not (tmp_path / "backups" / "new").exists()
    assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]
