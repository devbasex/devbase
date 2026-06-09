#!/usr/bin/env python3
"""bin/rc（シェル有効化スクリプト）のテスト (PLAN31_1)。

`. bin/rc` を source すると、いま開いているシェルへ devbase の有効化
（`DEVBASE_ROOT` の設定 / `DEVBASE_ROOT/bin` の PATH 追加）が即時適用される
ことを検証する。`devbase shell-rc`（廃止）+ `source "$(...)"` を置き換えた
軽量パス（Python/uv 起動なし・コマンド置換なし）であることが要点。

補完の読み込みはシェル種別依存のため、ここでは PATH / DEVBASE_ROOT と冪等性の
みを検証する（補完登録ロジックは init テストの責務）。
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RC = REPO_ROOT / "bin" / "rc"
BIN = REPO_ROOT / "bin"
BASH = shutil.which("bash") or "/bin/bash"


def _source_rc(snippet: str) -> subprocess.CompletedProcess:
    """クリーンな環境で bin/rc を source し、続けて snippet を実行する。"""
    env = {**os.environ}
    env.pop("DEVBASE_ROOT", None)
    script = f'. "{RC}"\n{snippet}'
    return subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, env=env,
    )


def test_rc_file_exists():
    assert RC.exists(), "bin/rc が存在すること"


def test_sourcing_sets_devbase_root():
    r = _source_rc('printf "ROOT=%s\\n" "$DEVBASE_ROOT"')
    assert r.returncode == 0, r.stderr
    assert f"ROOT={REPO_ROOT}" in r.stdout, r.stdout


def test_sourcing_prepends_bin_to_path():
    r = _source_rc('printf "PATH=%s\\n" "$PATH"')
    assert r.returncode == 0, r.stderr
    path_value = next(
        line[len("PATH="):] for line in r.stdout.splitlines()
        if line.startswith("PATH=")
    )
    assert f":{BIN}:" in f":{path_value}:", f"{BIN} が PATH に含まれること: {path_value}"


def test_devbase_resolves_after_sourcing():
    """source 後に `devbase` 実行ファイルが PATH 経由で解決できること。"""
    r = _source_rc('command -v devbase')
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(BIN / "devbase"), r.stdout


def test_path_addition_is_idempotent():
    """2 回 source しても bin が PATH に重複追加されないこと。"""
    r = _source_rc(f'. "{RC}"\nprintf "%s" "$PATH"')
    assert r.returncode == 0, r.stderr
    count = (":" + r.stdout + ":").count(f":{BIN}:")
    assert count == 1, f"{BIN} は PATH に 1 回だけ: count={count}\n{r.stdout}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
