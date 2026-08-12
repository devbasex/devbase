"""env rekey / doctor: 受信者の更新と、端末に残る平文の点検"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess

import pyrage
import pytest

from devbase.commands import env_ops
from devbase.env import agekeys
from devbase.env.secret_store import SecretRef, SecretStore


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')


def git_init(path):
    """点検用に Git リポジトリを作る。

    除外設定の点検は ``git check-ignore`` に委ねているため、テストも実際に
    ``git init`` したリポジトリで確かめる。利用者の global / system の除外設定に
    左右されないよう、設定ファイルは空に固定する。
    """
    subprocess.run(['git', 'init', '-q'], cwd=str(path), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('PWD', str(tmp_path))
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', os.devnull)
    monkeypatch.chdir(tmp_path)
    git_init(tmp_path)
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


def test_rekey_rolls_back_when_a_later_rewrite_fails(root, with_key, colleague,
                                                     monkeypatch):
    """途中で書き込みに失敗しても、受信者リストも暗号文も元のまま残る"""
    public, key_path = colleague
    store = seed_encrypted(root)
    agekeys.save_recipients(root, [with_key])
    before_global = store.age.path(GLOBAL).read_bytes()
    before_web = store.age.path(WEB).read_bytes()

    # 2 件目 (プロジェクト web) の差し替えだけを失敗させる
    original = env_ops._write_blob
    web_path = store.age.path(WEB)

    def fail_on_web(path, blob):
        if path == web_path:
            raise OSError('ディスクがいっぱいです')
        return original(path, blob)

    monkeypatch.setattr(env_ops, '_write_blob', fail_on_web)

    assert env_ops.cmd_env_rekey(root, add=[public], assume_yes=True) == 1

    # 受信者リストは元のまま
    assert agekeys.load_recipients(root) == [with_key]
    # 1 件目も元の暗号文へ戻っている (同僚の鍵ではまだ読めない)
    assert store.age.path(GLOBAL).read_bytes() == before_global
    assert store.age.path(WEB).read_bytes() == before_web
    # 旧受信者 (自分) の鍵で引き続き全件読める
    assert SecretStore(root).load(GLOBAL) == {'TOKEN': 'sk-1'}
    assert SecretStore(root).load(WEB) == {'DB_PASSWORD': 'pw'}

    from devbase.env.secret_store import SecretStoreError

    reader = SecretStore(root, identities=[key_path])
    with pytest.raises(SecretStoreError):
        reader.load(GLOBAL)


def test_rekey_removes_the_created_recipients_file_on_rollback(root, with_key,
                                                               colleague,
                                                               monkeypatch):
    """リストが無い状態から始めた場合、巻き戻しで作ったリストごと消える"""
    public, _ = colleague
    store = seed_encrypted(root)
    assert not agekeys.recipients_file(root).exists()

    original = env_ops._write_blob
    web_path = store.age.path(WEB)

    def fail_on_web(path, blob):
        if path == web_path:
            raise OSError('ディスクがいっぱいです')
        return original(path, blob)

    monkeypatch.setattr(env_ops, '_write_blob', fail_on_web)

    assert env_ops.cmd_env_rekey(root, add=[public], assume_yes=True) == 1
    assert not agekeys.recipients_file(root).exists()


def test_rekey_rewrites_every_secret_on_success(root, with_key, colleague):
    """成功時は全件が新しい受信者で読める"""
    public, key_path = colleague
    seed_encrypted(root)

    assert env_ops.cmd_env_rekey(root, add=[public], assume_yes=True) == 0

    reader = SecretStore(root, identities=[key_path])
    assert reader.load(GLOBAL) == {'TOKEN': 'sk-1'}
    assert reader.load(WEB) == {'DB_PASSWORD': 'pw'}
    assert agekeys.load_recipients(root) == [with_key, public]


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


def test_doctor_reports_which_paths_are_not_ignored(root, with_key, capsys):
    """不足はパターン名ではなく、除外されない実パスで報告する"""
    seed_encrypted(root)
    (root / '.gitignore').write_text('.env\n.env.bak*\n')   # secrets/ が無い

    assert env_ops.cmd_env_doctor(root) == 1
    out = capsys.readouterr().out
    assert '除外設定から漏れているパスがあります' in out
    assert 'secrets/global.env.age' in out


def test_doctor_reports_wildcardless_backup_pattern(root, with_key, capsys):
    """日時付きの控えは完全一致では弾けない"""
    seed_encrypted(root)
    (root / '.gitignore').write_text('.env\n.env.bak\nsecrets/\n')

    assert env_ops.cmd_env_doctor(root) == 1
    out = capsys.readouterr().out
    assert '除外設定から漏れているパスがあります' in out
    assert '.env.bak-20260807172231' in out


@pytest.mark.parametrize('body', [
    '/.env\n/.env.bak*\n/secrets/\n/projects/*\n',        # ルート指定
    '.env\n.env.bak*\nsecrets\n',                         # 末尾スラッシュ無し
    '**/.env\n**/.env.bak*\n/secrets/\n',                 # 任意階層
    '.env\n.env*\nsecrets/\n',                            # 控えを広く拾う指定
    '# 機密は暗号化して secrets/ へ\n\n.env\n.env.bak*\nsecrets/\n',   # 行頭コメント・空行
])
def test_doctor_accepts_equivalent_ignore_notations(root, with_key, capsys, body):
    """Git が実際に除外できている書き方は「漏れ」と誤検知しない"""
    seed_encrypted(root)
    (root / '.gitignore').write_text(body)

    assert env_ops.cmd_env_doctor(root) == 0
    assert '問題は見つかりませんでした' in capsys.readouterr().out


@pytest.mark.parametrize('body', [
    # Git は行頭の `#` だけをコメントとして扱う。`.env # 機密` は
    # 「`.env # 機密`」というパターンであって `.env` を除外しない
    '.env # 機密\n.env.bak*\nsecrets/\n',
    # 後段の `!` で再包含されると除外は取り消される
    '.env\n!.env\n.env.bak*\nsecrets/\n',
    # 行頭の空白は落とされない (落ちるのは行末だけ)
    '  .env\n.env.bak*\nsecrets/\n',
])
def test_doctor_reports_patterns_git_does_not_honor(root, with_key, capsys, body):
    """Git の解釈では除外できていない書き方を「問題なし」にしない"""
    seed_encrypted(root)
    (root / '.gitignore').write_text(body)

    assert env_ops.cmd_env_doctor(root) == 1
    out = capsys.readouterr().out
    assert '除外設定から漏れているパスがあります' in out
    assert '除外されない: .env' in out


def test_doctor_reports_partially_ignored_secrets_dir(root, with_key, capsys):
    """`secrets/*.age` だけでは配下の平文が漏れる"""
    seed_encrypted(root)
    (root / '.gitignore').write_text('.env\n.env.bak*\nsecrets/*.age\n')

    assert env_ops.cmd_env_doctor(root) == 1
    out = capsys.readouterr().out
    assert '除外設定から漏れているパスがあります' in out
    assert 'secrets/leftover.env' in out


def test_doctor_cannot_check_ignores_without_a_git_repository(root, with_key, capsys):
    """Git リポジトリでなければ「確認できなかった」と言う (成功にしない)"""
    seed_encrypted(root)
    write_gitignore(root)
    shutil.rmtree(root / '.git')

    assert env_ops.cmd_env_doctor(root) == 1
    out = capsys.readouterr().out
    assert '除外設定を確認できませんでした' in out
    assert '問題は見つかりませんでした' not in out


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
