"""`devbase build` が継承の連なりを下の段から建てる (#404 受け入れ条件 1〜6・9)。

プロジェクトの Dockerfile が派生イメージ (`devbase-php` など) を `FROM` に取るとき、`bin/devbase build` が
`containers/<名前>/Dockerfile` の `FROM devbase-*` をたどり、下の段から建ててからプロジェクトを建てることを、
実プロセスの `bin/devbase` と偽の `uv` で固定する。Docker は起動しない。

偽の `uv` は、引数に `UV_FAIL_ON` を含む呼び出しだけを失敗させる (`image inspect` を失敗させると
「`devbase-base:latest` が手元に無い」状態になる)。
"""

from __future__ import annotations

import os

import pytest

_FAIL_UV = """\
#!/bin/bash
echo "UV:$*"
case "$*" in
  *"$UV_FAIL_ON"*) exit 1 ;;
esac
exit 0
"""


def _uv_lines(result):
    return [line[len("UV:"):] for line in result.stdout.splitlines() if line.startswith("UV:")]


def _built_images(result):
    """偽の `uv` が受けた `docker buildx build --load -t <イメージ>` のイメージを順に返す。"""
    images = []
    for line in _uv_lines(result):
        if "docker buildx build --load -t " in line:
            images.append(line.split("docker buildx build --load -t ", 1)[1].split()[0])
    return images


def _uv_index(result, predicate):
    for i, line in enumerate(_uv_lines(result)):
        if predicate(line):
            return i
    return None


def _project_build_index(result):
    return _uv_index(result, lambda line: "docker compose build dev" in line)


def _set_fail_on(exec_wrapper, fail_on):
    uv = exec_wrapper.root / "fakebin" / "uv"
    uv.write_text(_FAIL_UV.replace("$UV_FAIL_ON", fail_on))
    os.chmod(uv, 0o755)


def _container(exec_wrapper, name, dockerfile):
    path = exec_wrapper.root / "containers" / name
    path.mkdir(parents=True)
    (path / "Dockerfile").write_text(dockerfile)
    return path


def _project(exec_wrapper, dockerfile):
    project = exec_wrapper.work / "myproj"
    project.mkdir()
    (project / "Dockerfile").write_text(dockerfile)
    return project


@pytest.fixture
def php_project(exec_wrapper):
    """dev の Dockerfile が `FROM devbase-php:latest`、`containers/php` が `FROM devbase-base:latest`。"""
    _container(exec_wrapper, "base", "FROM ubuntu:26.04\n")
    _container(exec_wrapper, "php", "FROM devbase-base:latest\nRUN echo php\n")
    return _project(exec_wrapper, "FROM devbase-php:latest\nRUN echo app\n")


