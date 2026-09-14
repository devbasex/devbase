"""機密の注入をスキップするコマンドの判定

鍵の生成や暗号化・復号は「まだ鍵が無い」「復号できない」状態でこそ実行される。
グループ (`env`) 単位ではなくサブコマンドまで見ないと、`env keygen` などでも
注入が走ってしまう。
"""

from __future__ import annotations

import pytest

from devbase import cli


@pytest.fixture
def calls(tmp_path, monkeypatch):
    """`runtime.inject` の呼び出し回数を数える"""
    from devbase.env import runtime

    recorded = []
    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))
    monkeypatch.setattr(runtime, 'current_project_name', lambda root: None)
    monkeypatch.setattr(runtime, 'inject',
                        lambda root, project: recorded.append((root, project)))
    return recorded


@pytest.mark.parametrize('subcommand', ['keygen', 'encrypt', 'decrypt'])
def test_env_key_and_migration_subcommands_skip_injection(calls, subcommand):
    cli._load_secret_env('env', subcommand)
    assert calls == []


@pytest.mark.parametrize('subcommand', ['list', 'set', 'get', 'edit', 'sync',
                                        'export', 'import'])
def test_other_env_subcommands_still_inject(calls, subcommand):
    cli._load_secret_env('env', subcommand)
    assert len(calls) == 1


def test_init_skips_injection_regardless_of_subcommand(calls):
    cli._load_secret_env('init', None)
    assert calls == []


def test_unrelated_commands_inject(calls):
    cli._load_secret_env('project', 'up')
    assert len(calls) == 1


def test_env_without_a_subcommand_injects(calls):
    """`devbase env` 単体 (ヘルプ表示) はグループ丸ごとの除外にはしない"""
    cli._load_secret_env('env', None)
    assert len(calls) == 1


def test_injection_is_skipped_before_devbase_root_is_read(monkeypatch):
    """DEVBASE_ROOT が無くても判定自体は成立する (例外を出さない)"""
    monkeypatch.delenv('DEVBASE_ROOT', raising=False)
    cli._load_secret_env('env', 'keygen')


@pytest.mark.parametrize('required', [True, False])
def test_container_injection_without_root_returns_empty_secrets(monkeypatch, required):
    """現状固定: root 未設定なら必須指定でも例外を出さず空を返す。"""
    from devbase.commands import container
    from devbase.env.runtime import SecretEnv

    monkeypatch.delenv('DEVBASE_ROOT', raising=False)

    secrets = container._inject_secrets(required=required)

    assert isinstance(secrets, SecretEnv)
    assert not secrets
    assert secrets.values == {}
    assert secrets.names == []


def test_container_injection_error_swallowed_when_not_required(tmp_path, monkeypatch):
    """現状固定: required=False では DevbaseError を握り潰して空の SecretEnv を返し、
    required=True では再送出する。
    """
    from devbase.commands import container
    from devbase.env import runtime
    from devbase.env.runtime import SecretEnv
    from devbase.errors import DevbaseError

    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))

    def stub_inject(*_args, **_kwargs):
        raise DevbaseError('failed to inject secrets')

    monkeypatch.setattr(runtime, 'inject', stub_inject)

    secrets = container._inject_secrets(required=False)
    assert isinstance(secrets, SecretEnv)
    assert not secrets
    assert secrets.values == {}
    assert secrets.names == []

    with pytest.raises(DevbaseError, match='failed to inject secrets'):
        container._inject_secrets(required=True)

