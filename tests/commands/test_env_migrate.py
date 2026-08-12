"""env encrypt / decrypt: 平文と暗号化構成の往復"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import pytest

from devbase.commands import env_migrate
from devbase.env.secret_store import SecretRef, SecretStore


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')
API = SecretRef.for_project('api')

COMPOSE = """services:
  dev:
    image: alpine
    env_file:
      - ${DEVBASE_ROOT}/.env
      - env
      - .env
"""


@pytest.fixture
def root(tmp_path, monkeypatch):
    from devbase.env import agekeys

    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    (tmp_path / 'projects' / 'web' / 'compose.yml').write_text(COMPOSE)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('PWD', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def with_key(root):
    from devbase.env import agekeys

    agekeys.generate_key_file()
    return root


def seed_plaintext(root):
    store = SecretStore(root)
    store.plaintext.save(GLOBAL, {'ANTHROPIC_API_KEY': 'sk-1'})
    store.plaintext.save(WEB, {'DB_PASSWORD': 'pw'})
    return store


@pytest.fixture
def two_projects(with_key):
    """複数対象の途中失敗を見るための構成 (対象は global → api → web の順)"""
    (with_key / 'projects' / 'api').mkdir(parents=True)
    (with_key / 'projects' / 'api' / 'compose.yml').write_text(COMPOSE)
    store = seed_plaintext(with_key)
    store.plaintext.save(API, {'API_TOKEN': 'tk'})
    return with_key


def compose_texts(root):
    return {name: (root / 'projects' / name / 'compose.yml').read_text()
            for name in ('api', 'web')}


def age_files(root):
    return sorted(p.name for p in root.glob('secrets/**/*.age'))


def plaintext_files(root):
    paths = [root / '.env']
    paths += [root / 'projects' / name / '.env' for name in ('api', 'web')]
    return sorted(str(p) for p in paths if p.exists())


# ---------------------------------------------------------------------------
# encrypt
# ---------------------------------------------------------------------------

def test_encrypt_requires_a_key(root, capsys):
    seed_plaintext(root)

    assert env_migrate.cmd_env_encrypt(root, assume_yes=True) == 1
    assert (root / '.env').exists()          # 平文はそのまま


def test_encrypt_reports_nothing_to_do(with_key, capsys):
    assert env_migrate.cmd_env_encrypt(with_key, assume_yes=True) == 0
    assert '暗号化する平文の設定はありません' in capsys.readouterr().out


def test_encrypt_moves_plaintext_into_the_store(with_key):
    seed_plaintext(with_key)

    assert env_migrate.cmd_env_encrypt(with_key, assume_yes=True) == 0

    store = SecretStore(with_key)
    assert store.is_encrypted(GLOBAL)
    assert store.is_encrypted(WEB)
    assert store.load(GLOBAL) == {'ANTHROPIC_API_KEY': 'sk-1'}
    assert store.load(WEB) == {'DB_PASSWORD': 'pw'}
    assert not (with_key / '.env').exists()
    assert not (with_key / 'projects' / 'web' / '.env').exists()


def test_encrypt_keeps_the_plaintext_in_backups(with_key, capsys):
    seed_plaintext(with_key)

    env_migrate.cmd_env_encrypt(with_key, assume_yes=True)

    backups = list((with_key / 'backups' / 'env-encrypt').iterdir())
    assert len(backups) == 1
    assert (backups[0] / 'global.env').read_text().strip() == 'ANTHROPIC_API_KEY=sk-1'
    assert (backups[0] / 'projects' / 'web.env').exists()
    # 消すのは利用者の判断。場所を案内する
    assert '退避しました' in capsys.readouterr().out


def test_encrypt_rewrites_the_compose_file(with_key):
    from devbase.env import compose_migrate

    seed_plaintext(with_key)

    env_migrate.cmd_env_encrypt(with_key, assume_yes=True)

    text = (with_key / 'projects' / 'web' / 'compose.yml').read_text()
    assert f'{compose_migrate.DISABLED_MARK}- ${{DEVBASE_ROOT}}/.env' in text
    assert f'{compose_migrate.DISABLED_MARK}- .env' in text
    assert '      - env\n' in text


def test_encrypt_dry_run_changes_nothing(with_key, capsys):
    seed_plaintext(with_key)
    before = (with_key / 'projects' / 'web' / 'compose.yml').read_text()

    assert env_migrate.cmd_env_encrypt(with_key, dry_run=True) == 0

    assert (with_key / '.env').exists()
    assert not (with_key / 'secrets' / 'global.env.age').exists()
    assert (with_key / 'projects' / 'web' / 'compose.yml').read_text() == before
    assert '--dry-run' in capsys.readouterr().out


def test_encrypt_shows_the_compose_diff(with_key, capsys):
    seed_plaintext(with_key)

    env_migrate.cmd_env_encrypt(with_key, dry_run=True)

    out = capsys.readouterr().out
    assert 'コンテナ構成の変更' in out
    assert '-      - ${DEVBASE_ROOT}/.env' in out


def test_encrypt_can_target_one_project(with_key):
    seed_plaintext(with_key)

    env_migrate.cmd_env_encrypt(with_key, assume_yes=True, projects=['web'])

    store = SecretStore(with_key)
    assert store.is_encrypted(WEB)
    # 共通設定は対象外なので平文のまま
    assert not store.is_encrypted(GLOBAL)


def test_encrypt_aborts_without_confirmation(with_key, monkeypatch):
    seed_plaintext(with_key)
    monkeypatch.setattr(env_migrate, 'safe_input', lambda prompt: 'no')

    assert env_migrate.cmd_env_encrypt(with_key) == 1
    assert (with_key / '.env').exists()
    assert not (with_key / 'secrets' / 'global.env.age').exists()


def test_encrypt_keeps_plaintext_when_the_result_cannot_be_read_back(with_key,
                                                                    monkeypatch):
    """読み戻せない暗号文のために平文を失わない"""
    seed_plaintext(with_key)

    from devbase.env.secret_store import AgeBackend, SecretStoreError

    def broken_load(self, ref):
        raise SecretStoreError('復号できません')

    monkeypatch.setattr(AgeBackend, 'load', broken_load)

    assert env_migrate.cmd_env_encrypt(with_key, assume_yes=True) == 1
    assert (with_key / '.env').exists()
    assert not (with_key / 'secrets' / 'global.env.age').exists()


def test_encrypt_keeps_plaintext_when_the_result_differs(with_key, monkeypatch):
    seed_plaintext(with_key)

    from devbase.env.secret_store import AgeBackend

    monkeypatch.setattr(AgeBackend, 'load', lambda self, ref: {'WRONG': 'x'})

    assert env_migrate.cmd_env_encrypt(with_key, assume_yes=True) == 1
    assert (with_key / '.env').exists()


# ---------------------------------------------------------------------------
# encrypt: 途中で失敗したときの巻き戻し
# ---------------------------------------------------------------------------

def test_encrypt_rolls_back_when_a_later_target_fails(two_projects, monkeypatch):
    """後続対象の失敗で「先行対象だけ移行済み」の中間状態を残さない"""
    root = two_projects
    before = compose_texts(root)

    from devbase.env.secret_store import AgeBackend, SecretStoreError

    original_save = AgeBackend.save

    def fail_on_web(self, ref, data):
        if ref == WEB:
            raise SecretStoreError('暗号化できません')
        return original_save(self, ref, data)

    monkeypatch.setattr(AgeBackend, 'save', fail_on_web)

    assert env_migrate.cmd_env_encrypt(root, assume_yes=True) == 1

    # 先行対象の平文は元の場所のまま。暗号文も compose.yml も動いていない
    assert plaintext_files(root) == sorted([
        str(root / '.env'),
        str(root / 'projects' / 'api' / '.env'),
        str(root / 'projects' / 'web' / '.env'),
    ])
    assert age_files(root) == []
    assert compose_texts(root) == before
    assert list(root.glob('backups/**/*.env')) == []


def test_encrypt_rolls_back_when_the_compose_write_fails(two_projects,
                                                         monkeypatch):
    """構成ファイルを書けなければ、機密の移動ごと巻き戻して失敗を返す"""
    root = two_projects
    before = compose_texts(root)

    original_write = env_migrate._write_compose

    def fail_on_web_compose(path, text, mode):
        # api → web の順に書くので、api だけ書けた状態から巻き戻すことになる
        if path.name == 'compose.yml' and path.parent.name == 'web':
            raise OSError('読み取り専用ファイルシステムです')
        return original_write(path, text, mode)

    monkeypatch.setattr(env_migrate, '_write_compose', fail_on_web_compose)

    assert env_migrate.cmd_env_encrypt(root, assume_yes=True) == 1

    assert plaintext_files(root) == sorted([
        str(root / '.env'),
        str(root / 'projects' / 'api' / '.env'),
        str(root / 'projects' / 'web' / '.env'),
    ])
    assert age_files(root) == []
    assert list(root.glob('backups/**/*.env')) == []
    # 先に書けてしまった api の compose.yml も元へ戻る
    assert compose_texts(root) == before


# ---------------------------------------------------------------------------
# 構成ファイルの書き込みは原子的か / 権限を保つか
# ---------------------------------------------------------------------------

def test_compose_is_not_left_partially_written(two_projects, monkeypatch):
    """途中で失敗しても、壊れかけの compose.yml をディスクに残さない"""
    root = two_projects
    before = compose_texts(root)

    original_replace = os.replace

    def fail_on_web_compose(src, dst, **kwargs):
        dst_path = Path(dst)
        if dst_path.name == 'compose.yml' and dst_path.parent.name == 'web':
            raise OSError('デバイスに空き領域がありません')
        return original_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, 'replace', fail_on_web_compose)

    assert env_migrate.cmd_env_encrypt(root, assume_yes=True) == 1

    # 元の内容がそのまま残っている (truncate された痕跡が無い)
    assert compose_texts(root) == before
    # 一時ファイルも掃除されている
    assert list((root / 'projects' / 'web').glob('.compose.yml.*')) == []


def test_compose_permissions_are_preserved(with_key):
    """compose.yml は機密ではない。原子的書き込みの既定 0600 へ落とさない"""
    compose = with_key / 'projects' / 'web' / 'compose.yml'
    compose.chmod(0o644)
    seed_plaintext(with_key)

    assert env_migrate.cmd_env_encrypt(with_key, assume_yes=True) == 0

    assert stat.S_IMODE(compose.stat().st_mode) == 0o644


# ---------------------------------------------------------------------------
# decrypt
# ---------------------------------------------------------------------------

def test_decrypt_reports_nothing_to_do(with_key, capsys):
    assert env_migrate.cmd_env_decrypt(with_key, assume_yes=True) == 0
    assert '平文へ戻す暗号化済みの設定はありません' in capsys.readouterr().out


def test_round_trip_restores_everything(with_key):
    seed_plaintext(with_key)
    compose = with_key / 'projects' / 'web' / 'compose.yml'
    before_compose = compose.read_text()

    env_migrate.cmd_env_encrypt(with_key, assume_yes=True)
    assert env_migrate.cmd_env_decrypt(with_key, assume_yes=True) == 0

    store = SecretStore(with_key)
    assert store.mode(GLOBAL) == 'plaintext'
    assert store.load(GLOBAL) == {'ANTHROPIC_API_KEY': 'sk-1'}
    assert store.load(WEB) == {'DB_PASSWORD': 'pw'}
    assert compose.read_text() == before_compose
    assert not (with_key / 'secrets' / 'global.env.age').exists()


def test_decrypt_dry_run_changes_nothing(with_key):
    seed_plaintext(with_key)
    env_migrate.cmd_env_encrypt(with_key, assume_yes=True)

    assert env_migrate.cmd_env_decrypt(with_key, dry_run=True) == 0

    assert SecretStore(with_key).is_encrypted(GLOBAL)
    assert not (with_key / '.env').exists()


def test_decrypt_aborts_without_confirmation(with_key, monkeypatch):
    seed_plaintext(with_key)
    env_migrate.cmd_env_encrypt(with_key, assume_yes=True)
    monkeypatch.setattr(env_migrate, 'safe_input', lambda prompt: '')

    assert env_migrate.cmd_env_decrypt(with_key) == 1
    assert SecretStore(with_key).is_encrypted(GLOBAL)


# ---------------------------------------------------------------------------
# decrypt: 途中で失敗したときの巻き戻し
# ---------------------------------------------------------------------------

def test_decrypt_rolls_back_when_a_later_target_fails(two_projects, monkeypatch):
    """後続対象を復号できないなら、先行対象の暗号文も消さない"""
    root = two_projects
    assert env_migrate.cmd_env_encrypt(root, assume_yes=True) == 0
    encrypted_compose = compose_texts(root)

    from devbase.env.secret_store import AgeBackend, SecretStoreError

    original_load = AgeBackend.load

    def fail_on_web(self, ref):
        if ref == WEB:
            raise SecretStoreError('復号できません')
        return original_load(self, ref)

    monkeypatch.setattr(AgeBackend, 'load', fail_on_web)

    assert env_migrate.cmd_env_decrypt(root, assume_yes=True) == 1

    assert age_files(root) == ['api.env.age', 'global.env.age', 'web.env.age']
    assert plaintext_files(root) == []
    assert compose_texts(root) == encrypted_compose


def test_decrypt_rolls_back_when_removing_the_ciphertext_fails(two_projects,
                                                              monkeypatch):
    """削除は最後。失敗しても控えたバイト列から暗号文を復元して元へ戻す"""
    root = two_projects
    assert env_migrate.cmd_env_encrypt(root, assume_yes=True) == 0
    encrypted_compose = compose_texts(root)

    from devbase.env.secret_store import AgeBackend, SecretStoreError

    original_remove = AgeBackend.remove

    def fail_on_web(self, ref):
        if ref == WEB:
            raise SecretStoreError('削除できません')
        return original_remove(self, ref)

    monkeypatch.setattr(AgeBackend, 'remove', fail_on_web)

    assert env_migrate.cmd_env_decrypt(root, assume_yes=True) == 1

    assert age_files(root) == ['api.env.age', 'global.env.age', 'web.env.age']
    assert plaintext_files(root) == []
    assert compose_texts(root) == encrypted_compose
    # 復元した暗号文はそのまま復号できる
    store = SecretStore(root)
    assert store.load(GLOBAL) == {'ANTHROPIC_API_KEY': 'sk-1'}
    assert store.load(API) == {'API_TOKEN': 'tk'}


def test_decrypt_of_one_project_leaves_the_global_reference_disabled(two_projects):
    """部分復号で、まだ暗号化されたままの共通設定の参照まで戻さない"""
    root = two_projects
    assert env_migrate.cmd_env_encrypt(root, assume_yes=True) == 0

    assert env_migrate.cmd_env_decrypt(root, assume_yes=True,
                                       projects=['web']) == 0

    store = SecretStore(root)
    assert not store.is_encrypted(WEB)
    assert store.is_encrypted(GLOBAL)

    from devbase.env import compose_migrate as cm

    web = (root / 'projects' / 'web' / 'compose.yml').read_text()
    # プロジェクト側だけが戻り、共通設定の参照は無効のまま
    assert '      - .env\n' in web
    assert f'{cm.DISABLED_MARK}- ${{DEVBASE_ROOT}}/.env' in web
    # 対象外のプロジェクトの構成には手を触れない
    api = (root / 'projects' / 'api' / 'compose.yml').read_text()
    assert f'{cm.DISABLED_MARK}- .env' in api


def test_decrypt_of_everything_after_a_partial_decrypt_restores_the_original(
        two_projects):
    root = two_projects
    before = compose_texts(root)
    assert env_migrate.cmd_env_encrypt(root, assume_yes=True) == 0

    assert env_migrate.cmd_env_decrypt(root, assume_yes=True,
                                       projects=['web']) == 0
    assert env_migrate.cmd_env_decrypt(root, assume_yes=True) == 0

    assert compose_texts(root) == before


def test_inline_env_file_is_warned_about(with_key, caplog):
    """自動で書き換えられない記法は黙って見逃さない"""
    compose = with_key / 'projects' / 'web' / 'compose.yml'
    compose.write_text("""services:
  dev:
    env_file: [ "${DEVBASE_ROOT}/.env", .env ]