# ---------------------------------------------------------------------------
# 受け入れ条件 1: base → devbase-php → プロジェクトの順に建てる
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("base_missing", [False, True], ids=["base-present", "base-missing"])
def test_build_builds_the_chain_from_the_bottom(exec_wrapper, php_project, base_missing):
    if base_missing:
        _set_fail_on(exec_wrapper, "image inspect")

    result = exec_wrapper.run(["build"], cwd=php_project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _built_images(result) == ["devbase-base:latest", "devbase-php:latest"], result.stdout
    project_index = _project_build_index(result)
    last_base = _uv_index(result, lambda line: "-t devbase-php:latest" in line)
    assert project_index is not None and last_base < project_index, result.stdout
    # 段の名前と順を出力から読み取れる
    assert "building its base images: devbase-base -> devbase-php" in result.stdout
    out = result.stdout
    assert out.index("[1/3] Building devbase-base...") < out.index("[2/3] Building devbase-php...")
    assert out.index("[2/3] Building devbase-php...") < out.index("[3/3] Building project image...")


# ---------------------------------------------------------------------------
# 受け入れ条件 2: --no-cache は全段とプロジェクトをキャッシュなしで建てる
# ---------------------------------------------------------------------------

def test_no_cache_builds_every_stage_without_cache(exec_wrapper, php_project):
    result = exec_wrapper.run(["build", "--no-cache"], cwd=php_project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _built_images(result) == ["devbase-base:latest", "devbase-php:latest"], result.stdout
    builds = [line for line in _uv_lines(result) if "buildx build" in line]
    assert all("--no-cache" in line for line in builds), builds
    project_line = _uv_lines(result)[_project_build_index(result)]
    assert "--no-cache" in project_line


# ---------------------------------------------------------------------------
# 受け入れ条件 3: --project-no-cache は段をキャッシュありで、プロジェクトだけキャッシュなしで建てる
# ---------------------------------------------------------------------------

def test_project_no_cache_builds_stages_with_cache(exec_wrapper, php_project):
    result = exec_wrapper.run(["build", "--project-no-cache", "--no-cache"], cwd=php_project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _built_images(result) == ["devbase-base:latest", "devbase-php:latest"], result.stdout
    builds = [line for line in _uv_lines(result) if "buildx build" in line]
    assert not any("--no-cache" in line for line in builds), builds
    project_line = _uv_lines(result)[_project_build_index(result)]
    assert "--no-cache" in project_line
    assert "[1/3] Building devbase-base (using cache)..." in result.stdout
    assert "[3/3] Building project image without cache..." in result.stdout


# ---------------------------------------------------------------------------
# 受け入れ条件 4: 下の段の失敗で止まる
# ---------------------------------------------------------------------------

def test_failure_of_a_lower_stage_stops_before_upper_stages(exec_wrapper, php_project):
    _set_fail_on(exec_wrapper, "-t devbase-base:latest")

    result = exec_wrapper.run(["build"], cwd=php_project)

    assert result.returncode != 0
    assert _built_images(result) == ["devbase-base:latest"], result.stdout
    assert _project_build_index(result) is None, result.stdout
    assert "✗ Failed to build devbase-base" in result.stdout + result.stderr


# ---------------------------------------------------------------------------
# 受け入れ条件 5: 途中の段の Dockerfile が無いとき何も建てない
# ---------------------------------------------------------------------------

def test_missing_first_stage_directory_builds_nothing(exec_wrapper):
    _container(exec_wrapper, "base", "FROM ubuntu:26.04\n")
    project = _project(exec_wrapper, "FROM devbase-foo:latest\n")

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode != 0
    assert _built_images(result) == [] and _project_build_index(result) is None, result.stdout
    assert str(exec_wrapper.root / "containers" / "foo") in result.stdout + result.stderr


def test_missing_second_stage_directory_builds_nothing(exec_wrapper):
    _container(exec_wrapper, "foo", "FROM devbase-bar:latest\n")
    project = _project(exec_wrapper, "FROM devbase-foo:latest\n")

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode != 0
    assert _built_images(result) == [] and _project_build_index(result) is None, result.stdout
    assert str(exec_wrapper.root / "containers" / "bar") in result.stdout + result.stderr


def test_stage_directory_without_dockerfile_builds_nothing(exec_wrapper):
    (exec_wrapper.root / "containers" / "foo").mkdir()
    project = _project(exec_wrapper, "FROM devbase-foo:latest\n")

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode != 0
    assert _built_images(result) == [] and _project_build_index(result) is None, result.stdout
    assert str(exec_wrapper.root / "containers" / "foo" / "Dockerfile") in result.stdout


# ---------------------------------------------------------------------------
# 受け入れ条件 6: 循環するとき何も建てずに止まる
# ---------------------------------------------------------------------------

def test_loop_in_the_chain_builds_nothing(exec_wrapper):
    _container(exec_wrapper, "a", "FROM devbase-b:latest\n")
    _container(exec_wrapper, "b", "FROM devbase-a:latest\n")
    project = _project(exec_wrapper, "FROM devbase-a:latest\n")

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode != 0
    assert _built_images(result) == [] and _project_build_index(result) is None, result.stdout
    assert "✗ Base image chain loops: devbase-a -> devbase-b -> devbase-a" in result.stdout


def test_self_loop_builds_nothing(exec_wrapper):
    _container(exec_wrapper, "a", "FROM devbase-a:latest\n")
    project = _project(exec_wrapper, "FROM devbase-a:latest\n")

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode != 0
    assert _built_images(result) == [], result.stdout
    assert "✗ Base image chain loops: devbase-a -> devbase-a" in result.stdout


# ---------------------------------------------------------------------------
# 決定 4: containers/ の外を指す参照は連結せずに止まる
# ---------------------------------------------------------------------------

def test_reference_outside_containers_builds_nothing(exec_wrapper):
    (exec_wrapper.root / "x").mkdir()
    (exec_wrapper.root / "x" / "Dockerfile").write_text("FROM ubuntu:26.04\n")
    project = _project(exec_wrapper, "FROM devbase-../x:latest\n")

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode != 0
    assert _built_images(result) == [] and _project_build_index(result) is None, result.stdout
    assert "✗ Invalid base image reference in Dockerfile: devbase-../x:latest" in result.stdout


# ---------------------------------------------------------------------------
# 受け入れ条件 9: devbase-* を FROM に取らないプロジェクトは今のまま
# ---------------------------------------------------------------------------

def test_plain_project_skips_base_when_present(exec_wrapper):
    _container(exec_wrapper, "base", "FROM ubuntu:26.04\n")
    project = _project(exec_wrapper, "FROM ubuntu:26.04\n")

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _built_images(result) == [], result.stdout
    assert _project_build_index(result) is not None


def test_plain_project_builds_base_when_missing(exec_wrapper):
    _container(exec_wrapper, "base", "FROM ubuntu:26.04\n")
    project = _project(exec_wrapper, "FROM ubuntu:26.04\n")
    _set_fail_on(exec_wrapper, "image inspect")

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _built_images(result) == ["devbase-base:latest"], result.stdout
    base_index = _uv_index(result, lambda line: "-t devbase-base:latest" in line)
    assert base_index < _project_build_index(result), result.stdout


# ---------------------------------------------------------------------------
# I7: 1 段の連なりは今の行と回数のまま
# ---------------------------------------------------------------------------

def test_single_stage_keeps_the_current_lines(exec_wrapper):
    _container(exec_wrapper, "base", "FROM ubuntu:26.04\n")
    _container(exec_wrapper, "php", "FROM devbase-base:latest\n")
    project = _project(exec_wrapper, "FROM devbase-base:latest\n")

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _built_images(result) == ["devbase-base:latest"], result.stdout
    assert "[1/2] Project uses devbase-base, building base image..." in result.stdout
    assert "[2/2] Building project image..." in result.stdout
    assert "building its base images" not in result.stdout
