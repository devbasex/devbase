"""runtime.store_for / release_store: 1 回のライフサイクル操作の間 SecretStore を持ち回る (PLAN55)

設計「`store_for` の規則」の表を固定する。控えを持ち回る目的はサーバ backend の往復を
減らすことで、同じインスタンスなら 2 度目の解決は `_seen` から返る。
"""

from __future__ import annotations

import pytest

from devbase.env import runtime
from devbase.env.secret_store import SecretRef, SecretStore

TEAM_GLOBAL_PATH = 'team/global'
USER_GLOBAL_PATH = 'users/member01/global'


@pytest.fixture(autouse=True)
def _clean_store():
    runtime.release_store()
    yield
    runtime.release_store()


def test_store_for_returns_the_same_instance_for_the_same_root(tmp_path):
    first = runtime.store_for(tmp_path)
    assert isinstance(first, SecretStore)
    assert runtime.store_for(tmp_path) is first


def test_store_for_rebuilds_when_the_root_changes(tmp_path):
    first = runtime.store_for(tmp_path / 'a')
    second = runtime.store_for(tmp_path / 'b')
    assert second is not first
    assert second.root == tmp_path / 'b'


def test_release_store_makes_the_next_call_rebuild(tmp_path):
    first = runtime.store_for(tmp_path)
    runtime.release_store()
    assert runtime.store_for(tmp_path) is not first


def test_release_store_without_a_store_is_a_no_op():
    runtime.release_store()
    runtime.release_store()


def test_explicit_store_is_not_retained(tmp_path):
    """明示的に渡した SecretStore (移行など設定と違う backend) は控えに入れない"""
    explicit = SecretStore(tmp_path)
    runtime.resolve(tmp_path, None, store=explicit)
    assert runtime.store_for(tmp_path) is not explicit


def test_resolve_twice_reuses_the_store_and_does_not_refetch(openbao_root, openbao):
    """同じ操作の中で 2 度解決しても、サーバへは参照ごとに 1 回しか行かない"""
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})

    runtime.resolve(openbao_root, None)
    runtime.resolve(openbao_root, None)

    assert openbao.logins == 1
    assert sorted(r.kv_path for r in openbao.requests_of('GET')) == sorted([
        TEAM_GLOBAL_PATH, USER_GLOBAL_PATH])


def test_release_then_resolve_reads_the_server_again(openbao_root, openbao):
    """捨てた後の解決は現物を読む (決定 5 の前提)"""
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    runtime.resolve(openbao_root, None)
    openbao.put(TEAM_GLOBAL_PATH, {'A': '2'})

    assert runtime.resolve(openbao_root, None).values['A'] == '1'
    runtime.release_store()
    assert runtime.resolve(openbao_root, None).values['A'] == '2'
    assert openbao.logins == 2


def test_inject_and_child_env_share_the_store(openbao_root, openbao, monkeypatch):
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    environ: dict = {}

    runtime.inject(openbao_root, None, environ=environ)
    env = runtime.child_env(openbao_root, None, base={})

    assert environ['A'] == '1' and env['A'] == '1'
    assert openbao.logins == 1
    assert len(openbao.requests_of('GET')) == 2


def test_store_for_exists_is_served_from_seen(openbao_root, openbao):
    """resolve の後の exists は _seen から返り、GET を足さない (受け入れ条件 4 の土台)"""
    openbao.put(TEAM_GLOBAL_PATH, {'A': '1'})
    runtime.resolve(openbao_root, 'web')
    before = len(openbao.requests_of('GET'))

    store = runtime.store_for(openbao_root)
    assert store.exists(SecretRef.for_global()) is True
    assert store.exists(SecretRef.for_project('web')) is False

    assert len(openbao.requests_of('GET')) == before
