"""未指定・未知の snapshot サブコマンドの終了コードを現状固定する。"""

from types import SimpleNamespace

import pytest

from devbase.commands.snapshot import cmd_snapshot


@pytest.mark.parametrize("subcommand", [None, "bogus"])
def test_missing_or_unknown_subcommand_returns_error(tmp_path, subcommand):
    args = SimpleNamespace(subcommand=subcommand)

    assert cmd_snapshot(tmp_path, args) == 1
