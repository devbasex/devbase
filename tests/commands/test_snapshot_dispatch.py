"""未指定・未知の snapshot サブコマンドの終了コードを現状固定する。"""

from types import SimpleNamespace

import pytest

from devbase.commands.snapshot import cmd_snapshot


@pytest.mark.parametrize("subcommand", [None, "bogus"])
def test_missing_or_unknown_subcommand_returns_error(tmp_path, subcommand):
    args = SimpleNamespace(subcommand=subcommand)

    assert cmd_snapshot(tmp_path, args) == 1


def test_handler_snapshot_error_returns_error(tmp_path, monkeypatch):
    """handler が SnapshotError を送出すると、捕えて 1 を返す (未指定・未知とは別経路)。"""
    from devbase.errors import SnapshotError
    from devbase.snapshot.manager import SnapshotManager

    def boom(self, name, new_name):
        raise SnapshotError("copy failed")

    monkeypatch.setattr(SnapshotManager, 'copy', boom)
    args = SimpleNamespace(subcommand="copy", name="a", new_name="b")

    assert cmd_snapshot(tmp_path, args) == 1
