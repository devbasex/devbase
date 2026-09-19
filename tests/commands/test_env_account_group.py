"""``devbase env set DEVBASE_ACCOUNT_GROUP=...`` は置き場へ書かずに拒否する (PLAN62 受け入れ条件 7)"""

from __future__ import annotations

import logging

import pytest

from devbase.commands import env as env_cmd
from devbase.env import agekeys, keys


ACCOUNT_GROUP = keys.DEVBASE_ACCOUNT_GROUP


@pytest.fixture
def bao_root(openbao_root, monkeypatch):
    """偽の OpenBao を backend にした DEVBASE_ROOT (projects/web で実行)"""
    monkeypatch.setenv('DEVBASE_ROOT', str(openbao_root))
    monkeypatch.setenv('PWD', str(openbao_root / 'projects' / 'web'))
    return openbao_root


@pytest.fixture
def file_root(tmp_path, monkeypatch):
    """backend 未設定 (平文) の DEVBASE_ROOT (projects/web で実行)"""
    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('PWD', str(tmp_path / 'projects' / 'web'))
    monkeypatch.chdir(tmp_path)
    agekeys.generate_key_file()
    return tmp_path


def errors(caplog) -> str:
    return '\n'.join(r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)


TARGETS = [
    dict(),
    dict(project=True),
    dict(user=True),
    dict(project=True, user=True),
    dict(group='kkg'),
    dict(project=True, group='kkg'),
]


@pytest.mark.parametrize('kwargs', TARGETS, ids=lambda kw: ' '.join(kw) or 'global')
def test_set_refuses_the_account_group_without_opening_the_store(bao_root, openbao,
                                                                  caplog, kwargs):
    """どの宛先でも 1。ログインも書き込みも要求しない"""
    rc = env_cmd.cmd_env_set(bao_root, f'{ACCOUNT_GROUP}=kkg', **kwargs)

    assert rc == 1
    assert openbao.requests_of('POST') == []
    assert openbao.received == []
    text = errors(caplog)
    assert ACCOUNT_GROUP in text
    assert 'projects/<name>/env' in text and '$DEVBASE_ROOT/env' in text


@pytest.mark.parametrize('kwargs', [dict(), dict(project=True)],
                         ids=['global', 'project'])
def test_set_refuses_the_account_group_without_creating_the_file(file_root, caplog, kwargs):
    """ファイル backend では .env を作らない"""
    rc = env_cmd.cmd_env_set(file_root, f'{ACCOUNT_GROUP}=kkg', **kwargs)

    assert rc == 1
    assert not (file_root / '.env').exists()
    assert not (file_root / 'projects' / 'web' / '.env').exists()
    assert not (file_root / 'secrets').exists()
    assert ACCOUNT_GROUP in errors(caplog)


def test_set_refuses_the_account_group_with_surrounding_spaces(file_root):
    assert env_cmd.cmd_env_set(file_root, f'  {ACCOUNT_GROUP} = kkg') == 1
    assert not (file_root / '.env').exists()


def test_set_of_another_key_still_writes(file_root):
    """他のキーの env set は変わらない"""
    assert env_cmd.cmd_env_set(file_root, 'TOKEN=t') == 0
    assert 'TOKEN=t' in (file_root / '.env').read_text()
