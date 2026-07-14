"""collectors/orca.py: Orca 連携 (SSH 公開鍵) コレクタ"""

from __future__ import annotations

import builtins

import pytest

from devbase.env import keys
from devbase.env.store import EnvFile
from devbase.env.collectors import orca


@pytest.fixture
def env_file(tmp_path):
    return EnvFile(tmp_path / ".env")


# 長大な (数百文字相当) ダミー公開鍵
_LONG_KEY = "ssh-ed25519 " + ("A" * 400) + "1234ZZ user@host"


def _patch_input(monkeypatch, responses, captured=None):
    """input() を順番に responses で返すモックに差し替える。

    responses が尽きたら EOFError を送出し、非対話 (EOF) 経路を再現する。
    captured を渡すと呼び出し時の prompt 文字列を追記する。
    """
    it = iter(responses)

    def fake_input(prompt=""):
        if captured is not None:
            captured.append(prompt)
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)


def test_abbrev_key_for_prompt_shortens_ssh_key():
    """ssh- 形式の鍵は種別 + 末尾数文字 + (設定済み) に短縮される"""
    out = orca._abbrev_key_for_prompt(_LONG_KEY)
    assert out == "ssh-ed25519 …1234ZZ (設定済み)"
    # 元の鍵より十分短い / 生の鍵本体を含まない
    assert len(out) < 40
    assert "A" * 20 not in out


def test_abbrev_key_for_prompt_multiline_and_unknown():
    """複数行・非 ssh- 形式は (設定済み) にフォールバックする"""
    assert orca._abbrev_key_for_prompt("garbage\nsecond line") == "(設定済み)"
    assert orca._abbrev_key_for_prompt("") == "(設定済み)"


def test_prompt_is_abbreviated_but_default_is_full_key(monkeypatch, env_file):
    """プロンプト表示は短縮され、Enter (EOF) では full 鍵が既定として保存される"""
    env_file.set(keys.SSH_AUTHORIZED_KEYS, _LONG_KEY)
    captured: list[str] = []
    _patch_input(monkeypatch, [], captured=captured)  # 全入力 EOF

    orca.collect_orca_info(env_file)

    # プロンプトに生の鍵本体が漏れていない (短縮表示)
    ssh_prompt = next(p for p in captured if keys.SSH_AUTHORIZED_KEYS in p)
    assert "A" * 20 not in ssh_prompt
    assert "(設定済み)" in ssh_prompt
    # 保存 (既定) 値はフル鍵のまま
    assert env_file.get(keys.SSH_AUTHORIZED_KEYS) == _LONG_KEY
