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


# ---------------------------------------------------------------------------
# 名前を指定したライフサイクル操作の dispatch 前の注入 (PLAN56 決定 11)
# ---------------------------------------------------------------------------

GROUPED_CONFIG = """\
version: 2
backend: openbao
openbao:
  url: https://openbao.example.com
  user: member01
  layout: group
"""


def _grouped(root):
    path = root / 'secrets' / 'backend.yml'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(GROUPED_CONFIG, encoding='utf-8')
    (root / 'projects' / 'web').mkdir(parents=True)


@pytest.mark.parametrize(('cmd', 'subcommand'), [('up', None), ('scale', None),
                                                  ('project', 'up'), ('project', 'down')])
def test_named_lifecycle_injects_the_target_with_the_group_layout(calls, tmp_path, cmd,
                                                                  subcommand):
    _grouped(tmp_path)

    cli._load_secret_env(cmd, subcommand, name='web')

    assert calls == [(tmp_path, 'web')]


def test_named_lifecycle_keeps_the_current_project_with_the_flat_layout(calls, tmp_path):
    """``version: 1`` では PLAN55 の往復の表のまま、実行時のディレクトリで解決する"""
    (tmp_path / 'projects' / 'web').mkdir(parents=True)

    cli._load_secret_env('project', 'up', name='web')

    assert calls == [(tmp_path, None)]


def test_unknown_name_keeps_the_current_project(calls, tmp_path):
    _grouped(tmp_path)

    cli._load_secret_env('project', 'up', name='missing')

    assert calls == [(tmp_path, None)]


@pytest.mark.parametrize('name', ['../etc', 'a/b', '.', '..'])
def test_malformed_name_is_not_a_project_and_reads_nothing(calls, tmp_path, monkeypatch, name):
    """受け入れ条件 4: 形に合わない名前は projects/ の外の env を読まず、設定も読まずに None (PLAN61)。

    `projects/../etc` が実在すると、名前を連結してから実在を見る形では `etc/env` の宣言まで
    読みに行く。名前の形を先に見て、`groups.declare` と `runtime.store_for` を呼ばない。
    """
    from devbase.env import groups, runtime

    _grouped(tmp_path)
    (tmp_path / 'etc').mkdir()
    (tmp_path / 'etc' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=leaked\n', encoding='utf-8')
    monkeypatch.setattr(groups, 'declare',
                        lambda root, project: pytest.fail('groups.declare を呼んではならない'))
    monkeypatch.setattr(runtime, 'store_for',
                        lambda root: pytest.fail('runtime.store_for を呼んではならない'))

    assert cli._named_lifecycle_project(tmp_path, 'project', 'up', name) is None

    cli._load_secret_env('project', 'up', name=name)
    assert calls == [(tmp_path, None)]


def test_name_of_a_non_lifecycle_command_is_not_a_project(calls, tmp_path):
    _grouped(tmp_path)

    cli._load_secret_env('plugin', 'info', name='web')

    assert calls == [(tmp_path, None)]


def test_main_passes_the_parsed_name(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, '_load_secret_env',
                        lambda cmd, subcommand=None, name=None: seen.update(
                            cmd=cmd, subcommand=subcommand, name=name))
    monkeypatch.setattr(cli, '_dispatch', lambda cmd, args: 0)
    monkeypatch.setattr('sys.argv', ['devbase', 'project', 'up', 'web'])

    assert cli.main() == 0
    assert seen == {'cmd': 'project', 'subcommand': 'up', 'name': 'web'}


def test_main_preserves_command_result_after_secret_devbase_error(monkeypatch, tmp_path):
    """現状固定: 任意注入の DevbaseError はコマンド結果を置き換えない。"""
    from devbase.commands import container
    from devbase.env import runtime
    from devbase.errors import DevbaseError

    def inject(root, project):
        raise DevbaseError('failed to inject secrets')

    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))
    monkeypatch.setattr('sys.argv', ['devbase', 'project', 'ps'])
    monkeypatch.setattr(runtime, 'inject', inject)
    monkeypatch.setattr(container, 'cmd_project', lambda args: 7)

    assert cli.main() == 7


def test_main_preserves_command_result_after_secret_runtime_error(monkeypatch, tmp_path):
    """現状固定: 任意注入の一般例外でもコマンドを継続する。"""
    from devbase.commands import container
    from devbase.env import runtime

    def inject(root, project):
        raise RuntimeError('unexpected injection failure')

    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))
    monkeypatch.setattr('sys.argv', ['devbase', 'project', 'ps'])
    monkeypatch.setattr(runtime, 'inject', inject)
    monkeypatch.setattr(container, 'cmd_project', lambda args: 7)

    assert cli.main() == 7


def test_main_returns_one_after_command_devbase_error(monkeypatch, tmp_path):
    """現状固定: 注入成功後のコマンドの DevbaseError は終了値 1 になる。"""
    from devbase.commands import container
    from devbase.env import runtime
    from devbase.errors import DevbaseError

    def command(args):
        raise DevbaseError('command failed')

    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))
    monkeypatch.setattr('sys.argv', ['devbase', 'project', 'ps'])
    monkeypatch.setattr(runtime, 'inject', lambda root, project: None)
    monkeypatch.setattr(container, 'cmd_project', command)

    assert cli.main() == 1


@pytest.mark.parametrize('subcommand', ['list', 'get', 'set', 'delete', 'edit', 'init',
                                        'sync', 'project', 'export', 'import'])
def test_env_subcommands_resolve_their_own_group_with_the_group_layout(calls, tmp_path,
                                                                        subcommand):
    """PLAN56: ``--group`` / ``-p`` の検証より前に実行時のディレクトリのグループで注入しない"""
    _grouped(tmp_path)

    cli._load_secret_env('env', subcommand)

    assert calls == []


def test_env_exec_still_injects_with_the_group_layout(calls, tmp_path):
    _grouped(tmp_path)

    cli._load_secret_env('env', 'exec')

    assert len(calls) == 1
