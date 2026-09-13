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
from tests.conftest import configure_infisical


GLOBAL = SecretRef.for_global()


@pytest.fixture
def git_root(infisical_root, monkeypatch):
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', os.devnull)
    subprocess.run(['git', 'init', '-q'], cwd=str(infisical_root), check=True)
    (infisical_root / '.gitignore').write_text('.env\n.env.bak*\nsecrets/\nprojects/*/.env\n')
    return infisical_root


@pytest.fixture
def colleague(tmp_path):
    identity = pyrage.x25519.Identity.generate()
    path = tmp_path / 'colleague.key'
    path.write_text(str(identity))
    return str(identity.to_public()), str(path)


def errors(caplog) -> str:
    return '\n'.join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


def seed_cache(root, infisical):
    infisical.put('/team/global', {'A': '1'})
    SecretStore(root).load(GLOBAL)
    assert cache.entry_path(root, GLOBAL).exists()


# ---------------------------------------------------------------------------
# rekey
# ---------------------------------------------------------------------------

def test_rekey_reencrypts_bootstrap_and_cache(infisical_root, infisical, colleague):
    public, key_file = colleague
    seed_cache(infisical_root, infisical)
    own_key = agekeys.key_file_path()
    bootstrap_path = bootstrap.path(infisical_root)
    cache_path = cache.entry_path(infisical_root, GLOBAL)
    before = (bootstrap_path.read_bytes(), cache_path.read_bytes())

    assert env_ops.cmd_env_rekey(infisical_root, add=[public], assume_yes=True) == 0

    assert (bootstrap_path.read_bytes(), cache_path.read_bytes()) != before
    for path in (bootstrap_path, cache_path):
        cipher.decrypt(path.read_bytes(), identities=[key_file])      # 同僚が読める
        cipher.decrypt(path.read_bytes(), identities=[str(own_key)])  # 自分も読める
    assert bootstrap.load(infisical_root).client_id == 'cid'


def test_rekey_removed_recipient_cannot_read_bootstrap_or_cache(infisical_root, infisical,
                                                                colleague):
    public, key_file = colleague
    seed_cache(infisical_root, infisical)
    assert env_ops.cmd_env_rekey(infisical_root, add=[public], assume_yes=True) == 0

    assert env_ops.cmd_env_rekey(infisical_root, remove=[public], assume_yes=True) == 0

    for path in (bootstrap.path(infisical_root), cache.entry_path(infisical_root, GLOBAL)):
        with pytest.raises(cipher.CipherError):
            cipher.decrypt(path.read_bytes(), identities=[key_file])
    assert bootstrap.load(infisical_root).client_id == 'cid'


def test_rekey_dry_run_lists_bootstrap_and_cache(infisical_root, infisical, colleague, capsys):
    public, _ = colleague
    seed_cache(infisical_root, infisical)

    assert env_ops.cmd_env_rekey(infisical_root, add=[public], dry_run=True) == 0

    out = capsys.readouterr().out
    assert 'bootstrap.env.age' in out
    assert str(cache.entry_path(infisical_root, GLOBAL)) in out


def test_rekey_works_with_backend_infisical_and_no_age_secrets(infisical_root, colleague):
    """backend の選択を理由に止まらない (再暗号化する対象はブートストラップだけ)"""
    public, key_file = colleague

    assert env_ops.cmd_env_rekey(infisical_root, add=[public], assume_yes=True) == 0

    cipher.decrypt(bootstrap.path(infisical_root).read_bytes(), identities=[key_file])


# ---------------------------------------------------------------------------
# encrypt / decrypt
# ---------------------------------------------------------------------------

def test_encrypt_and_decrypt_are_age_only(infisical_root, caplog):
    (infisical_root / '.env').write_text('A=1\n')

    assert env_migrate.cmd_env_encrypt(infisical_root, assume_yes=True) == 1
    assert env_migrate.cmd_env_decrypt(infisical_root, assume_yes=True) == 1

    assert 'age' in errors(caplog) and 'infisical' in errors(caplog)
    assert (infisical_root / '.env').read_text() == 'A=1\n'
    assert not (infisical_root / 'secrets' / 'global.env.age').exists()


def test_encrypt_still_works_when_backend_is_age(infisical_root):
    config = bc.load(infisical_root)
    bc.save(infisical_root, bc.BackendConfig(backend='age', infisical=config.infisical))
    (infisical_root / '.env').write_text('A=1\n')

    assert env_migrate.cmd_env_encrypt(infisical_root, assume_yes=True) == 0

    assert (infisical_root / 'secrets' / 'global.env.age').exists()


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def test_doctor_is_quiet_on_a_healthy_server_setup(git_root, infisical, capsys):
    seed_cache(git_root, infisical)

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
        f"backend: infisical\ninfisical:\n  url: {'https://x.example.com'}\n  project_id: p\n")

    assert env_ops.cmd_env_doctor(git_root) == 1
    assert 'infisical.user' in capsys.readouterr().out


def test_doctor_reports_missing_bootstrap_keys(git_root, capsys):
    bootstrap.path(git_root).unlink()

    assert env_ops.cmd_env_doctor(git_root) == 1
    out = capsys.readouterr().out
    assert 'bootstrap.env.age' in out and 'DEVBASE_INFISICAL_CLIENT_ID' in out


@pytest.mark.parametrize('target', ['backend.yml', 'bootstrap.env.age', 'cache/team/global.env.age'])
def test_doctor_reports_loose_file_permissions(git_root, infisical, capsys, target):
    seed_cache(git_root, infisical)
    path = git_root / 'secrets' / target
    path.chmod(0o644)

    assert env_ops.cmd_env_doctor(git_root) == 1
    out = capsys.readouterr().out
    assert str(path) in out and 'chmod 600' in out


def test_doctor_reports_a_loose_cache_directory(git_root, infisical, capsys):
    seed_cache(git_root, infisical)
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
