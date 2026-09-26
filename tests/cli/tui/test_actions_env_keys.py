"""TUI のキーの一覧と編集 (#273 F1〜F4)

``menu.*`` を台本で差し替えて操作を流し込み、偽の OpenBao サーバとファイルの backend の
上で一覧・追加・変更・削除を確かめる。
"""

from __future__ import annotations

import inspect
import io
import logging
import os
from contextlib import redirect_stdout

import pytest

from devbase.commands import env as env_cmd
from devbase.commands.env_rows import KeyListing, KeyRow
from devbase.env import agekeys
from devbase.env.secret_store import SecretRef, SecretStore
from devbase.tui import actions_env_keys as keys_ui
from devbase.tui import flow, menu

TEAM = 'team/team-a/global'
USER = 'users/member01/team-a/global'


class Script:
    """``menu.select`` / ``text`` / ``secret`` / ``confirm`` を順に答える台本"""

    def __init__(self, monkeypatch, **queues):
        self.calls = []
        for kind in ('select', 'text', 'secret', 'confirm'):
            monkeypatch.setattr(menu, kind, self._make(kind, list(queues.get(kind, []))))
        monkeypatch.setattr(menu, 'clear_screen', lambda: None)
        monkeypatch.setattr(flow, 'pause_for_review', lambda: True)

    def _make(self, kind, queue):
        def answer(message, *args, **kwargs):
            self.calls.append((kind, message, args[0] if args else None))
            if not queue:
                pytest.fail(f'台本に無い {kind}: {message}')
            return queue.pop(0)
        return answer

    def messages(self, kind='select'):
        return [m for k, m, _ in self.calls if k == kind]

    def choices_of(self, prefix):
        return [c for k, m, c in self.calls if k == 'select' and m.startswith(prefix)]


def logs(caplog) -> str:
    return '\n'.join(r.getMessage() for r in caplog.records)