""")
    seed_plaintext(with_key)

    with caplog.at_level(logging.WARNING,
                         logger='devbase.env.compose_migrate'):
        env_migrate.cmd_env_encrypt(with_key, dry_run=True)

    messages = [r.getMessage() for r in caplog.records]
    assert any('compose.yml:3' in m and 'env_file' in m for m in messages)


def test_inline_secret_env_file_aborts_the_migration(with_key, capsys):
    """機密を指すインライン記法は警告では済まない (参照が有効なまま残る)"""
    compose = with_key / 'projects' / 'web' / 'compose.yml'
    compose.write_text("""services:
  dev:
    env_file: [ "${DEVBASE_ROOT}/.env", .env ]
""")
    before = compose.read_text()
    seed_plaintext(with_key)

    assert env_migrate.cmd_env_encrypt(with_key, assume_yes=True) == 1

    # 何も動いていない: 平文も暗号文も構成ファイルもそのまま
    assert (with_key / '.env').exists()
    assert (with_key / 'projects' / 'web' / '.env').exists()
    assert age_files(with_key) == []
    assert list(with_key.glob('backups/**/*.env')) == []
    assert compose.read_text() == before


def test_inline_env_file_without_secrets_only_warns(with_key, caplog):
    """機密と無関係なインライン記法は移行に影響しない。警告だけで続行する"""
    compose = with_key / 'projects' / 'web' / 'compose.yml'
    compose.write_text("""services:
  dev:
    env_file: config/app.env
  worker:
    env_file:
      - ${DEVBASE_ROOT}/.env
      - env
