"""env rekey / encrypt / decrypt / doctor の backend 対応 (PLAN51 決定 8 / 設計 1「検査の手段」)"""

from __future__ import annotations

import logging
import os
import subprocess

import pyrage
import pytest

from devbase.commands import env_migrate, env_ops
from devbase.env import agekeys, backend_config as bc, bootstrap, cache, cipher
from devbase.env.secret_store import SecretRef, SecretStore


GLOBAL = SecretRef.for_global()


@pytest.fixture
def git_root(openbao_root, monkeypatch):
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', os.devnull)
    subprocess.run(['git', 'init', '-q'], cwd=str(openbao_root), check=True)
    (openbao_root / '.gitignore').write_text('.env\n.env.bak*\nsecrets/\nprojects/*/.env\n')
    return openbao_root


@pytest.fixture
def colleague(tmp_path):
    identity = pyrage.x25519.Identity.generate()
    path = tmp_path / 'colleague.key'
    path.write_text(str(identity))
    return str(identity.to_public()), str(path)


def errors(caplog) -> str:
    return '\n'.join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


def seed_cache(root, openbao):
    openbao.put('team/global', {'A': '1'})
    SecretStore(root).load(GLOBAL)
    assert cache.entry_path(root, GLOBAL).exists()


# ---------------------------------------------------------------------------
# rekey
# ---------------------------------------------------------------------------

def test_rekey_reencrypts_bootstrap_and_cache(openbao_root, openbao, colleague):
    public, key_file = colleague
    seed_cache(openbao_root, openbao)
    own_key = agekeys.key_file_path()
    bootstrap_path = bootstrap.path(openbao_root)
    cache_path = cache.entry_path(openbao_root, GLOBAL)
    before = (bootstrap_path.read_bytes(), cache_path.read_bytes())

    assert env_ops.cmd_env_rekey(openbao_root, add=[public], assume_yes=True) == 0

    assert (bootstrap_path.read_bytes(), cache_path.read_bytes()) != before
    for path in (bootstrap_path, cache_path):
        cipher.decrypt(path.read_bytes(), identities=[key_file])      # 同僚が読める
        cipher.decrypt(path.read_bytes(), identities=[str(own_key)])  # 自分も読める
    assert bootstrap.load(openbao_root).role_id == 'rid'


def test_rekey_removed_recipient_cannot_read_bootstrap_or_cache(openbao_root, openbao,
                                                                colleague):
    public, key_file = colleague
    seed_cache(openbao_root, openbao)
    assert env_ops.cmd_env_rekey(openbao_root, add=[public], assume_yes=True) == 0

    assert env_ops.cmd_env_rekey(openbao_root, remove=[public], assume_yes=True) == 0

    for path in (bootstrap.path(openbao_root), cache.entry_path(openbao_root, GLOBAL)):
        with pytest.raises(cipher.CipherError):
            cipher.decrypt(path.read_bytes(), identities=[key_file])
    assert bootstrap.load(openbao_root).role_id == 'rid'


def test_rekey_dry_run_lists_bootstrap_and_cache(openbao_root, openbao, colleague, capsys):
    public, _ = colleague
    seed_cache(openbao_root, openbao)

    assert env_ops.cmd_env_rekey(openbao_root, add=[public], dry_run=True) == 0

    out = capsys.readouterr().out
    assert 'bootstrap.env.age' in out
    assert str(cache.entry_path(openbao_root, GLOBAL)) in out


def test_rekey_works_with_backend_openbao_and_no_age_secrets(openbao_root, colleague):
    """backend の選択を理由に止まらない (再暗号化する対象はブートストラップだけ)"""
    public, key_file = colleague

    assert env_ops.cmd_env_rekey(openbao_root, add=[public], assume_yes=True) == 0

    cipher.decrypt(bootstrap.path(openbao_root).read_bytes(), identities=[key_file])


# ---------------------------------------------------------------------------
# encrypt / decrypt
# ---------------------------------------------------------------------------

def test_encrypt_and_decrypt_are_age_only(openbao_root, caplog):
    (openbao_root / '.env').write_text('A=1\n')

    assert env_migrate.cmd_env_encrypt(openbao_root, assume_yes=True) == 1
    assert env_migrate.cmd_env_decrypt(openbao_root, assume_yes=True) == 1

    assert 'age' in errors(caplog) and 'openbao' in errors(caplog)
    assert (openbao_root / '.env').read_text() == 'A=1\n'
    assert not (openbao_root / 'secrets' / 'global.env.age').exists()


def test_encrypt_is_refused_when_backend_is_plaintext(openbao_root, caplog):
    """変換後に設定が指す先から機密が消える向きは拒む"""
    config = bc.load(openbao_root)
    bc.save(openbao_root, bc.BackendConfig(backend='plaintext', openbao=config.openbao))
    (openbao_root / '.env').write_text('A=1\n')

    assert env_migrate.cmd_env_encrypt(openbao_root, assume_yes=True) == 1

    assert (openbao_root / '.env').read_text() == 'A=1\n'
    assert 'plaintext' in errors(caplog)
    assert SecretStore(openbao_root).load(GLOBAL) == {'A': '1'}


