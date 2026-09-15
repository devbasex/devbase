"""groups.py: 機密の置き場のグループを非機密の env ファイルから決める (PLAN56 決定 3)"""

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
# 決める順
# ---------------------------------------------------------------------------

def test_project_env_is_read_first(root):
    write_env(root / 'env', 'DEVBASE_ACCOUNT_GROUP=nyle\n')
    write_env(root / 'projects' / 'web' / 'env', 'FOO=1\nDEVBASE_ACCOUNT_GROUP=with\n')

    assert groups.declared_group(root, 'web') == 'with'
    declared = groups.declare(root, 'web')
    assert declared.name == 'with'
    assert declared.source == root / 'projects' / 'web' / 'env'


def test_root_env_is_the_fallback(root):
    write_env(root / 'env', 'DEVBASE_ACCOUNT_GROUP=with\n')
    write_env(root / 'projects' / 'api' / 'env', 'FOO=1\n')

    assert groups.declared_group(root, 'api') == 'with'
    assert groups.declare(root, 'api').source == root / 'env'


def test_default_when_nothing_declares(root):
    """受け入れ条件 2 の前提: どちらにも宣言が無ければ ``default``"""
    declared = groups.declare(root, 'api')

    assert declared.name == 'default'
    assert declared.source is None


def test_without_a_project_only_the_root_env_is_read(root):
    write_env(root / 'env', 'DEVBASE_ACCOUNT_GROUP=with\n')
    write_env(root / 'projects' / 'web' / 'env', 'DEVBASE_ACCOUNT_GROUP=kkg\n')

    assert groups.declared_group(root, None) == 'with'


def test_empty_value_in_the_project_env_means_default(root):
    """ラッパーの ``source`` と同じく、空の宣言は共通の宣言を打ち消して ``default`` になる"""
    write_env(root / 'env', 'DEVBASE_ACCOUNT_GROUP=with\n')
    write_env(root / 'projects' / 'web' / 'env', 'DEVBASE_ACCOUNT_GROUP=\n')

    declared = groups.declare(root, 'web')
    assert declared.name == 'default'
    assert declared.source == root / 'projects' / 'web' / 'env'


def test_process_environment_is_not_read(root):
    """決定 3: ``DEVBASE_ACCOUNT_GROUP=kkg`` がプロセスにあっても、ファイルの値で決まる"""
    assert groups.declared_group(root, 'web') == 'default'


def test_does_not_take_a_store():
    """受け入れ条件 4: 機密の置き場を受け取らない (シグネチャで固定)"""
    assert list(inspect.signature(groups.declared_group).parameters) == ['root', 'project']


def test_value_in_the_store_does_not_change_the_group(openbao_root, openbao, monkeypatch):
    """受け入れ条件 4: 置き場に書いた ``DEVBASE_ACCOUNT_GROUP`` は使わない"""
    root = openbao_root
    write_env(root / 'projects' / 'web' / 'env', 'DEVBASE_ACCOUNT_GROUP=with\n')
    openbao.put('team/with/global', {'DEVBASE_ACCOUNT_GROUP': 'nyle'})
    openbao.put('team/global', {'DEVBASE_ACCOUNT_GROUP': 'nyle'})

    assert groups.declared_group(root, 'web') == 'with'


@pytest.mark.parametrize('name', ['ubuntu', '1', 'bad name', 'a/b'])
def test_invalid_names_are_rejected_with_the_volume_reason(root, name):
    write_env(root / 'projects' / 'web' / 'env', f'DEVBASE_ACCOUNT_GROUP="{name}"\n')

    with pytest.raises(DevbaseError) as exc:
        groups.declared_group(root, 'web')

    from devbase.volume.manager import resolve_account_group
    with pytest.raises(DevbaseError) as expected:
        resolve_account_group(name)
    assert str(expected.value) in str(exc.value)
    assert 'projects/web/env' in str(exc.value)


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
                        '  group_aliases:\n    default: nyle\n')

    store = SecretStore(root)
    assert store.ref_group('web') == 'with'
    # 読み替えは参照ではなくパスの組み立てで行う (参照は宣言どおりの名前を持つ)
    assert store.ref_group('api') == 'default'
    assert store.ref_group(None) == 'default'


def test_describe_source_names_root_env_explicitly(tmp_path):
    """``$DEVBASE_ROOT/env`` で決まったときは、プロジェクトの ``env`` と見分けられる名前で出す"""
    (tmp_path / 'env').write_text('DEVBASE_ACCOUNT_GROUP=kkg\n')
    declared = groups.declare(tmp_path, None)
    assert groups.describe_source(tmp_path, declared, None) == '$DEVBASE_ROOT/env'


def test_export_prefixed_declaration_is_read(root):
    """ラッパーの ``source`` と同じく ``export DEVBASE_ACCOUNT_GROUP=...`` も宣言として読む"""
    write_env(root / 'env', 'DEVBASE_ACCOUNT_GROUP=nyle\n')
    write_env(root / 'projects' / 'web' / 'env', 'export DEVBASE_ACCOUNT_GROUP=with\n')

    assert groups.declared_group(root, 'web') == 'with'