""")
    seed_plaintext(with_key)

    with caplog.at_level(logging.WARNING,
                         logger='devbase.env.compose_migrate'):
        assert env_migrate.cmd_env_encrypt(with_key, assume_yes=True) == 0

    from devbase.env import compose_migrate as cm

    messages = [r.getMessage() for r in caplog.records]
    assert any('compose.yml:3' in m for m in messages)
    # ブロックシーケンスで書かれた機密参照はいつも通り無効化される
    assert f'{cm.DISABLED_MARK}- ${{DEVBASE_ROOT}}/.env' in compose.read_text()


def test_multi_line_long_syntax_secret_aborts_the_migration(with_key):
    """続きの行を持つ long syntax は行単位で外せない。移行ごと止める"""
    compose = with_key / 'projects' / 'web' / 'compose.yml'
    compose.write_text("""services:
  dev:
    env_file:
      - path: ${DEVBASE_ROOT}/.env
        required: false
""")
    before = compose.read_text()
    seed_plaintext(with_key)

    assert env_migrate.cmd_env_encrypt(with_key, assume_yes=True) == 1

    # 何も動いていない: 平文も暗号文も構成ファイルもそのまま
    assert (with_key / '.env').exists()
    assert age_files(with_key) == []
    assert compose.read_text() == before


def test_crlf_compose_keeps_its_line_endings(with_key):
    """CRLF の compose.yml を LF へ潰さない (往復でバイト単位に戻る)"""
    compose = with_key / 'projects' / 'web' / 'compose.yml'
    original = COMPOSE.replace('\n', '\r\n').encode('utf-8')
    compose.write_bytes(original)
    seed_plaintext(with_key)

    assert env_migrate.cmd_env_encrypt(with_key, assume_yes=True) == 0

    encrypted = compose.read_bytes()
    # 書き換えた行も含めて LF 単独の行は生まれない
    assert b'\n' not in encrypted.replace(b'\r\n', b'')
    assert b'# devbase(PLAN35)' in encrypted

    assert env_migrate.cmd_env_decrypt(with_key, assume_yes=True) == 0
    assert compose.read_bytes() == original


def test_unreadable_compose_aborts_the_migration(with_key, monkeypatch):
    """読めない構成ファイルを飛ばすと、機密だけ退避されて参照が残る"""
    seed_plaintext(with_key)

    # 構成ファイルは改行コードを保つために read_bytes で読む
    original_read = Path.read_bytes

    def fail_on_compose(self, *args, **kwargs):
        if self.name == 'compose.yml':
            raise OSError('アクセスが拒否されました')
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_bytes', fail_on_compose)

    assert env_migrate.cmd_env_encrypt(with_key, assume_yes=True) == 1

    # 平文は退避されず、暗号文も作られていない
    assert (with_key / '.env').exists()
    assert (with_key / 'projects' / 'web' / '.env').exists()
    assert age_files(with_key) == []
    assert list(with_key.glob('backups/**/*.env')) == []


def test_unreadable_compose_aborts_the_decrypt(two_projects, monkeypatch):
    root = two_projects
    assert env_migrate.cmd_env_encrypt(root, assume_yes=True) == 0

    original_read = Path.read_bytes

    def fail_on_compose(self, *args, **kwargs):
        if self.name == 'compose.yml' and self.parent.name == 'web':
            raise OSError('アクセスが拒否されました')
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_bytes', fail_on_compose)

    assert env_migrate.cmd_env_decrypt(root, assume_yes=True) == 1

    # 暗号文はそのまま。平文も書かれていない
    assert age_files(root) == ['api.env.age', 'global.env.age', 'web.env.age']
    assert plaintext_files(root) == []
