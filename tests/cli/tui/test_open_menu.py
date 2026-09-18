"""PLAN59: `devbase list` の起動中メニューの先頭から、エディタだけを開き直す

先頭は Enter 1 回で届く位置で、窓を閉じた後に開き直すための項目を置く。実行後は
コンテナの数が変わらないため、サブメニューに留まる (login / ps と同じ扱い)。
"""

from __future__ import annotations

import pytest

from devbase.tui import actions_project


def test_open_is_the_first_running_operation():
    """受け入れ条件 12"""
    assert actions_project._RUNNING_OPS[0] == ("エディタを開く (open)", "open")
    # 既存の操作は先頭の後ろにそのまま並ぶ
    assert [v for _label, v in actions_project._RUNNING_OPS[1:4]] == ["up", "down", "login"]


def test_open_dispatches_to_the_shared_handler(monkeypatch, tmp_path):
    """受け入れ条件 13"""
    calls = []
    monkeypatch.setattr(actions_project, "dispatch_lifecycle",
                        lambda sub, name, **attrs: calls.append((sub, name, attrs)) or 0)
    assert actions_project._OP_HANDLERS["open"](tmp_path, "carmo") == 0
    assert calls == [("open", "carmo", {"open_index": None})]


def test_open_stays_in_the_submenu():
    """受け入れ条件 14"""
    assert "open" not in actions_project._BACK_TO_TOP_OPS


@pytest.mark.parametrize('open_rc', [0, 1], ids=['open-success', 'open-failure'])
def test_current_open_result_allows_next_operation(monkeypatch, tmp_path, open_rc):
    """現状固定: open の成否によらず同じプロジェクトの ps を続けて選べる。"""
    from devbase.commands import container

    calls = []
    choices = iter(['open', 'ps', actions_project.menu.MENU_BACK])

    def dispatch(sub, name, **attrs):
        calls.append((sub, name, attrs))
        return open_rc if sub == 'open' else 0

    monkeypatch.setattr(container, 'project_profile_names', lambda name: [])
    monkeypatch.setattr(actions_project.menu, 'select', lambda *a, **kw: next(choices))
    monkeypatch.setattr(actions_project.menu, 'clear_screen', lambda: None)
    monkeypatch.setattr('builtins.input', lambda prompt: '')
    monkeypatch.setattr(actions_project, 'dispatch_lifecycle', dispatch)

    result = actions_project.handle_row(tmp_path, {'name': 'carmo', 'status': 'running'})

    assert result is actions_project.menu.MENU_BACK
    assert calls == [('open', 'carmo', {'open_index': None}),
                     ('ps', 'carmo', {'all': False})]
