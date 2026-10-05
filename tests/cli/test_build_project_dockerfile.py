"""通常のビルドが開発サービスの `build` からプロジェクトの Dockerfile を決める (#415)。

`bin/devbase build` を実プロセスで起動し、場所を出す入口 (`python -m devbase.commands.project_dockerfile`)
は本物で動かす。`docker compose config` は偽の `docker` がテストの置いた構成の JSON を返し、ほかの外への
呼び出し (`buildx build`・`image inspect`・`compose build`) は偽の `uv` が `UV:` の行で受ける。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.cli.conftest import REPO_ROOT

FAILED_LINE = "✗ Failed to read the compose configuration; no image was built"


def _uv_lines(result):
    return [line[len("UV:"):] for line in result.stdout.splitlines() if line.startswith("UV:")]


def _built(result):
    """`docker buildx build` で建てた段の名前 (起動の順)。"""
    names = []
    for line in _uv_lines(result):
        if "buildx build --load -t " in line:
            names.append(line.split("buildx build --load -t ", 1)[1].split(":", 1)[0])
    return names


def _project(compose_wrapper, *containers):
    for name in containers:
        compose_wrapper.container(name)
    project = compose_wrapper.work / "myproj"
    project.mkdir()
    # 中身は偽の docker が返す構成で決まる。入口を起動する分岐へ入るために置く
    (project / "compose.yml").write_text("services: {}\n")
    return project


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# ---- 受け入れ条件 1: 開発サービスより前に別のサービスの build がある ----


def test_build_ignores_a_service_listed_before_the_dev_service(compose_wrapper):
    project = _project(compose_wrapper, "base", "other")
    _write(project / "other" / "Dockerfile", "FROM devbase-other\n")
    _write(project / "dev" / "Dockerfile", "FROM devbase-base\n")
    compose_wrapper.compose_config({
        "other": {"build": {"context": str(project / "other"), "dockerfile": "Dockerfile"}},
        "dev": {"build": {"context": str(project / "dev"), "dockerfile": "Dockerfile"}},
    })

    result = compose_wrapper.run(["build"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _built(result) == ["devbase-base"], result.stdout
    assert "devbase-other" not in result.stdout
    assert _uv_lines(result)[-1].endswith("docker compose build dev"), result.stdout


# ---- 受け入れ条件 2: build が文字列の形 ----


def test_build_reads_the_dockerfile_of_a_string_build(compose_wrapper):
    project = _project(compose_wrapper, "general", "other")
    _write(project / "Dockerfile", "FROM devbase-other\n")
    _write(project / "dev" / "Dockerfile", "FROM devbase-general\n")
    # 文字列の形 (build: ./dev) を構成の外から渡しても同じ規則で読む
    compose_wrapper.compose_config({"dev": {"build": "./dev"}})

    result = compose_wrapper.run(["build"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Project uses devbase-general" in result.stdout
    assert _built(result) == ["devbase-general"], result.stdout
    assert "devbase-other" not in result.stdout


# ---- 受け入れ条件 3: DEV_SERVICE_NAME ----


def test_build_uses_the_dev_service_name_to_find_the_dockerfile(compose_wrapper):
    project = _project(compose_wrapper, "general", "other")
    _write(project / "dev" / "Dockerfile", "FROM devbase-other\n")
    _write(project / "app" / "Dockerfile", "FROM devbase-general\n")
    compose_wrapper.compose_config({
        "dev": {"build": {"context": str(project / "dev")}},
        "app": {"build": {"context": str(project / "app")}},
    })
    compose_wrapper.extra_env["DEV_SERVICE_NAME"] = "app"

    result = compose_wrapper.run(["build"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _built(result) == ["devbase-general"], result.stdout
    assert "devbase-other" not in result.stdout
    assert _uv_lines(result)[-1].endswith("docker compose build app"), result.stdout


# ---- 受け入れ条件 5: 開発サービスが build を持たない ----


def _image_only_project(compose_wrapper):
    project = _project(compose_wrapper, "base", "other")
    _write(project / "Dockerfile", "FROM devbase-other\n")
    compose_wrapper.compose_config({"dev": {"image": "example/dev:latest"}})
    return project


def test_build_without_dev_build_skips_existing_devbase_base(compose_wrapper):
    project = _image_only_project(compose_wrapper)

    result = compose_wrapper.run(["build"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[1/2] devbase-base already exists (use --no-cache to rebuild)" in result.stdout
    assert _built(result) == [], result.stdout
    assert "devbase-other" not in result.stdout


def test_build_without_dev_build_builds_missing_devbase_base(compose_wrapper):
    project = _image_only_project(compose_wrapper)
    compose_wrapper.extra_env["UV_FAIL_ON"] = "image inspect"

    result = compose_wrapper.run(["build"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _built(result) == ["devbase-base"], result.stdout
    assert "devbase-other" not in result.stdout


# ---- 受け入れ条件 6: 構成を読めない ----


def test_build_stops_before_any_build_when_compose_config_fails(compose_wrapper):
    project = _project(compose_wrapper, "base")
    _write(project / "Dockerfile", "FROM devbase-base\n")
    compose_wrapper.compose_error("required variable MISSING is missing a value: need it\n")

    result = compose_wrapper.run(["build"], cwd=project)

    assert result.returncode == 1, result.stdout + result.stderr
    assert _uv_lines(result) == [], result.stdout
    assert FAILED_LINE in result.stdout
    assert "required variable MISSING is missing a value: need it" in result.stderr


# ---- 受け入れ条件 8: 構成を読むのは 1 回以下 ----


@pytest.mark.parametrize("args", [
    ["build"],
    ["build", "--no-cache"],
    ["build", "--project-no-cache", "--no-cache"],
])
def test_build_reads_compose_config_once(compose_wrapper, args):
    project = _project(compose_wrapper, "base")
    _write(project / "Dockerfile", "FROM devbase-base\n")
    compose_wrapper.compose_config({"dev": {"build": {"context": str(project)}}})

    result = compose_wrapper.run(args, cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert compose_wrapper.docker_calls() == ["compose config --no-env-resolution --format json"]


def test_build_passes_the_context_to_the_entry(compose_wrapper):
    """`devbase build --context NAME` の接続先で構成を読む (`docker compose build` と同じ接続先)。"""
    project = _project(compose_wrapper, "base")
    _write(project / "Dockerfile", "FROM devbase-base\n")
    compose_wrapper.compose_config({"dev": {"build": {"context": str(project)}}})

    result = compose_wrapper.run(["build", "--context", "remote-a"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert compose_wrapper.docker_calls() == ["compose config --no-env-resolution --format json"]
    assert compose_wrapper.docker_contexts() == ["remote-a"]


def test_build_without_compose_yml_does_not_read_compose_config(compose_wrapper):
    compose_wrapper.container("base")
    project = compose_wrapper.work / "myproj"
    project.mkdir()
    _write(project / "Dockerfile", "FROM devbase-base\n")

    result = compose_wrapper.run(["build"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert compose_wrapper.docker_calls() == []
    assert _built(result) == ["devbase-base"], result.stdout


# ---- 場所を出す入口の契約 ----


def _run_entry(compose_wrapper, project, *args):
    env = {k: v for k, v in os.environ.items()
           if k not in ("DOCKER_CONTEXT", "DOCKER_HOST", "DEVBASE_DOCKER_CONTEXT", "COMPOSE_FILE")}
    env["PATH"] = f"{compose_wrapper.root / 'fakebin'}{os.pathsep}{env.get('PATH', '')}"
    env["HOME"] = str(compose_wrapper.root / "home")
    env["DEVBASE_ROOT"] = str(compose_wrapper.root)
    env["PYTHONPATH"] = str(REPO_ROOT / "lib")
    return subprocess.run(
        [sys.executable, "-m", "devbase.commands.project_dockerfile", *args],
        capture_output=True, text=True, env=env, cwd=str(project), check=False,
    )


def test_entry_prints_one_line_with_the_path(compose_wrapper):
    project = _project(compose_wrapper)
    compose_wrapper.compose_config({"dev": {"build": {"context": str(project), "dockerfile": "D"}}})

    result = _run_entry(compose_wrapper, project, "--service", "dev")

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{project / 'D'}\n"


def test_entry_prints_an_empty_line_without_build(compose_wrapper):
    project = _project(compose_wrapper)
    compose_wrapper.compose_config({"dev": {"image": "x"}})

    result = _run_entry(compose_wrapper, project, "--service", "dev")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "\n"


def test_entry_fails_without_stdout_when_compose_config_fails(compose_wrapper):
    project = _project(compose_wrapper)
    compose_wrapper.compose_error("broken yaml\n")

    result = _run_entry(compose_wrapper, project, "--service", "dev")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "broken yaml" in result.stderr


def test_entry_fails_without_stdout_when_compose_config_is_not_json(compose_wrapper):
    project = _project(compose_wrapper)
    (compose_wrapper.root / "compose_config.json").write_text("not json")

    result = _run_entry(compose_wrapper, project, "--service", "dev")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Unable to read the compose configuration as JSON" in result.stderr


def test_entry_requires_the_service(compose_wrapper):
    project = _project(compose_wrapper)

    result = _run_entry(compose_wrapper, project)

    assert result.returncode == 2
    assert result.stdout == ""
