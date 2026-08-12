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


@pytest.fixture(autouse=True)
def _isolate_injection_state(monkeypatch):
    """注入記録 (モジュールレベル) をテストごとに独立させる"""
    monkeypatch.setattr(runtime, '_injected_originals', {})


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')
API = SecretRef.for_project('api')


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


def test_names_are_kept_per_origin(root, store):
    """由来ごとに分けて持つ (構成生成側がサービスごとに絞り込むため)"""
    store.age.save(GLOBAL, {'TOKEN': 'global', 'ONLY_GLOBAL': 'g'})
    store.age.save(WEB, {'TOKEN': 'project', 'ONLY_PROJECT': 'p'})

    resolved = runtime.resolve(root, 'web', store=store)

    assert sorted(resolved.global_names) == ['ONLY_GLOBAL', 'TOKEN']
    assert sorted(resolved.project_names) == ['ONLY_PROJECT', 'TOKEN']
    # 両方にあるキーは全体としては 1 件に畳む
    assert sorted(resolved.names) == ['ONLY_GLOBAL', 'ONLY_PROJECT', 'TOKEN']


def test_no_secrets_is_falsy(root, store):
    resolved = runtime.resolve(root, None, store=store)

    assert not resolved
    assert resolved.names == []


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


# ---------------------------------------------------------------------------
# 注入の解除 (プロジェクト切替時の残留対策)
# ---------------------------------------------------------------------------

def test_switching_projects_drops_the_source_only_secret(root, store):
    """切替元にしか無い機密は、切替先の機密を載せ直すと消える。

    単に上書きするだけでは、切替先に同名キーが無い機密が残ってしまう。
    """
    (root / 'projects' / 'api').mkdir()
    store.age.save(GLOBAL, {'SHARED': 'common'})
    store.age.save(WEB, {'WEB_ONLY': 'w'})
    store.age.save(API, {'API_ONLY': 'a'})
    environ = {}

    runtime.inject(root, 'web', environ=environ, store=store)
    assert environ['WEB_ONLY'] == 'w'

    runtime.clear_injected(environ)
    runtime.inject(root, 'api', environ=environ, store=store)

    # 切替元固有の機密は残らない
    assert 'WEB_ONLY' not in environ
    assert environ['API_ONLY'] == 'a'
    # 共通の機密は切替後も残る
    assert environ['SHARED'] == 'common'


def test_clear_injected_restores_the_users_own_value(root, store):
    """利用者がシェルで設定していた同名の変数は消さず元の値へ戻す"""
    store.age.save(GLOBAL, {'TOKEN': 'from-secret'})
    environ = {'TOKEN': 'from-shell', 'PATH': '/bin'}

    runtime.inject(root, None, environ=environ, store=store)
    assert environ['TOKEN'] == 'from-secret'

    cleared = runtime.clear_injected(environ)

    assert environ['TOKEN'] == 'from-shell'
    assert environ['PATH'] == '/bin'
    assert cleared == ['TOKEN']


def test_clear_injected_removes_keys_that_did_not_exist(root, store):
    store.age.save(GLOBAL, {'TOKEN': 'from-secret'})
    environ = {}

    runtime.inject(root, None, environ=environ, store=store)
    runtime.clear_injected(environ)

    assert environ == {}


def test_repeated_injection_keeps_the_original_value(root, store):
    """載せ直しても記録するのは「最初に載せる前の値」"""
    store.age.save(GLOBAL, {'TOKEN': 'from-secret'})
    environ = {'TOKEN': 'from-shell'}

    runtime.inject(root, None, environ=environ, store=store)
    runtime.inject(root, None, environ=environ, store=store)
    runtime.clear_injected(environ)

    assert environ['TOKEN'] == 'from-shell'


def test_clear_injected_without_injection_is_noop(root):
    environ = {'TOKEN': 'from-shell'}

    assert runtime.clear_injected(environ) == []
    assert environ == {'TOKEN': 'from-shell'}


def test_clear_injected_only_touches_the_given_mapping(root, store):
    """履歴は注入先ごとに持つ (別のマッピングを巻き込まない)

    履歴が全体で 1 つしか無いと、A へ注入した記録で B を「復元」してしまい、
    B の値が壊れるうえ A には機密が残る。
    """
    store.age.save(GLOBAL, {'TOKEN': 'from-secret'})
    a = {'TOKEN': 'a-shell'}
    b = {'TOKEN': 'b-shell'}

    runtime.inject(root, None, environ=a, store=store)
    runtime.inject(root, None, environ=b, store=store)

    assert runtime.clear_injected(a) == ['TOKEN']

    # A だけが元へ戻り、B は注入したままで壊れない
    assert a == {'TOKEN': 'a-shell'}
    assert b == {'TOKEN': 'from-secret'}

    # B の履歴は残っているので、後から解除すれば B も元へ戻る
    assert runtime.clear_injected(b) == ['TOKEN']
    assert b == {'TOKEN': 'b-shell'}


def test_clearing_one_mapping_keeps_secrets_out_of_the_other(root, store):
    """A の解除が B の機密を消し残さない (逆に A には機密を残さない)"""
    store.age.save(GLOBAL, {'ONLY_SECRET': 's'})
    a = {}
    b = {}

    runtime.inject(root, None, environ=a, store=store)
    runtime.inject(root, None, environ=b, store=store)
    runtime.clear_injected(b)

    assert b == {}
    assert a == {'ONLY_SECRET': 's'}

    runtime.clear_injected(a)
    assert a == {}


def test_inject_and_clear_default_to_os_environ(root, store, monkeypatch):
    """既定の対象は従来どおり os.environ"""
    monkeypatch.delenv('TOKEN', raising=False)
    store.age.save(GLOBAL, {'TOKEN': 'from-secret'})
    other = {'TOKEN': 'other'}

    runtime.inject(root, None, store=store)
    assert os.environ['TOKEN'] == 'from-secret'

    # 別マッピングへの注入は os.environ の履歴に混ざらない
    runtime.inject(root, None, environ=other, store=store)

    assert runtime.clear_injected() == ['TOKEN']
    assert 'TOKEN' not in os.environ
    assert other == {'TOKEN': 'from-secret'}


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
