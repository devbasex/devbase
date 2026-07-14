"""commands/container.py: up 後の Orca 同期ゲート (_maybe_orca_sync) (PLAN33 / round5)

`_maybe_orca_sync` は「ENABLE_SSH 有効」または「Orca config が既に存在する」とき
再生成する。後者により、ENABLE_SSH を true→false へ切り替えて再 up した場合に停止した
コンテナの古いエントリが剪定される。config 未作成の純粋な非 Orca ユーザーでは何もしない
(無用なファイル生成を避ける)。実 docker は呼ばず、依存関数を monkeypatch して検証する。
"""

from __future__ import annotations

import pytest

from devbase.commands import container
from devbase.commands import orca


def _stub_regenerate(monkeypatch):
    """orca.regenerate_config を呼び出し回数カウンタ付きスタブへ差し替える。"""
    calls = {"n": 0}

    def _fake():
        calls["n"] += 1
        return [], orca._config_path()

    monkeypatch.setattr(orca, "regenerate_config", _fake)
    return calls


def test_sync_skipped_when_disabled_and_no_config(monkeypatch):
    """SSH 無効 + config 未作成 (純粋な非 Orca ユーザー) なら再生成しない。"""
    monkeypatch.setattr(container, "_ssh_enabled", lambda: False)
    monkeypatch.setattr(orca, "config_exists", lambda: False)
    calls = _stub_regenerate(monkeypatch)

    container._maybe_orca_sync()

    assert calls["n"] == 0


def test_sync_runs_when_disabled_but_config_exists(monkeypatch):
    """SSH 無効でも config が既に存在すれば再生成する (停止コンテナの剪定)。

    ENABLE_SSH=true → false の切り替えで残る stale エントリをヘッダのみへ剪定する経路。
    """
    monkeypatch.setattr(container, "_ssh_enabled", lambda: False)
    monkeypatch.setattr(orca, "config_exists", lambda: True)
    calls = _stub_regenerate(monkeypatch)

    container._maybe_orca_sync()

    assert calls["n"] == 1


def test_sync_runs_when_ssh_enabled(monkeypatch):
    """SSH 有効なら config の有無に依らず再生成する (従来挙動)。"""
    monkeypatch.setattr(container, "_ssh_enabled", lambda: True)
    monkeypatch.setattr(orca, "config_exists", lambda: False)
    calls = _stub_regenerate(monkeypatch)

    container._maybe_orca_sync()

    assert calls["n"] == 1


def test_sync_never_raises_on_regenerate_failure(monkeypatch):
    """再生成が例外を投げても best-effort で握り潰し up を倒さない。"""
    monkeypatch.setattr(container, "_ssh_enabled", lambda: True)
    monkeypatch.setattr(orca, "config_exists", lambda: False)

    def _boom():
        raise RuntimeError("docker down")

    monkeypatch.setattr(orca, "regenerate_config", _boom)

    # 例外が伝播しないこと。
    container._maybe_orca_sync()
