"""cmd_env_keygen: 生成先の契約と、鍵ローテーションの原子性"""

from __future__ import annotations

import stat

import pyrage
import pytest

from devbase.commands import env as env_cmd
from devbase.env import agekeys
from devbase.env.secret_store import SecretRef, SecretStore
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


# ---------------------------------------------------------------------------
# --force の確認プロンプト
#
# 鍵はグローバル (全ワークスペース共通) なので、確認の要否をカレントの
# DEVBASE_ROOT に機密があるかで決めてはいけない。機密がまだ無いプロジェクトから
# --force しても、他プロジェクトの機密は旧鍵でしか復号できないため。
# ---------------------------------------------------------------------------

def _answers(monkeypatch, *values):
    """safe_input の応答をスクリプト化し、実際に聞かれた回数を返す"""
    asked = []

    def fake_input(prompt, default=''):
        asked.append(prompt)
        return values[len(asked) - 1] if len(asked) <= len(values) else default

    monkeypatch.setattr(env_cmd, 'safe_input', fake_input)
    return asked


def test_keygen_force_prompts_even_without_encrypted_secrets(devbase_root,
                                                             monkeypatch):
    """機密がまだ無いワークスペースでも --force は必ず確認する"""
    assert _keygen(devbase_root) == 0
    before = agekeys.key_file_path().read_bytes()

    asked = _answers(monkeypatch, 'yes')
    assert env_cmd.cmd_env_keygen(devbase_root, force=True) == 0

    assert asked, "確認プロンプトが出ていない"
    assert agekeys.key_file_path().read_bytes() != before


def test_keygen_force_prompt_mentions_other_workspaces(devbase_root,
                                                       monkeypatch, capsys):
    """鍵がグローバルで他ワークスペースにも影響する旨をプロンプトで明示する"""
    assert _keygen(devbase_root) == 0

    _answers(monkeypatch, 'yes')
    assert env_cmd.cmd_env_keygen(devbase_root, force=True) == 0

    out = capsys.readouterr().out
    assert '全プロジェクト共通' in out
    assert 'ワークスペース' in out


def test_keygen_force_aborts_and_keeps_the_key_when_not_confirmed(devbase_root,
                                                                  monkeypatch):
    """yes 以外を入力したら中止し、鍵は 1 バイトも変えない"""
    assert _keygen(devbase_root) == 0
    before = agekeys.key_file_path().read_bytes()

    _answers(monkeypatch, 'y')
    assert env_cmd.cmd_env_keygen(devbase_root, force=True) == 1

    assert agekeys.key_file_path().read_bytes() == before


def test_keygen_force_aborts_on_empty_answer(devbase_root, monkeypatch):
    """非対話 (EOF → 空文字) でも黙って上書きせず中止する"""
    assert _keygen(devbase_root) == 0
    before = agekeys.key_file_path().read_bytes()

    _answers(monkeypatch, '')
    assert env_cmd.cmd_env_keygen(devbase_root, force=True) == 1

    assert agekeys.key_file_path().read_bytes() == before


def test_keygen_force_skips_the_prompt_with_assume_yes(devbase_root, monkeypatch):
    """--yes / -y でのみ確認を飛ばせる"""
    assert _keygen(devbase_root) == 0
    before = agekeys.key_file_path().read_bytes()

    asked = _answers(monkeypatch)
    assert env_cmd.cmd_env_keygen(devbase_root, force=True, assume_yes=True) == 0

    assert asked == []
    assert agekeys.key_file_path().read_bytes() != before


def test_keygen_does_not_prompt_when_no_key_exists(devbase_root, monkeypatch):
    """初回生成は失うものが無いので確認しない"""
    asked = _answers(monkeypatch)
    assert env_cmd.cmd_env_keygen(devbase_root, force=True) == 0
    assert asked == []


def test_keygen_force_prompt_is_stronger_when_local_secrets_exist(devbase_root,
                                                                  monkeypatch,
                                                                  capsys):
    """カレントに機密があるときは、その旨も併せて警告する"""
    assert _keygen(devbase_root) == 0
    store = SecretStore(devbase_root)
    store.age.save(SecretRef.for_global(), {'TOKEN': 'x'})

    _answers(monkeypatch, 'yes')
    assert env_cmd.cmd_env_keygen(devbase_root, force=True) == 0

    out = capsys.readouterr().out
    assert 'このワークスペースには暗号化済みの機密があり' in out


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
