"""shell の `devbase build --context NAME` が context を引数のまま Python へ渡す (PLAN52 Task 5)。

wrapper は `exec_wrapper` (conftest.py) で動く。本物の bin/devbase を tmp へ複製して起動し、
外への呼び出しの境界の `uv` だけを差し替える。`cmd_build` と `compose_with_secrets` は本物の
まま動くため、shell の経路は `=== Building devbase images ===` と、`compose_with_secrets` が
起こす `env exec` の `UV:` 行で確かめる。偽の `uv` は 0 で終わるので、通常のビルドでは
`devbase-base` が既にあるものとして扱われ、プロジェクトのイメージの行だけが出る。
"""

from __future__ import annotations

import re

import pytest

from tests.cli.conftest import WRAPPER, python_args

SHELL_BUILD = "=== Building devbase images ==="


def _env_exec_lines(result):
    """`compose_with_secrets` が起こした `env exec` の `UV:` 行の、`env exec` から後ろ。"""
    return [line.split(" devbase.cli ", 1)[1] for line in result.stdout.splitlines()
            if line.startswith("UV:") and " devbase.cli env exec " in line]


@pytest.fixture
def wrapper_root(exec_wrapper):
    exec_wrapper.container("base")
    return exec_wrapper


def test_context_is_extracted_before_image_scan(wrapper_root):
    """`build --context NAME` の NAME を単体イメージ名として拾わない。"""
    result = wrapper_root(["build", "--context", "gpu-wsl"])
    assert SHELL_BUILD in result.stdout, result.stdout
    assert _env_exec_lines(result) == [
        "env exec --context gpu-wsl -- docker compose build dev"], result.stdout


def test_context_equals_form(wrapper_root):
    result = wrapper_root(["build", "--context=gpu-wsl", "--no-cache"])
    assert SHELL_BUILD in result.stdout, result.stdout
    lines = _env_exec_lines(result)
    assert lines and all(line.startswith("env exec --context gpu-wsl -- ") for line in lines), lines
    assert lines[-1] == "env exec --context gpu-wsl -- docker compose build dev --no-cache", lines


def test_context_reaches_env_exec_as_argument(wrapper_root):
    result = wrapper_root(["build", "--context", "gpu-wsl"])
    lines = _env_exec_lines(result)
    assert lines, result.stdout
    assert re.fullmatch(r"env exec --context gpu-wsl -- docker compose build dev", lines[-1]), lines


def test_context_argument_beats_env_file(wrapper_root):
    """env に DEVBASE_DOCKER_CONTEXT=a があっても --context b が引数として届く。"""
    (wrapper_root.work / "env").write_text("DEVBASE_DOCKER_CONTEXT=a\n")
    result = wrapper_root(["build", "--context", "b"])
    lines = _env_exec_lines(result)
    assert lines and all(line.startswith("env exec --context b -- ") for line in lines), lines


def test_without_context_env_exec_has_no_flag(wrapper_root):
    result = wrapper_root(["build"])
    assert _env_exec_lines(result) == ["env exec -- docker compose build dev"], result.stdout


def test_context_is_forwarded_to_python_single_build(wrapper_root):
    result = wrapper_root(["build", "base", "--context", "gpu-wsl"])
    assert SHELL_BUILD not in result.stdout, result.stdout
    assert python_args(result) == "project build base --context gpu-wsl", result.stdout


def test_missing_context_value_is_an_error(wrapper_root):
    result = wrapper_root(["build", "--context"])
    assert result.returncode == 2
    assert "--context" in result.stderr


def test_shell_docker_calls_go_through_env_exec():
    """cmd_build の docker 直接呼び出し (buildx build / image inspect) が残っていない。"""
    lines = [line for line in WRAPPER.read_text(encoding='utf-8').splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    direct = [line for line in lines
              if re.search(r'\bdocker (buildx build|image inspect)\b', line)
              and 'compose_with_secrets' not in line]
    assert direct == [], direct


@pytest.mark.parametrize("args", [["build", "--context", ""], ["build", "--context="],
                                  ["build", "--context", "  "]])
def test_empty_context_value_is_an_error(wrapper_root, args):
    result = wrapper_root(args)
    assert result.returncode == 2
    assert "--context" in result.stderr
    assert SHELL_BUILD not in result.stdout, result.stdout
    assert "UV:" not in result.stdout, result.stdout
