"""ブラウザの派生イメージを選んだプロジェクトの `devbase build` が base を先に建てる (#402 受け入れ条件 10)。

dev の `build.context` が `${DEVBASE_ROOT}/containers/browser` のプロジェクトで `bin/devbase build` を
実プロセスで起動する。`containers/browser/Dockerfile` は本物を複製し、最初の `FROM` から
`devbase-base` を見つけて、base を建ててから `docker compose build dev` を打つことを固定する。
Docker は起動しない (外への呼び出しは偽の `uv` が受ける)。
"""

from __future__ import annotations

import shutil

from tests.cli.conftest import REPO_ROOT

BROWSER_DOCKERFILE = REPO_ROOT / "containers" / "browser" / "Dockerfile"


def _uv_lines(result):
    return [line[len("UV:"):] for line in result.stdout.splitlines() if line.startswith("UV:")]


def _browser_project(exec_wrapper):
    exec_wrapper.container("base")
    browser = exec_wrapper.root / "containers" / "browser"
    browser.mkdir()
    shutil.copy(BROWSER_DOCKERFILE, browser / "Dockerfile")
    project = exec_wrapper.work / "myproj"
    project.mkdir()
    (project / "compose.yml").write_text(
        "services:\n"
        "  dev:\n"
        "    build:\n"
        "      context: ${DEVBASE_ROOT}/containers/browser/\n"
        "      dockerfile: Dockerfile\n"
    )
    return project


def test_build_of_a_browser_project_builds_base_before_the_project(exec_wrapper):
    project = _browser_project(exec_wrapper)

    result = exec_wrapper.run(["build"], cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Project uses devbase-base" in result.stdout
    uv = _uv_lines(result)
    base = [i for i, line in enumerate(uv) if "buildx build --load -t devbase-base:latest" in line]
    project_build = [i for i, line in enumerate(uv) if line.endswith("docker compose build dev")]
    assert base and project_build, result.stdout
    assert base[0] < project_build[0], result.stdout
