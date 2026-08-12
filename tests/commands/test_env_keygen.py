"""cmd_env_keygen: 鍵ローテーションの原子性 (失敗時に旧鍵を失わないこと)"""

from __future__ import annotations

import stat

import pytest

from devbase.commands import env as env_cmd
from devbase.env import agekeys
from devbase.errors import DevbaseError


@pytest.fixture
def devbase_root(tmp_path, monkeypatch):
    """鍵・受信者リストとも tmp_path 配下へ閉じ込める"""
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.delenv(agekeys.KEY_FILE_ENV, raising=False)
    root = tmp_path / 'devbase'
    root.mkdir()
    return root


def _keygen(root, **kwargs):
    return env_cmd.cmd_env_keygen(root, assume_yes=True, **kwargs)


def test_keygen_creates_key_and_registers_recipient(devbase_root):
    assert _keygen(devbase_root) == 0

    key_path = agekeys.key_file_path()
    assert key_path.exists()
    assert agekeys.load_recipients(devbase_root) == [agekeys.read_public_key(key_path)]


def test_keygen_without_force_keeps_existing_key(devbase_root):
    assert _keygen(devbase_root) == 0
    before = agekeys.key_file_path().read_bytes()

    assert _keygen(devbase_root) == 0
    assert agekeys.key_file_path().read_bytes() == before


def test_keygen_force_swaps_recipient(devbase_root):
    assert _keygen(devbase_root) == 0
    old_public = agekeys.read_public_key(agekeys.key_file_path())

    assert _keygen(devbase_root, force=True) == 0
    new_public = agekeys.read_public_key(agekeys.key_file_path())

    assert new_public != old_public
    assert agekeys.load_recipients(devbase_root) == [new_public]


def test_keygen_force_rolls_back_when_recipient_update_fails(devbase_root,
                                                             monkeypatch):
    """受信者リストの更新が落ちたら、旧鍵と旧受信者リストを書き戻す。

    ここで巻き戻さないと「旧鍵は消えたのに新公開鍵も登録されていない」状態になり、
    既存の暗号文が誰にも復号できなくなる。
    """
    assert _keygen(devbase_root) == 0
    key_path = agekeys.key_file_path()
    key_before = key_path.read_bytes()
    recipients_before = agekeys.recipients_file(devbase_root).read_bytes()

    def boom(*args, **kwargs):
        raise DevbaseError('受信者リストを書けませんでした')

    monkeypatch.setattr(agekeys, 'add_recipient', boom)

    assert _keygen(devbase_root, force=True) == 1
    assert key_path.read_bytes() == key_before
    assert agekeys.recipients_file(devbase_root).read_bytes() == recipients_before
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_keygen_force_rolls_back_when_old_recipient_removal_fails(devbase_root,
                                                                  monkeypatch):
    """旧公開鍵の削除で落ちた場合も、鍵と受信者リストが差し替え前へ戻る"""
    assert _keygen(devbase_root) == 0
    key_path = agekeys.key_file_path()
    key_before = key_path.read_bytes()
    recipients_before = agekeys.recipients_file(devbase_root).read_bytes()

    def boom(*args, **kwargs):
        raise DevbaseError('受信者を削除できませんでした')

    monkeypatch.setattr(agekeys, 'remove_recipient', boom)

    assert _keygen(devbase_root, force=True) == 1
    assert key_path.read_bytes() == key_before
    assert agekeys.recipients_file(devbase_root).read_bytes() == recipients_before
    assert agekeys.load_recipients(devbase_root) == \
        [agekeys.read_public_key(key_path)]


def test_keygen_rolls_back_to_absent_when_first_keygen_fails(devbase_root,
                                                             monkeypatch):
    """初回生成が途中で落ちたら、中途半端な鍵・受信者リストを残さない"""
    def boom(*args, **kwargs):
        raise DevbaseError('受信者リストを書けませんでした')

    monkeypatch.setattr(agekeys, 'add_recipient', boom)

    assert _keygen(devbase_root) == 1
    assert not agekeys.key_file_path().exists()
    assert not agekeys.recipients_file(devbase_root).exists()


def test_keygen_force_rolls_back_on_oserror(devbase_root, monkeypatch):
    """OSError (ディスク枯渇など) でも旧鍵は無傷のまま残る"""
    assert _keygen(devbase_root) == 0
    key_path = agekeys.key_file_path()
    key_before = key_path.read_bytes()

    def boom(*args, **kwargs):
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(agekeys, 'add_recipient', boom)

    assert _keygen(devbase_root, force=True) == 1
    assert key_path.read_bytes() == key_before