@pytest.fixture
def grouped(openbao_root, openbao):
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, layout='group')
    (openbao_root / 'env').write_text('DEVBASE_ACCOUNT_GROUP=team-a\n')
    (openbao_root / 'projects' / 'web' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=team-a\n')
    return openbao_root


def run(root):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = keys_ui.run(root)
    return rc, out.getvalue()


# ---------------------------------------------------------------------------
# 一覧
# ---------------------------------------------------------------------------

def test_rows_show_the_place_and_mask_the_values(grouped, openbao, monkeypatch):
    openbao.put(TEAM, {'A': 'plain-team-value'})
    openbao.put(USER, {'B': 'plain-user-value'})
    script = Script(monkeypatch, select=['global', 'team-a', menu.MENU_BACK])

    rc, _ = run(grouped)

    assert rc is flow.ARG_CANCEL
    [choices] = script.choices_of('キーの一覧')
    titles = [t for t, _ in choices]
    assert titles[0] == '＋ キーを追加'
    row_a = next(t for t in titles if t.startswith('A '))
    row_b = next(t for t in titles if t.startswith('B '))
    assert 'チーム' in row_a and '個人' in row_b
    for row in (row_a, row_b):
        assert '共通' in row and 'team-a' in row and '******' in row
    assert not any('plain-' in t for t in titles)
    assert 'キーの一覧 2 件' in script.messages()[-1]


def test_rows_carry_no_winning_mark(grouped, openbao, monkeypatch):
    openbao.put(TEAM, {'K': 't'})
    openbao.put(USER, {'K': 'u'})
    script = Script(monkeypatch, select=['global', 'team-a', menu.MENU_BACK])

    run(grouped)

    [choices] = script.choices_of('キーの一覧')
    rows = [t for t, v in choices if v != keys_ui.ADD]
    assert len(rows) == 2 and all(t.startswith('K ') for t in rows)
    assert not any('★' in t for t in rows)


def test_version_one_does_not_ask_for_a_group(openbao_root, openbao, monkeypatch):
    openbao.put('team/global', {'A': '1'})
    script = Script(monkeypatch, select=['global', menu.MENU_BACK])

    run(openbao_root)

    assert not any('グループ' in m for m in script.messages())


def test_an_unreachable_server_prints_one_line_and_returns_to_the_menu(
        grouped, openbao, monkeypatch, caplog):
    openbao.stop()
    Script(monkeypatch, select=['global', 'team-a'])

    rc, _ = run(grouped)

    assert rc == 1
    assert 'キーの一覧を読めません（共通・グループ team-a）' in logs(caplog)


def test_a_forbidden_ref_prints_one_line_and_returns_to_the_menu(grouped, openbao, monkeypatch,
                                                                 caplog):
    openbao.forbidden_prefixes = ['users/']
    Script(monkeypatch, select=['global', 'team-a'])

    rc, _ = run(grouped)

    assert rc == 1
    assert '読めません' in logs(caplog)


def test_an_unusable_group_name_goes_back_to_the_group_selection(grouped, monkeypatch, caplog):
    script = Script(monkeypatch, select=['global', keys_ui.TYPE_GROUP, menu.MENU_BACK,
                                         menu.MENU_BACK],
                    text=['../x'])

    rc, _ = run(grouped)

    assert rc is flow.ARG_CANCEL
    assert '--group に使えない名前です' in logs(caplog)
    assert len([m for m in script.messages() if m.startswith('グループを選択')]) == 2


def test_the_project_scope_is_not_offered_without_projects(openbao_root, openbao, monkeypatch):
    (openbao_root / 'projects' / 'web').rmdir()
    script = Script(monkeypatch, select=[menu.MENU_BACK])

    run(openbao_root)

    [choices] = script.choices_of('キーの範囲')
    assert [v for _, v in choices] == ['global']


# ---------------------------------------------------------------------------
# 追加・変更・削除
# ---------------------------------------------------------------------------

def test_adding_a_user_key_writes_the_user_global_of_the_group(grouped, openbao, monkeypatch,
                                                              capsys):
    Script(monkeypatch, select=['global', 'team-a', keys_ui.ADD, 'user', menu.MENU_BACK],
           text=['K'], secret=['v'])

    rc, _ = run(grouped)

    assert rc is flow.ARG_CANCEL
    assert openbao.get(USER) == {'K': 'v'}
    assert openbao.get(TEAM) == {}
    capsys.readouterr()
    assert env_cmd.cmd_env_get(grouped, 'K', user=True, group='team-a') == 0
    assert capsys.readouterr().out == 'v\n'


def test_changing_a_value_is_read_back_by_get(grouped, openbao, monkeypatch, capsys):
    openbao.put(TEAM, {'K': 'old'})
    assert env_cmd.cmd_env_get(grouped, 'K') == 0          # キャッシュに old を控える
    Script(monkeypatch, select=['global', 'team-a', 0, 'change', menu.MENU_BACK],
           secret=['new'])

    run(grouped)

    capsys.readouterr()
    assert env_cmd.cmd_env_get(grouped, 'K') == 0
    assert capsys.readouterr().out == 'new\n'


def ref_listing(ref, *, project=None):
    row = KeyRow(key='K', ref=ref, owner_label='x', scope_label='x', group_label=None)
    return KeyListing(rows=[row], refs=[ref], has_user_refs=True, grouped=True,
                      group=ref.group if ref.kind == 'global' else None, project=project,
                      backend='openbao')


@pytest.mark.parametrize('ref, attrs, where', [
    (SecretRef.for_global(group='g'), {'project': False, 'user': False, 'group': 'g'}, ''),
    (SecretRef.for_global(owner='user', group='g'), {'project': False, 'user': True, 'group': 'g'},
     ''),
    (SecretRef.for_project('web', group='g'), {'project': True, 'user': False, 'group': None},
     'projects/web'),
    (SecretRef.for_project('web', owner='user', group='g'),
     {'project': True, 'user': True, 'group': None}, 'projects/web'),
])
@pytest.mark.parametrize('op', ['change', 'delete'])
def test_writes_are_delegated_with_the_mapped_attributes(tmp_path, monkeypatch, ref, attrs, where,
                                                         op):
    from devbase.commands import env_rows

    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(keys_ui, '_grouped', lambda root: False)
    monkeypatch.setattr(env_rows, 'collect_key_rows',
                        lambda root, project=None, group=None: ref_listing(ref))
    captured = {}

    def spy(root, args):
        captured.update(vars(args), pwd=os.environ.get('PWD'))
        return 0

    monkeypatch.setattr(env_cmd, 'cmd_env', spy)
    Script(monkeypatch, select=['global', 0, op, menu.MENU_BACK], secret=['v'], confirm=[True])

    keys_ui.run(tmp_path)

    expected = dict(attrs, subcommand='set' if op == 'change' else 'delete')
    expected.update({'assignment': 'K=v'} if op == 'change' else {'key': 'K'})
    assert {k: captured[k] for k in expected} == expected
    assert captured['pwd'] == (str(tmp_path / where) if where else None)


def test_values_do_not_reach_the_output_logs_or_subprocesses(grouped, openbao, monkeypatch,
                                                            caplog, capsys):
    import subprocess

    def forbid(*a, **k):
        raise AssertionError('子プロセスを起動してはいけない')

    for name in ('run', 'Popen', 'call', 'check_call', 'check_output'):
        monkeypatch.setattr(subprocess, name, forbid)
    caplog.set_level(logging.DEBUG)
    Script(monkeypatch, select=['global', 'team-a', keys_ui.ADD, 'team', menu.MENU_BACK],
           text=['K'], secret=['very-secret-value'])

    _, out = run(grouped)

    captured = capsys.readouterr()
    assert openbao.get(TEAM) == {'K': 'very-secret-value'}
    for text in (out, captured.out, captured.err, logs(caplog)):
        assert 'very-secret-value' not in text


def test_bad_keys_and_values_are_asked_again_without_writing(grouped, openbao, monkeypatch,
                                                             caplog):
    calls = []
    real = env_cmd.cmd_env_set
    monkeypatch.setattr(env_cmd, 'cmd_env_set', lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    script = Script(monkeypatch,
                    select=['global', 'team-a', keys_ui.ADD, 'team', menu.MENU_BACK],
                    text=['1BAD', 'DEVBASE_ACCOUNT_GROUP', 'GOOD'],
                    secret=['', 'a\nb', ' v ', 'v'])

    run(grouped)

    assert calls == ['GOOD=v']
    text = logs(caplog)
    for message in (keys_ui.MSG_BAD_KEY, keys_ui.MSG_ACCOUNT_GROUP, keys_ui.MSG_EMPTY_VALUE,
                    keys_ui.MSG_MULTILINE, keys_ui.MSG_SURROUNDING_SPACE):
        assert message in text
    assert len(script.messages('text')) == 3 and len(script.messages('secret')) == 4


@pytest.mark.parametrize('answer, deleted', [(True, True), (False, False),
                                             (menu.MENU_BACK, False)])
def test_delete_only_after_yes(grouped, openbao, monkeypatch, answer, deleted):
    openbao.put(TEAM, {'K': '1'})
    Script(monkeypatch, select=['global', 'team-a', 0, 'delete', menu.MENU_BACK],
           confirm=[answer])

    run(grouped)

    assert ('K' not in openbao.get(TEAM)) is deleted


def test_a_version_conflict_does_not_overwrite_and_asks_to_reload(grouped, openbao, monkeypatch,
                                                                  caplog):
    from devbase.env.openbao import OpenBaoBackend

    openbao.put(TEAM, {'K': 'old'})
    real_save = OpenBaoBackend.save

    def racing_save(self, ref, data):
        openbao.put(TEAM, {'K': 'theirs'})             # 読んでから書くまでの間に他の誰かが書く
        return real_save(self, ref, data)

    monkeypatch.setattr(OpenBaoBackend, 'save', racing_save)
    script = Script(monkeypatch, select=['global', 'team-a', 0, 'change', menu.MENU_BACK],
                    secret=['mine'])

    run(grouped)

    assert openbao.get(TEAM) == {'K': 'theirs'}
    assert '読み直します' in logs(caplog)
    assert len(script.choices_of('キーの一覧')) == 2      # 一覧を読み直して出す


def test_the_tui_never_writes_the_store_itself(grouped, openbao, monkeypatch):
    real = SecretStore.save

    def guarded(self, ref, data):
        caller = inspect.stack()[1].frame.f_globals.get('__name__', '')
        assert not caller.startswith('devbase.tui'), caller
        return real(self, ref, data)

    monkeypatch.setattr(SecretStore, 'save', guarded)
    Script(monkeypatch, select=['global', 'team-a', keys_ui.ADD, 'team', menu.MENU_BACK],
           text=['K'], secret=['v'])

    run(grouped)

    assert openbao.get(TEAM) == {'K': 'v'}


# ---------------------------------------------------------------------------
# プロジェクトの範囲 (#312)
# ---------------------------------------------------------------------------

def test_the_project_list_shows_only_the_project_rows(grouped, openbao, monkeypatch):
    openbao.put(TEAM, {'G': 'x'})
    openbao.put(USER, {'U': 'x'})
    openbao.put('team/team-a/projects/web', {'P': 'x'})
    openbao.put('users/member01/team-a/projects/web', {'Q': 'x'})
    script = Script(monkeypatch, select=['project', 'web', menu.MENU_BACK])

    run(grouped)

    [choices] = script.choices_of('キーの一覧')
    rows = [t for t, v in choices if v != keys_ui.ADD]
    assert [t.split()[0] for t in rows] == ['P', 'Q']
    assert all('プロジェクト web' in t for t in rows)
    assert 'キーの一覧 2 件（参照 2 件' in script.messages()[-1]


def test_adding_in_a_project_scope_writes_the_project_without_asking_the_scope(
        grouped, openbao, monkeypatch):
    script = Script(monkeypatch, select=['project', 'web', keys_ui.ADD, 'user', menu.MENU_BACK],
                    text=['K'], secret=['v'])

    run(grouped)

    assert openbao.get('users/member01/team-a/projects/web') == {'K': 'v'}
    assert openbao.get(USER) == {}
    assert not any(m.startswith('適用範囲') for m in script.messages())


def test_the_project_choices_show_key_counts_and_no_status(grouped, openbao, monkeypatch):
    (grouped / 'projects' / 'api').mkdir()
    (grouped / 'projects' / 'api' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=team-a\n')
    openbao.put('team/team-a/projects/web', {'P': 'x', 'R': 'x'})
    openbao.put('users/member01/team-a/projects/web', {'Q': 'x'})
    script = Script(monkeypatch, select=['project', menu.MENU_BACK, menu.MENU_BACK])

    run(grouped)

    [choices] = script.choices_of('対象プロジェクト')
    assert choices == [('api  キー 0 件', 'api'), ('web  キー 3 件', 'web')]
    assert not any(s in t for t, _ in choices for s in ('running', 'stopped'))


def test_an_unreadable_project_shows_a_question_mark(grouped, openbao, monkeypatch):
    openbao.forbidden_prefixes = ['users/member01/team-a/projects/']
    script = Script(monkeypatch, select=['project', menu.MENU_BACK, menu.MENU_BACK])

    run(grouped)

    [choices] = script.choices_of('対象プロジェクト')
    assert choices == [('web  キー ? 件', 'web')]


def test_back_from_the_project_choice_returns_to_the_scope(grouped, monkeypatch):
    script = Script(monkeypatch, select=['project', menu.MENU_BACK, menu.MENU_BACK])

    rc, _ = run(grouped)

    assert rc is flow.ARG_CANCEL
    assert [m.split()[0] for m in script.messages()] == ['キーの範囲を選択', '対象プロジェクトを選択',
                                                         'キーの範囲を選択']


def test_the_project_choice_goes_back_with_the_left_key(grouped, monkeypatch):
    seen = {}

    def fake_select(message, choices, **kwargs):
        seen.update(kwargs)
        return menu.MENU_BACK

    monkeypatch.setattr(menu, 'select', fake_select)
    with pytest.raises(flow.BackOut):
        keys_ui._select_project(grouped)
    assert seen == {'back': True, 'search': True, 'left_back': True}
    assert '←・Esc 戻る' in menu.HINT_SEARCH_LEFT


def test_the_project_choice_goes_back_when_no_project_is_left(grouped, monkeypatch):
    from devbase.commands import env_rows

    monkeypatch.setattr(env_rows, 'count_project_keys', lambda root: [])
    with pytest.raises(flow.BackOut):
        keys_ui._select_project(grouped)


# ---------------------------------------------------------------------------
# ファイルの backend
# ---------------------------------------------------------------------------

@pytest.fixture
def file_root(tmp_path, monkeypatch):
    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('PWD', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_a_plaintext_backend_has_no_owner_choice_and_writes_the_env(file_root, monkeypatch):
    (file_root / '.env').write_text('A=1\n')
    script = Script(monkeypatch, select=['global', keys_ui.ADD, menu.MENU_BACK],
                    text=['K'], secret=['v'])

    rc, _ = run(file_root)

    assert rc is flow.ARG_CANCEL
    assert 'K=v' in (file_root / '.env').read_text()
    assert not any(m.startswith('持ち主') for m in script.messages())
    assert all('個人' not in t for c in script.choices_of('キーの一覧') for t, _ in c)


def test_an_age_backend_writes_the_encrypted_global(file_root, monkeypatch):
    agekeys.generate_key_file()
    store = SecretStore(file_root)
    store.age.save(SecretRef.for_global(), {'A': '1'})
    Script(monkeypatch, select=['global', keys_ui.ADD, menu.MENU_BACK], text=['K'], secret=['v'])

    run(file_root)

    assert (file_root / 'secrets' / 'global.env.age').is_file()
    assert not (file_root / '.env').exists()
    assert SecretStore(file_root).load(SecretRef.for_global()) == {'A': '1', 'K': 'v'}
