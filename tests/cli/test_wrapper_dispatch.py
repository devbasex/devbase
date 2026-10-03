#!/usr/bin/env python3
"""bin/devbase wrapper の command dispatch のテスト。

`project` サブコマンドが wrapper の resolve_command 候補と case dispatch に
含まれており、`devbase project ...` が Python 実装へルーティングされることを
検証する (含まれていないと `*)` 節で `unknown command` で終了してしまう)。

wrapper は `exec_wrapper` (conftest.py) で動く。本物の bin/devbase を tmp へ複製して起動し、
外への呼び出しの境界の `uv` だけを差し替える。Python へ届いたことは ` devbase.cli <args>` で
終わる `UV:` 行で確かめる。
"""

import re
import sys
from pathlib import Path

import pytest

from tests.cli.conftest import python_args

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


@pytest.fixture
def run_wrapper(exec_wrapper):
    return lambda *args: exec_wrapper(list(args))


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
    def test_project_reaches_python(self, run_wrapper):
        result = run_wrapper("project", "--help")
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert python_args(result) == "project --help", result.stdout

    def test_project_subcommand_reaches_python(self, run_wrapper):
        result = run_wrapper("project", "up")
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert python_args(result) == "project up", result.stdout

    def test_project_prefix_resolves_to_project(self, run_wrapper):
        # `proj` は project に一意に解決される。
        result = run_wrapper("proj", "up")
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert python_args(result) == "project up", result.stdout

    def test_unknown_command_still_errors(self, run_wrapper):
        result = run_wrapper("bogus")
        assert "unknown command" in result.stderr.lower()
        assert result.returncode != 0

    def test_top_level_list_reaches_python(self, run_wrapper):
        """PLAN06 Task 3: `devbase list` シノニムが Python へルーティングされる。"""
        result = run_wrapper("list")
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert python_args(result) == "list", result.stdout

    def test_top_level_list_interactive_flag_passthrough(self, run_wrapper):
        result = run_wrapper("list", "--interactive")
        assert python_args(result) == "list --interactive", result.stdout

    def test_project_list_reaches_python(self, run_wrapper):
        result = run_wrapper("project", "list")
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert python_args(result) == "project list", result.stdout

    def test_list_prefix_resolves(self, run_wrapper):
        # `li` は list に一意解決される (login は lo)。
        result = run_wrapper("li")
        assert python_args(result) == "list", result.stdout

    def test_l_prefix_resolves_to_login(self, run_wrapper):
        # 後方互換: `list` 追加で ambiguous になった `devbase l` を login に維持する
        # (互換性指摘 #36)。preference 無しだと unknown command 'l' になる。
        result = run_wrapper("l")
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert python_args(result) == "login", result.stdout

    def test_lo_prefix_resolves_to_login(self, run_wrapper):
        result = run_wrapper("lo")
        assert python_args(result) == "login", result.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
