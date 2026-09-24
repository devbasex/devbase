"""作り直しても残るシェルの設定の読み込み器 (PLAN70)

``containers/base/shellrc-dir.sh`` を対話シェル相当 (``shopt -s expand_aliases``) で
source し、置き場所に置いた ``*.sh`` の読まれ方を確かめる。Docker には依存しない
(``tests/containers/test_ai_cli_aliases.py`` と同じ方式)。

固定する契約:

- 置き場所は ``DEVBASE_SHELLRC_DIR``、空か未設定なら ``$HOME/.shellrc.d``
- 置き場所の直下の ``*.sh`` を名前の昇順で全部読む。``.`` で始まる名前・他の拡張子・
  ディレクトリは読まない。1 つの誤りで後ろを止めない
- 置き場所が無い・空でも何も出さず終了状態 0。``failglob`` / ``dotglob`` は利用者の
  状態のまま読み、読んだ後も残す
- 読み込み器の変数をシェルに残さない。外部コマンドもサブシェルも起動しない
- base イメージは ``~/.bashrc`` で ``ai-cli-aliases.sh`` の**後**に読み込み器を読み、
  ``DEVBASE_SHELLRC_DIR`` をイメージの ``ENV`` で示す
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[2] / "containers" / "base"
LOADER = BASE / "shellrc-dir.sh"
ALIASES = BASE / "ai-cli-aliases.sh"
DOCKERFILE = BASE / "Dockerfile"
#: PATH を空にして走らせる検査でも起動できるよう、bash は絶対パスで呼ぶ
BASH = shutil.which("bash") or "/bin/bash"


def _run(script: str, home: Path, env: dict | None = None,
         before: str = "", path: str | None = None) -> subprocess.CompletedProcess:
    """対話シェル相当で ``before`` → 読み込み器 → ``script`` の順に実行する。"""
    base = {k: v for k, v in os.environ.items() if not k.startswith("DEVBASE_")}
    base["HOME"] = str(home)
    if path is not None:
        base["PATH"] = path
    base.update(env or {})
    return subprocess.run(
        [BASH, "-c", f'shopt -s expand_aliases\n{before}\n. "{LOADER}"\n{script}'],
        capture_output=True, text=True, env=base,
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def rcdir(home: Path) -> Path:
    d = home / ".shellrc.d"
    d.mkdir()
    return d


def _alias(result: subprocess.CompletedProcess, name: str) -> str:
    for line in result.stdout.splitlines():
        if line.startswith(f"alias {name}="):
            return line
    return ""


# ===========================================================================
# 受け入れ条件 4: 名前の昇順で全部読む
# ===========================================================================

def test_files_are_read_in_name_order(home, rcdir):
    (rcdir / "20-b.sh").write_text("alias probe='echo b'\n")
    (rcdir / "10-a.sh").write_text("alias probe='echo a'\nalias only_a='echo a'\n")

    result = _run("probe; only_a", home)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["b", "a"]


# ===========================================================================
# 受け入れ条件 5: *.sh 以外・. で始まる名前・ディレクトリを読まない
# ===========================================================================

@pytest.mark.parametrize("before", ["", "shopt -s dotglob"])
def test_only_plain_sh_files_are_read(home, rcdir, before):
    (rcdir / "x.txt").write_text("echo read-txt\n")
    (rcdir / "README").write_text("echo read-readme\n")
    (rcdir / ".hidden.sh").write_text("echo read-hidden\n")
    (rcdir / "sub.sh").mkdir()
    (rcdir / "sub.sh" / "inner.sh").write_text("echo read-inner\n")
    (rcdir / "ok.sh").write_text("echo read-ok\n")

    result = _run("", home, before=before)

    assert result.stdout.splitlines() == ["read-ok"]
    assert result.stderr == ""


# ===========================================================================
# 受け入れ条件 6: 無い・空・一致なしで何も出さず 0
# ===========================================================================

@pytest.mark.parametrize("state", ["missing", "empty", "no-match"])
@pytest.mark.parametrize("before", ["", "shopt -s failglob"])
def test_missing_or_empty_directory_is_silent(home, state, before):
    if state != "missing":
        (home / ".shellrc.d").mkdir()
    if state == "no-match":
        (home / ".shellrc.d" / "x.txt").write_text("echo read-txt\n")

    result = _run('echo "rc=$?"', home, before=before)

    assert result.stdout == "rc=0\n"
    assert result.stderr == ""


# ===========================================================================
# 受け入れ条件 6a: 利用者の failglob / dotglob のまま読み、後にも残す
# ===========================================================================

SHOW = 'shopt -q failglob && echo fg=on || echo fg=off; shopt -q dotglob && echo dg=on || echo dg=off'


def test_glob_options_are_restored_before_reading(home, rcdir):
    (rcdir / "10-show.sh").write_text(f"{SHOW}\n")

    result = _run(SHOW, home, before="shopt -s failglob dotglob")

    assert result.stdout.splitlines() == ["fg=on", "dg=on", "fg=on", "dg=on"]


def test_option_set_by_a_file_is_kept(home, rcdir):
    (rcdir / "10-set.sh").write_text("shopt -s failglob\n")
    (rcdir / "20-show.sh").write_text("shopt -q failglob && echo fg=on || echo fg=off\n")

    result = _run("shopt -q failglob && echo fg=on || echo fg=off", home,
                  before="shopt -u failglob")

    assert result.stdout.splitlines() == ["fg=on", "fg=on"]


def test_options_off_stay_off(home, rcdir):
    (rcdir / "10-a.sh").write_text(":\n")

    result = _run(SHOW, home, before="shopt -u failglob dotglob")

    assert result.stdout.splitlines() == ["fg=off", "dg=off"]


# ===========================================================================
# 受け入れ条件 7: 誤りの後も続く
# ===========================================================================

def test_broken_file_does_not_stop_later_files(home, rcdir):
    (rcdir / "10-a.sh").write_text("echo read-a\n")
    (rcdir / "15-bad.sh").write_text("if then fi (\n")
    (rcdir / "20-b.sh").write_text("echo read-b\n")

    result = _run("", home)

    assert result.stdout.splitlines() == ["read-a", "read-b"]
    assert "15-bad.sh" in result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root は chmod 000 のファイルも読める")
def test_unreadable_file_is_skipped_and_later_files_continue(home, rcdir):
    (rcdir / "10-a.sh").write_text("echo read-a\n")
    noperm = rcdir / "20-noperm.sh"
    noperm.write_text("echo read-noperm\n")
    noperm.chmod(0o000)
    (rcdir / "30-b.sh").write_text("echo read-b\n")

    result = _run("", home)

    assert result.stdout.splitlines() == ["read-a", "read-b"]
    assert result.returncode == 0


@pytest.mark.parametrize("include_unreadable", [False, True])
def test_unreadable_and_dangling_files_are_silently_skipped(home, rcdir, include_unreadable):
    if include_unreadable and os.geteuid() == 0:
        pytest.skip("root は chmod 000 のファイルも読める")

    (rcdir / "10-a.sh").write_text("echo read-a\n")
    (rcdir / "17-dangling.sh").symlink_to(rcdir / "missing")
    (rcdir / "20-b.sh").write_text("echo read-b\n")
    unreadable = rcdir / "15-unreadable.sh"
    if include_unreadable:
        unreadable.write_text("echo read-u\n")
        unreadable.chmod(0o000)

    try:
        result = _run('echo "rc=$?"', home)

        assert result.stdout.splitlines() == ["read-a", "read-b", "rc=0"]
        assert result.stderr == ""
        assert result.returncode == 0
    finally:
        if include_unreadable:
            unreadable.chmod(0o644)


# ===========================================================================
# 受け入れ条件 8: 起動定義より勝つ
# ===========================================================================

def test_directory_alias_wins_over_launcher_definition(home, rcdir):
    (rcdir / "ndf-relay.sh").write_text("alias claude='echo relayed'\n")

    result = _run("alias claude", home, before=f'. "{ALIASES}"')

    assert _alias(result, "claude") == "alias claude='echo relayed'"


# ===========================================================================
# 受け入れ条件 9: 変数を残さない
# ===========================================================================

@pytest.mark.parametrize("populated", [False, True])
def test_loader_leaves_no_variables(home, populated):
    if populated:
        (home / ".shellrc.d").mkdir()
        (home / ".shellrc.d" / "a.sh").write_text(":\n")

    result = _run('echo "f=$f"; compgen -v __devbase_ || true', home, before="f=keep")

    assert result.stdout.splitlines() == ["f=keep"]


# ===========================================================================
# 受け入れ条件 10: 置き場所は DEVBASE_SHELLRC_DIR、空なら ~/.shellrc.d
# ===========================================================================

def test_variable_points_at_directory(home, rcdir, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.sh").write_text("echo from-variable\n")
    (rcdir / "a.sh").write_text("echo from-home\n")

    result = _run("", home, env={"DEVBASE_SHELLRC_DIR": str(other)})

    assert result.stdout.splitlines() == ["from-variable"]


def test_missing_variable_directory_does_not_fall_back_to_home(home, rcdir, tmp_path):
    (rcdir / "a.sh").write_text("echo from-home\n")

    result = _run('echo "rc=$?"', home,
                  env={"DEVBASE_SHELLRC_DIR": str(tmp_path / "missing")})

    assert result.stdout == "rc=0\n"
    assert result.stderr == ""


@pytest.mark.parametrize("env", [{}, {"DEVBASE_SHELLRC_DIR": ""}])
def test_empty_variable_falls_back_to_home(home, rcdir, env):
    (rcdir / "a.sh").write_text("echo from-home\n")

    result = _run("", home, env=env)

    assert result.stdout.splitlines() == ["from-home"]


def test_directory_that_is_a_symlink_is_followed(home, tmp_path):
    """置き場所が別ディレクトリへの symlink でも中の ``*.sh`` を読む。

    実配置では entrypoint.sh が置き場所をアカウントグループのボリュームへの
    symlink にする (``shellrc-dir.sh`` 冒頭のコメント)。``rcdir`` フィクスチャは
    実ディレクトリしか作らないため使わず、symlink 先を辿る経路を固定する。
    """
    volume = tmp_path / "volume"
    volume.mkdir()
    (volume / "a.sh").write_text("echo read-volume\n")
    (home / ".shellrc.d").symlink_to(volume)

    result = _run('echo "rc=$?"', home)

    assert result.stdout.splitlines() == ["read-volume", "rc=0"]
    assert result.stderr == ""


# ===========================================================================
# 非機能の性能: 外部コマンドもサブシェルも起動しない
# ===========================================================================

@pytest.mark.parametrize("populated", [False, True])
def test_loader_runs_without_path(home, populated):
    if populated:
        (home / ".shellrc.d").mkdir()
        (home / ".shellrc.d" / "a.sh").write_text("alias probe='echo p'\n")

    result = _run('echo "rc=$?"', home, path="")

    assert result.stdout == "rc=0\n"
    assert result.stderr == ""


def test_loader_has_no_command_substitution_or_pipe():
    statements = "\n".join(
        line for line in LOADER.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    for token in ("$(", "`", "|"):
        assert token not in statements, f"{token!r} を使っている"


# ===========================================================================
# 配置 (受け入れ条件 8 / 11 / 14): Dockerfile
# ===========================================================================

def test_dockerfile_copies_loader_into_etc_devbase():
    dockerfile = DOCKERFILE.read_text()
    mkdir = "RUN sudo install -d -m 0755 /etc/devbase"
    copy = "COPY --chmod=0644 shellrc-dir.sh /etc/devbase/shellrc-dir.sh"

    assert copy in dockerfile
    assert dockerfile.index(mkdir) < dockerfile.index(copy)


def test_dockerfile_declares_shellrc_dir_env():
    dockerfile = DOCKERFILE.read_text()

    assert re.search(r"^ENV DEVBASE_SHELLRC_DIR=/home/\$\{USERNAME\}/\.shellrc\.d$",
                     dockerfile, re.MULTILINE)


def test_bashrc_reads_loader_after_launcher_definitions():
    dockerfile = DOCKERFILE.read_text()
    aliases = "echo '. /etc/devbase/ai-cli-aliases.sh' >> ~/.bashrc"
    loader = "echo '. /etc/devbase/shellrc-dir.sh' >> ~/.bashrc"

    assert loader in dockerfile
    assert dockerfile.index(aliases) < dockerfile.index(loader)


def test_dockerfile_does_not_touch_zshrc_for_loader():
    """zsh は対象外 (設計の決定 5)。読み込み器を ~/.zshrc へ書かない。"""
    for line in DOCKERFILE.read_text().splitlines():
        if "shellrc" in line:
            assert ".zshrc" not in line, line
