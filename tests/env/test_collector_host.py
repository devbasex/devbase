"""collectors/host.py: ホスト接続情報 (SSH) コレクタ + sync backfill"""

from __future__ import annotations

import builtins

import pytest

from devbase.env import keys
from devbase.env.store import EnvFile
from devbase.env.collector import CollectorRegistry
from devbase.env.collectors import host
from devbase.commands import env as env_cmd


@pytest.fixture
def env_file(tmp_path):
    return EnvFile(tmp_path / ".env")


def _patch_input(monkeypatch, responses):
    """input() を順番に responses で返すモックに差し替える。

    responses が尽きたら EOFError を送出し、非対話 (EOF) 経路を再現する。
    """
    it = iter(responses)

    def fake_input(prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)


def test_defaults_set_on_eof(monkeypatch, env_file):
    """getuser 既定値 + 入力 EOF → USER/HOST が既定値で設定される (非対話/CI)"""
    monkeypatch.setattr(host.getpass, "getuser", lambda: "alice")
    _patch_input(monkeypatch, [])  # 全入力 EOF

    host.collect_host_info(env_file)

    assert env_file.get(keys.HOST_SSH_USER) == "alice"
    assert env_file.get(keys.HOST_SSH_HOST) == host.DEFAULT_HOST_SSH_HOST


def test_user_can_override(monkeypatch, env_file):
    """対話入力でユーザー名・ホスト名を上書きできる"""
    monkeypatch.setattr(host.getpass, "getuser", lambda: "alice")
    _patch_input(monkeypatch, ["bob", "192.168.1.10"])

    host.collect_host_info(env_file)

    assert env_file.get(keys.HOST_SSH_USER) == "bob"
    assert env_file.get(keys.HOST_SSH_HOST) == "192.168.1.10"


def test_getuser_failure_skips_user(monkeypatch, env_file):
    """getuser が例外 → USER は未設定 (安全スキップ)・HOST は既定で設定"""
    def _boom():
        raise OSError("no username")

    monkeypatch.setattr(host.getpass, "getuser", _boom)
    _patch_input(monkeypatch, [])

    host.collect_host_info(env_file)

    assert env_file.get(keys.HOST_SSH_USER) is None
    assert env_file.get(keys.HOST_SSH_HOST) == host.DEFAULT_HOST_SSH_HOST


def test_existing_value_used_as_default(monkeypatch, env_file):
    """既存値があれば getuser ではなく既存値が既定として採用される"""
    env_file.set(keys.HOST_SSH_USER, "carol")
    monkeypatch.setattr(host.getpass, "getuser", lambda: "alice")
    _patch_input(monkeypatch, [])  # EOF → default (=carol) が確定

    host.collect_host_info(env_file)

    assert env_file.get(keys.HOST_SSH_USER) == "carol"


def test_default_host_user_returns_empty_on_exception(monkeypatch):
    monkeypatch.setattr(host.getpass, "getuser", lambda: (_ for _ in ()).throw(KeyError()))
    assert host._default_host_user() == ""


def test_collector_registered():
    """CollectorRegistry が host コレクタを自動検出する"""
    registry = CollectorRegistry()
    registry.discover()
    names = {c.name for c in registry.collectors}
    assert "host" in names


def test_sync_host_backfills_missing(monkeypatch, env_file):
    """_sync_host: 欠落キーを既定値で補完し更新件数を返す"""
    monkeypatch.setattr(host.getpass, "getuser", lambda: "dave")

    updated = env_cmd._sync_host(env_file)

    assert updated == 2
    assert env_file.get(keys.HOST_SSH_USER) == "dave"
    assert env_file.get(keys.HOST_SSH_HOST) == host.DEFAULT_HOST_SSH_HOST


def test_sync_host_respects_existing(monkeypatch, env_file):
    """_sync_host: 既存値 (手動上書き) は尊重して上書きしない"""
    env_file.set(keys.HOST_SSH_USER, "manual")
    env_file.set(keys.HOST_SSH_HOST, "10.0.0.1")
    monkeypatch.setattr(host.getpass, "getuser", lambda: "dave")

    updated = env_cmd._sync_host(env_file)

    assert updated == 0
    assert env_file.get(keys.HOST_SSH_USER) == "manual"
    assert env_file.get(keys.HOST_SSH_HOST) == "10.0.0.1"


def test_sync_host_skips_user_when_getuser_empty(monkeypatch, env_file):
    """_sync_host: getuser が空 → USER はスキップ・HOST のみ補完"""
    monkeypatch.setattr(host.getpass, "getuser", lambda: (_ for _ in ()).throw(OSError()))

    updated = env_cmd._sync_host(env_file)

    assert updated == 1
    assert env_file.get(keys.HOST_SSH_USER) is None
    assert env_file.get(keys.HOST_SSH_HOST) == host.DEFAULT_HOST_SSH_HOST
