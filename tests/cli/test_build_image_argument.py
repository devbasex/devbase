"""PLAN49 (#139): `devbase build <image>` 単体ビルドのテスト。

検証対象:
  - wrapper (bin/devbase) の `build)` dispatch: 位置引数 `<image>` と `--expires` は
    Python (project build) へ、フラグのみの経路は shell の cmd_build へ振り分ける。
  - Python `container.cmd_build(image=...)`: `containers/<image>` を
    `devbase-<image>:latest` として単体ビルドする docker 引数列を組み立てる。

修正前は `<image>` が剥がされないまま shell の cmd_build へ流れ、
`docker buildx build ... <context> <image>` と PATH が 2 つになって必ず失敗した。

wrapper の節はすべて `exec_wrapper` (conftest.py) で動く。本物の bin/devbase を tmp へ複製して
起動し、外への呼び出しの境界の `uv` だけを差し替える。Python の経路は ` devbase.cli project build
<引数>` で終わる `UV:` 行で、shell の経路は `=== Building devbase images ===` の行で見分ける。
"""

from __future__ import annotations

import logging

import pytest

from devbase.commands import container
from tests.cli.conftest import stdout_field

BUILDING = "=== Building devbase images ==="
PY_BUILD = " devbase.cli project build"


# ===========================================================================
# wrapper: build の振り分け (位置引数 / --expires / フラグのみ)
# ===========================================================================

def _uv_lines(result):
    """標準出力の `UV:` 行の残りをすべて返す。shell の経路は 2 行以上を出す。"""
    return [line[len("UV:"):] for line in result.stdout.splitlines() if line.startswith("UV:")]


def _assert_python_route(result, args):
    """Python の経路: ` devbase.cli project build <args>` で終わる `UV:` 行があり、shell に入らない。"""
    assert result.returncode == 0, result.stderr
    expected = " ".join([PY_BUILD, *args])
    assert any(uv.endswith(expected) for uv in _uv_lines(result)), result.stdout
    assert BUILDING not in result.stdout, result.stdout


def _assert_shell_route(result):
    """shell の経路: `=== Building` が出て、Python の project build を呼ばない。UV: 行を返す。"""
    assert result.returncode == 0, result.stderr
    assert BUILDING in result.stdout, result.stdout
    uvs = _uv_lines(result)
    assert not any(uv.endswith(PY_BUILD) or f"{PY_BUILD} " in uv for uv in uvs), result.stdout
    return uvs


@pytest.fixture
def wrapper_root(exec_wrapper):
    """`containers/base` を持ち、`projects/` は空の複製。

    `projects/` を空にするのは、wrapper 冒頭の name 解決 (実在プロジェクト名なら cd して
    引数を除去する) を発火させないためである。`base` が name 解決へ吸われると、この
    テストが検証したい dispatch まで引数が届かない。
    """
    exec_wrapper.container("base")
    return exec_wrapper


def test_wrapper_routes_build_image_to_python(wrapper_root):
    """`devbase build base` は Python の project build へ渡り、image が保たれる。"""
    _assert_python_route(wrapper_root(["build", "base"]), ["base"])


def test_wrapper_build_image_ignores_inherited_devbase_root(wrapper_root, tmp_path_factory, monkeypatch):
    """継承した `DEVBASE_ROOT` に同名のプロジェクトがあっても、複製の root だけを見る。

    継承した値を使うと `base` が name 解決に吸われて shell の経路になる。
    """
    other = tmp_path_factory.mktemp("other_root")
    (other / "projects" / "base").mkdir(parents=True)
    monkeypatch.setenv("DEVBASE_ROOT", str(other))

    r = wrapper_root(["build", "base"])

    _assert_python_route(r, ["base"])
    assert stdout_field(r, "PWD:") == str(wrapper_root.work), r.stdout


def test_wrapper_routes_build_image_no_cache_to_python(wrapper_root):
    """`--no-cache` を伴っても image 指定は Python 経路で、引数の順序が保たれる。"""
    _assert_python_route(wrapper_root(["build", "base", "--no-cache"]), ["base", "--no-cache"])


def test_wrapper_routes_bare_build_to_shell(wrapper_root):
    """image 省略・フラグなしは shell の cmd_build (2 段の compose ビルド)。"""
    _assert_shell_route(wrapper_root(["build"]))


def test_wrapper_routes_build_no_cache_to_shell(wrapper_root):
    """`devbase build --no-cache` は shell 経路のまま (退行防止)。`--no-cache` は base のビルドへ届く。"""
    uvs = _assert_shell_route(wrapper_root(["build", "--no-cache"]))
    buildx = [uv for uv in uvs if "docker buildx build" in uv]
    assert buildx and all(uv.endswith(" --no-cache") for uv in buildx), uvs


def test_wrapper_routes_build_project_no_cache_to_shell(wrapper_root):
    """`--project-no-cache` は shell 経路のまま。

    Python の `_run_build(project_no_cache=True)` がこの形で wrapper を呼び戻すため、
    ここが Python へ振り分けられると shell と Python の間で再帰する。base のビルドには
    `--no-cache` を付けない (`--no-cache` と同じ扱いにしない)。
    """
    uvs = _assert_shell_route(wrapper_root(["build", "--project-no-cache"]))
    buildx = [uv for uv in uvs if "docker buildx build" in uv]
    assert buildx and not any("--no-cache" in uv for uv in buildx), uvs


@pytest.mark.parametrize("flag", ["--expires", "--expires=7"])
def test_wrapper_routes_build_expires_to_python(wrapper_root, flag):
    """`--expires` は作成日判定のため Python 経路 (既存仕様の維持)。"""
    _assert_python_route(wrapper_root(["build", flag]), [flag])


def test_wrapper_routes_build_image_with_expires_to_python(wrapper_root):
    """image と `--expires` の併用も Python へ渡し、警告は Python 側で出す。"""
    _assert_python_route(wrapper_root(["build", "base", "--expires=7"]), ["base", "--expires=7"])


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
             "php", "php85", "snapshot", "trygroup"]
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
    """受け入れ条件 11: `build myapp --help` も cd せず、myapp の env を読まずに使い方を出す。

    `projects/myapp/env` に `echo MYAPP_ENV_READ >&2` を置く。wrapper は env を source するため、
    この行は cd と読み込みが起きたときだけ stderr に出る。
    """
    exec_wrapper.project("myapp", env="echo MYAPP_ENV_READ >&2\n")

    r = exec_wrapper(["build", "myapp", flag])

    _assert_build_usage(r)
    assert stdout_field(r, "PWD:") is None, r.stdout
    assert "MYAPP_ENV_READ" not in r.stderr, r.stderr


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
    exec_wrapper.project("myapp")

    r = exec_wrapper(["build", "myapp"])

    assert "=== Building devbase images ===" in r.stdout, r.stdout
    assert stdout_field(r, "PWD:") == str(exec_wrapper.root / "projects" / "myapp"), r.stdout
    assert r.stderr.strip() == "", r.stderr


def test_wrapper_build_container_only_name_has_no_note(exec_wrapper):
    """受け入れ条件 8: containers/ にだけある名前は今と同じく単体ビルドで、知らせは出ない。"""
    exec_wrapper.container("go")

    r = exec_wrapper(["build", "go"])

    uv = stdout_field(r, "UV:")
    assert uv is not None and uv.endswith(" devbase.cli project build go"), r.stdout
    assert r.stderr.strip() == "", r.stderr
