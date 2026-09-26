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
