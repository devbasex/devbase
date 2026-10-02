"""TUI の OpenBao の接続設定 (#273 F8・F9)"""

from __future__ import annotations

import dataclasses
import io
import logging
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from devbase.commands import env_backend
from devbase.env import agekeys, backend_config as bc, bootstrap
from devbase.tui import actions_env_openbao as openbao_ui
from devbase.tui import flow, menu

from tests.cli.tui.test_actions_env_keys import Script, logs


def run(root):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = openbao_ui.run(root)
    return rc, out.getvalue()


def snapshot(root: Path):
    return {name: (root / 'secrets' / name).read_bytes()
            for name in ('backend.yml', 'bootstrap.env.age')
            if (root / 'secrets' / name).exists()}


@pytest.fixture
def file_root(tmp_path, monkeypatch):
    (tmp_path / 'projects').mkdir()
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('PWD', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize('prepare', ['unset', 'age'])
def test_a_file_backend_only_shows_the_guidance(file_root, monkeypatch, prepare):
    if prepare == 'age':
        agekeys.generate_key_file()
        bc.save(file_root, bc.BackendConfig(backend='age'))
    before = snapshot(file_root)
    Script(monkeypatch)             # 欄を 1 つも出さない

    rc, out = run(file_root)

    assert rc == 0
    assert 'devbase env backend use openbao --url <OpenBao の URL> --role-id <role_id> ' \
           '--secret-id-stdin' in out
    assert 'devbase env backend migrate --to openbao' in out
    assert ('age' if prepare == 'age' else 'plaintext') in out
    assert snapshot(file_root) == before


def test_changing_only_the_url_keeps_everything_else(openbao_root, openbao, monkeypatch):
    before = bc.load(openbao_root)
    creds = (openbao_root / 'secrets' / 'bootstrap.env.age').read_bytes()
    url = f'http://localhost:{openbao.port}'
    Script(monkeypatch, text=[url], secret=['', ''])

    rc, out = run(openbao_root)

    assert rc == 0
    after = bc.load(openbao_root)
    assert after.openbao.url == url
    assert dataclasses.replace(after.openbao, url=before.openbao.url) == before.openbao
    assert (after.version, after.cache_enabled) == (before.version, before.cache_enabled)
    assert (openbao_root / 'secrets' / 'bootstrap.env.age').read_bytes() == creds
    assert '読めた参照' in out          # 保存の後に接続を確かめる


def test_a_non_https_url_is_not_saved(openbao_root, monkeypatch, caplog):
    before = snapshot(openbao_root)
    Script(monkeypatch, text=['http://openbao.example.com', menu.MENU_BACK], secret=['', ''])

    rc, _ = run(openbao_root)

    assert rc is flow.ARG_CANCEL
    assert snapshot(openbao_root) == before
    assert 'https' in logs(caplog)


def test_a_new_secret_id_is_saved_masked_and_no_token_is_written(openbao_root, openbao,
                                                                  monkeypatch, caplog, capsys):
    import subprocess

    def forbid(*a, **k):
        raise AssertionError('子プロセスを起動してはいけない')

    for name in ('run', 'Popen', 'call', 'check_call', 'check_output'):
        monkeypatch.setattr(subprocess, name, forbid)
    caplog.set_level(logging.DEBUG)
    openbao.role_id, openbao.secret_id = 'fresh-role-id', 'fresh-secret-id'
    script = Script(monkeypatch, text=[''], secret=['fresh-role-id', 'fresh-secret-id'])

    rc, out = run(openbao_root)

    assert rc == 0
    assert bootstrap.load(openbao_root) == bootstrap.Credentials(openbao.role_id,
                                                                 'fresh-secret-id')
    assert [k for k, _, _ in script.calls] == ['text', 'secret', 'secret']
    captured = capsys.readouterr()
    for text in (out, captured.out, captured.err, logs(caplog)):
        assert 'fresh-secret-id' not in text and 'fresh-role-id' not in text
    assert openbao.token is not None
    for path in openbao_root.rglob('*'):
        if path.is_file():
            assert openbao.token.encode() not in path.read_bytes(), path


def test_empty_fields_change_nothing(openbao_root, monkeypatch):
    before = snapshot(openbao_root)
    monkeypatch.setattr(env_backend, 'cmd_env_backend_use',
                        lambda *a: pytest.fail('use を呼んではいけない'))
    Script(monkeypatch, text=[''], secret=['', ''])

    rc, out = run(openbao_root)

    assert rc == 0
    assert '変更はありません' in out
    assert snapshot(openbao_root) == before


def test_a_new_role_id_needs_a_secret_id(openbao_root, monkeypatch, caplog):
    before = snapshot(openbao_root)
    Script(monkeypatch, text=['', menu.MENU_BACK], secret=['rid-2', ''])

    rc, _ = run(openbao_root)

    assert rc is flow.ARG_CANCEL
    assert 'role_id を変えるときは secret_id も入れてください' in logs(caplog)
    assert snapshot(openbao_root) == before


def test_a_failed_check_keeps_the_saved_settings(openbao_root, openbao, monkeypatch):
    script = Script(monkeypatch, text=[''], secret=['', 'wrong-secret'], confirm=[False])

    rc, _ = run(openbao_root)

    assert rc == 1
    assert bootstrap.load(openbao_root).secret_id == 'wrong-secret'
    assert script.messages('confirm')


def test_the_heading_shows_the_url_and_whether_credentials_are_set(openbao_root, openbao,
                                                                   monkeypatch):
    Script(monkeypatch, text=[menu.MENU_BACK])

    _, out = run(openbao_root)

    assert openbao.url in out
    assert '設定済み' in out


# -- グループ別の置き場での確認 (#386) ----------------------------------------------

@pytest.fixture
def grouped(openbao_root, openbao):
    """version: 2・layout: group。projects/web は acme を宣言している (openbao_root)"""
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, layout='group')
    return openbao_root


