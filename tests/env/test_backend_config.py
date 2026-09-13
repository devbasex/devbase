"""backend_config.py: backend の選択と非機密設定 (secrets/backend.yml)"""

from __future__ import annotations

import stat

import pytest

from devbase.env import backend_config as bc


@pytest.fixture
def root(tmp_path):
    (tmp_path / 'projects').mkdir()
    return tmp_path


def write_yaml(root, text: str):
    path = root / 'secrets' / 'backend.yml'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


INFISICAL_MINIMAL = """\
version: 1
backend: infisical
infisical:
  url: https://infisical.example.com
  project_id: 7f0e2c1a
  user: member01
"""


# ---------------------------------------------------------------------------
# 既定
# ---------------------------------------------------------------------------

def test_missing_file_means_auto(root):
    config = bc.load(root)

    assert config.backend == 'auto'
    assert config.cache_enabled is True


def test_empty_backend_falls_back_to_auto(root):
    write_yaml(root, "backend: ''\n")

    assert bc.load(root).backend == 'auto'


def test_infisical_defaults_are_filled_in(root):
    write_yaml(root, INFISICAL_MINIMAL)

    config = bc.load(root)

    assert config.backend == 'infisical'
    inf = config.infisical
    assert inf.url == 'https://infisical.example.com'
    assert inf.project_id == '7f0e2c1a'
    assert inf.environment == 'common'
    assert inf.user == 'member01'
    assert inf.path_team_global == '/team/global'
    assert inf.path_team_project_prefix == '/team/projects'
    assert inf.path_user_prefix == '/users'
    assert inf.api_version == 'v4'
    assert inf.timeout_seconds == 5


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------

def test_unknown_backend_lists_the_available_names(root):
    write_yaml(root, "backend: vaultwarden\n")

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)

    message = str(exc.value)
    assert 'vaultwarden' in message
    for name in ('auto', 'plaintext', 'age', 'infisical'):
        assert name in message


def test_infisical_requires_url_project_id_and_user(root):
    write_yaml(root, "backend: infisical\ninfisical: {}\n")

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)

    message = str(exc.value)
    assert 'url' in message and 'project_id' in message and 'user' in message


@pytest.mark.parametrize('url', [
    'https://infisical.example.com',
    'http://localhost:8080',
    'http://127.0.0.1:8080',
    'http://[::1]:8080',
])
def test_https_and_loopback_http_are_accepted(root, url):
    write_yaml(root, INFISICAL_MINIMAL.replace('https://infisical.example.com', url))

    assert bc.load(root).infisical.url == url


def test_http_to_a_remote_host_is_rejected(root):
    write_yaml(root, INFISICAL_MINIMAL.replace('https://', 'http://'))

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert 'http' in str(exc.value)


def test_unsupported_api_version_is_rejected_not_defaulted(root):
    write_yaml(root, INFISICAL_MINIMAL + "  api_version: v3\n")

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)

    message = str(exc.value)
    assert 'api_version' in message and 'v4' in message and 'v3' in message


@pytest.mark.parametrize('user', ['a/b', '..', '.', 'a\\b', ''])
def test_user_with_path_separators_is_rejected(root, user):
    write_yaml(root, INFISICAL_MINIMAL.replace('member01', repr(user)))

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert 'user' in str(exc.value)


def test_unreadable_yaml_names_the_file(root):
    path = write_yaml(root, "backend: [\n")

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert str(path) in str(exc.value)


def test_cache_can_be_disabled(root):
    write_yaml(root, INFISICAL_MINIMAL + "cache:\n  enabled: false\n")

    assert bc.load(root).cache_enabled is False


# ---------------------------------------------------------------------------
# 書き込み
# ---------------------------------------------------------------------------

def test_save_writes_0600_and_roundtrips(root):
    config = bc.BackendConfig(
        backend='infisical',
        infisical=bc.InfisicalSettings(
            url='https://infisical.example.com', project_id='pid', user='me'),
        cache_enabled=False,
    )

    path = bc.save(root, config)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = bc.load(root)
    assert loaded.backend == 'infisical'
    assert loaded.infisical.project_id == 'pid'
    assert loaded.cache_enabled is False


def test_save_rejects_an_invalid_config_without_writing(root):
    config = bc.BackendConfig(backend='vaultwarden')

    with pytest.raises(bc.BackendConfigError):
        bc.save(root, config)

    assert not (root / 'secrets' / 'backend.yml').exists()


# ---------------------------------------------------------------------------
# secretPath の組み立て
# ---------------------------------------------------------------------------

def test_secret_paths_follow_the_documented_layout():
    from devbase.env.secret_store import SecretRef

    inf = bc.InfisicalSettings(url='https://x', project_id='p', user='member01')

    assert inf.secret_path(SecretRef.for_global()) == '/team/global'
    assert inf.secret_path(SecretRef.for_project('carmo')) == '/team/projects/carmo'
    assert inf.secret_path(SecretRef.for_global(owner='user')) == '/users/member01/global'
    assert inf.secret_path(SecretRef.for_project('carmo', owner='user')) == \
        '/users/member01/projects/carmo'


def test_secret_paths_honor_configured_prefixes():
    from devbase.env.secret_store import SecretRef

    inf = bc.InfisicalSettings(url='https://x', project_id='p', user='u',
                               path_team_global='/shared',
                               path_team_project_prefix='/shared/p',
                               path_user_prefix='/people')

    assert inf.secret_path(SecretRef.for_global()) == '/shared'
    assert inf.secret_path(SecretRef.for_project('a')) == '/shared/p/a'
    assert inf.secret_path(SecretRef.for_global(owner='user')) == '/people/u/global'


@pytest.mark.parametrize('value', ['abc', '1.5', '[1]'])
def test_non_integer_version_is_a_config_error(root, value):
    write_yaml(root, f"version: {value}\nbackend: age\n")

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert 'version' in str(exc.value)


def test_unsupported_version_is_a_config_error(root):
    write_yaml(root, "version: 2\nbackend: age\n")

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert 'version' in str(exc.value) and '1' in str(exc.value)
