"""shellcheck の指摘を直した `bin/devbase` が、直す前と同じに動く (#247 I5・I6a〜I6c)。

宣言と代入を分けると、置換の中のコマンドの非 0 が `set -e` で wrapper を止めうる。止まりうる
2 か所 (ベースイメージの判定と `DOCKER_GID`) を、`bin/devbase` を実プロセスで起動して固定する。
あわせて、compose の `build` からの Dockerfile の解決と、`cmd_build` の分岐 (ベースの要否・
`--no-cache` の渡し方・サービス名・失敗時) も同じ起動で固定する。
"""

from __future__ import annotations

import shutil

import pytest

from tests.cli.conftest import stdout_field

_ENV_UV = """\
#!/bin/bash
echo "DOCKER_GID:${DOCKER_GID-<unset>}"
echo "COMPOSE_PROJECT_NAME:${COMPOSE_PROJECT_NAME-<unset>}"
exit 0
"""


def _write_exe(path, text):
    path.write_text(text)
    path.chmod(0o755)


def _uv_lines(result):
    return [line[len("UV:"):] for line in result.stdout.splitlines() if line.startswith("UV:")]


# ---- I5: ベースイメージの判定の非 0 で止まらない ----


def test_build_without_devbase_base_image_reaches_project_build(exec_wrapper):
    """Dockerfile が devbase-* を使わないとき、判定の 1 で止まらずプロジェクトのビルドへ進む。"""
    project = exec_wrapper.work / "myproj"
    project.mkdir()
    (project / "Dockerfile").write_text("FROM ubuntu:26.04\n")

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[2/2] Building project image..." in result.stdout
    assert any(line.endswith("docker compose build dev") for line in _uv_lines(result)), result.stdout


def test_build_resolves_dockerfile_from_compose_context(exec_wrapper):
    """compose.yml の context と dockerfile から Dockerfile を解き、devbase-* のベースを建てる。"""
    exec_wrapper.container("general")
    project = exec_wrapper.work / "myproj"
    project.mkdir()
    (project / "compose.yml").write_text(
        "services:\n"
        "  dev:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: Dockerfile.dev\n"
    )
    (project / "Dockerfile.dev").write_text("FROM devbase-general:latest\n")

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Project uses devbase-general" in result.stdout
    uv = _uv_lines(result)
    assert any("-t devbase-general:latest" in line for line in uv), result.stdout
    assert any(line.endswith("docker compose build dev") for line in uv), result.stdout


def test_build_with_compose_build_but_no_dockerfile_key(exec_wrapper):
    """`build:` に `dockerfile:` が無くても止まらずプロジェクトのビルドへ進む。"""
    project = exec_wrapper.work / "myproj"
    project.mkdir()
    (project / "compose.yml").write_text(
        "services:\n"
        "  dev:\n"
        "    build:\n"
        "      context: .\n"
    )
    (project / "Dockerfile").write_text("FROM ubuntu:26.04\n")

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert any(line.endswith("docker compose build dev") for line in _uv_lines(result)), result.stdout


def test_build_stops_when_context_expansion_fails(exec_wrapper, monkeypatch):
    """context の展開が失敗したら (`${VAR:?}` の未設定)、置換の中でも止まりビルドへ進まない。"""
    monkeypatch.delenv("BUILD_CONTEXT", raising=False)
    project = exec_wrapper.work / "myproj"
    project.mkdir()
    (project / "compose.yml").write_text(
        "services:\n"
        "  dev:\n"
        "    build:\n"
        "      context: ${BUILD_CONTEXT:?required}\n"
    )
    (project / "Dockerfile").write_text("FROM ubuntu:26.04\n")

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "BUILD_CONTEXT" in result.stderr
    assert not _uv_lines(result), result.stdout


# ---- I6a〜I6c: DOCKER_GID と COMPOSE_PROJECT_NAME ----


@pytest.fixture
def env_wrapper(exec_wrapper, tmp_path):
    """偽の `uv` が環境変数を出し、偽の `uname` と `grep` が OS と /etc/group を差し替える。"""
    fakebin = exec_wrapper.root / "fakebin"
    _write_exe(fakebin / "uv", _ENV_UV)
    real_grep = shutil.which("grep")
    assert real_grep is not None
    group = tmp_path / "group"

    def run(os_name: str, group_text: str):
        group.write_text(group_text)
        _write_exe(fakebin / "uname", f"#!/bin/bash\necho {os_name}\n")
        _write_exe(
            fakebin / "grep",
            "#!/bin/bash\n"
            'if [ "$#" = 2 ] && [ "$1" = docker ] && [ "$2" = /etc/group ]; then\n'
            f'    exec "{real_grep}" docker "{group}"\n'
            "fi\n"
            f'exec "{real_grep}" "$@"\n',
        )
        project = exec_wrapper.work / "myproj"
        project.mkdir(exist_ok=True)
        return exec_wrapper.run(["--help"], cwd=project)

    return run


