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


OPENBAO_MINIMAL = """\
version: 1
backend: openbao
openbao:
  url: https://openbao.example.com
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


def test_openbao_defaults_are_filled_in(root):
    write_yaml(root, OPENBAO_MINIMAL)

    config = bc.load(root)

    assert config.backend == 'openbao'
    ob = config.openbao
    assert ob.url == 'https://openbao.example.com'
    assert ob.mount == 'devbase'
    assert ob.user == 'member01'
    assert ob.path_team_global == 'team/global'
    assert ob.path_team_project_prefix == 'team/projects'
    assert ob.path_user_prefix == 'users'
    assert ob.timeout_seconds == 5


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------

def test_unknown_backend_lists_the_available_names(root):
    write_yaml(root, "backend: vaultwarden\n")

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)

    message = str(exc.value)
    assert 'vaultwarden' in message
    for name in ('auto', 'plaintext', 'age', 'openbao'):
        assert name in message


def test_openbao_requires_url_and_user(root):
    write_yaml(root, "backend: openbao\nopenbao: {}\n")

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)

    message = str(exc.value)
    assert 'openbao.url' in message and 'openbao.user' in message


def test_infisical_is_no_longer_a_known_backend(root):
    write_yaml(root, "backend: infisical\ninfisical:\n  url: https://x\n")

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert 'infisical' in str(exc.value) and 'openbao' in str(exc.value)


@pytest.mark.parametrize('url', [
    'https://openbao.example.com',
    'http://localhost:8080',
    'http://127.0.0.1:8080',
    'http://[::1]:8080',
])
def test_https_and_loopback_http_are_accepted(root, url):
    write_yaml(root, OPENBAO_MINIMAL.replace('https://openbao.example.com', url))

    assert bc.load(root).openbao.url == url


@pytest.mark.parametrize('url', ['https://openbao.example.com/v1', 'https://openbao.example.com/?x=1',
                                 'https://openbao.example.com/#f'])
def test_url_with_a_path_query_or_fragment_is_rejected(root, url):
    write_yaml(root, OPENBAO_MINIMAL.replace('https://openbao.example.com', url))

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert 'openbao.url' in str(exc.value) and 'ホスト' in str(exc.value)


def test_url_with_a_trailing_slash_is_accepted(root):
    write_yaml(root, OPENBAO_MINIMAL.replace('https://openbao.example.com', 'https://openbao.example.com/'))

    assert bc.load(root).openbao.url == 'https://openbao.example.com/'


def test_http_to_a_remote_host_is_rejected(root):
    write_yaml(root, OPENBAO_MINIMAL.replace('https://', 'http://'))

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert 'http' in str(exc.value)


@pytest.mark.parametrize('key', ['path_team_global', 'path_team_project_prefix',
                                 'path_user_prefix'])
@pytest.mark.parametrize('value', ['/team/global', 'team/global/', 'a/../b'])
def test_paths_must_be_relative_without_dotdot(root, key, value):
    write_yaml(root, OPENBAO_MINIMAL + f"  {key}: {value!r}\n")

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert key in str(exc.value)


@pytest.mark.parametrize('mount', ['a/b', '..', 'a\\b'])
def test_mount_with_path_separators_is_rejected(root, mount):
    write_yaml(root, OPENBAO_MINIMAL + f"  mount: {mount!r}\n")

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert 'mount' in str(exc.value)


def test_non_positive_timeout_is_rejected(root):
    write_yaml(root, OPENBAO_MINIMAL + "  timeout_seconds: 0\n")

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert 'timeout_seconds' in str(exc.value)


@pytest.mark.parametrize('user', ['a/b', '..', '.', 'a\\b', ''])
def test_user_with_path_separators_is_rejected(root, user):
    write_yaml(root, OPENBAO_MINIMAL.replace('member01', repr(user)))

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert 'user' in str(exc.value)


def test_unreadable_yaml_names_the_file(root):
    path = write_yaml(root, "backend: [\n")

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert str(path) in str(exc.value)


def test_cache_can_be_disabled(root):
    write_yaml(root, OPENBAO_MINIMAL + "cache:\n  enabled: false\n")

    assert bc.load(root).cache_enabled is False


# ---------------------------------------------------------------------------
# 書き込み
# ---------------------------------------------------------------------------

def test_save_writes_0600_and_roundtrips(root):
    config = bc.BackendConfig(
        backend='openbao',
        openbao=bc.OpenBaoSettings(
            url='https://openbao.example.com', user='me', mount='kv'),
        cache_enabled=False,
    )

    path = bc.save(root, config)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = bc.load(root)
    assert loaded.backend == 'openbao'
    assert loaded.openbao.mount == 'kv'
    assert loaded.cache_enabled is False


def test_save_rejects_an_invalid_config_without_writing(root):
    config = bc.BackendConfig(backend='vaultwarden')

    with pytest.raises(bc.BackendConfigError):
        bc.save(root, config)

    assert not (root / 'secrets' / 'backend.yml').exists()


# ---------------------------------------------------------------------------
# パスの組み立て
# ---------------------------------------------------------------------------

def test_paths_follow_the_documented_layout():
    from devbase.env.secret_store import SecretRef

    ob = bc.OpenBaoSettings(url='https://x', user='member01')

    assert ob.path_of(SecretRef.for_global()) == 'team/global'
    assert ob.path_of(SecretRef.for_project('carmo')) == 'team/projects/carmo'
    assert ob.path_of(SecretRef.for_global(owner='user')) == 'users/member01/global'
    assert ob.path_of(SecretRef.for_project('carmo', owner='user')) == \
        'users/member01/projects/carmo'
    assert ob.display_path(SecretRef.for_global()) == 'devbase/team/global'


def test_paths_honor_configured_prefixes_and_mount():
    from devbase.env.secret_store import SecretRef

    ob = bc.OpenBaoSettings(url='https://x', user='u', mount='kv',
                            path_team_global='shared',
                            path_team_project_prefix='shared/p',
                            path_user_prefix='people')

    assert ob.path_of(SecretRef.for_global()) == 'shared'
    assert ob.path_of(SecretRef.for_project('a')) == 'shared/p/a'
    assert ob.path_of(SecretRef.for_global(owner='user')) == 'people/u/global'
    assert ob.display_path(SecretRef.for_project('a')) == 'kv/shared/p/a'


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
