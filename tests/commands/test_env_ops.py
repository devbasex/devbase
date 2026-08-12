"""env rekey / doctor: 受信者の更新と、端末に残る平文の点検"""

from __future__ import annotations

import os
import stat

import pyrage
import pytest

from devbase.commands import env_ops
from devbase.env import agekeys
from devbase.env.secret_store import SecretRef, SecretStore


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('PWD', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def with_key(root):
    _, public = agekeys.generate_key_file()
    return public


@pytest.fixture
def colleague(tmp_path):
    """同僚の鍵 (公開鍵と、復号を確かめるための秘密鍵ファイル)"""
    identity = pyrage.x25519.Identity.generate()
    path = tmp_path / 'colleague.key'
    path.write_text(str(identity))
    return str(identity.to_public()), str(path)


def seed_encrypted(root):
    store = SecretStore(root)
    store.age.save(GLOBAL, {'TOKEN': 'sk-1'})
    store.age.save(WEB, {'DB_PASSWORD': 'pw'})
    return store


# ---------------------------------------------------------------------------
# rekey
# ---------------------------------------------------------------------------

def test_rekey_without_a_key_fails(root):
    assert env_ops.cmd_env_rekey(root, add=['age1invalid'], assume_yes=True) == 1


def test_rekey_adds_a_recipient_and_reencrypts(root, with_key, colleague):
    public, key_path = colleague
    seed_encrypted(root)

    assert env_ops.cmd_env_rekey(root, add=[public], assume_yes=True) == 0

    # 同僚の鍵で読める
    reader = SecretStore(root, identities=[key_path])
    assert reader.load(GLOBAL) == {'TOKEN': 'sk-1'}
    assert reader.load(WEB) == {'DB_PASSWORD': 'pw'}
    # 自分の鍵でも引き続き読める
    assert SecretStore(root).load(GLOBAL) == {'TOKEN': 'sk-1'}


def test_rekey_registers_the_own_key_when_the_list_was_empty(root, with_key, colleague):
    """リストが無い状態から追加しても、自分が受信者から外れない"""
    public, _ = colleague
    seed_encrypted(root)
    assert agekeys.load_recipients(root) == []

    env_ops.cmd_env_rekey(root, add=[public], assume_yes=True)

    assert agekeys.load_recipients(root) == [with_key, public]


def test_rekey_removes_a_recipient(root, with_key, colleague):
    public, key_path = colleague
    seed_encrypted(root)
    env_ops.cmd_env_rekey(root, add=[public], assume_yes=True)

    assert env_ops.cmd_env_rekey(root, remove=[public], assume_yes=True) == 0

    assert agekeys.load_recipients(root) == [with_key]
    reader = SecretStore(root, identities=[key_path])
    from devbase.env.secret_store import SecretStoreError

    with pytest.raises(SecretStoreError):
        reader.load(GLOBAL)


def test_rekey_rejects_removing_an_unknown_recipient(root, with_key, colleague):
    public, _ = colleague
    seed_encrypted(root)

    assert env_ops.cmd_env_rekey(root, remove=[public], assume_yes=True) == 1


def test_rekey_refuses_to_empty_the_list(root, with_key):
    seed_encrypted(root)
    agekeys.save_recipients(root, [with_key])

    assert env_ops.cmd_env_rekey(root, remove=[with_key], assume_yes=True) == 1
    assert agekeys.load_recipients(root) == [with_key]


def test_rekey_reports_no_change(root, with_key, capsys):
    seed_encrypted(root)
    agekeys.save_recipients(root, [with_key])

    assert env_ops.cmd_env_rekey(root, add=[with_key], assume_yes=True) == 0
    assert '受信者に変更はありません' in capsys.readouterr().out


def test_rekey_dry_run_changes_nothing(root, with_key, colleague):
    public, key_path = colleague
    store = seed_encrypted(root)
    before = store.age.path(GLOBAL).read_bytes()

    assert env_ops.cmd_env_rekey(root, add=[public], dry_run=True) == 0

    assert agekeys.load_recipients(root) == []
    assert store.age.path(GLOBAL).read_bytes() == before


def test_rekey_aborts_without_confirmation(root, with_key, colleague, monkeypatch):
    public, _ = colleague
    seed_encrypted(root)
    monkeypatch.setattr(env_ops, 'safe_input', lambda prompt: 'no')

    assert env_ops.cmd_env_rekey(root, add=[public]) == 1
    assert agekeys.load_recipients(root) == []


def test_rekey_warns_when_dropping_your_own_key(root, with_key, colleague, capsys):
    public, _ = colleague
    seed_encrypted(root)
    agekeys.save_recipients(root, [with_key, public])

    env_ops.cmd_env_rekey(root, remove=[with_key], dry_run=True)

    assert '自分の公開鍵が受信者から外れています' in capsys.readouterr().out


def test_rekey_keeps_the_recipients_when_decryption_fails(root, with_key,
                                                         colleague, monkeypatch):
    public, _ = colleague
    seed_encrypted(root)

    from devbase.env.secret_store import AgeBackend, SecretStoreError

    def broken(self, ref):
        raise SecretStoreError('復号できません')

    monkeypatch.setattr(AgeBackend, 'load_bytes', broken)

    assert env_ops.cmd_env_rekey(root, add=[public], assume_yes=True) == 1
    assert agekeys.load_recipients(root) == []


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def write_gitignore(root, *extra):
    lines = ['.env', '.env.bak*', 'secrets/', *extra]
    (root / '.gitignore').write_text('\n'.join(lines) + '\n')


def test_doctor_is_quiet_on_a_healthy_setup(root, with_key, capsys):
    seed_encrypted(root)
    write_gitignore(root)

    assert env_ops.cmd_env_doctor(root) == 0
    assert '問題は見つかりませんでした' in capsys.readouterr().out


def test_doctor_reports_both_formats_present(root, with_key, capsys):
    store = seed_encrypted(root)
    store.plaintext.save(GLOBAL, {'TOKEN': 'plain'})
    write_gitignore(root)

    assert env_ops.cmd_env_doctor(root) == 1
    out = capsys.readouterr().out
    assert '両方にあります' in out
    assert '問題 1 件' in out


def test_doctor_reports_leftover_migration_backups(root, with_key, capsys):
    seed_encrypted(root)
    write_gitignore(root)
    backup = root / 'backups' / 'env-encrypt' / '20260101000000'
    backup.mkdir(parents=True)
    (backup / 'global.env').write_text('TOKEN=sk-1\n')

    assert env_ops.cmd_env_doctor(root) == 1
    assert '退避した平文が残っています' in capsys.readouterr().out


def test_doctor_ignores_encrypted_backups(root, with_key, capsys):
    seed_encrypted(root)
    write_gitignore(root)
    backup = root / 'backups' / 'env-import' / 'dbenv-1'
    backup.mkdir(parents=True)
    (backup / 'global.env.age').write_bytes(b'ciphertext')

    assert env_ops.cmd_env_doctor(root) == 0


def test_doctor_reports_stale_plaintext_copies(root, with_key, capsys):
    seed_encrypted(root)
    write_gitignore(root)
    (root / '.env.bak-20260807172231').write_text('TOKEN=sk-1\n')

    assert env_ops.cmd_env_doctor(root) == 1
    assert '平文の控えファイルが残っています' in capsys.readouterr().out


def test_doctor_reports_missing_ignore_patterns(root, with_key, capsys):
    seed_encrypted(root)
    (root / '.gitignore').write_text('.env\n.env.bak*\n')   # secrets/ が無い

    assert env_ops.cmd_env_doctor(root) == 1
    out = capsys.readouterr().out
    assert '除外設定に不足があります' in out
    assert 'secrets/' in out


def test_doctor_reports_wildcardless_backup_pattern(root, with_key, capsys):
    """日時付きの控えは完全一致では弾けない"""
    seed_encrypted(root)
    (root / '.gitignore').write_text('.env\n.env.bak\nsecrets/\n')

    assert env_ops.cmd_env_doctor(root) == 1
    assert '日時付きの控えファイルが除外されません' in capsys.readouterr().out


def test_doctor_reports_a_world_readable_key(root, with_key, capsys):
    seed_encrypted(root)
    write_gitignore(root)
    key_file = agekeys.key_file_path()
    os.chmod(key_file, 0o644)

    assert env_ops.cmd_env_doctor(root) == 1
    out = capsys.readouterr().out
    assert '鍵ファイルが他ユーザーから読めます' in out
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o644   # 勝手に直さない


def test_doctor_reports_a_missing_key(root, capsys):
    write_gitignore(root)

    assert env_ops.cmd_env_doctor(root) == 1
    assert '暗号化に使う鍵がありません' in capsys.readouterr().out
