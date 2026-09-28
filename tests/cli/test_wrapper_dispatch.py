#!/usr/bin/env python3
"""bin/devbase wrapper の command dispatch のテスト。

`project` サブコマンドが wrapper の resolve_command 候補と case dispatch に
含まれており、`devbase project ...` が Python 実装へルーティングされることを
検証する (含まれていないと `*)` 節で `unknown command` で終了してしまう)。

実際の `uv run` を起動すると環境依存になるため、run_python / ensure_uv を
差し替えた薄いハーネス経由で wrapper の dispatch ロジックだけを実行する。
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "bin" / "devbase"


def _parse_wrapper_top_prefix_preferences() -> dict[str, str]:
    """bin/devbase の resolve_command 内 ambiguous preference を抽出する。

    `case "$input" in` ... `<input>) preferred="<cmd>" ;;` 形式の対を
    辞書に変換する。cli.py の TOP_PREFIX_PREFERENCES と同期検証するため。
    """
    text = WRAPPER.read_text()
    # resolve_command の case ブロックを切り出す。
    block = text.split('case "$input" in', 1)[1].split("esac", 1)[0]
    prefs: dict[str, str] = {}
    for inp, cmd in re.findall(r'(\w+)\)\s*preferred="(\w+)"', block):
        prefs[inp] = cmd
    return prefs


def _run_wrapper(*args):
    """run_python を no-op に差し替えて wrapper の dispatch だけを実行する。

    wrapper を関数定義のみ読み込む形にできないため、`run_python` /
    `ensure_uv` を export -f で先に定義し、wrapper 末尾の dispatch を
    別プロセスで評価する。wrapper は自身の run_python を再定義するので、
    `sed` で wrapper の run_python / ensure_uv 定義を取り除いてから評価する。
    """
    harness = (
        'run_python() { echo "PYTHON:$*"; exit 0; }\n'
        'ensure_uv() { :; }\n'
        # wrapper から関数再定義を除いた本体を読み込む
        'eval "$(sed -e \'/^run_python()/,/^}/d\' '
        '-e \'/^ensure_uv()/,/^}/d\' "$WRAPPER_PATH")"\n'
    )
    env = {
        **os.environ,
        "DEVBASE_ROOT": str(REPO_ROOT),
        "WRAPPER_PATH": str(WRAPPER),
    }
    return subprocess.run(
        ["bash", "-c", harness, "devbase", *args],
        capture_output=True,
        text=True,
        env=env,
    )


class TestWrapperStaticContent:
    """静的に project が両所に登録されていることを確認 (回帰防止)。"""

    def test_project_in_resolve_command_list(self):
        text = WRAPPER.read_text()
        # resolve_command の候補リスト
        assert " project " in text.split('local commands="', 1)[1].split('"', 1)[0] + " "

    def test_project_in_dispatch_case(self):
        text = WRAPPER.read_text()
        # Python-implemented commands の case ラベルに project が含まれる
        case_labels = [
            line for line in text.splitlines()
            if "run_python " in line and "_resolved_cmd" in line
        ]
        # 直前行 (case パターン) に project があること
        assert any("project|" in line or "|project|" in line
                   for line in text.splitlines())

    def test_top_prefix_preferences_synced_with_cli(self):
        """wrapper と cli.py の top-level ambiguous preference が一致すること。

        `l` → `login` の後方互換 preference は bin/devbase の resolve_command と
        cli.py の TOP_PREFIX_PREFERENCES の 2 箇所に独立して定義されている。
        片方だけ更新して乖離すると個別テストは通るのに挙動が割れるため、
        両者の対応表が完全一致することをここで検証する (正確性指摘 #36)。
        """
        from devbase.cli import TOP_PREFIX_PREFERENCES

        wrapper_prefs = _parse_wrapper_top_prefix_preferences()
        assert wrapper_prefs, "wrapper の preference 抽出に失敗"
        assert wrapper_prefs == TOP_PREFIX_PREFERENCES, (
            f"wrapper={wrapper_prefs} vs cli.py={TOP_PREFIX_PREFERENCES} が乖離"
        )


class TestWrapperDispatch:
    def test_project_reaches_python(self):
        result = _run_wrapper("project", "--help")
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert "PYTHON:project --help" in result.stdout, result.stdout

    def test_project_subcommand_reaches_python(self):
        result = _run_wrapper("project", "up")
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert "PYTHON:project up" in result.stdout, result.stdout

    def test_project_prefix_resolves_to_project(self):
        # `proj` は project に一意に解決される。
        result = _run_wrapper("proj", "up")
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert "PYTHON:project up" in result.stdout, result.stdout

    def test_unknown_command_still_errors(self):
        result = _run_wrapper("bogus")
        assert "unknown command" in result.stderr.lower()
        assert result.returncode != 0

    def test_top_level_list_reaches_python(self):
        """PLAN06 Task 3: `devbase list` シノニムが Python へルーティングされる。"""
        result = _run_wrapper("list")
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert "PYTHON:list" in result.stdout, result.stdout

    def test_top_level_list_interactive_flag_passthrough(self):
        result = _run_wrapper("list", "--interactive")
        assert "PYTHON:list --interactive" in result.stdout, result.stdout

    def test_project_list_reaches_python(self):
        result = _run_wrapper("project", "list")
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert "PYTHON:project list" in result.stdout, result.stdout

    def test_list_prefix_resolves(self):
        # `li` は list に一意解決される (login は lo)。
        result = _run_wrapper("li")
        assert "PYTHON:list" in result.stdout, result.stdout

    def test_l_prefix_resolves_to_login(self):
        # 後方互換: `list` 追加で ambiguous になった `devbase l` を login に維持する
        # (互換性指摘 #36)。preference 無しだと unknown command 'l' になる。
        result = _run_wrapper("l")
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert "PYTHON:login" in result.stdout, result.stdout

    def test_lo_prefix_resolves_to_login(self):
        result = _run_wrapper("lo")
        assert "PYTHON:login" in result.stdout, result.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
