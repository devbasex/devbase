"""`env init --group`: 子プロセスの `env init` が書くグループの置き場 (PLAN56 決定 10)"""

from __future__ import annotations

import types

import pytest

from devbase import cli
from devbase.commands import env as env_cmd


@pytest.fixture
def collect(monkeypatch):
    class _Registry:
        collectors = [types.SimpleNamespace(
            display_name='init', collect_fn=lambda env_file: env_file.set('INIT_KEY', 'value'))]

        def discover(self):
            pass

    monkeypatch.setattr(env_cmd, 'CollectorRegistry', _Registry)
    monkeypatch.setattr(env_cmd, '_update_source_metadata', lambda root, env_file: None)


@pytest.fixture
def grouped_root(openbao_root, openbao):
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, layout='group')
    return openbao_root


def _init(root, *argv):
    args = cli._create_parser().parse_args(['env', 'init', *argv])
    return env_cmd.cmd_env(root, args)


def test_parser_accepts_group_on_init():
    args = cli._create_parser().parse_args(['env', 'init', '--group', 'with'])
    assert args.group == 'with'
    assert cli._create_parser().parse_args(['env', 'init']).group is None


def test_init_with_a_group_writes_that_groups_team_global(grouped_root, openbao, collect):
    assert _init(grouped_root, '--group', 'with') == 0

    assert openbao.get('team/with/global') == {'INIT_KEY': 'value'}
    assert {r.kv_path for r in openbao.received if r.kv_path} == {'team/with/global'}


def test_init_group_before_the_alias_writes_the_aliased_path(grouped_root, openbao, collect):
    from tests.conftest import configure_openbao

    configure_openbao(grouped_root, openbao, layout='group', group_aliases={'acme': 'nyle'})

    assert _init(grouped_root, '--group', 'acme') == 0

    assert openbao.get('team/nyle/global') == {'INIT_KEY': 'value'}


def test_init_without_a_group_uses_the_declared_group(grouped_root, openbao, collect,
                                                      monkeypatch):
    (grouped_root / 'projects' / 'web' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=kkg\n')
    monkeypatch.setenv('PWD', str(grouped_root / 'projects' / 'web'))

    assert _init(grouped_root) == 0

    assert openbao.get('team/kkg/global') == {'INIT_KEY': 'value'}


def test_init_outside_projects_without_a_group_is_a_usage_error(grouped_root, openbao, collect,
                                                               caplog):
    """#315 I5: プロジェクトの外で --group が無ければ置き場を開かずに 2"""
    (grouped_root / 'env').write_text('FOO=1\n')

    assert _init(grouped_root) == 2

    assert openbao.received == []
    assert '--group' in caplog.text


def test_init_refuses_the_reserved_default(grouped_root, openbao, collect, caplog):
    """#315 前提 4: --group default は予約語として 2"""
    assert _init(grouped_root, '--group', 'default') == 2

    assert openbao.received == []
    assert 'default' in caplog.text


@pytest.mark.parametrize('name', ['ubuntu', '1', 'bad name', 'a/b', 'global'])
def test_init_rejects_unusable_group_names(grouped_root, openbao, collect, name, caplog):
    assert _init(grouped_root, '--group', name) == 2

    assert openbao.received == []


def test_init_group_is_refused_with_the_flat_layout(openbao_root, openbao, collect, caplog):
    assert _init(openbao_root, '--group', 'with') == 2

    assert openbao.received == []
    assert 'グループ別の置き場' in caplog.text


def test_init_group_is_refused_with_a_file_backend(tmp_path, collect, caplog):
    assert _init(tmp_path, '--group', 'with') == 2

    assert not (tmp_path / '.env').exists()
