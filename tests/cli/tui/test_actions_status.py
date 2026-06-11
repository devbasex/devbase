"""PLAN31_2 PR5: tui.actions_status (status 閲覧) のテスト。

status は引数なしの閲覧のみ (`cmd_status(devbase_root)` / plan 2.2)。
`cmd_status` を mock し、devbase_root だけで呼ばれて rc がそのまま返ることを検証する。
"""

from __future__ import annotations

import pytest

from devbase.tui import actions_status


@pytest.mark.parametrize("rc", [0, 1])
def test_run_delegates_to_cmd_status_and_returns_rc(monkeypatch, tmp_path, rc):
    """cmd_status(devbase_root) へ委譲し、rc (0/非0) をそのままトップへ返す。"""
    captured = {}

    def _spy(devbase_root):
        captured["devbase_root"] = devbase_root
        return rc

    monkeypatch.setattr(actions_status, "cmd_status", _spy)
    assert actions_status.run(tmp_path) == rc
    assert captured["devbase_root"] == tmp_path
