"""`bin/devbase` を実プロセスで起動するハーネス (PLAN61 決定 11)。

wrapper の本文を `sed` で削って `eval` する形にはしない。その形では関数の表記に依存して
削る範囲が黙って外れ、`DEVBASE_ROOT=` の行を削って環境変数の値を使わせるため、pytest が
継承した実環境の `DEVBASE_ROOT` を渡し忘れると実環境の `projects/` を見る (#339)。

ここでは `bin/devbase` を `<tmp>/bin/devbase` へ複製する。wrapper は自身の場所から
`DEVBASE_ROOT` を `<tmp>` に決め、継承した環境変数は wrapper の代入で上書きされる。外へ
出る呼び出しはすべて `uv` を通る (`run_python` と `compose_with_secrets`) ので、
`<tmp>/fakebin/uv` を `PATH` の先頭に置けば dispatch の先だけを差し替えられる。
`maybe_cd_project` と `cmd_build` は本物のまま動く。複製にするのは、シンボリックリンクだと
wrapper がリンクを解いて実物の場所を `DEVBASE_ROOT` にするためである。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "bin" / "devbase"

_FAKE_UV = """\
#!/bin/bash
echo "PWD:$PWD"
echo "UV:$*"
echo "MARKER:${MARKER:-<unset>}"
exit 0
"""


class WrapperRoot:
    """tmp の `DEVBASE_ROOT`。`projects/` `containers/` `etc/env` をテストごとに作る。"""

    def __init__(self, root: Path):
        self.root = root
        self.work = root / "work"

    def project(self, name: str, env: str | None = None) -> Path:
        path = self.root / "projects" / name
        path.mkdir(parents=True)
        if env is not None:
            (path / "env").write_text(env)
        return path

    def container(self, name: str) -> Path:
        path = self.root / "containers" / name
        path.mkdir(parents=True)
        (path / "Dockerfile").write_text("FROM ubuntu:26.04\n")
        return path

    def etc_env(self, text: str = "MARKER=leaked\n") -> Path:
        """`projects/../etc/env`。名前の形を弾けていないと wrapper がここを読む。"""
        (self.root / "etc").mkdir(exist_ok=True)
        path = self.root / "etc" / "env"
        path.write_text(text)
        return path

    def run(self, args, cwd: Path | None = None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "MARKER"}
        env["PATH"] = f"{self.root / 'fakebin'}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(
            ["bash", str(self.root / "bin" / "devbase"), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(cwd or self.work),
        )

    __call__ = run


@pytest.fixture
def exec_wrapper(tmp_path) -> WrapperRoot:
    """`bin/devbase` の複製と偽の `uv` を持つ tmp の `DEVBASE_ROOT`。"""
    (tmp_path / "bin").mkdir()
    shutil.copy(WRAPPER, tmp_path / "bin" / "devbase")
    (tmp_path / "projects").mkdir()
    (tmp_path / "containers").mkdir()
    (tmp_path / "work").mkdir()
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    uv = fakebin / "uv"
    uv.write_text(_FAKE_UV)
    uv.chmod(0o755)
    return WrapperRoot(tmp_path)


def stdout_field(result: subprocess.CompletedProcess, prefix: str) -> str | None:
    """標準出力から `prefix` で始まる最初の行の残りを返す。無ければ None。"""
    for line in result.stdout.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


def python_args(result: subprocess.CompletedProcess) -> str | None:
    """`UV:` 行から Python (`python -m devbase.cli`) へ渡った引数を返す。

    `run_python` を通らなかった (`UV:` 行が無い、または `devbase.cli` を起動していない) ときは None。
    """
    uv = stdout_field(result, "UV:")
    if uv is None:
        return None
    head, sep, tail = (uv + " ").partition(" devbase.cli ")
    return tail.rstrip() if sep else None


# ---------------------------------------------------------------------------
# 場所を出す入口を本物で動かすハーネス (#415)
# ---------------------------------------------------------------------------

_PASS_UV = """\
#!/bin/bash
# python -m devbase.commands.project_dockerfile の起動だけを本物の Python へ渡す
case " $* " in
    *" -m devbase.commands.project_dockerfile "*)
        while [ "$#" -gt 0 ] && [ "$1" != "python" ]; do shift; done
        shift
        PYTHONPATH="__LIB__${PYTHONPATH:+:$PYTHONPATH}" exec "__PYTHON__" "$@"
        ;;
esac
echo "PWD:$PWD"
echo "UV:$*"
echo "MARKER:${MARKER:-<unset>}"
exit 0
"""

_FAKE_DOCKER = """\
#!/bin/bash
# 起動した引数を記録する。compose config には置いた構成を返す (理由があれば標準エラーへ出して 1)
echo "$*" >> "__ROOT__/docker.log"
case "$*" in
    "compose config --format json")
        if [ -f "__ROOT__/compose_error.txt" ]; then
            cat "__ROOT__/compose_error.txt" >&2
            exit 1
        fi
        cat "__ROOT__/compose_config.json"
        exit 0
        ;;
esac
exit 0
"""


class ComposeWrapperRoot(WrapperRoot):
    """`WrapperRoot` に、構成の JSON を返す偽の `docker` と入口を本物で動かす偽の `uv` を足したもの。"""

    def compose_config(self, services: dict) -> None:
        """偽の `docker compose config --format json` が返す構成を置く。"""
        import json
        (self.root / "compose_config.json").write_text(json.dumps({"services": services}))

    def compose_error(self, text: str) -> None:
        """偽の `docker compose config` が理由を出して 1 で終わるようにする。"""
        (self.root / "compose_error.txt").write_text(text)

    def docker_calls(self) -> list[str]:
        """偽の `docker` が受けた引数の行 (入口が起動した `compose config` だけが並ぶ)。"""
        log = self.root / "docker.log"
        return log.read_text().splitlines() if log.exists() else []

    def run(self, args, cwd: Path | None = None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "MARKER"}
        env["PATH"] = f"{self.root / 'fakebin'}{os.pathsep}{env.get('PATH', '')}"
        # 入口が実環境の機密の置き場・docker の接続先を見ないようにする
        env["HOME"] = str(self.root / "home")
        for key in ("DOCKER_CONTEXT", "DOCKER_HOST", "DEVBASE_DOCKER_CONTEXT", "COMPOSE_FILE",
                    "DEV_SERVICE_NAME"):
            env.pop(key, None)
        env.update(self.extra_env)
        return subprocess.run(
            ["bash", str(self.root / "bin" / "devbase"), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(cwd or self.work),
        )

    __call__ = run


@pytest.fixture
def compose_wrapper(exec_wrapper) -> ComposeWrapperRoot:
    """`exec_wrapper` の tmp の `DEVBASE_ROOT` に、入口を本物で動かす偽の `uv` と偽の `docker` を置く。"""
    import sys

    root = exec_wrapper.root
    (root / "home").mkdir()
    fakebin = root / "fakebin"
    uv = fakebin / "uv"
    uv.write_text(_PASS_UV.replace("__LIB__", str(REPO_ROOT / "lib")).replace("__PYTHON__", sys.executable))
    uv.chmod(0o755)
    docker = fakebin / "docker"
    docker.write_text(_FAKE_DOCKER.replace("__ROOT__", str(root)))
    docker.chmod(0o755)
    wrapper = ComposeWrapperRoot(root)
    wrapper.extra_env = {}
    return wrapper
