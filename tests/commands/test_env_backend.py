"""devbase env backend: status / use (PLAN51)"""

from __future__ import annotations

import io
import logging
from types import SimpleNamespace

import pytest

from devbase.commands import env_backend
from devbase.env import agekeys, backend_config as bc, bootstrap
from devbase.env.secret_store import SecretRef, SecretStore


GLOBAL = SecretRef.for_global()


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('PWD', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def with_key(root):
    _, public = agekeys.generate_key_file()
    return public


def use_args(name, **kw):
    base = dict(name=name, url=None, mount=None, user=None, role_id=None,
                secret_id_stdin=False, no_cache=False)
    base.update(kw)
    return SimpleNamespace(**base)


def errors(caplog) -> str:
    return '\n'.join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


def use_openbao(root, monkeypatch, **kw):
    """secret_id は stdin から渡す (argv には載せない)"""
    monkeypatch.setattr('sys.stdin', io.StringIO('s3cret\n'))
    args = use_args('openbao', url='https://openbao.example.com',
                    user='member01', role_id='rid', secret_id_stdin=True)
    for key, value in kw.items():
        setattr(args, key, value)
    return env_backend.cmd_env_backend_use(root, args)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def test_status_without_config_reports_plaintext_and_path(root, capsys):
    assert env_backend.cmd_env_backend_status(root) == 0

    out = capsys.readouterr().out
    assert 'plaintext' in out
    assert str(root / '.env') in out


def test_status_without_config_reports_age_when_encrypted(root, with_key, capsys):
    SecretStore(root).age.save(GLOBAL, {'A': '1'})

    assert env_backend.cmd_env_backend_status(root) == 0

    out = capsys.readouterr().out
    assert 'age' in out
    assert str(root / 'secrets' / 'global.env.age') in out


def test_status_reports_a_broken_config(root, caplog):
    path = root / 'secrets' / 'backend.yml'
    path.parent.mkdir()
    path.write_text('backend: vaultwarden\n')

    assert env_backend.cmd_env_backend_status(root) == 1
    assert 'vaultwarden' in errors(caplog)


def test_status_shows_openbao_paths_and_user(root, with_key, monkeypatch, capsys):
    assert use_openbao(root, monkeypatch) == 0
    capsys.readouterr()

    assert env_backend.cmd_env_backend_status(root) == 0

    out = capsys.readouterr().out
    assert 'openbao' in out
    assert 'https://openbao.example.com' in out
    assert 'devbase/team/global' in out and 'devbase/team/projects' in out
    assert 'devbase/users/member01/global' in out and 'devbase/users/member01/projects' in out
    assert 'member01' in out
    assert 's3cret' not in out and 'rid' in out


# ---------------------------------------------------------------------------
# use
# ---------------------------------------------------------------------------

def test_use_openbao_saves_config_and_bootstrap(root, with_key, monkeypatch, capsys):
    assert use_openbao(root, monkeypatch) == 0

    config = bc.load(root)
    assert config.backend == 'openbao'
    assert config.openbao.url == 'https://openbao.example.com'
    assert config.openbao.user == 'member01'
    assert config.openbao.mount == 'devbase'
    assert config.cache_enabled is True
    assert bootstrap.load(root) == bootstrap.Credentials('rid', 's3cret')
    out = capsys.readouterr().out
    assert 's3cret' not in out
    # 平文の機密が増えていない
    for path in (root / 'secrets').rglob('*'):
        if path.is_file():
            assert b's3cret' not in path.read_bytes()


def test_use_openbao_with_no_cache(root, with_key, monkeypatch):
    assert use_openbao(root, monkeypatch, no_cache=True) == 0
    assert bc.load(root).cache_enabled is False


def test_use_rejects_unknown_backend_without_writing(root, caplog):
    assert env_backend.cmd_env_backend_use(root, use_args('vaultwarden')) == 2

    assert not (root / 'secrets' / 'backend.yml').exists()
    err = errors(caplog)
    assert 'vaultwarden' in err and 'openbao' in err and 'age' in err


def test_use_openbao_reports_missing_settings_without_writing(root, with_key, caplog):
    args = use_args('openbao', url='https://openbao.example.com')

    assert env_backend.cmd_env_backend_use(root, args) == 2

    assert not (root / 'secrets' / 'backend.yml').exists()
    assert 'openbao.user' in errors(caplog)


def test_use_openbao_rejects_remote_http(root, with_key, monkeypatch):
    assert use_openbao(root, monkeypatch, url='http://openbao.example.com') == 2
    assert not (root / 'secrets' / 'backend.yml').exists()


def test_use_openbao_requires_credentials_when_none_stored(root, with_key, caplog):
    args = use_args('openbao', url='https://openbao.example.com', user='member01')

    assert env_backend.cmd_env_backend_use(root, args) == 2

    assert not (root / 'secrets' / 'backend.yml').exists()
    assert 'role-id' in errors(caplog)


def test_use_openbao_without_a_key_does_not_write_plaintext(root, monkeypatch, caplog):
    assert use_openbao(root, monkeypatch) == 1

    assert not (root / 'secrets' / 'backend.yml').exists()
    assert not (root / 'secrets' / 'bootstrap.env.age').exists()
    assert 'keygen' in errors(caplog)


def test_use_openbao_keeps_stored_credentials(root, with_key, monkeypatch):
    assert use_openbao(root, monkeypatch) == 0

    args = use_args('openbao', user='member02', mount='kv')
    assert env_backend.cmd_env_backend_use(root, args) == 0

    config = bc.load(root)
    assert config.openbao.user == 'member02'
    assert config.openbao.mount == 'kv'
    assert config.openbao.url == 'https://openbao.example.com'
    assert bootstrap.load(root) == bootstrap.Credentials('rid', 's3cret')


def test_use_age_switches_back_and_keeps_the_age_store(root, with_key, monkeypatch, capsys):
    SecretStore(root).age.save(GLOBAL, {'KEEP': 'me'})
    assert use_openbao(root, monkeypatch) == 0

    assert env_backend.cmd_env_backend_use(root, use_args('age')) == 0

    config = bc.load(root)
    assert config.backend == 'age'
    assert config.openbao.url == 'https://openbao.example.com'   # 設定は残る
    assert SecretStore(root).load(GLOBAL) == {'KEEP': 'me'}
    assert bootstrap.exists(root)
    capsys.readouterr()
    assert env_backend.cmd_env_backend_status(root) == 0
    assert 'age' in capsys.readouterr().out


def test_use_parser_has_no_secret_id_option():
    """secret_id を argv で受ける経路が無い (ps から読めない)"""
    import argparse

    from devbase import cli

    parser = argparse.ArgumentParser()
    cli._add_env_parser(parser.add_subparsers(dest='command'))
    with pytest.raises(SystemExit):
        parser.parse_args(['env', 'backend', 'use', 'openbao', '--secret-id', 'x'])
    ns = parser.parse_args(['env', 'backend', 'use', 'openbao', '--secret-id-stdin',
                            '--mount', 'kv', '--role-id', 'r'])
    assert ns.secret_id_stdin is True
    assert ns.mount == 'kv' and ns.role_id == 'r'
    assert not hasattr(ns, 'secret_id')
    assert not hasattr(ns, 'project_id') and not hasattr(ns, 'client_id')


@pytest.mark.parametrize('action', [None, 'unknown'])
def test_backend_missing_or_unknown_action_reports_usage(root, caplog, action):
    """未指定・未知のアクションの現在の終了コードと案内を固定する。"""
    args = SimpleNamespace(backend_action=action)

    with caplog.at_level(logging.ERROR, logger='devbase.commands.env_backend'):
        assert env_backend.cmd_env_backend(root, args) == 2

    assert any(
        record.levelno == logging.ERROR
        and record.getMessage() ==
        'サブコマンドを指定してください: status, use, test, migrate'
        for record in caplog.records
    )
