#!/usr/bin/env python3
"""bin/devbase wrapper の command dispatch のテスト。

`project` サブコマンドが wrapper の resolve_command 候補と case dispatch に
含まれており、`devbase project ...` が Python 実装へルーティングされることを
検証する (含まれていないと `*)` 節で `unknown command` で終了してしまう)。

実際の `uv run` を起動すると環境依存になるため、`bin/devbase` の複製を偽の `uv` と
一緒に起動する exec_wrapper (tests/cli/conftest.py) で、`devbase.cli` へ渡る引数を確かめる。
"""

import re
import sys
from pathlib import Path

import pytest

from tests.cli.conftest import stdout_field

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


def _cli_args(result) -> str | None:
    """偽の uv が受けた呼び出しのうち、`devbase.cli` へ渡った引数を返す。届いていなければ None。"""
    uv = stdout_field(result, "UV:")
    if uv is None or " -m devbase.cli" not in uv:
        return None
    return uv.split(" -m devbase.cli", 1)[1].strip()


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
    def test_project_reaches_python(self, exec_wrapper):
        result = exec_wrapper.run(["project", "--help"])
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert _cli_args(result) == "project --help", result.stdout

    def test_project_subcommand_reaches_python(self, exec_wrapper):
        result = exec_wrapper.run(["project", "up"])
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert _cli_args(result) == "project up", result.stdout

    def test_project_prefix_resolves_to_project(self, exec_wrapper):
        # `proj` は project に一意に解決される。
        result = exec_wrapper.run(["proj", "up"])
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert _cli_args(result) == "project up", result.stdout

    def test_unknown_command_still_errors(self, exec_wrapper):
        result = exec_wrapper.run(["bogus"])
        assert "unknown command" in result.stderr.lower()
        assert result.returncode != 0

    def test_top_level_list_reaches_python(self, exec_wrapper):
        """PLAN06 Task 3: `devbase list` シノニムが Python へルーティングされる。"""
        result = exec_wrapper.run(["list"])
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert _cli_args(result) == "list", result.stdout

    def test_top_level_list_interactive_flag_passthrough(self, exec_wrapper):
        result = exec_wrapper.run(["list", "--interactive"])
        assert _cli_args(result) == "list --interactive", result.stdout

    def test_project_list_reaches_python(self, exec_wrapper):
        result = exec_wrapper.run(["project", "list"])
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert _cli_args(result) == "project list", result.stdout

    def test_list_prefix_resolves(self, exec_wrapper):
        # `li` は list に一意解決される (login は lo)。
        result = exec_wrapper.run(["li"])
        assert _cli_args(result) == "list", result.stdout

    def test_l_prefix_resolves_to_login(self, exec_wrapper):
        # 後方互換: `list` 追加で ambiguous になった `devbase l` を login に維持する
        # (互換性指摘 #36)。preference 無しだと unknown command 'l' になる。
        result = exec_wrapper.run(["l"])
        assert "unknown command" not in result.stderr.lower(), result.stderr
        assert _cli_args(result) == "login", result.stdout

    def test_lo_prefix_resolves_to_login(self, exec_wrapper):
        result = exec_wrapper.run(["lo"])
        assert _cli_args(result) == "login", result.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
