"""Characterization of the real build wrapper before R3-002 extraction.

Only the external uv boundary is replaced; argument duplication is intentional
current behavior, including --project-no-cache combined with --no-cache.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def build_wrapper(tmp_path):
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "containers" / "base").mkdir(parents=True)
    wrapper = root / "bin" / "devbase"
    shutil.copyfile(Path(__file__).resolve().parents[2] / "bin" / "devbase", wrapper)
    project = root / "projects" / "sample"
    project.mkdir(parents=True)
    (project / "Dockerfile").write_text("FROM devbase-base:latest\n")
    (project / "env").write_text("DEV_SERVICE_NAME=workspace\n")
    log = root / "calls.jsonl"
    uv = root / "bin" / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['BUILD_LOG'], 'a') as f:\n"
        "    f.write(json.dumps(args) + '\\n')\n"
        "kind = 'base' if 'buildx' in args else 'project'\n"
        "sys.exit(17 if os.environ['BUILD_FAIL'] == kind else 0)\n"
    )
    uv.chmod(0o755)

    def run(flags, context, failure, service):
        (project / "env").write_text(f"DEV_SERVICE_NAME={service}\n")
        env = {**os.environ, "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
               "BUILD_LOG": str(log), "BUILD_FAIL": failure}
        result = subprocess.run(
            ["bash", str(wrapper), "build", *context, *flags],
            cwd=project, env=env, capture_output=True, text=True,
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        return result, calls, root

    return run


@pytest.mark.parametrize("flags,base_args,project_args", [
    (["--pull", "--quiet"], ["--pull", "--quiet"], ["--pull", "--quiet"]),
    (["--pull", "--no-cache", "--quiet"],
     ["--pull", "--no-cache", "--quiet"], ["--pull", "--no-cache", "--quiet"]),
    (["--pull", "--project-no-cache", "--quiet"],
     ["--pull", "--quiet"], ["--no-cache", "--pull", "--quiet"]),
    (["--pull", "--project-no-cache", "--no-cache", "--quiet", "--no-cache"],
     ["--pull", "--quiet"],
     ["--no-cache", "--pull", "--no-cache", "--quiet", "--no-cache"]),
])
@pytest.mark.parametrize("context", [[], ["--context", "remote"], ["--context=remote"]])
@pytest.mark.parametrize("failure", ["", "base", "project"])
@pytest.mark.parametrize("service", ["workspace", ""])
def test_build_modes(build_wrapper, flags, base_args, project_args, context, failure, service):
    result, calls, root = build_wrapper(flags, context, failure, service)
    prefix = ["run", "--project", str(root), "python", "-m", "devbase.cli", "env", "exec"]
    prefix += ["--context", "remote", "--"] if context else ["--"]
    expected = [prefix + ["docker", "buildx", "build", "--load", "-t",
                          "devbase-base:latest", str(root / "containers" / "base"), *base_args]]
    if failure != "base":
        expected.append(prefix + ["docker", "compose", "build", service or "dev", *project_args])
    assert calls == expected
    assert result.returncode == (1 if failure else 0)
    assert result.stderr == ""
    assert ("✓ All images built successfully" in result.stdout) == (not failure)
    assert ("✗ Failed to build devbase-base" in result.stdout) == (failure == "base")
    assert ("✗ Failed to build project image" in result.stdout) == (failure == "project")
    assert ("✓ devbase-base built successfully" in result.stdout) == (failure != "base")
    assert ("[2/2]" in result.stdout) == (failure != "base")
    if "--project-no-cache" in flags:
        assert "[1/2] Building devbase-base (using cache)..." in result.stdout
        if failure != "base":
            assert "[2/2] Building project image without cache..." in result.stdout
