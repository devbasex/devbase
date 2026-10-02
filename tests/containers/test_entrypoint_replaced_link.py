"""置き換わった共有のリンクの中身を控えに残してから張り直す (#372)

``containers/base/entrypoint.sh`` を ``DEVBASE_ENTRYPOINT_LIB_ONLY=1`` で source し、一時ディレクトリの
共通のボリュームとグループのボリュームでリンクの段を走らせる。Docker には依存しない。
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest

from tests.containers.test_entrypoint_ai_settings_concurrent import (
    NEEDS_FLOCK,
    NOT_ROOT,
    Volumes,
    assert_all_ok,
    run_concurrent,
    run_script,
    setup_cmd,
)

FILE_ENTRIES = ["settings.json", "CLAUDE.md"]
SHARED_CONTENT = {"settings.json": '{"shared": true}', "CLAUDE.md": "# shared"}
LOCAL_CONTENT = {"settings.json": '{"shared": false}', "CLAUDE.md": "# local!"}
ROUNDS = 4


def _prepare(v: Volumes, name: str, content: str, mode: int = 0o600) -> Path:
    """リンクを張り終えた後、共有のリンクの位置を ``content`` の通常のファイルに置き換える"""
    v.link_once()
    (v.ai / ".claude" / name).write_text(SHARED_CONTENT[name])
    p = v.shared(name)
    p.unlink()
    p.write_text(content)
    p.chmod(mode)
    return p


def _copies(v: Volumes, name: str) -> list[Path]:
    return sorted((v.grp / ".claude").glob(f"{name}.replaced-*"))


def _warnings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("WARNING:")]


def _stage(v: Volumes, home: str = "0", pre: str = ""):
    return run_script(setup_cmd(v.home(home), v.ai, v.grp), v.base, pre=pre)


def _assert_linked(v: Volumes, name: str) -> None:
    p = v.shared(name)
    assert p.is_symlink(), f"{name} が symlink でない"
    assert os.readlink(p) == str(v.ai / ".claude" / name)


@pytest.mark.parametrize("name", FILE_ENTRIES)
def test_same_content_is_relinked_without_a_copy(tmp_path, name):
    """受け入れ条件 1・3・5"""
    v = Volumes(tmp_path)
    _prepare(v, name, SHARED_CONTENT[name])
    r = _stage(v)
    assert r.returncode == 0, r.stderr
    _assert_linked(v, name)
    assert _copies(v, name) == []
    assert _warnings(r.stderr) == []


@pytest.mark.parametrize("name", FILE_ENTRIES)
def test_different_content_is_kept_as_a_copy(tmp_path, name):
    """受け入れ条件 2・3・5・7 / I1・I2・I5"""
    v = Volumes(tmp_path)
    p = _prepare(v, name, LOCAL_CONTENT[name], mode=0o600)
    before = p.stat()
    r = _stage(v)
    assert r.returncode == 0, r.stderr
    _assert_linked(v, name)
    copies = _copies(v, name)
    assert len(copies) == 1, copies
    copy = copies[0]
    assert re.fullmatch(re.escape(name) + r"\.replaced-\d{8}T\d{6}Z", copy.name)
    assert copy.read_text() == LOCAL_CONTENT[name]
    st = copy.stat()
    assert (st.st_uid, st.st_gid) == (before.st_uid, before.st_gid)
    assert stat.S_IMODE(st.st_mode) == stat.S_IMODE(before.st_mode) == 0o600
    warnings = _warnings(r.stderr)
    assert len(warnings) == 1 and str(copy) in warnings[0], r.stderr
    assert (v.ai / ".claude" / name).read_text() == SHARED_CONTENT[name]


@pytest.mark.parametrize("name", FILE_ENTRIES)
def test_same_content_copy_is_not_made_twice(tmp_path, name):
    """受け入れ条件 4・5 / I3"""
    v = Volumes(tmp_path)
    p = _prepare(v, name, LOCAL_CONTENT[name])
    assert _stage(v).returncode == 0
    assert len(_copies(v, name)) == 1
    p.unlink()
    p.write_text(LOCAL_CONTENT[name])
    r = _stage(v, "1")
    assert r.returncode == 0, r.stderr
    _assert_linked(v, name)
    assert len(_copies(v, name)) == 1
    assert _warnings(r.stderr) == []


@pytest.mark.parametrize("name", FILE_ENTRIES)
def test_existing_copies_and_shared_content_are_left_alone(tmp_path, name):
    """受け入れ条件 7 / I4: 共通のボリュームと既にある控えを書き換えず、消さない"""
    v = Volumes(tmp_path)
    _prepare(v, name, LOCAL_CONTENT[name])
    old = v.grp / ".claude" / f"{name}.replaced-20200101T000000Z"
    old.write_text("old copy")
    shared = {n: (v.ai / ".claude" / n).read_bytes() for n in FILE_ENTRIES}
    r = _stage(v)
    assert r.returncode == 0, r.stderr
    assert old.read_text() == "old copy"
    assert len(_copies(v, name)) == 2
    assert {n: (v.ai / ".claude" / n).read_bytes() for n in FILE_ENTRIES} == shared


@NOT_ROOT
def test_unwritable_claude_dir_keeps_the_file_and_goes_on(tmp_path):
    """受け入れ条件 6 / I6"""
    v = Volumes(tmp_path)
    p = _prepare(v, "settings.json", LOCAL_CONTENT["settings.json"])
    claude = v.grp / ".claude"
    claude.chmod(0o555)
    try:
        r = _stage(v)
    finally:
        claude.chmod(0o755)
    assert r.returncode == 0, r.stderr
    assert not p.is_symlink()
    assert p.read_text() == LOCAL_CONTENT["settings.json"]
    assert _copies(v, "settings.json") == []
    warnings = _warnings(r.stderr)
    assert len(warnings) == 1 and str(p) in warnings[0], r.stderr
    for other in ("plugins", "skills", "commands", "CLAUDE.md"):
        _assert_linked(v, other)


@NEEDS_FLOCK
def test_concurrent_stages_make_exactly_one_copy(tmp_path):
    """受け入れ条件 8 / I3"""
    for rnd in range(ROUNDS):
        v = Volumes(tmp_path / f"r{rnd}")
        _prepare(v, "settings.json", LOCAL_CONTENT["settings.json"])
        jobs, _ = run_concurrent(v.base / "run", v.stage_jobs(2))
        assert_all_ok(jobs)
        _assert_linked(v, "settings.json")
        copies = _copies(v, "settings.json")
        assert len(copies) == 1, copies
        assert copies[0].read_text() == LOCAL_CONTENT["settings.json"]


def test_correct_links_produce_the_same_output_as_before(tmp_path):
    """受け入れ条件 9 / I7: 正しいリンクでは控えも WARNING も出ない"""
    v = Volumes(tmp_path)
    v.link_once()
    r = _stage(v)
    assert r.returncode == 0, r.stderr
    assert _warnings(r.stderr) == []
    assert "Removing existing" not in r.stdout
    for name in FILE_ENTRIES:
        assert _copies(v, name) == []


def test_real_plugins_directory_is_relinked_without_a_copy(tmp_path):
    """I7: ディレクトリの共有のリンクは今と同じく控えを作らずに張り直す"""
    v = Volumes(tmp_path)
    v.link_once()
    p = v.shared("plugins")
    p.unlink()
    p.mkdir()
    (p / "x.json").write_text("{}")
    r = _stage(v)
    assert r.returncode == 0, r.stderr
    _assert_linked(v, "plugins")
    assert list((v.grp / ".claude").glob("*.replaced-*")) == []
    assert _warnings(r.stderr) == []


@pytest.mark.parametrize("partner", ["removed-the-file", "made-the-same-copy"])
def test_failed_hard_link_is_judged_by_reading_again(tmp_path, partner):
    """決定 2 の読み直し: 相手が先に位置を消した・同じ中身の控えを先に作ったときは張る"""
    v = Volumes(tmp_path)
    p = _prepare(v, "settings.json", LOCAL_CONTENT["settings.json"])
    copy = v.grp / ".claude" / "settings.json.replaced-20261002T000000Z"
    if partner == "removed-the-file":
        act = f'command rm -f "{p}"'
    else:
        act = f'command cp -p "{p}" "{copy}"'
    # 控えのハードリンクを作る直前に相手が動いた状態を作る (ln -s は今のまま通す)
    pre = ('date() { echo 20261002T000000Z; }\n'
           f'ln() {{ if [ "$1" != "-sn" ]; then {act}; fi; command ln "$@"; }}')
    r = _stage(v, pre=pre)
    assert r.returncode == 0, r.stderr
    _assert_linked(v, "settings.json")
    assert _warnings(r.stderr) == []


def test_position_relinked_before_the_hard_link_leaves_no_copy(tmp_path):
    """決定 2 の読み直し: 通常のファイルと見た後、ハードリンクの前に相手が張り直したときは控えを残さない

    ロックなしで同時に走ると、相手が位置を symlink に張り直した直後に ``ln`` が走り、symlink そのものの
    ハードリンクが控えの名前で残る。控えは中身を持たないため消し、警告も出さない。
    """
    v = Volumes(tmp_path)
    p = _prepare(v, "settings.json", LOCAL_CONTENT["settings.json"])
    target = v.ai / ".claude" / "settings.json"
    act = f'command rm -f "{p}"; command ln -s "{target}" "{p}"'
    pre = ('date() { echo 20261002T000000Z; }\n'
           f'ln() {{ if [ "$1" != "-sn" ]; then {act}; fi; command ln "$@"; }}')
    r = _stage(v, pre=pre)
    assert r.returncode == 0, r.stderr
    _assert_linked(v, "settings.json")
    assert _copies(v, "settings.json") == []
    assert _warnings(r.stderr) == []
