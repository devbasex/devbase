"""shellcheck の指摘を直した `bin/devbase` が、直す前と同じに動く (#247 I5・I6a〜I6c)。

宣言と代入を分けると、置換の中のコマンドの非 0 が `set -e` で wrapper を止めうる。止まりうる
2 か所 (ベースイメージの判定と `DOCKER_GID`) を、`bin/devbase` を実プロセスで起動して固定する。
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
