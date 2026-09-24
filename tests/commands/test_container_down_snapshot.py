"""``cmd_down`` の停止後のローテーションが失敗したときの現状固定テスト。

停止は疑似コンテナの状態を変えるだけにし、Docker と実データの ``backups/`` に触らない。
"""

from __future__ import annotations

import pytest

from devbase.commands import container
from devbase.env import runtime as secret_runtime
from devbase.snapshot.manager import SnapshotManager


@pytest.fixture
def stopped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path))
    monkeypatch.setattr(container, '_apply_context', lambda context: None)
    monkeypatch.setattr(container, '_inject_secrets',
                        lambda *, required: secret_runtime.SecretEnv())
    state = {'dev-1': 'running'}

    def fake_down(compose_file=None):
        state['dev-1'] = 'stopped'

    monkeypatch.setattr(container, 'docker_compose_down', fake_down)
    return state


def test_rotate_failure_after_stop_returns_zero(stopped, monkeypatch):
    """現状固定: ローテーションの例外は吸収し、停止済みのまま 0 を返す。"""
    def boom(self, *a, **k):
        raise RuntimeError("rotate failed")

    monkeypatch.setattr(SnapshotManager, 'rotate', boom)

    assert container.cmd_down() == 0
    assert stopped == {'dev-1': 'stopped'}
