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


@pytest.mark.parametrize('candidates', [
    None,
    ([(1, 'myapp-dev-1')], 1),
], ids=['unknown', 'one-running'])
def test_open_dispatches_to_the_shared_handler(monkeypatch, tmp_path, candidates):
    """受け入れ条件 13。動いている dev コンテナが 1 つ以下・数が分からないときは選ばせない"""
    from devbase.commands import container

    calls = []
    monkeypatch.setattr(container, 'project_open_candidates', lambda name: candidates)
    monkeypatch.setattr(actions_project.menu, 'select',
                        lambda *a, **kw: pytest.fail('選択を出さない'))
    monkeypatch.setattr(actions_project, "dispatch_lifecycle",
                        lambda sub, name, **attrs: calls.append((sub, name, attrs)) or 0)
    assert actions_project._OP_HANDLERS["open"](tmp_path, "myapp") == 0
    assert calls == [("open", "myapp", {"open_index": None})]


def test_open_asks_which_container_when_two_or_more_run(monkeypatch, tmp_path):
    """動いている dev コンテナが 2 つ以上なら、開くものを選ばせ、選んだ番号で開く"""
    from devbase.commands import container

    calls, asked = [], {}
    monkeypatch.setattr(container, 'project_open_candidates',
                        lambda name: ([(1, 'myapp-dev-1'), (3, 'myapp-dev-3')], 3))

    def select(message, choices, **kw):
        asked.update(message=message, choices=list(choices), **kw)
        return 3

    monkeypatch.setattr(actions_project.menu, 'select', select)
    monkeypatch.setattr(actions_project, "dispatch_lifecycle",
                        lambda sub, name, **attrs: calls.append((sub, name, attrs)) or 0)

    assert actions_project._OP_HANDLERS["open"](tmp_path, "myapp") == 0
    assert asked['choices'] == [('myapp-dev-1', 1), ('myapp-dev-3', 3)]
    # カーソルの初期位置は既定の番号 (DEVBASE_OPEN_INDEX、無ければ 1)
    assert asked['default'] == 3
    assert asked['back'] is True
    assert calls == [("open", "myapp", {"open_index": 3})]


def test_open_default_falls_back_to_first_running(monkeypatch, tmp_path):
    """既定の番号が動いていなければ、カーソルは動いているものの先頭に置く"""
    from devbase.commands import container

    asked = {}
    monkeypatch.setattr(container, 'project_open_candidates',
                        lambda name: ([(2, 'myapp-dev-2'), (3, 'myapp-dev-3')], 1))
    monkeypatch.setattr(actions_project.menu, 'select',
                        lambda message, choices, **kw: asked.update(kw) or 2)
    monkeypatch.setattr(actions_project, "dispatch_lifecycle", lambda sub, name, **attrs: 0)

    actions_project._OP_HANDLERS["open"](tmp_path, "myapp")
    assert asked['default'] == 2


@pytest.mark.parametrize('answer,expected', [
    (actions_project.menu.MENU_BACK, actions_project._ARG_CANCEL),
    (None, None),
], ids=['esc-back-to-submenu', 'ctrl-c-abort'])
def test_open_pick_can_be_cancelled(monkeypatch, tmp_path, answer, expected):
    """選択で Esc ならサブメニューへ戻り、Ctrl-C なら全体を中止する。どちらも開かない"""
    from devbase.commands import container

    monkeypatch.setattr(container, 'project_open_candidates',
                        lambda name: ([(1, 'myapp-dev-1'), (2, 'myapp-dev-2')], 1))
    monkeypatch.setattr(actions_project.menu, 'select', lambda *a, **kw: answer)
    monkeypatch.setattr(actions_project, "dispatch_lifecycle",
                        lambda *a, **kw: pytest.fail('開かない'))

    assert actions_project._run_operation(tmp_path, "myapp", "open") is expected


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
    monkeypatch.setattr(container, 'project_open_candidates', lambda name: None)
    monkeypatch.setattr(actions_project.menu, 'select', lambda *a, **kw: next(choices))
    monkeypatch.setattr(actions_project.menu, 'clear_screen', lambda: None)
    monkeypatch.setattr('builtins.input', lambda prompt: '')
    monkeypatch.setattr(actions_project, 'dispatch_lifecycle', dispatch)

    result = actions_project.handle_row(tmp_path, {'name': 'myapp', 'status': 'running'})

    assert result is actions_project.menu.MENU_BACK
    assert calls == [('open', 'myapp', {'open_index': None}),
                     ('ps', 'myapp', {'all': False})]
