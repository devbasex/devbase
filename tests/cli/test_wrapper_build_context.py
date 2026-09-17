"""shell の `devbase build --context NAME` が context を引数のまま Python へ渡す (PLAN52 Task 5)。

wrapper テストは実際の `uv run` を避けるため、`uv` と `cmd_build` / `run_python` を
シェル関数で差し替えて dispatch と `compose_with_secrets` だけを実行する。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / 'bin' / 'devbase'


def _run_wrapper(args, devbase_root, extra_env=None):
    """run_python / cmd_build を出力するだけの関数に差し替え、`uv` も関数で受ける。

    `compose_with_secrets` は**実物のまま**残す (context を引数で渡す当事者のため)。
    """
    harness = (
        'run_python() { echo "PYTHON:$*"; exit 0; }\n'
        'cmd_build() { echo "BUILD:$*"; echo "CTX:$_BUILD_CONTEXT"; '
        '  compose_with_secrets docker image inspect x; exit 0; }\n'
        'ensure_uv() { :; }\n'
        'uv() { echo "UV:$*"; }\n'
        'eval "$(sed -e \'/^run_python()/,/^}/d\' '
        '            -e \'/^ensure_uv()/,/^}/d\' '
        '            -e \'/^cmd_build()/,/^}/d\' '
        '            -e \'/^DEVBASE_ROOT=/d\' "$WRAPPER_PATH")"\n'
    )
    env = {**os.environ, "DEVBASE_ROOT": str(devbase_root), "WRAPPER_PATH": str(WRAPPER),
           **(extra_env or {})}
    return subprocess.run(["bash", "-c", harness, "devbase", *args],
                          capture_output=True, text=True, env=env, cwd=str(devbase_root))


def _line(result, prefix):
    for line in result.stdout.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


@pytest.fixture
def wrapper_root(tmp_path):
    (tmp_path / "containers" / "base").mkdir(parents=True)
    (tmp_path / "projects").mkdir()
    return tmp_path


def test_context_is_extracted_before_image_scan(wrapper_root):
    """`build --context NAME` の NAME を単体イメージ名として拾わない。"""
    result = _run_wrapper(["build", "--context", "gpu-wsl"], wrapper_root)
    assert _line(result, "PYTHON:") is None
    assert _line(result, "BUILD:") == ""
    assert _line(result, "CTX:") == "gpu-wsl"


def test_context_equals_form(wrapper_root):
    result = _run_wrapper(["build", "--context=gpu-wsl", "--no-cache"], wrapper_root)
    assert _line(result, "BUILD:") == "--no-cache"
    assert _line(result, "CTX:") == "gpu-wsl"


def test_context_reaches_env_exec_as_argument(wrapper_root):
    result = _run_wrapper(["build", "--context", "gpu-wsl"], wrapper_root)
    uv = _line(result, "UV:")
    assert uv is not None
    assert re.search(r"env exec --context gpu-wsl -- docker image inspect x$", uv), uv


def test_context_argument_beats_env_file(wrapper_root):
    """env に DEVBASE_DOCKER_CONTEXT=a があっても --context b が引数として届く。"""
    (wrapper_root / "env").write_text("DEVBASE_DOCKER_CONTEXT=a\n")
    result = _run_wrapper(["build", "--context", "b"], wrapper_root)
    assert "env exec --context b --" in (_line(result, "UV:") or "")


def test_without_context_env_exec_has_no_flag(wrapper_root):
    result = _run_wrapper(["build"], wrapper_root)
    assert _line(result, "CTX:") == ""
    assert "env exec -- docker image inspect x" in (_line(result, "UV:") or "")


def test_context_is_forwarded_to_python_single_build(wrapper_root):
    result = _run_wrapper(["build", "base", "--context", "gpu-wsl"], wrapper_root)
    assert _line(result, "PYTHON:") == "project build base --context gpu-wsl"


def test_missing_context_value_is_an_error(wrapper_root):
    result = _run_wrapper(["build", "--context"], wrapper_root)
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
    result = _run_wrapper(args, wrapper_root)
    assert result.returncode == 2
    assert "--context" in result.stderr
    assert _line(result, "BUILD:") is None