def kv_paths(openbao):
    return {r.kv_path for r in openbao.received if r.kv_path}


def local_url(openbao):
    return f'http://localhost:{openbao.port}'


def test_outside_projects_the_check_uses_the_chosen_group(grouped, openbao, monkeypatch,
                                                         caplog):
    """AC1・AC2: プロジェクトの外では選んだグループで確かめ、成功で終わる"""
    script = Script(monkeypatch, text=[local_url(openbao)], secret=['', ''], select=['acme'])

    rc, out = run(grouped)

    assert rc == 0
    assert '読めた参照' in out
    assert script.messages('select') == [f'グループを選択 {menu.HINT_BACK}:']
    assert not script.messages('confirm')
    assert '接続を確かめられませんでした' not in logs(caplog)
    paths = kv_paths(openbao)
    assert 'team/acme/global' in paths
    assert all('/acme/' in p for p in paths), paths       # --group acme と同じ置き場
    assert bc.load(grouped).version == 2


def test_outside_projects_a_failed_check_still_offers_to_retry(grouped, openbao, monkeypatch,
                                                              caplog):
    """AC3: 認証に失敗すれば、グループを選んだ後に今と同じ警告と入れ直しの問い"""
    script = Script(monkeypatch, text=[''], secret=['', 'wrong-secret'], select=['acme'],
                    confirm=[False])

    rc, _ = run(grouped)

    assert rc == 1
    assert '接続を確かめられませんでした。保存した設定は残っています' in logs(caplog)
    assert script.messages('confirm') == ['接続設定を入れ直しますか?']
    assert bootstrap.load(grouped).secret_id == 'wrong-secret'


def test_backing_out_of_the_group_skips_the_check(grouped, openbao, monkeypatch, caplog):
    """AC4: グループの選択で戻ると確かめずに戻る。保存した設定は残り、入れ直しは問わない"""
    monkeypatch.setattr(env_backend, 'cmd_env_backend_test',
                        lambda *a, **k: pytest.fail('test を呼んではいけない'))
    script = Script(monkeypatch, text=[''], secret=['', 'new-secret'],
                    select=[menu.MENU_BACK])

    rc, out = run(grouped)

    assert rc is flow.ARG_CANCEL
    assert '保存した設定は残っています' in out
    assert not script.messages('confirm')
    assert bootstrap.load(grouped).secret_id == 'new-secret'


def test_an_unusable_group_name_returns_to_the_selection(grouped, openbao, monkeypatch,
                                                        caplog):
    """AC5: 使えない名前は --group と同じ検証の文を出して選択へ戻る"""
    from devbase.tui.actions_env_keys import TYPE_GROUP

    script = Script(monkeypatch, text=[local_url(openbao), 'Bad Name!'], secret=['', ''],
                    select=[TYPE_GROUP, 'acme'])

    rc, out = run(grouped)

    assert rc == 0
    assert '--group に使えない名前です' in logs(caplog)
    assert len(script.messages('select')) == 2
    assert '読めた参照' in out


def test_inside_a_project_the_check_uses_the_declared_group(grouped, openbao, monkeypatch):
    """AC7: プロジェクトの中ではグループを選ばせず、宣言のグループで確かめる"""
    monkeypatch.setenv('PWD', str(grouped / 'projects' / 'web'))
    script = Script(monkeypatch, text=[local_url(openbao)], secret=['', ''])

    rc, out = run(grouped)

    assert rc == 0
    assert '読めた参照' in out
    assert not script.messages('select')
    paths = kv_paths(openbao)
    assert 'team/acme/projects/web' in paths
    assert all('/acme/' in p for p in paths), paths


def test_a_flat_layout_checks_without_a_group(openbao_root, openbao, monkeypatch):
    """AC6: version: 1 ではグループを選ばせず、group を渡さずに確かめる"""
    seen = []
    real = env_backend.cmd_env_backend_test
    monkeypatch.setattr(env_backend, 'cmd_env_backend_test',
                        lambda root, group=None: seen.append(group) or real(root, group))
    script = Script(monkeypatch, text=[local_url(openbao)], secret=['', ''])

    rc, _ = run(openbao_root)

    assert rc == 0
    assert seen == [None]
    assert not script.messages('select')
