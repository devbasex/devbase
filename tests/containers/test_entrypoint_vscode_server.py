"""VS Code Server の本体の置き場の用意と片付け (PLAN36 / #463)

``~/.vscode-server/bin`` には全コンテナ共通の named volume が付く (#463)。空の
named volume は **root 所有**で付き、マウント先の親 ``~/.vscode-server`` も Docker が
root 所有で作るため、開発ユーザー (uid 1000) のままでは VS Code Server を置けない。
コンテナの起動は、共通の ``bin/`` の旧版・同時の展開の入れ子の本体・古い展開の
途中のディレクトリを片付け、版の数を残す数 (5) に収める。

``containers/base/entrypoint.sh`` を ``DEVBASE_ENTRYPOINT_LIB_ONLY=1`` で source し、
一時ディレクトリを ``$HOME`` に見立てて関数を直接呼ぶ。Docker には依存しない。
root 所有からの ``chown`` そのものは実機の検証手順 (AC5) で確かめる。
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "containers" / "base" / "entrypoint.sh"


def run(script: str, cwd: Path) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("DEVBASE_", "GIT_"))}
    full = f'set -e\nDEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"\n{script}\n'
    return subprocess.run(
        ["bash", "-c", full], cwd=cwd, env=base, capture_output=True, text=True)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


def setup(home: Path) -> subprocess.CompletedProcess:
    result = run(f'devbase_setup_vscode_server_dir "{home}" "$(id -un)"', home)
    assert result.returncode == 0, result.stderr or result.stdout
    return result


def test_directory_is_prepared(home: Path):
    """~/.vscode-server と bin が開発ユーザーの書ける形で用意される (受け入れ条件 10)。"""
    setup(home)

    for path in (home / ".vscode-server", home / ".vscode-server" / "bin"):
        assert path.is_dir()
        assert os.access(path, os.W_OK)


def test_existing_contents_are_kept(home: Path):
    """2 回目以降は何も壊さない。再ダウンロードを避ける前提そのもの (AC1)。"""
    server = home / ".vscode-server"
    version = make_version(server / "bin", 0)
    (version / "marker").write_text("kept")

    setup(home)

    assert (version / "marker").read_text() == "kept"


def test_is_idempotent(home: Path):
    """繰り返し呼んでも失敗しない (entrypoint は起動のたびに走る)。"""
    setup(home)
    setup(home)

    assert (home / ".vscode-server").is_dir()


def test_writable_directory_does_not_invoke_sudo(home: Path):
    """既に書けるなら sudo を呼ばない。sudo の無い環境でも起動を落とさない。"""
    (home / ".vscode-server").mkdir()

    # PATH の先頭へ「呼ばれたら失敗する sudo」を置いて、呼び出しの有無を見る
    fake_bin = home.parent / "bin"
    fake_bin.mkdir()
    sudo = fake_bin / "sudo"
    sudo.write_text("#!/bin/sh\necho 'sudo was called' >&2\nexit 1\n")
    sudo.chmod(0o755)

    result = subprocess.run(
        ["bash", "-c",
         f'set -e\nDEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"\n'
         f'devbase_setup_vscode_server_dir "{home}" "$(id -un)"\n'],
        cwd=home,
        env={**{k: v for k, v in os.environ.items()
                if not k.startswith(("DEVBASE_", "GIT_"))},
             "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
        capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "sudo was called" not in result.stderr


# ---------------------------------------------------------------------------
# bin/ の片付け (受け入れ条件 7〜9、I6〜I9)
# ---------------------------------------------------------------------------

KEEP = 5
NOW = time.time()


def commit(n: int) -> str:
    return f"{n:040x}"


def make_version(bin_dir: Path, n: int, age_days: float = 0.0) -> Path:
    """本体の版 bin/<commit> を作る。product.json の時刻を age_days 日だけ古くする。"""
    version = bin_dir / commit(n)
    (version / "bin").mkdir(parents=True, exist_ok=True)
    (version / "bin" / "code-server").write_text("#!/bin/sh\n")
    product = version / "product.json"
    product.write_text("{}")
    stamp = NOW - age_days * 86400
    os.utime(product, (stamp, stamp))
    return version


def prune(home: Path, extra: str = "") -> subprocess.CompletedProcess:
    bin_dir = home / ".vscode-server" / "bin"
    result = run(f'devbase_prune_vscode_server_bin "{bin_dir}" {extra}', home)
    assert result.returncode == 0, result.stderr
    return result


def versions(bin_dir: Path) -> set[str]:
    return {p.name for p in bin_dir.iterdir() if (p / "product.json").is_file()}


@pytest.fixture
def bin_dir(home: Path) -> Path:
    path = home / ".vscode-server" / "bin"
    path.mkdir(parents=True)
    return path


def test_old_versions_beyond_keep_are_removed(home: Path, bin_dir: Path):
    """8 版あれば product.json の新しい 5 版だけが残る (受け入れ条件 7)。

    ディレクトリの更新時刻ではなく product.json の時刻で並べることを、
    ディレクトリの時刻を逆向きにして確かめる。
    """
    for n in range(8):
        version = make_version(bin_dir, n, age_days=30 * n)
        stamp = NOW - 30 * (8 - n) * 86400
        os.utime(version, (stamp, stamp))

    result = prune(home)

    assert versions(bin_dir) == {commit(n) for n in range(KEEP)}
    assert "WARNING" not in result.stderr


def test_same_time_is_ordered_by_name(home: Path, bin_dir: Path):
    """時刻が同じ版は名前の順で残す (同時に走る片付けどうしで並びが同じになる)。"""
    for n in range(7):
        make_version(bin_dir, n, age_days=1)

    prune(home)

    assert versions(bin_dir) == {commit(n) for n in range(KEEP)}


def test_nothing_is_removed_within_keep(home: Path, bin_dir: Path):
    """版が残す数以下なら何も消さない。"""
    for n in range(KEEP):
        make_version(bin_dir, n, age_days=n)

    prune(home)

    assert versions(bin_dir) == {commit(n) for n in range(KEEP)}


def test_concurrent_prunes_keep_exactly_keep_versions(home: Path, bin_dir: Path):
    """2 つの片付けを同時に走らせても、残る版は 5 つで、WARNING は出ない (I6・I9)。"""
    for n in range(8):
        make_version(bin_dir, n, age_days=30 * n)
        (bin_dir / commit(n) / "bin" / "payload").mkdir()
        for i in range(50):
            (bin_dir / commit(n) / "bin" / "payload" / f"f{i}").write_text("x")

    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("DEVBASE_", "GIT_"))}
    script = (f'DEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"\n'
              f'devbase_prune_vscode_server_bin "{bin_dir}"\n')
    procs = [subprocess.Popen(["bash", "-c", script], env=base,
                              stderr=subprocess.PIPE, text=True)
             for _ in range(2)]
    errors = [p.communicate()[1] for p in procs]

    assert [p.returncode for p in procs] == [0, 0]
    assert versions(bin_dir) == {commit(n) for n in range(KEEP)}
    assert not [e for e in errors if "WARNING" in e]


def test_entries_that_are_not_versions_are_not_counted(home: Path, bin_dir: Path):
    """product.json の無いディレクトリ・40 桁でない名前は版に数えず、消さない (I8)。"""
    for n in range(KEEP):
        make_version(bin_dir, n, age_days=n)
    (bin_dir / commit(99)).mkdir()  # product.json が無い
    other = bin_dir / "not-a-version"
    other.mkdir()
    (other / "product.json").write_text("{}")
    (bin_dir / "note.txt").write_text("kept")

    prune(home)

    assert versions(bin_dir) == {commit(n) for n in range(KEEP)} | {"not-a-version"}
    assert (bin_dir / commit(99)).is_dir()
    assert (bin_dir / "note.txt").is_file()


def test_nested_server_is_removed(home: Path, bin_dir: Path):
    """同時の展開で残った入れ子の本体だけが消え、本体は残る (受け入れ条件 8・I7)。"""
    version = make_version(bin_dir, 0)
    nested = version / "vscode-server-linux-arm64"
    (nested / "bin").mkdir(parents=True)
    (nested / "product.json").write_text("{}")
    (version / "node_modules").mkdir()  # product.json の無い直下のものは残る

    prune(home)

    assert not nested.exists()
    assert (version / "bin" / "code-server").is_file()
    assert (version / "product.json").is_file()
    assert (version / "node_modules").is_dir()


def test_nested_is_kept_when_version_has_no_product_json(home: Path, bin_dir: Path):
    """bin/<commit>/ 自身に product.json が無いときは入れ子を消さない。"""
    version = bin_dir / commit(0)
    nested = version / "vscode-server-linux-arm64"
    nested.mkdir(parents=True)
    (nested / "product.json").write_text("{}")

    prune(home)

    assert (nested / "product.json").is_file()


def test_only_stale_extraction_dirs_are_removed(home: Path, bin_dir: Path):
    """60 分より古い bin/<commit>_<数字> だけが消え、新しいものは残る (I8)。"""
    stale = bin_dir / f"{commit(1)}_1760000000000"
    fresh = bin_dir / f"{commit(2)}_1760000000001"
    for path in (stale, fresh):
        (path / "vscode-server-linux-arm64").mkdir(parents=True)
    old = NOW - 61 * 60
    os.utime(stale, (old, old))
    odd = bin_dir / "keep_123"
    odd.mkdir()
    os.utime(odd, (old, old))

    prune(home)

    assert not stale.exists()
    assert fresh.is_dir()
    assert odd.is_dir()


def test_outside_of_bin_is_untouched(home: Path, bin_dir: Path):
    """~/.vscode-server の bin 以外 (extensions/・extensionsCache/・data/) に触らない。"""
    server = home / ".vscode-server"
    for n in range(8):
        make_version(bin_dir, n, age_days=n)
    for name in ("extensions", "extensionsCache", "data"):
        (server / name / commit(0)).mkdir(parents=True)
        (server / name / commit(0) / "product.json").write_text("{}")

    setup(home)

    for name in ("extensions", "extensionsCache", "data"):
        assert (server / name / commit(0) / "product.json").is_file()


def test_missing_bin_does_nothing(home: Path):
    """bin が無ければ何もせず 0 で終わる。"""
    prune(home)

    assert not (home / ".vscode-server").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root は書けないディレクトリも消せる")
def test_unremovable_path_warns_and_does_not_fail(home: Path, bin_dir: Path):
    """消せないものがあっても 0 で終わり、そのパスを WARNING に 1 行出す (受け入れ条件 9)。"""
    for n in range(KEEP + 1):
        make_version(bin_dir, n, age_days=n)
    oldest = bin_dir / commit(KEEP)
    locked = oldest / "bin"
    locked.chmod(0o555)
    try:
        result = prune(home)
    finally:
        locked.chmod(0o755)

    warnings = [line for line in result.stderr.splitlines() if "WARNING" in line]
    assert len(warnings) == 1
    assert str(oldest) in warnings[0]


def test_setup_survives_prune_warning(home: Path, bin_dir: Path):
    """片付けの WARNING があってもコンテナの起動の関数は 0 で終わる。"""
    if os.geteuid() == 0:
        pytest.skip("root は書けないディレクトリも消せる")
    for n in range(KEEP + 1):
        make_version(bin_dir, n, age_days=n)
    locked = bin_dir / commit(KEEP) / "bin"
    locked.chmod(0o555)
    try:
        result = run(
            f'devbase_setup_vscode_server_dir "{home}" "$(id -un)"\necho done', home)
    finally:
        locked.chmod(0o755)

    assert result.returncode == 0, result.stderr
    assert "done" in result.stdout
