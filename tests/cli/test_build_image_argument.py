"""PLAN49 (#139): `devbase build <image>` 単体ビルドのテスト。

検証対象:
  - wrapper (bin/devbase) の `build)` dispatch: 位置引数 `<image>` と `--expires` は
    Python (project build) へ、フラグのみの経路は shell の cmd_build へ振り分ける。
  - Python `container.cmd_build(image=...)`: `containers/<image>` を
    `devbase-<image>:latest` として単体ビルドする docker 引数列を組み立てる。

修正前は `<image>` が剥がされないまま shell の cmd_build へ流れ、
`docker buildx build ... <context> <image>` と PATH が 2 つになって必ず失敗した。

wrapper テストは実際の `uv run` を避けるため run_python / cmd_build / compose_with_secrets
をスタブへ差し替え、DEVBASE_ROOT を一時ディレクトリへ向けた薄いハーネスで dispatch だけを
実行する (wrapper 冒頭の DEVBASE_ROOT 自動解決行も sed で除去する)。
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import pytest

from devbase.commands import container

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "bin" / "devbase"


# ===========================================================================
# wrapper: build の振り分け (位置引数 / --expires / フラグのみ)
# ===========================================================================

def _run_wrapper(args, devbase_root, cwd=None):
    """run_python / cmd_build / compose_with_secrets をスタブ化して dispatch だけ実行する。

    - run_python           -> "PYTHON:<args>" を出力して終了
    - cmd_build            -> "BUILD:<args>" を出力して終了
    - compose_with_secrets -> "COMPOSE:<args>" (cmd_build へ入った場合の保険)
    """
    harness = (
        'run_python() { echo "PYTHON:$*"; exit 0; }\n'
        'cmd_build() { echo "BUILD:$*"; exit 0; }\n'
        'compose_with_secrets() { echo "COMPOSE:$*"; exit 0; }\n'
        'ensure_uv() { :; }\n'
        'eval "$(sed -e \'/^run_python()/,/^}/d\' '
        '            -e \'/^ensure_uv()/,/^}/d\' '
        '            -e \'/^cmd_build()/,/^}/d\' '
        '            -e \'/^compose_with_secrets()/,/^}/d\' '
        '            -e \'/^DEVBASE_ROOT=/d\' "$WRAPPER_PATH")"\n'
    )
    env = {
        **os.environ,
        "DEVBASE_ROOT": str(devbase_root),
        "WRAPPER_PATH": str(WRAPPER),
    }
    return subprocess.run(
        ["bash", "-c", harness, "devbase", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd or REPO_ROOT),
    )


def _line(result, prefix):
    for line in result.stdout.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


@pytest.fixture
def wrapper_root(tmp_path):
    """`containers/base` を持ち、`projects/` は空の DEVBASE_ROOT。

    `projects/` を空にするのは、wrapper 冒頭の name 解決 (実在プロジェクト名なら cd して
    引数を除去する) を発火させないためである。`base` が name 解決へ吸われると、この
    テストが検証したい dispatch まで引数が届かない。
    """
    (tmp_path / "containers" / "base").mkdir(parents=True)
    (tmp_path / "containers" / "base" / "Dockerfile").write_text("FROM ubuntu:26.04\n")
    (tmp_path / "projects").mkdir()
    return tmp_path


def test_wrapper_routes_build_image_to_python(wrapper_root):
    """`devbase build base` は Python の project build へ渡り、image が保たれる。"""
    result = _run_wrapper(["build", "base"], wrapper_root)
    assert _line(result, "PYTHON:") == "project build base"
    # shell の compose ビルド経路へ落ちない (AC2)
    assert _line(result, "BUILD:") is None
    assert _line(result, "COMPOSE:") is None


def test_wrapper_routes_build_image_no_cache_to_python(wrapper_root):
    """`--no-cache` を伴っても image 指定は Python 経路で、引数の順序が保たれる。"""
    result = _run_wrapper(["build", "base", "--no-cache"], wrapper_root)
    assert _line(result, "PYTHON:") == "project build base --no-cache"
    assert _line(result, "BUILD:") is None


def test_wrapper_routes_bare_build_to_shell(wrapper_root):
    """image 省略・フラグなしは shell の cmd_build (2 段の compose ビルド)。"""
    result = _run_wrapper(["build"], wrapper_root)
    assert _line(result, "BUILD:") == ""
    assert _line(result, "PYTHON:") is None


def test_wrapper_routes_build_no_cache_to_shell(wrapper_root):
    """`devbase build --no-cache` は shell 経路のまま (退行防止)。"""
    result = _run_wrapper(["build", "--no-cache"], wrapper_root)
    assert _line(result, "BUILD:") == "--no-cache"
    assert _line(result, "PYTHON:") is None


def test_wrapper_routes_build_project_no_cache_to_shell(wrapper_root):
    """`--project-no-cache` は shell 経路のまま。

    Python の `_run_build(project_no_cache=True)` がこの形で wrapper を呼び戻すため、
    ここが Python へ振り分けられると shell と Python の間で再帰する。
    """
    result = _run_wrapper(["build", "--project-no-cache"], wrapper_root)
    assert _line(result, "BUILD:") == "--project-no-cache"
    assert _line(result, "PYTHON:") is None


@pytest.mark.parametrize("flag", ["--expires", "--expires=7"])
def test_wrapper_routes_build_expires_to_python(wrapper_root, flag):
    """`--expires` は作成日判定のため Python 経路 (既存仕様の維持)。"""
    result = _run_wrapper(["build", flag], wrapper_root)
    assert _line(result, "PYTHON:") == f"project build {flag}"
    assert _line(result, "BUILD:") is None


def test_wrapper_routes_build_image_with_expires_to_python(wrapper_root):
    """image と `--expires` の併用も Python へ渡し、警告は Python 側で出す。"""
    result = _run_wrapper(["build", "base", "--expires=7"], wrapper_root)
    assert _line(result, "PYTHON:") == "project build base --expires=7"


# ===========================================================================
# Python: cmd_build(image=...) が組み立てる docker 引数列
# ===========================================================================

@pytest.fixture
def devbase_root(tmp_path, monkeypatch):
    (tmp_path / "containers" / "base").mkdir(parents=True)
    (tmp_path / "containers" / "base" / "Dockerfile").write_text("FROM ubuntu:26.04\n")
    monkeypatch.setenv("DEVBASE_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def captured_run(monkeypatch):
    """`container.subprocess.run` を差し替え、渡された引数列を記録する。"""
    calls = []

    class _Result:
        returncode = 0

    def _fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return _Result()

    monkeypatch.setattr(container.subprocess, "run", _fake_run)
    return calls


def test_single_build_uses_devbase_prefixed_tag(devbase_root, captured_run):
    """単体ビルドは `devbase-<image>:latest` を buildx で作る。

    `<image>:latest` では他の Dockerfile の `FROM devbase-base:latest` から解決できず、
    ビルドしても使われない。
    """
    rc = container.cmd_build(image="base")

    assert rc == 0
    assert captured_run == [[
        "docker", "buildx", "build", "--load",
        "-t", "devbase-base:latest",
        str(devbase_root / "containers" / "base"),
    ]]


def test_single_build_appends_no_cache(devbase_root, captured_run):
    """`--no-cache` はコンテキストパスの後ろに 1 つだけ足す。"""
    container.cmd_build(image="base", no_cache=True)

    assert captured_run[0][-1] == "--no-cache"
    assert captured_run[0].count("--no-cache") == 1
    # コンテキストパスは 1 つだけ (issue #139 の PATH 2 つ問題の回帰防止)
    assert captured_run[0].count(str(devbase_root / "containers" / "base")) == 1


def test_single_build_tag_maps_one_to_one_to_directory(devbase_root, captured_run):
    """タグは `containers/` 配下のディレクトリ名と 1:1 に対応する。

    接頭辞を剥がすと `containers/xxx` と `containers/devbase-xxx` が同じタグを取り合い、
    別ディレクトリなのに互いのイメージを上書きするため、剥がさずそのまま前置する。
    """
    for name in ("xxx", "devbase-xxx"):
        (devbase_root / "containers" / name).mkdir()
        (devbase_root / "containers" / name / "Dockerfile").write_text("FROM x\n")

    container.cmd_build(image="xxx")
    container.cmd_build(image="devbase-xxx")

    tags = [cmd[cmd.index("-t") + 1] for cmd in captured_run]
    assert tags == ["devbase-xxx:latest", "devbase-devbase-xxx:latest"]
    assert len(set(tags)) == 2


def test_single_build_missing_directory_fails(devbase_root, captured_run, caplog):
    """存在しないイメージ名は非 0 で終わり、探したパスを出す。docker は起動しない。"""
    with caplog.at_level(logging.ERROR):
        rc = container.cmd_build(image="nosuchimage")

    assert rc == 1
    assert captured_run == []
    assert str(devbase_root / "containers" / "nosuchimage") in caplog.text


def test_single_build_missing_dockerfile_fails(devbase_root, captured_run, caplog):
    """Dockerfile が無い場合も非 0 で終わり、docker は起動しない。"""
    (devbase_root / "containers" / "nodockerfile").mkdir()

    with caplog.at_level(logging.ERROR):
        rc = container.cmd_build(image="nodockerfile")

    assert rc == 1
    assert captured_run == []
    assert "Dockerfile" in caplog.text


def test_single_build_without_devbase_root_fails(monkeypatch, captured_run, caplog):
    """DEVBASE_ROOT 未設定は非 0 で終わる。"""
    monkeypatch.delenv("DEVBASE_ROOT", raising=False)

    with caplog.at_level(logging.ERROR):
        rc = container.cmd_build(image="base")

    assert rc == 1
    assert captured_run == []


def test_single_build_propagates_docker_exit_code(devbase_root, monkeypatch):
    """docker の終了コードをそのまま返す。"""
    class _Result:
        returncode = 42

    monkeypatch.setattr(container.subprocess, "run", lambda *a, **k: _Result())

    assert container.cmd_build(image="base") == 42


def test_single_build_ignores_expires_with_warning(devbase_root, captured_run, caplog):
    """`--expires` は単体ビルドの対象外。警告を出したうえで単体ビルドする。"""
    with caplog.at_level(logging.WARNING):
        rc = container.cmd_build(image="base", expires=7)

    assert rc == 0
    assert "--expires" in caplog.text
    # 期限判定 (docker image inspect) を挟まず、ビルド 1 回だけ
    assert len(captured_run) == 1
    assert captured_run[0][:4] == ["docker", "buildx", "build", "--load"]


@pytest.mark.parametrize("bad_image", [
    "../etc",
    "base/../../etc",
    "a/b",
    "..",
    ".",
    "",
    "..\\etc",
    "-base",
])
def test_single_build_rejects_invalid_image_name(
        devbase_root, captured_run, caplog, bad_image):
    """ディレクトリ名として不正な `image` は docker を起動せず 1 で終わる。

    `/` や `\\`、`..` を通すと $DEVBASE_ROOT の外を指せてしまい、Docker タグとしても
    不正な名前を渡せてしまうため、パス組み立ての前に弾く (PR #144 のレビュー指摘)。
    """
    with caplog.at_level(logging.ERROR):
        rc = container.cmd_build(image=bad_image)

    assert rc == 1
    assert captured_run == []
    assert "Invalid image name" in caplog.text


def test_cli_project_build_rejects_traversal_image(devbase_root, captured_run, monkeypatch, caplog):
    """受け入れ条件 1 (単体): `python -m devbase.cli project build ../etc` は docker を起動せず 1。"""
    from devbase import cli

    monkeypatch.setattr("sys.argv", ["devbase", "project", "build", "../etc"])
    with caplog.at_level(logging.ERROR):
        assert cli.main() == 1

    assert captured_run == []
    assert "Invalid image name" in caplog.text


def test_single_build_accepts_real_container_directory_names(devbase_root, captured_run):
    """`containers/` 配下の実在ディレクトリ名は検証を通る。"""
    names = ["base", "bi-tools", "general", "go", "latex",
             "lfm", "php", "php85", "snapshot", "trygroup"]
    for name in names:
        d = devbase_root / "containers" / name
        d.mkdir(exist_ok=True)
        (d / "Dockerfile").write_text("FROM x\n")

    for name in names:
        assert container.cmd_build(image=name) == 0

    tags = [cmd[cmd.index("-t") + 1] for cmd in captured_run]
    assert tags == [f"devbase-{name}:latest" for name in names]


# ===========================================================================
# wrapper (実プロセス): `build --help` / `-h` (PLAN61 / #196)
#
# `exec_wrapper` (conftest.py) は bin/devbase を tmp へ複製して起動し、`uv` だけを PATH で
# 差し替える。cmd_build は本物のまま動くので `=== Building devbase images ===` が出ないことを
# 確かめられる。run_python も docker も `uv` を通るため、`UV:` が無いことで両方を確かめる。
# ===========================================================================

from tests.cli.conftest import stdout_field  # noqa: E402

BUILD_USAGE_TOKENS = ["--no-cache", "--project-no-cache", "--expires[=DAYS]", "--context NAME",
                      "<image>"]


def _assert_build_usage(result):
    assert result.returncode == 0, result.stderr
    assert "=== Building devbase images ===" not in result.stdout
    assert stdout_field(result, "UV:") is None, result.stdout
    assert "Usage: devbase build" in result.stdout
    for token in BUILD_USAGE_TOKENS:
        assert token in result.stdout, f"{token!r} が使い方に無い:\n{result.stdout}"


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_wrapper_build_help_prints_usage_without_building(exec_wrapper, flag):
    """受け入れ条件 9・10: `build --help` / `-h` は終了コード 0 で使い方を出し、ビルドしない。"""
    _assert_build_usage(exec_wrapper(["build", flag]))


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_wrapper_build_name_help_does_not_cd_or_read_env(exec_wrapper, flag):
    """受け入れ条件 11: `build carmo --help` も cd せず、carmo の env を読まずに使い方を出す。

    `projects/carmo/env` に `echo CARMO_ENV_READ >&2` を置く。wrapper は env を source するため、
    この行は cd と読み込みが起きたときだけ stderr に出る。
    """
    exec_wrapper.project("carmo", env="echo CARMO_ENV_READ >&2\n")

    r = exec_wrapper(["build", "carmo", flag])

    _assert_build_usage(r)
    assert stdout_field(r, "PWD:") is None, r.stdout
    assert "CARMO_ENV_READ" not in r.stderr, r.stderr


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_wrapper_build_context_followed_by_help_is_usage(exec_wrapper, flag):
    """決定 8: `--context --help` の `--help` は context の値ではなく使い方。"""
    _assert_build_usage(exec_wrapper(["build", "--context", flag]))


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_wrapper_build_context_equals_help_is_not_usage(exec_wrapper, flag):
    """決定 8: `--context=--help` は使い方にせず、値としてそのまま下流へ渡す。

    実際の argparse では値不足の usage エラー (終了コード 2) になる。`-` 始まりの context 名は
    受け付けない。
    """
    r = exec_wrapper(["build", f"--context={flag}"])

    assert "Usage: devbase build" not in r.stdout
    uv = stdout_field(r, "UV:")
    assert uv is not None and f"env exec --context {flag} --" in uv, r.stdout


# ===========================================================================
# wrapper (実プロセス): `build <x>` の名前の形とイメージ / プロジェクトの衝突 (PLAN61 / #146 #142)
# ===========================================================================

def test_wrapper_build_traversal_does_not_cd_or_read_outside_env(exec_wrapper):
    """受け入れ条件 1: `build ../etc` は `$DEVBASE_ROOT/etc` へ cd せず、そこの env を読まない。

    形に合わない値は名前として扱わず、そのまま Python の単体ビルドへ渡す。Python 側の
    `Invalid image name` で終了コード 1 になる (`test_cli_project_build_rejects_traversal_image`)。
    """
    exec_wrapper.etc_env()

    r = exec_wrapper(["build", "../etc"])

    assert "=== Building devbase images ===" not in r.stdout
    assert stdout_field(r, "PWD:") == str(exec_wrapper.work), r.stdout
    assert stdout_field(r, "MARKER:") == "<unset>", r.stdout
    uv = stdout_field(r, "UV:")
    assert uv is not None and uv.endswith(" devbase.cli project build ../etc"), r.stdout


def test_wrapper_build_image_wins_over_same_named_project_and_notes(exec_wrapper):
    """受け入れ条件 6: containers/ と projects/ の両方にある名前はイメージ。知らせを stderr に 1 行。"""
    exec_wrapper.container("bi-tools")
    exec_wrapper.project("bi-tools")

    r = exec_wrapper(["build", "bi-tools", "--no-cache"])

    uv = stdout_field(r, "UV:")
    assert uv is not None and uv.endswith(" devbase.cli project build bi-tools --no-cache"), r.stdout
    assert stdout_field(r, "PWD:") == str(exec_wrapper.work), r.stdout
    notes = [line for line in r.stderr.splitlines() if line.strip()]
    assert len(notes) == 1, r.stderr
    assert "projects/bi-tools" in notes[0] and "devbase build" in notes[0], r.stderr
    assert str(exec_wrapper.root / "projects" / "bi-tools") in notes[0], r.stderr


def test_wrapper_build_project_only_name_cds_and_builds_project(exec_wrapper):
    """受け入れ条件 7: projects/ にだけある名前は今と同じくプロジェクトのビルド (cmd_build)。"""
    exec_wrapper.project("carmo")

    r = exec_wrapper(["build", "carmo"])

    assert "=== Building devbase images ===" in r.stdout, r.stdout
    assert stdout_field(r, "PWD:") == str(exec_wrapper.root / "projects" / "carmo"), r.stdout
    assert r.stderr.strip() == "", r.stderr


def test_wrapper_build_container_only_name_has_no_note(exec_wrapper):
    """受け入れ条件 8: containers/ にだけある名前は今と同じく単体ビルドで、知らせは出ない。"""
    exec_wrapper.container("go")

    r = exec_wrapper(["build", "go"])

    uv = stdout_field(r, "UV:")
    assert uv is not None and uv.endswith(" devbase.cli project build go"), r.stdout
    assert r.stderr.strip() == "", r.stderr


def test_wrapper_routes_no_cache_before_image_to_python(wrapper_root):
    """現状固定: フラグが image の前に来ても Python 経路で、引数の順序が保たれる。"""
    result = _run_wrapper(["build", "--no-cache", "base"], wrapper_root)
    assert result.returncode == 0
    assert _line(result, "PYTHON:") == "project build --no-cache base"
    assert _line(result, "BUILD:") is None
    assert _line(result, "COMPOSE:") is None
