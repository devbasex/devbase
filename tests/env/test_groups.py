"""groups.py: プロジェクトのグループの宣言を非機密の env ファイルから読む (PLAN56 決定 3・#315)"""

from __future__ import annotations

import inspect

import pytest

from devbase.env import groups
from devbase.env import backend_config as bc
from devbase.env.secret_store import SecretRef, SecretStore, SecretStoreError
from devbase.errors import DevbaseError


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    (tmp_path / 'projects' / 'api').mkdir(parents=True)
    # プロセスの環境変数は見ない。見ていれば下のテストの期待値が崩れるよう、別の値を置く
    monkeypatch.setenv('DEVBASE_ACCOUNT_GROUP', 'kkg')
    return tmp_path


def write_env(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


# ---------------------------------------------------------------------------
# 宣言の読み取り (#315 I1)
# ---------------------------------------------------------------------------

def test_project_declaration_is_read_with_its_line(root):
    write_env(root / 'projects' / 'web' / 'env', 'FOO=1\nDEVBASE_ACCOUNT_GROUP=with\n')

    declared = groups.declare(root, 'web')
    assert declared.name == 'with'
    assert declared.source == root / 'projects' / 'web' / 'env'
    assert declared.line == 2
    assert groups.describe_source(root, declared) == 'projects/web/env:2'


def test_missing_declaration_stops_with_how_to_write_it(root):
    """宣言が無ければ既定のグループへ落ちずに止まり、書くファイルと行を示す"""
    write_env(root / 'projects' / 'api' / 'env', 'FOO=1\n')

    with pytest.raises(groups.GroupDeclarationError) as exc:
        groups.declare(root, 'api')
    assert 'projects/api/env' in str(exc.value)
    assert 'DEVBASE_ACCOUNT_GROUP=<グループ>' in str(exc.value)


def test_missing_env_file_stops_too(root):
    with pytest.raises(groups.GroupDeclarationError) as exc:
        groups.declare(root, 'api')
    assert 'projects/api/env' in str(exc.value)


def test_empty_declaration_stops_with_its_line(root):
    """空の宣言は宣言が無いものとして止まる (前提 1)。行番号を名指しする"""
    write_env(root / 'projects' / 'web' / 'env', 'FOO=1\nBAR=2\nDEVBASE_ACCOUNT_GROUP=\n')

    with pytest.raises(groups.GroupDeclarationError) as exc:
        groups.declare(root, 'web')
    assert 'projects/web/env:3' in str(exc.value)
    assert 'DEVBASE_ACCOUNT_GROUP=<グループ>' in str(exc.value)


def test_root_env_declaration_is_refused_even_when_the_project_declares(root):
    """``$DEVBASE_ROOT/env`` の宣言は名前を変えた既定になるため止める (前提 2)"""
    write_env(root / 'env', 'FOO=1\nDEVBASE_ACCOUNT_GROUP=nyle\n')
    write_env(root / 'projects' / 'web' / 'env', 'DEVBASE_ACCOUNT_GROUP=with\n')

    with pytest.raises(groups.GroupDeclarationError) as exc:
        groups.declare(root, 'web')
    assert '$DEVBASE_ROOT/env:2' in str(exc.value)
    assert '消し' in str(exc.value)


def test_empty_root_env_declaration_is_refused_too(root):
    write_env(root / 'env', 'DEVBASE_ACCOUNT_GROUP=\n')
    write_env(root / 'projects' / 'web' / 'env', 'DEVBASE_ACCOUNT_GROUP=with\n')

    with pytest.raises(groups.GroupDeclarationError):
        groups.declare(root, 'web')


def test_outside_a_project_the_group_is_not_decided(root):
    with pytest.raises(groups.GroupDeclarationError):
        groups.declare(root, None)


def test_default_declaration_is_refused_with_the_migration_hint(root):
    """前提 4: ``default`` は予約語。移し先のグループと移行のコマンドを示す"""
    write_env(root / 'projects' / 'web' / 'env', 'DEVBASE_ACCOUNT_GROUP=default\n')

    with pytest.raises(groups.GroupDeclarationError) as exc:
        groups.declare(root, 'web')
    message = str(exc.value)
    assert 'projects/web/env:1' in message
    assert 'migrate-volume --to' in message


def test_process_environment_is_not_read(root):
    """決定 3: ``DEVBASE_ACCOUNT_GROUP=kkg`` がプロセスにあっても、ファイルの宣言で決まる"""
    write_env(root / 'projects' / 'web' / 'env', 'DEVBASE_ACCOUNT_GROUP=with\n')
    assert groups.declare(root, 'web').name == 'with'
    with pytest.raises(groups.GroupDeclarationError):
        groups.declare(root, 'api')


def test_does_not_take_a_store():
    """受け入れ条件 4: 機密の置き場を受け取らない (シグネチャで固定)"""
    assert list(inspect.signature(groups.declare).parameters) == ['root', 'project']


def test_value_in_the_store_does_not_change_the_group(openbao_root, openbao, monkeypatch):
    """受け入れ条件 4: 置き場に書いた ``DEVBASE_ACCOUNT_GROUP`` は使わない"""
    root = openbao_root
    write_env(root / 'projects' / 'web' / 'env', 'DEVBASE_ACCOUNT_GROUP=with\n')
    openbao.put('team/with/global', {'DEVBASE_ACCOUNT_GROUP': 'nyle'})
    openbao.put('team/global', {'DEVBASE_ACCOUNT_GROUP': 'nyle'})

    assert groups.declare(root, 'web').name == 'with'


@pytest.mark.parametrize('name', ['ubuntu', 'default', '1', 'bad name', 'a/b'])
def test_invalid_names_are_rejected_with_the_volume_reason(root, name):
    write_env(root / 'projects' / 'web' / 'env', f'DEVBASE_ACCOUNT_GROUP="{name}"\n')

    with pytest.raises(groups.GroupDeclarationError) as exc:
        groups.declare(root, 'web')

    from devbase.volume.manager import validate_account_group
    with pytest.raises(DevbaseError) as expected:
        validate_account_group(name)
    assert str(expected.value) in str(exc.value)
    assert 'projects/web/env:1' in str(exc.value)


# ---------------------------------------------------------------------------
# 候補の一覧と全プロジェクトの検査 (#315 I11・I12)
# ---------------------------------------------------------------------------

def test_declared_groups_lists_only_declarations(root):
    write_env(root / 'projects' / 'web' / 'env', 'DEVBASE_ACCOUNT_GROUP=with\n')
    write_env(root / 'projects' / 'api' / 'env', 'FOO=1\n')
    (root / 'projects' / 'cli').mkdir()
    write_env(root / 'projects' / 'cli' / 'env', 'DEVBASE_ACCOUNT_GROUP=nyle\n')
    (root / 'projects' / 'dup').mkdir()
    write_env(root / 'projects' / 'dup' / 'env', 'DEVBASE_ACCOUNT_GROUP=with\n')

    assert groups.declared_groups(root) == ['nyle', 'with']


def test_undeclared_projects_are_listed_in_order(root):
    write_env(root / 'projects' / 'web' / 'env', 'DEVBASE_ACCOUNT_GROUP=with\n')

    assert groups.undeclared_projects(root, ['api', 'web']) == ['api']
    with pytest.raises(groups.GroupDeclarationError) as exc:
        groups.require_declared(root, ['web', 'api'])
    assert 'api' in str(exc.value)
    assert '--exclude-project' in str(exc.value)
    groups.require_declared(root, ['web'])


# ---------------------------------------------------------------------------
# 参照のグループ (SecretRef.group / SecretStore.ref_group)
# ---------------------------------------------------------------------------

def test_reference_group_defaults_to_none_and_keeps_equality():
    """決定 5: グループを持たない参照は今と同じ値になる"""
    assert SecretRef.for_global().group is None
    assert SecretRef.for_global() == SecretRef('global')
    assert SecretRef.for_project('web', owner='user') == SecretRef('project', 'web', 'user')


def test_reference_group_is_part_of_the_equality_and_the_label():
    with_ref = SecretRef.for_global(group='with')
    nyle_ref = SecretRef.for_global(group='nyle')

    assert with_ref != nyle_ref
    assert with_ref.label() == 'グローバル（グループ with）'
    assert SecretRef.for_project('web', owner='user', group='with').label() == \
        "個人のプロジェクト 'web'（グループ with）"
    assert SecretRef.for_global().label() == 'グローバル'


def test_reference_rejects_invalid_group_names():
    with pytest.raises(SecretStoreError):
        SecretRef.for_global(group='ubuntu')


def _write_config(root, text):
    path = root / 'secrets' / 'backend.yml'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def test_ref_group_is_none_for_file_backends(root):
    write_env(root / 'projects' / 'web' / 'env', 'DEVBASE_ACCOUNT_GROUP=with\n')
    _write_config(root, 'version: 1\nbackend: age\n')

    assert SecretStore(root).ref_group('web') is None
    assert SecretStore(root, config=bc.BackendConfig()).ref_group('web') is None


def test_ref_group_is_none_for_the_flat_layout(root):
    write_env(root / 'projects' / 'web' / 'env', 'DEVBASE_ACCOUNT_GROUP=with\n')
    _write_config(root, 'version: 1\nbackend: openbao\nopenbao:\n'
                        '  url: https://x.example.com\n  user: me\n')

    assert SecretStore(root).ref_group('web') is None


def test_storage_group_is_none_without_the_group_layout(root):
    """グループ名を渡しても、グループ別の置き場でない設定では ``None`` (決定 5)"""
    _write_config(root, 'version: 1\nbackend: age\n')
    assert SecretStore(root).storage_group('with') is None
    assert SecretStore(root, config=bc.BackendConfig()).storage_group('with') is None

    _write_config(root, 'version: 1\nbackend: openbao\nopenbao:\n'
                        '  url: https://x.example.com\n  user: me\n')
    assert SecretStore(root).storage_group('with') is None


def test_ref_group_reads_the_declaration_for_the_group_layout(root):
    write_env(root / 'projects' / 'web' / 'env', 'DEVBASE_ACCOUNT_GROUP=with\n')
    _write_config(root, 'version: 2\nbackend: openbao\nopenbao:\n'
                        '  url: https://x.example.com\n  user: me\n  layout: group\n'
                        '  group_aliases:\n    with: nyle\n')

    store = SecretStore(root)
    # 読み替えは参照ではなくパスの組み立てで行う (参照は宣言どおりの名前を持つ)
    assert store.ref_group('web') == 'with'
    # 宣言が無い・プロジェクトの外ではグループが決まらない (既定の値は無い。#315)
    with pytest.raises(groups.GroupDeclarationError):
        store.ref_group('api')
    with pytest.raises(groups.GroupRequiredError):
        store.ref_group(None)


def test_export_prefixed_declaration_is_read(root):
    """ラッパーの ``source`` と同じく ``export DEVBASE_ACCOUNT_GROUP=...`` も宣言として読む"""
    write_env(root / 'projects' / 'web' / 'env', 'export DEVBASE_ACCOUNT_GROUP=with\n')

    assert groups.declare(root, 'web').name == 'with'


def test_last_declaration_wins_across_export_and_plain_lines(root):
    """同じキーを ``export`` の有無を混ぜて繰り返したときも、``source`` と同じく最後の行が勝つ"""
    write_env(root / 'projects' / 'web' / 'env',
              'DEVBASE_ACCOUNT_GROUP=nyle\nexport DEVBASE_ACCOUNT_GROUP=kkg\n'
              'DEVBASE_ACCOUNT_GROUP=with\n')

    declared = groups.declare(root, 'web')
    assert declared.name == 'with'
    assert declared.line == 3