def test_decrypt_is_refused_when_backend_is_age(openbao_root, caplog):
    config = bc.load(openbao_root)
    bc.save(openbao_root, bc.BackendConfig(backend='age', openbao=config.openbao))
    SecretStore(openbao_root).age.save(GLOBAL, {'A': '1'})

    assert env_migrate.cmd_env_decrypt(openbao_root, assume_yes=True) == 1

    assert (openbao_root / 'secrets' / 'global.env.age').exists()
    assert SecretStore(openbao_root).load(GLOBAL) == {'A': '1'}


def test_decrypt_works_when_backend_is_plaintext(openbao_root):
    config = bc.load(openbao_root)
    bc.save(openbao_root, bc.BackendConfig(backend='plaintext', openbao=config.openbao))
    SecretStore(openbao_root).age.save(GLOBAL, {'A': '1'})

    assert env_migrate.cmd_env_decrypt(openbao_root, assume_yes=True) == 0

    assert SecretStore(openbao_root).load(GLOBAL) == {'A': '1'}
    assert not (openbao_root / 'secrets' / 'global.env.age').exists()


def test_encrypt_still_works_when_backend_is_age(openbao_root):
    config = bc.load(openbao_root)
    bc.save(openbao_root, bc.BackendConfig(backend='age', openbao=config.openbao))
    (openbao_root / '.env').write_text('A=1\n')

    assert env_migrate.cmd_env_encrypt(openbao_root, assume_yes=True) == 0

    assert (openbao_root / 'secrets' / 'global.env.age').exists()


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def test_doctor_is_quiet_on_a_healthy_server_setup(git_root, openbao, capsys):
    seed_cache(git_root, openbao)

    assert env_ops.cmd_env_doctor(git_root) == 0
    out = capsys.readouterr().out
    assert 'backend.yml' in out


def test_doctor_reports_a_broken_backend_config(git_root, capsys):
    (git_root / 'secrets' / 'backend.yml').write_text('backend: vaultwarden\n')

    assert env_ops.cmd_env_doctor(git_root) == 1
    out = capsys.readouterr().out
    assert 'backend' in out and 'vaultwarden' in out


def test_doctor_reports_a_missing_user(git_root, capsys):
    (git_root / 'secrets' / 'backend.yml').write_text(
        f"backend: openbao\nopenbao:\n  url: {'https://x.example.com'}\n")

    assert env_ops.cmd_env_doctor(git_root) == 1
    assert 'openbao.user' in capsys.readouterr().out


def test_doctor_reports_missing_bootstrap_keys(git_root, capsys):
    bootstrap.path(git_root).unlink()

    assert env_ops.cmd_env_doctor(git_root) == 1
    out = capsys.readouterr().out
    assert 'bootstrap.env.age' in out and 'DEVBASE_OPENBAO_ROLE_ID' in out


@pytest.mark.parametrize('target', ['backend.yml', 'bootstrap.env.age', 'cache/team/global.env.age'])
def test_doctor_reports_loose_file_permissions(git_root, openbao, capsys, target):
    seed_cache(git_root, openbao)
    path = git_root / 'secrets' / target
    path.chmod(0o644)

    assert env_ops.cmd_env_doctor(git_root) == 1
    out = capsys.readouterr().out
    assert str(path) in out and 'chmod 600' in out


def test_doctor_reports_a_loose_cache_directory(git_root, openbao, capsys):
    seed_cache(git_root, openbao)
    (git_root / 'secrets' / 'cache').chmod(0o755)

    assert env_ops.cmd_env_doctor(git_root) == 1
    assert 'chmod 700' in capsys.readouterr().out


def test_doctor_probes_the_new_paths_for_git_ignore(git_root, capsys):
    (git_root / '.gitignore').write_text('.env\n.env.bak*\nsecrets/*.age\nsecrets/projects/\n')

    assert env_ops.cmd_env_doctor(git_root) == 1
    out = capsys.readouterr().out
    assert 'secrets/backend.yml' in out
    assert 'secrets/cache/team/global.env.age' in out


def test_doctor_without_a_backend_config_is_unchanged(tmp_path, monkeypatch, capsys):
    (tmp_path / 'projects').mkdir()
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', os.devnull)
    agekeys.generate_key_file()
    subprocess.run(['git', 'init', '-q'], cwd=str(tmp_path), check=True)
    (tmp_path / '.gitignore').write_text('.env\n.env.bak*\nsecrets/\nprojects/*/.env\n')

    assert env_ops.cmd_env_doctor(tmp_path) == 0
    assert '問題は見つかりませんでした' in capsys.readouterr().out


def test_doctor_probes_the_grouped_sources_file_and_cache_for_git_ignore(git_root, openbao,
                                                                        capsys):
    """PLAN56 決定 13: layout: group では .env.sources.<g>.yml の除外も確かめる"""
    from tests.conftest import configure_openbao

    configure_openbao(git_root, openbao, layout='group', group_aliases={'default': 'nyle'})
    (git_root / '.gitignore').write_text(
        '.env\n.env.bak*\nsecrets/*.age\nsecrets/projects/\nsecrets/backend.yml\n'
        'secrets/leftover.env\nprojects/*/.env\n.env.sources.yml\n')

    assert env_ops.cmd_env_doctor(git_root) == 1
    out = capsys.readouterr().out
    assert '.env.sources.nyle.yml' in out
    assert 'secrets/cache/team/nyle/global.env.age' in out

    (git_root / '.gitignore').write_text(
        '.env\n.env.bak*\nsecrets/\nprojects/*/.env\n.env.sources*.yml\n')
    assert env_ops.cmd_env_doctor(git_root) == 0
