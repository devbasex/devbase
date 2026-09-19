"""`bin/devbase` を実プロセスで起動するハーネス (PLAN61 決定 11)。

既存の `sed` ハーネス (`test_build_image_argument.py` など) は wrapper の本文を削って `eval`
する。その形では `DEVBASE_ROOT=` の行を削って環境変数の値を使わせるため、pytest が継承した
実環境の `DEVBASE_ROOT` を渡し忘れると実環境の `projects/` を見る。また `run_python` と
`cmd_build` を関数ごと置き換えるため、`=== Building devbase images ===` が出ないことを
確かめられない。

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
