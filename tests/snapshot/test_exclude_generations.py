"""世代と除外の一覧 (#368)

``meta.yml`` の ``exclude`` の記録・自動スナップショットの積み先・名前を指定した差分・
読めない設定の扱いを縛る。``DEVBASE_ROOT`` を使わず ``tmp_path`` に閉じ、
``_run_docker_tar`` を差し替えて Docker を起動しない。
"""

from __future__ import annotations

import logging
import re
import shlex
import types

import pytest
import yaml

from devbase.commands.snapshot import cmd_snapshot
from devbase.errors import SnapshotError
from devbase.snapshot.exclude import DEFAULT_EXCLUDES, USER_CONFIG
from devbase.snapshot.manager import SNAR_FILE, SnapshotManager

from .test_manager_series import vols, write_state

DEFAULTS = sorted(DEFAULT_EXCLUDES)
OTHER = sorted(set(DEFAULT_EXCLUDES) | {"recorded/only"})


@pytest.fixture(autouse=True)
def _group_env(monkeypatch):
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "acme")


class RecordingManager(SnapshotManager):
    """``docker run`` を実行せず、渡されたコマンドだけを記録する。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list = []

    def _run_docker_tar(self, snap_dir, mode, command, volumes=None):
        self.calls.append({"mode": mode, "command": command})
        if mode == "backup":
            archive = re.search(r"-o /backup/(\S+)", command).group(1)
            (snap_dir / archive).write_text("archive")
            (snap_dir / SNAR_FILE).write_text("snar")

    def backup_excludes(self) -> list:
        """記録した控えのコマンドごとに、``--exclude=`` の値の並びを返す。"""
        result = []
        for call in self.calls:
            if call["mode"] != "backup":
                continue
            result.append([a.split("=", 1)[1] for a in shlex.split(call["command"])
                           if a.startswith("--exclude=")])
        return result


def _expected_args(patterns, members=("ai", "group")) -> list:
    return [f"{m}/{p}" for m in members for p in sorted(patterns)]


def _meta(backups, name) -> dict:
    return yaml.safe_load((backups / name / "meta.yml").read_text())


def _set_exclude(backups, name, value) -> None:
    path = backups / name / "meta.yml"
    meta = yaml.safe_load(path.read_text())
    if value is None:
        meta.pop("exclude", None)
    else:
        meta["exclude"] = value
    path.write_text(yaml.safe_dump(meta))


def _broken_config(backups) -> None:
    (backups / USER_CONFIG).write_text("exclude: [a\n")


def _info(caplog) -> list:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]


# ---------------------------------------------------------------------------
# 記録 (受け入れ条件 14・I2) と tar への受け渡し (受け入れ条件 1・3・4・5)
# ---------------------------------------------------------------------------

def test_full_records_sorted_excludes_and_incremental_keeps_them(tmp_path):
    mgr = RecordingManager(tmp_path)
    mgr.create(name="G")
    assert _meta(tmp_path / "backups", "G")["exclude"] == DEFAULTS
    mgr.create(name="G")
    meta = _meta(tmp_path / "backups", "G")
    assert meta["exclude"] == DEFAULTS
    assert meta["incremental_count"] == 1
    assert mgr.backup_excludes() == [_expected_args(DEFAULTS)] * 2


def test_user_exclude_reaches_tar_with_defaults(tmp_path):
    """受け入れ条件 5・I6"""
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / USER_CONFIG).write_text("exclude:\n  - .cache/pip\n")
    mgr = RecordingManager(tmp_path)
    mgr.create(name="G")
    expected = sorted(set(DEFAULTS) | {".cache/pip"})
    assert mgr.backup_excludes() == [_expected_args(expected)]
    assert _meta(tmp_path / "backups", "G")["exclude"] == expected


def test_pre_restore_backup_applies_the_excludes(tmp_path):
    """受け入れ条件 4"""
    mgr = RecordingManager(tmp_path)
    mgr.create(name="G")
    mgr.calls.clear()
    mgr.restore("G")
    pre = [n for n in (tmp_path / "backups").iterdir() if n.name.startswith("pre-restore-")]
    assert len(pre) == 1
    assert mgr.backup_excludes() == [_expected_args(DEFAULTS)]
    assert _meta(tmp_path / "backups", pre[0].name)["exclude"] == DEFAULTS


def test_legacy_layout_pre_restore_backup_uses_dot_prefix(tmp_path):
    """I5 (旧レイアウト): 旧レイアウトの組では頭が ``./``。"""
    mgr = RecordingManager(tmp_path)
    (tmp_path / "backups" / "pre").mkdir()
    mgr._create_full("pre", tmp_path / "backups" / "pre", {"": "devbase_home_ubuntu"})
    assert mgr.backup_excludes() == [_expected_args(DEFAULTS, members=(".",))]


def test_warnings_are_emitted_once_per_manager(tmp_path, caplog):
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / USER_CONFIG).write_text("exclude:\n  - /abs\n")
    mgr = RecordingManager(tmp_path)
    with caplog.at_level(logging.WARNING, logger="devbase"):
        mgr.auto_snapshot_target()
        mgr.create(name="G")
        mgr.create(name="G")
    assert sum("を採りません" in r.getMessage() for r in caplog.records) == 1


# ---------------------------------------------------------------------------
# 自動スナップショットの積み先 (受け入れ条件 6・I1・I3)
# ---------------------------------------------------------------------------

def test_same_excludes_keep_appending(tmp_path, caplog):
    backups = write_state(tmp_path, [("A", "acme", 0)])
    _set_exclude(backups, "A", list(reversed(DEFAULTS)))  # 並びに依らない
    with caplog.at_level(logging.INFO, logger="devbase"):
        assert RecordingManager(tmp_path).auto_snapshot_target() == "A"
    assert not any("除外の一覧" in m for m in _info(caplog))


@pytest.mark.parametrize("recorded", [None, OTHER, [], "x", [1]])
def test_different_or_missing_excludes_start_a_new_generation(tmp_path, caplog, recorded):
    backups = write_state(tmp_path, [("A", "acme", 0)])
    _set_exclude(backups, "A", recorded)
    with caplog.at_level(logging.INFO, logger="devbase"):
        assert RecordingManager(tmp_path).auto_snapshot_target() is None
    lines = [m for m in _info(caplog) if "除外の一覧" in m]
    assert lines == ["世代 A は今と違う除外の一覧で作られているため、新しい世代を作成します"]


# ---------------------------------------------------------------------------
# 名前を指定した差分 (受け入れ条件 7)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("recorded", [None, OTHER])
@pytest.mark.parametrize("with_snar", [True, False])
def test_named_incremental_into_different_excludes_stops(tmp_path, recorded, with_snar):
    backups = write_state(tmp_path, [("A", "acme", 0)])
    _set_exclude(backups, "A", recorded)
    if not with_snar:
        (backups / "A" / SNAR_FILE).unlink()
    before = {p.name: p.read_bytes() for p in (backups / "A").iterdir()}
    mgr = RecordingManager(tmp_path)

    with pytest.raises(SnapshotError, match="今と違う除外の一覧"):
        mgr.create(name="A")

    assert mgr.calls == []
    assert {p.name: p.read_bytes() for p in (backups / "A").iterdir()} == before


# ---------------------------------------------------------------------------
# 積めない理由が重なるとき (現状固定): 対象ボリューム → 控えの起点 → 除外の一覧の順に
# 最初の 1 件だけを理由にする。自動の積み先と名前を指定した差分で同じ順
# ---------------------------------------------------------------------------

def _break_generation(backups, name, *, volumes: bool) -> None:
    """除外の記録を今と違えたうえで、控えの起点を古い形にし、``volumes`` なら対象ボリュームも違える。"""
    path = backups / name / "meta.yml"
    meta = yaml.safe_load(path.read_text())
    meta["exclude"] = OTHER
    del meta["archive_root"]
    if volumes:
        meta["volumes"] = vols("other")
    path.write_text(yaml.safe_dump(meta))


@pytest.mark.parametrize("volumes, reason", [
    (True, "世代 A の meta.yml の対象ボリューム (devbase_home_ubuntu, devbase_home_other) が"
           " グループ acme と一致しないため、新しい世代を作成します"),
    (False, "世代 A は控えの起点が古い形 (/source) のため、新しい世代を作成します"),
])
def test_auto_target_reports_only_the_first_blocking_reason(tmp_path, caplog, volumes, reason):
    backups = write_state(tmp_path, [("A", "acme", 0)])
    _break_generation(backups, "A", volumes=volumes)
    with caplog.at_level(logging.INFO, logger="devbase"):
        assert RecordingManager(tmp_path).auto_snapshot_target() is None
    assert _info(caplog) == [reason]


@pytest.mark.parametrize("volumes, reason", [
    (True, "別のボリューム構成"),
    (False, "控えの起点が古い形"),
])
def test_named_incremental_stops_on_the_first_blocking_reason(tmp_path, volumes, reason):
    backups = write_state(tmp_path, [("A", "acme", 0)])
    _break_generation(backups, "A", volumes=volumes)
    mgr = RecordingManager(tmp_path)
    with pytest.raises(SnapshotError, match=reason):
        mgr.create(name="A")
    assert mgr.calls == []


def test_cli_named_incremental_into_different_excludes_exits_1(tmp_path, caplog):
    backups = write_state(tmp_path, [("A", "acme", 0)])
    _set_exclude(backups, "A", OTHER)
    ns = types.SimpleNamespace(subcommand="create", name="A", full=False, group="acme")
    with caplog.at_level(logging.ERROR, logger="devbase"):
        assert cmd_snapshot(tmp_path, ns) == 1
    assert any("今と違う除外の一覧" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 読めない設定 (受け入れ条件 11・I9)
# ---------------------------------------------------------------------------

def test_unreadable_config_keeps_appending_with_the_record(tmp_path, caplog):
    backups = write_state(tmp_path, [("A", "acme", 0)])
    _set_exclude(backups, "A", OTHER)
    _broken_config(backups)

    mgr = RecordingManager(tmp_path)
    with caplog.at_level(logging.INFO, logger="devbase"):
        assert mgr.auto_snapshot_target() == "A"
    assert not any("除外の一覧で作られている" in m for m in _info(caplog))

    mgr.create(name="A")
    assert mgr.backup_excludes() == [_expected_args(OTHER)]
    assert _meta(backups, "A")["exclude"] == OTHER

    # 直した設定 (記録と同じ一覧) の次の判定も同じ世代
    (backups / USER_CONFIG).write_text("exclude:\n  - recorded/only\n")
    assert RecordingManager(tmp_path).auto_snapshot_target() == "A"


def test_unreadable_config_does_not_inherit_a_missing_record(tmp_path):
    backups = write_state(tmp_path, [("A", "acme", 0)])
    _set_exclude(backups, "A", None)
    _broken_config(backups)
    assert RecordingManager(tmp_path).auto_snapshot_target() is None

    mgr = RecordingManager(tmp_path)
    with pytest.raises(SnapshotError, match="今と違う除外の一覧"):
        mgr.create(name="A")


def test_unreadable_config_new_full_after_limit_inherits_the_record(tmp_path):
    """(a) 差分の上限で作る新しい世代のフル。直した設定の次の判定はその世代を返す。"""
    backups = write_state(tmp_path, [("A", "acme", 10)])
    _set_exclude(backups, "A", OTHER)
    _broken_config(backups)

    mgr = RecordingManager(tmp_path)
    assert mgr.auto_snapshot_target() is None
    mgr.create(name="B")
    assert mgr.backup_excludes() == [_expected_args(OTHER)]
    assert _meta(backups, "B")["exclude"] == OTHER

    (backups / USER_CONFIG).write_text("exclude:\n  - recorded/only\n")
    assert RecordingManager(tmp_path).auto_snapshot_target() == "B"


def test_unreadable_config_snar_fallback_keeps_the_record(tmp_path):
    """(b) snar の無い世代への差分はフルへ切り替わり、記録の一覧のまま。"""
    backups = write_state(tmp_path, [("A", "acme", 0)])
    _set_exclude(backups, "A", OTHER)
    (backups / "A" / SNAR_FILE).unlink()
    _broken_config(backups)

    mgr = RecordingManager(tmp_path)
    mgr.create(name="A")
    assert mgr.backup_excludes() == [_expected_args(OTHER)]
    assert _meta(backups, "A")["exclude"] == OTHER


def test_unreadable_config_pre_restore_backup_inherits_the_record(tmp_path):
    """(c) 復元前バックアップ"""
    backups = write_state(tmp_path, [("A", "acme", 0)])
    _set_exclude(backups, "A", OTHER)
    _broken_config(backups)

    mgr = RecordingManager(tmp_path)
    mgr.restore("A")
    pre = [n.name for n in backups.iterdir() if n.name.startswith("pre-restore-")]
    assert mgr.backup_excludes() == [_expected_args(OTHER)]
    assert _meta(backups, pre[0])["exclude"] == OTHER


def test_unreadable_config_without_any_record_uses_defaults(tmp_path):
    backups = write_state(tmp_path, [("A", "acme", 0)])
    _set_exclude(backups, "A", None)
    _broken_config(backups)

    mgr = RecordingManager(tmp_path)
    mgr.create(name="B")
    assert mgr.backup_excludes() == [_expected_args(DEFAULTS)]
    assert _meta(backups, "B")["exclude"] == DEFAULTS


# ---------------------------------------------------------------------------
# 除外を持たない世代の復元 (受け入れ条件 10・I8)
# ---------------------------------------------------------------------------

def test_restore_of_a_generation_without_excludes_is_unchanged(tmp_path):
    backups = write_state(tmp_path, [("A", "acme", 0)])
    _set_exclude(backups, "A", None)
    mgr = RecordingManager(tmp_path)
    mgr.restore("A")
    restores = [c["command"] for c in mgr.calls if c["mode"] == "restore"]
    assert restores and all("--exclude" not in c for c in restores)