def test_docker_gid_is_zero_on_darwin(env_wrapper):
    result = env_wrapper("Darwin", "docker:x:987:someone\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert stdout_field(result, "DOCKER_GID:") == "0"
    assert stdout_field(result, "COMPOSE_PROJECT_NAME:") == "myproj"


def test_docker_gid_is_group_gid_elsewhere(env_wrapper):
    result = env_wrapper("Linux", "root:x:0:\ndocker:x:987:someone\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert stdout_field(result, "DOCKER_GID:") == "987"
    assert stdout_field(result, "COMPOSE_PROJECT_NAME:") == "myproj"


def test_docker_gid_is_empty_without_docker_group(env_wrapper):
    """docker の行が無いと grep は 1 を返すが、wrapper は止まらず DOCKER_GID は空になる。"""
    result = env_wrapper("Linux", "root:x:0:\nstaff:x:20:\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert stdout_field(result, "DOCKER_GID:") == ""
    assert stdout_field(result, "COMPOSE_PROJECT_NAME:") == "myproj"


# ---- cmd_build の分岐 (現状固定) ----

_FAIL_UV = """\
#!/bin/bash
echo "UV:$*"
case "$*" in
  *"$UV_FAIL_ON"*) exit 1 ;;
esac
exit 0
"""


def _plain_project(exec_wrapper, fail_on=None):
    project = exec_wrapper.work / "myproj"
    project.mkdir()
    (project / "Dockerfile").write_text("FROM ubuntu:26.04\n")
    if fail_on is not None:
        _write_exe(exec_wrapper.root / "fakebin" / "uv", _FAIL_UV.replace("$UV_FAIL_ON", fail_on))
    return project


def test_build_skips_base_when_devbase_base_exists(exec_wrapper):
    project = _plain_project(exec_wrapper)
    result = exec_wrapper.run(["build"], cwd=project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[1/2] devbase-base already exists (use --no-cache to rebuild)" in result.stdout
    assert not any("buildx build" in line for line in _uv_lines(result))
    assert "✓ All images built successfully" in result.stdout


def test_build_builds_devbase_base_when_missing(exec_wrapper):
    exec_wrapper.container("base")
    project = _plain_project(exec_wrapper, fail_on="image inspect")
    result = exec_wrapper.run(["build"], cwd=project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[1/2] Building devbase-base..." in result.stdout
    assert "✓ devbase-base built successfully" in result.stdout
    assert any("buildx build --load -t devbase-base:latest" in line for line in _uv_lines(result))


def test_build_no_cache_passes_flag_to_base_and_project(exec_wrapper):
    exec_wrapper.container("base")
    project = _plain_project(exec_wrapper)
    result = exec_wrapper.run(["build", "--no-cache"], cwd=project)
    assert result.returncode == 0, result.stdout + result.stderr
    uv = _uv_lines(result)
    assert "[1/2] Building devbase-base..." in result.stdout
    assert any("-t devbase-base:latest" in line and line.endswith("--no-cache") for line in uv)
    assert any(line.endswith("docker compose build dev --no-cache") for line in uv)


def test_build_project_no_cache_keeps_base_cache(exec_wrapper):
    exec_wrapper.container("base")
    project = _plain_project(exec_wrapper)
    result = exec_wrapper.run(["build", "--project-no-cache", "--no-cache"], cwd=project)
    assert result.returncode == 0, result.stdout + result.stderr
    uv = _uv_lines(result)
    assert "[1/2] Building devbase-base (using cache)..." in result.stdout
    assert "[2/2] Building project image without cache..." in result.stdout
    base = [line for line in uv if "-t devbase-base:latest" in line]
    assert base and not any("--no-cache" in line for line in base)
    assert any(line.endswith("docker compose build dev --no-cache --no-cache") for line in uv)


def test_build_uses_dev_service_name(exec_wrapper, monkeypatch):
    monkeypatch.setenv("DEV_SERVICE_NAME", "app")
    project = _plain_project(exec_wrapper)
    result = exec_wrapper.run(["build"], cwd=project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(line.endswith("docker compose build app") for line in _uv_lines(result))


def test_build_fails_when_project_build_fails(exec_wrapper):
    project = _plain_project(exec_wrapper, fail_on="compose build")
    result = exec_wrapper.run(["build"], cwd=project)
    assert result.returncode == 1
    assert "✗ Failed to build project image" in result.stdout
    assert "✓ All images built successfully" not in result.stdout


def test_build_fails_when_base_container_dir_missing(exec_wrapper):
    project = _plain_project(exec_wrapper)
    result = exec_wrapper.run(["build", "--no-cache"], cwd=project)
    assert result.returncode == 1
    assert "✗ Container directory not found:" in result.stdout
    assert not any("compose build" in line for line in _uv_lines(result))
