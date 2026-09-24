"""復元の確認分岐を、疑似ボリュームの最終状態で現状固定する。"""

from types import SimpleNamespace

import pytest

from devbase.commands import snapshot


@pytest.mark.parametrize(
    "is_tty,answer,expected_content",
    [
        (True, "", "original"),
        (True, "n", "original"),
        (True, "y", "saved at point 2"),
        (True, "YES", "saved at point 2"),
        (False, None, "saved at point 2"),
    ],
)
def test_restore_confirmation(tmp_path, monkeypatch, is_tty, answer, expected_content):
    volume = {"content": "original"}
    saved = {("daily", 2): "saved at point 2"}

    class FakeSnapshotManager:
        def __init__(self, devbase_root):
            pass

        def restore(self, name, point=None):
            volume["content"] = saved[name, point]

    def respond(prompt):
        if not is_tty:
            pytest.fail("Non-TTY restore must not prompt for input")
        return answer

    monkeypatch.setattr(snapshot, "SnapshotManager", FakeSnapshotManager)
    monkeypatch.setattr(snapshot.sys, "stdin", SimpleNamespace(isatty=lambda: is_tty))
    monkeypatch.setattr("builtins.input", respond)
    args = SimpleNamespace(subcommand="restore", name="daily", point=2)

    assert snapshot.cmd_snapshot(tmp_path, args) == 0
    assert volume["content"] == expected_content
