"""collectors/editor.py: VS Code 自動オープン (DEVBASE_OPEN_EDITOR) コレクタ"""

from __future__ import annotations

import builtins

import pytest

from devbase.env import keys
from devbase.env.store import EnvFile
from devbase.env.collector import CollectorRegistry
from devbase.env.collectors import editor


@pytest.fixture
def env_file(tmp_path):
    return EnvFile(tmp_path / ".env")


def _patch_input(monkeypatch, responses):
    """input() を順番に responses で返すモックに差し替える (尽きたら EOFError)。"""
    it = iter(responses)

    def fake_input(prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)


def test_default_enabled_on_eof(monkeypatch, env_file):
    """入力 EOF (非対話/CI) → 既定 1 が設定される"""
    _patch_input(monkeypatch, [])

    editor.collect_open_editor(env_file)

    assert env_file.get(keys.DEVBASE_OPEN_EDITOR) == "1"


def test_empty_input_keeps_default(monkeypatch, env_file):
    """空入力 (Enter のみ) → 既定 1 を維持"""
    _patch_input(monkeypatch, [""])

    editor.collect_open_editor(env_file)

    assert env_file.get(keys.DEVBASE_OPEN_EDITOR) == "1"


@pytest.mark.parametrize("answer", ["n", "N", "no", "0", "false", "off"])
def test_can_disable(monkeypatch, env_file, answer):
    """否定的な応答 → 0 (無効) に設定できる (選択可能)"""
    _patch_input(monkeypatch, [answer])

    editor.collect_open_editor(env_file)

    assert env_file.get(keys.DEVBASE_OPEN_EDITOR) == "0"


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "1", "true", "on"])
def test_can_enable(monkeypatch, env_file, answer):
    """肯定的な応答 → 1 (有効) に設定できる"""
    _patch_input(monkeypatch, [answer])

    editor.collect_open_editor(env_file)

    assert env_file.get(keys.DEVBASE_OPEN_EDITOR) == "1"


def test_existing_value_used_as_default(monkeypatch, env_file):
    """既存値 (0) があれば空入力でそれを既定として維持する"""
    env_file.set(keys.DEVBASE_OPEN_EDITOR, "0")
    _patch_input(monkeypatch, [""])  # Enter → 既存の 0 を維持

    editor.collect_open_editor(env_file)

    assert env_file.get(keys.DEVBASE_OPEN_EDITOR) == "0"


def test_unknown_answer_falls_back_to_default(monkeypatch, env_file):
    """未知の応答は既定 (1) にフォールバック"""
    _patch_input(monkeypatch, ["maybe"])

    editor.collect_open_editor(env_file)

    assert env_file.get(keys.DEVBASE_OPEN_EDITOR) == "1"


def test_collector_registered():
    """CollectorRegistry が editor コレクタを自動検出する"""
    registry = CollectorRegistry()
    registry.discover()
    names = {c.name for c in registry.collectors}
    assert "editor" in names
