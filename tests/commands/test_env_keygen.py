"""cmd_env_keygen: 生成先の契約と、鍵ローテーションの原子性"""

from __future__ import annotations

import stat

import pyrage
import pytest

from devbase.commands import env as env_cmd
from devbase.env import agekeys
from devbase.errors import DevbaseError


@pytest.fixture
def devbase_root(tmp_path, monkeypatch):
    """鍵を tmp_path 配下へ閉じ込める。

    keygen は生成先を CLI で選べず必ず ``agekeys.key_file_path()`` へ書くため、
    テストからは ``DEVBASE_AGE_KEY_FILE`` を差し替えて既定パスごと tmp へ向ける。
    実運用で別の場所へ置きたい利用者と同じ経路を通ることになる。
    """
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'keys' / 'keys.txt'))
    root = tmp_path / 'devbase'
    root.mkdir()
    return root


def _keygen(root, **kwargs):
    return env_cmd.cmd_env_keygen(root, assume_yes=True, **kwargs)


# ---------------------------------------------------------------------------
# 生成先の契約
# ---------------------------------------------------------------------------

def test_keygen_writes_to_the_resolved_key_file_path(devbase_root, tmp_path):
    """生成先は常に agekeys.key_file_path() = 復号側が探索する場所"""
    assert _keygen(devbase_root) == 0

    key_path = agekeys.key_file_path()
    assert key_path == tmp_path / 'keys' / 'keys.txt'
    assert key_path.exists()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_generated_key_is_discoverable_for_decryption(devbase_root):
    """生成した鍵が resolve_identities() / resolve_recipients() の双方から見える。

    生成先と探索先がずれると「保存はできるが復号できない」機密ができてしまうため、
    keygen 直後に暗号化・復号の両側が同じ鍵へ到達することを固定する。
    """
    assert _keygen(devbase_root) == 0

    key_path = agekeys.key_file_path()
    assert str(key_path) in agekeys.resolve_identities()
    assert agekeys.resolve_recipients(devbase_root) == \
        [agekeys.read_public_key(key_path)]


def test_keygen_does_not_write_recipients_file(devbase_root):
    """keygen はワークスペース固有の recipients.txt を作らない。

    鍵はグローバルなのに受信者リストはワークスペースごとに存在するため、ここで
    書き込むと別ワークスペースへ古い公開鍵が取り残され、失われた秘密鍵に対応する
    公開鍵で暗号化してしまう。
    """
    assert _keygen(devbase_root) == 0
    assert not agekeys.recipients_file(devbase_root).exists()

    assert _keygen(devbase_root, force=True) == 0
    assert not agekeys.recipients_file(devbase_root).exists()


def test_keygen_leaves_an_existing_recipients_file_untouched(devbase_root):
    """明示的に登録済みの受信者リストは keygen が書き換えない (チーム運用の保全)"""
    other = str(pyrage.x25519.Identity.generate().to_public())
    agekeys.save_recipients(devbase_root, [other])
    before = agekeys.recipients_file(devbase_root).read_bytes()

    assert _keygen(devbase_root, force=True) == 0
    assert agekeys.recipients_file(devbase_root).read_bytes() == before


# ---------------------------------------------------------------------------
# 鍵の保全
# ---------------------------------------------------------------------------

def test_keygen_without_force_keeps_existing_key(devbase_root):
    assert _keygen(devbase_root) == 0
    before = agekeys.key_file_path().read_bytes()

    assert _keygen(devbase_root) == 0
    assert agekeys.key_file_path().read_bytes() == before


def test_keygen_force_replaces_the_key(devbase_root):
    assert _keygen(devbase_root) == 0
    old_public = agekeys.read_public_key(agekeys.key_file_path())

    assert _keygen(devbase_root, force=True) == 0
    new_public = agekeys.read_public_key(agekeys.key_file_path())

    assert new_public != old_public


def test_keygen_force_rolls_back_when_generation_fails(devbase_root, monkeypatch):
    """鍵生成が落ちたら旧鍵をそのまま残す。

    ここで旧鍵が失われると、既存の暗号文を誰も復号できなくなる。
    """
    assert _keygen(devbase_root) == 0
    key_path = agekeys.key_file_path()
    key_before = key_path.read_bytes()

    def boom(*args, **kwargs):
        raise DevbaseError('鍵を書けませんでした')

    monkeypatch.setattr(agekeys, 'generate_key_file', boom)

    assert _keygen(devbase_root, force=True) == 1
    assert key_path.read_bytes() == key_before
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_keygen_force_rolls_back_on_oserror(devbase_root, monkeypatch):
    """OSError (ディスク枯渇など) でも旧鍵は無傷のまま残る"""
    assert _keygen(devbase_root) == 0
    key_path = agekeys.key_file_path()
    key_before = key_path.read_bytes()

    def boom(*args, **kwargs):
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(agekeys, 'generate_key_file', boom)

    assert _keygen(devbase_root, force=True) == 1
    assert key_path.read_bytes() == key_before


def test_keygen_rolls_back_to_absent_when_first_keygen_fails(devbase_root,
                                                             monkeypatch):
    """初回生成が途中で落ちたら、中途半端な鍵ファイルを残さない"""
    real_generate = agekeys.generate_key_file

    def half_written(path, **kwargs):
        real_generate(path, **kwargs)
        raise DevbaseError('生成直後に失敗しました')

    monkeypatch.setattr(agekeys, 'generate_key_file', half_written)

    assert _keygen(devbase_root) == 1
    assert not agekeys.key_file_path().exists()
