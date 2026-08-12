"""env encrypt / decrypt: 平文と暗号化構成の往復"""

from __future__ import annotations

import pytest

from devbase.commands import env_migrate
from devbase.env.secret_store import SecretRef, SecretStore


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')

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
