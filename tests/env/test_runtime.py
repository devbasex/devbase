"""runtime.py: 機密の合成とコンテナへ渡す変数名"""

from __future__ import annotations

import os

import pyrage
import pytest

from devbase.env import runtime
from devbase.env.secret_store import SecretRef, SecretStore


@pytest.fixture
def root(tmp_path):
    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    return tmp_path


@pytest.fixture
def store(root, tmp_path):
    identity = pyrage.x25519.Identity.generate()
    key = tmp_path / 'id.key'
    key.write_text(str(identity))
    return SecretStore(root, recipients=[str(identity.to_public())],
                       identities=[str(key)])


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')


# ---------------------------------------------------------------------------
# 重ね順
# ---------------------------------------------------------------------------

def test_global_secrets_are_listed_for_the_container(root, store):
    store.age.save(GLOBAL, {'ANTHROPIC_API_KEY': 'sk-1'})

    resolved = runtime.resolve(root, None, store=store)

    assert resolved.values == {'ANTHROPIC_API_KEY': 'sk-1'}
    assert resolved.names == ['ANTHROPIC_API_KEY']


def test_project_secrets_override_global(root, store):
    store.age.save(GLOBAL, {'TOKEN': 'global', 'ONLY_GLOBAL': 'g'})
    store.age.save(WEB, {'TOKEN': 'project'})

    resolved = runtime.resolve(root, 'web', store=store)

    assert resolved.values['TOKEN'] == 'project'
    assert resolved.values['ONLY_GLOBAL'] == 'g'
    assert sorted(resolved.names) == ['ONLY_GLOBAL', 'TOKEN']


def test_project_env_overrides_global_for_the_same_key(root, store, monkeypatch):
    """非機密設定が共通設定を上書きする従来の関係を保つ"""
    (root / 'projects' / 'web' / 'env').write_text('AWS_DEFAULT_REGION=us-east-1\n')
    monkeypatch.setenv('AWS_DEFAULT_REGION', 'us-east-1')
    store.age.save(GLOBAL, {'AWS_DEFAULT_REGION': 'ap-northeast-1'})

    resolved = runtime.resolve(root, 'web', store=store)

    assert resolved.values['AWS_DEFAULT_REGION'] == 'us-east-1'


def test_project_env_only_keys_are_not_listed(root, store, monkeypatch):
    """非機密設定は env_file が直接読むので変数名を列挙しない"""
    (root / 'projects' / 'web' / 'env').write_text('GIT_REPO=web\n')
    monkeypatch.setenv('GIT_REPO', 'web')
    store.age.save(GLOBAL, {'TOKEN': 't'})

    resolved = runtime.resolve(root, 'web', store=store)

    assert resolved.names == ['TOKEN']
    assert 'GIT_REPO' not in resolved.values


def test_project_env_value_comes_from_the_environment(root, store, monkeypatch):
    """展開済みの値を採用する (生の行を読み直さない)"""
    (root / 'projects' / 'web' / 'env').write_text('WORK_DIR=/work/$GIT_REPO\n')
    monkeypatch.setenv('WORK_DIR', '/work/web')
    store.age.save(GLOBAL, {'WORK_DIR': '/work/unset'})

    resolved = runtime.resolve(root, 'web', store=store)

    assert resolved.values['WORK_DIR'] == '/work/web'


def test_project_env_is_ignored_when_not_in_the_environment(root, store, monkeypatch):
    monkeypatch.delenv('WORK_DIR', raising=False)
    (root / 'projects' / 'web' / 'env').write_text('WORK_DIR=/work/$GIT_REPO\n')
    store.age.save(GLOBAL, {'WORK_DIR': '/work/global'})

    resolved = runtime.resolve(root, 'web', store=store)

    assert resolved.values['WORK_DIR'] == '/work/global'


def test_resolve_without_any_secrets_is_empty(root, store):
    resolved = runtime.resolve(root, 'web', store=store)
    assert resolved.values == {}
    assert resolved.names == []
    assert not resolved


def test_plaintext_secrets_are_resolved_too(root, store):
    """移行前 (平文のまま) でも同じ経路で読める"""
    store.plaintext.save(GLOBAL, {'TOKEN': 'plain'})

    resolved = runtime.resolve(root, None, store=store)

    assert resolved.values == {'TOKEN': 'plain'}


# ---------------------------------------------------------------------------
# 注入
# ---------------------------------------------------------------------------

def test_inject_puts_values_into_the_given_environ(root, store):
    store.age.save(GLOBAL, {'TOKEN': 'sk-1'})
    environ = {}

    resolved = runtime.inject(root, None, environ=environ, store=store)

    assert environ == {'TOKEN': 'sk-1'}
    assert resolved.names == ['TOKEN']


def test_child_env_does_not_touch_os_environ(root, store, monkeypatch):
    monkeypatch.delenv('TOKEN', raising=False)
    store.age.save(GLOBAL, {'TOKEN': 'sk-1'})

    env = runtime.child_env(root, None, store=store)

    assert env['TOKEN'] == 'sk-1'
    assert 'TOKEN' not in os.environ


def test_child_env_keeps_the_existing_environment(root, store):
    store.age.save(GLOBAL, {'TOKEN': 'sk-1'})

    env = runtime.child_env(root, None, base={'PATH': '/bin'}, store=store)

    assert env['PATH'] == '/bin'
    assert env['TOKEN'] == 'sk-1'


# ---------------------------------------------------------------------------
# プロジェクトの特定
# ---------------------------------------------------------------------------

def test_current_project_name_from_a_subdirectory(root):
    sub = root / 'projects' / 'web' / 'src'
    sub.mkdir()
    assert runtime.current_project_name(root, sub) == 'web'


def test_current_project_name_outside_projects(root):
    assert runtime.current_project_name(root, root) is None


def test_current_project_name_rejects_paths_escaping_projects(root):
    escaped = root / 'projects' / 'web' / '..' / '..' / 'outside'
    assert runtime.current_project_name(root, escaped) is None


def test_current_project_name_follows_a_symlinked_project(root, tmp_path):
    target = tmp_path / 'linked-target'
    target.mkdir()
    (root / 'projects' / 'linked').symlink_to(target)

    assert runtime.current_project_name(root, root / 'projects' / 'linked') == 'linked'
