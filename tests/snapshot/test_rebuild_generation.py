"""世代の作り直し (#458)

既にある世代へ full を作るとき (``create --name <既存> --full``・snar の無い世代への差分の指定・
同じ秒の復元前バックアップ)、前の snar を引き継がずに仮の名前で控えてから入れ替える。
``DEVBASE_ROOT`` を使わず ``tmp_path`` に閉じ、``_run_docker_tar`` を差し替えて Docker を起動しない。
"""

from __future__ import annotations

import logging
import os
import types
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from devbase.commands.snapshot import cmd_snapshot
from devbase.errors import SnapshotCommandError, SnapshotError
from devbase.snapshot import manager as manager_mod
from devbase.snapshot.exclude import DEFAULT_EXCLUDES
from devbase.snapshot.manager import (
    FULL_ARCHIVE,
    FULL_ARCHIVE_TMP,
    META_FILE_TMP,
    REBUILDING_KEY,
    SNAR_FILE,
    SNAR_FILE_TMP,
    SnapshotManager,
)

from .fake_tar import write_backup_outputs

DEFAULTS = sorted(DEFAULT_EXCLUDES)
REBUILT = "rebuilt"


@pytest.fixture(autouse=True)
def _group_env(monkeypatch):
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "acme")


class FakeManager(SnapshotManager):
    """``docker run`` を実行せず、コマンドを記録して書くはずのファイルを置く。

    ``fail_backup`` が真なら控えのコマンドを失敗させる (仮の名前のファイルを書いた後に)。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list = []
        self.fail_backup = False
        self.content = "archive"

    def _run_docker_tar(self, snap_dir, mode, command, volumes=None):
        self.calls.append({"mode": mode, "command": command, "snap_dir": snap_dir})
        if mode != "backup":
            return None
        write_backup_outputs(snap_dir, command)
        if self.fail_backup:
            raise SnapshotCommandError("Dockerでのtar操作に失敗しました: boom", stderr="boom")
        if FULL_ARCHIVE_TMP in command:
            (snap_dir / FULL_ARCHIVE_TMP).write_text(REBUILT)
        return None


def _backups(root: Path) -> Path:
    return root / "backups"


def _listing(snap_dir: Path) -> set:
    return {p.name for p in snap_dir.iterdir()}


def _snapshot(snap_dir: Path) -> dict:
    return {p.name: p.read_bytes() for p in snap_dir.iterdir()}


def _meta(snap_dir: Path) -> dict:
    return yaml.safe_load((snap_dir / "meta.yml").read_text())


def _entries(root: Path) -> list:
    return yaml.safe_load((_backups(root) / "snapshot.yml").read_text())["snapshots"]


def _with_incrementals(root: Path, count: int = 1) -> FakeManager:
    """世代 ``g`` を full と ``count`` 本の差分で作る (``snapshot.snar.bak`` も持つ)。"""
    mgr = FakeManager(root)
    mgr.create(name="g")
    for _ in range(count):
        snap_dir = _backups(root) / "g"
        (snap_dir / f"{SNAR_FILE}.bak").write_text("bak")
        mgr.create(name="g")
    mgr.calls.clear()
    return mgr


# ---------------------------------------------------------------------------
# 作り直しの成功 (受け入れ条件 3・4・I1・INFO)
# ---------------------------------------------------------------------------

def test_rebuild_leaves_only_the_new_full(tmp_path, caplog):
    mgr = _with_incrementals(tmp_path, 2)
    snap_dir = _backups(tmp_path) / "g"
    created_at = _entries(tmp_path)[0]["created_at"]

    with caplog.at_level(logging.INFO, logger="devbase"):
        mgr.create(name="g", full=True)

    assert _listing(snap_dir) == {FULL_ARCHIVE, SNAR_FILE, "meta.yml"}
    assert (snap_dir / FULL_ARCHIVE).read_text() == REBUILT
    meta = _meta(snap_dir)
    assert meta["type"] == "full"
    assert meta["files"] == [FULL_ARCHIVE]
    assert meta["incremental_count"] == 0
    assert meta["exclude"] == DEFAULTS
    assert REBUILDING_KEY not in meta

    entries = _entries(tmp_path)
    assert [e["name"] for e in entries] == ["g"]
    assert entries[0]["incremental_count"] == 0
    assert entries[0]["created_at"] == created_at

    # 前の snar を tar に渡さず、仮の名前へ控える
    [call] = mgr.calls
    assert f"--listed-incremental=/backup/{SNAR_FILE_TMP} " in call["command"]
    assert call["command"].endswith(f"-o /backup/{FULL_ARCHIVE_TMP}")

    rebuilt = [r.getMessage() for r in caplog.records
               if r.levelno == logging.INFO and "作り直しました" in r.getMessage()]
    assert len(rebuilt) == 1
    assert "g" in rebuilt[0] and "2 本" in rebuilt[0]


def test_rebuild_uses_the_current_excludes(tmp_path):
    mgr = _with_incrementals(tmp_path)
    (_backups(tmp_path) / "exclude.yml").write_text("exclude:\n  - .cache/pip\n")
    mgr = FakeManager(tmp_path)
    mgr.create(name="g", full=True)
    assert _meta(_backups(tmp_path) / "g")["exclude"] == sorted(set(DEFAULTS) | {".cache/pip"})


def test_incremental_after_rebuild_starts_from_one(tmp_path):
    """受け入れ条件 7 (ファイルの側。中身は実の tar のテスト)"""
    mgr = _with_incrementals(tmp_path)
    mgr.create(name="g", full=True)
    mgr.create(name="g")
    snap_dir = _backups(tmp_path) / "g"
    assert _meta(snap_dir)["files"] == [FULL_ARCHIVE, "incr-001.tar.zst"]
    assert f"--listed-incremental=/backup/{SNAR_FILE} " in mgr.calls[-1]["command"]


# ---------------------------------------------------------------------------
# 新しい世代は今と同じ (受け入れ条件 9・I5)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("full", [False, True])
def test_new_generation_is_written_directly(tmp_path, caplog, full):
    mgr = FakeManager(tmp_path)
    with caplog.at_level(logging.INFO, logger="devbase"):
        mgr.create(name="g", full=full)
    [call] = mgr.calls
    assert call["command"] == SnapshotManager.backup_command(
        FULL_ARCHIVE, mgr.volumes, mgr.excludes)
    assert _listing(_backups(tmp_path) / "g") == {FULL_ARCHIVE, SNAR_FILE, "meta.yml"}
    assert "作り直しました" not in caplog.text


# ---------------------------------------------------------------------------
# snar の無い世代への差分の指定 (受け入れ条件 8)
# ---------------------------------------------------------------------------

def test_incremental_without_snar_rebuilds_and_drops_incrementals(tmp_path):
    mgr = _with_incrementals(tmp_path, 2)
    snap_dir = _backups(tmp_path) / "g"
    (snap_dir / SNAR_FILE).unlink()
    mgr.create(name="g")
    assert _listing(snap_dir) == {FULL_ARCHIVE, SNAR_FILE, "meta.yml"}
    assert _meta(snap_dir)["files"] == [FULL_ARCHIVE]


# ---------------------------------------------------------------------------
# 控えの失敗 (受け入れ条件 5・I2)
# ---------------------------------------------------------------------------

def test_failed_backup_keeps_the_generation_byte_for_byte(tmp_path):
    mgr = _with_incrementals(tmp_path)
    snap_dir = _backups(tmp_path) / "g"
    (snap_dir / f"{SNAR_FILE}.bak").unlink()
    before = _snapshot(snap_dir)
    assert set(before) == {FULL_ARCHIVE, "incr-001.tar.zst", SNAR_FILE, "meta.yml"}
    state_before = (_backups(tmp_path) / "snapshot.yml").read_bytes()

    mgr.fail_backup = True
    with pytest.raises(SnapshotError):
        mgr.create(name="g", full=True)

    assert _snapshot(snap_dir) == before
    assert (_backups(tmp_path) / "snapshot.yml").read_bytes() == state_before


def test_leftover_temps_are_discarded_before_the_backup(tmp_path):
    mgr = _with_incrementals(tmp_path)
    snap_dir = _backups(tmp_path) / "g"
    for tmp in (FULL_ARCHIVE_TMP, SNAR_FILE_TMP, META_FILE_TMP):
        (snap_dir / tmp).write_text("stale")
    seen = {}

    original = FakeManager._run_docker_tar

    def spy(self, snap_dir_, mode, command, volumes=None):
        seen["temps"] = {t for t in (FULL_ARCHIVE_TMP, SNAR_FILE_TMP, META_FILE_TMP)
                         if (snap_dir_ / t).exists()}
        return original(self, snap_dir_, mode, command, volumes)

    mgr._run_docker_tar = spy.__get__(mgr)
    mgr.create(name="g", full=True)
    assert seen["temps"] == set()


def test_cli_exits_1_when_the_backup_fails(tmp_path, monkeypatch):
    mgr = _with_incrementals(tmp_path)
    mgr.fail_backup = True
    monkeypatch.setattr("devbase.commands.snapshot.SnapshotManager", lambda *a, **k: mgr)
    args = types.SimpleNamespace(subcommand="create", name="g", full=True, group="acme")
    assert cmd_snapshot(tmp_path, args) == 1


# ---------------------------------------------------------------------------
# 入れ替えの途中の失敗 (受け入れ条件 6・I3)
# ---------------------------------------------------------------------------

def _fail_replace_at(monkeypatch, nth: int) -> None:
    """``os.replace`` の ``nth`` 回目 (1 始まり) を失敗させる。

    入れ替えの順は 1 印付きの meta.yml・2 full・3 印無しの meta.yml・4 状態ファイル。
    """
    real = os.replace
    count = {"n": 0}

    def fake(src, dst):
        count["n"] += 1
        if count["n"] == nth:
            raise OSError("injected")
        return real(src, dst)

    monkeypatch.setattr(manager_mod.os, "replace", fake)


def _fail_incr_unlink(monkeypatch) -> None:
    real = Path.unlink

    def fake(self, missing_ok=False):
        if self.name.startswith("incr-"):
            raise OSError("injected")
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fake)


def test_incrementals_are_dropped_from_the_highest_number(tmp_path, monkeypatch):
    """止まっても、残る差分は前の full から番号の小さい順に続く。"""
    mgr = _with_incrementals(tmp_path, 3)
    snap_dir = _backups(tmp_path) / "g"
    real = Path.unlink
    count = {"n": 0}

    def fake(self, missing_ok=False):
        if self.name.startswith("incr-"):
            count["n"] += 1
            if count["n"] == 2:
                raise OSError("injected")
        return real(self, missing_ok=missing_ok)

    with monkeypatch.context() as m:
        m.setattr(Path, "unlink", fake)
        with pytest.raises(SnapshotError):
            mgr.create(name="g", full=True)
    assert sorted(p.name for p in snap_dir.glob("incr-*")) == [
        "incr-001.tar.zst", "incr-002.tar.zst"]


@pytest.mark.parametrize("stop", ["incr", 1, 2, 3, 4])
def test_failed_install_reports_and_next_create_rebuilds(tmp_path, monkeypatch, stop):
    mgr = _with_incrementals(tmp_path)
    snap_dir = _backups(tmp_path) / "g"
    with monkeypatch.context() as m:
        if stop == "incr":
            _fail_incr_unlink(m)
        else:
            _fail_replace_at(m, stop)
        with pytest.raises(SnapshotError, match="作り直しを終えられませんでした"):
            mgr.create(name="g", full=True)

    names = _listing(snap_dir)
    assert SNAR_FILE not in names
    assert f"{SNAR_FILE}.bak" not in names
    assert not names & {FULL_ARCHIVE_TMP, SNAR_FILE_TMP, META_FILE_TMP}

    # 次の差分の指定は snar 無しの経路で作り直し、印を外す
    mgr.create(name="g")
    assert _listing(snap_dir) == {FULL_ARCHIVE, SNAR_FILE, "meta.yml"}
    assert REBUILDING_KEY not in _meta(snap_dir)
    assert _meta(snap_dir)["files"] == [FULL_ARCHIVE]


@pytest.mark.parametrize("stop,marked", [("incr", False), (1, False), (2, True),
                                          (3, True), (4, False)])
def test_marked_generation_is_not_restored(tmp_path, monkeypatch, stop, marked):
    """I3: 別のボリュームの組で作り直して止めると、印のある世代は消去の前に止まる。"""
    _with_incrementals(tmp_path)
    snap_dir = _backups(tmp_path) / "g"
    other = FakeManager(tmp_path, group="other")
    with monkeypatch.context() as m:
        if stop == "incr":
            _fail_incr_unlink(m)
        else:
            _fail_replace_at(m, stop)
        with pytest.raises(SnapshotError):
            other.create(name="g", full=True)

    meta = _meta(snap_dir)
    assert bool(meta.get(REBUILDING_KEY)) is marked
    rebuilt = (snap_dir / FULL_ARCHIVE).read_text() == REBUILT
    # 印が無ければ full と meta.yml の組が合う
    if not marked:
        assert rebuilt is (meta["volumes"]["group"] == "devbase_home_other")

    other.calls.clear()
    if marked:
        with pytest.raises(SnapshotError, match="作り直しの途中で止まっている"):
            other.restore("g")
        assert other.calls == []
    else:
        other.restore("g")
        restores = [c for c in other.calls if c["mode"] == "restore"]
        assert restores


# ---------------------------------------------------------------------------
# 同じ秒の復元前バックアップ (F3)
# ---------------------------------------------------------------------------

def test_pre_restore_backup_rebuilds_an_existing_generation(tmp_path, monkeypatch):
    class FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 12, 12, 0, 0)

    monkeypatch.setattr(manager_mod, "datetime", FixedNow)
    mgr = FakeManager(tmp_path)
    name = "pre-restore-20261012-120000"
    mgr.create(name=name)
    (_backups(tmp_path) / name / "incr-001.tar.zst").write_text("old")

    assert mgr._backup_before_restore(mgr.volumes) == name
    assert _listing(_backups(tmp_path) / name) == {FULL_ARCHIVE, SNAR_FILE, "meta.yml"}
