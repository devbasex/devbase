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


# ---------------------------------------------------------------------------
# グループ別の置き場 (PLAN56 決定 1・4・5)
# ---------------------------------------------------------------------------

OPENBAO_V2 = """\
version: 2
backend: openbao
openbao:
  url: https://openbao.example.com
  user: member01
  layout: group
  group_aliases:
    default: nyle
"""


def _refs(group):
    from devbase.env.secret_store import SecretRef

    return [SecretRef.for_global(group=group), SecretRef.for_project('web', group=group),
            SecretRef.for_global(owner='user', group=group),
            SecretRef.for_project('web', owner='user', group=group)]


def test_version_1_paths_and_cache_positions_are_unchanged(root):
    """受け入れ条件 9: ``version: 1`` のパスとキャッシュの位置の対応表を固定する"""
    write_yaml(root, OPENBAO_MINIMAL)
    ob = bc.load(root).openbao

    assert ob.layout == 'flat'
    assert [ob.path_of(ref) for ref in _refs(None)] == [
        'team/global', 'team/projects/web',
        'users/member01/global', 'users/member01/projects/web']
    assert [ob.cache_relpath(ref) for ref in _refs(None)] == [
        'team/global.env.age', 'team/projects/web.env.age',
        'user/global.env.age', 'user/projects/web.env.age']


def test_version_2_is_loaded_with_the_group_layout(root):
    write_yaml(root, OPENBAO_V2)

    config = bc.load(root)

    assert config.version == 2
    ob = config.openbao
    assert ob.layout == 'group'
    assert ob.path_team_prefix == 'team'
    assert ob.path_user_prefix == 'users'
    assert ob.group_aliases == {'default': 'nyle'}


def test_version_2_paths_and_cache_positions_follow_the_table(root):
    write_yaml(root, OPENBAO_V2)
    ob = bc.load(root).openbao

    assert [ob.path_of(ref) for ref in _refs('with')] == [
        'team/with/global', 'team/with/projects/web',
        'users/member01/with/global', 'users/member01/with/projects/web']
    # 読み替えはパスの上だけ (default → nyle)
    assert [ob.path_of(ref) for ref in _refs('default')] == [
        'team/nyle/global', 'team/nyle/projects/web',
        'users/member01/nyle/global', 'users/member01/nyle/projects/web']
    assert [ob.cache_relpath(ref) for ref in _refs('default')] == [
        'team/nyle/global.env.age', 'team/nyle/projects/web.env.age',
        'user/nyle/global.env.age', 'user/nyle/projects/web.env.age']
    assert ob.display_path(_refs('with')[0]) == 'devbase/team/with/global'


def test_version_2_honors_the_team_and_user_prefixes(root):
    write_yaml(root, OPENBAO_V2 + "  path_team_prefix: shared/t\n  path_user_prefix: people\n")
    ob = bc.load(root).openbao

    assert [ob.path_of(ref) for ref in _refs('with')] == [
        'shared/t/with/global', 'shared/t/with/projects/web',
        'people/member01/with/global', 'people/member01/with/projects/web']


def test_version_2_path_without_a_group_is_refused(root):
    from devbase.env.secret_store import SecretRef

    write_yaml(root, OPENBAO_V2)
    ob = bc.load(root).openbao

    with pytest.raises(bc.BackendConfigError):
        ob.path_of(SecretRef.for_global())


def test_storage_group_applies_the_alias(root):
    write_yaml(root, OPENBAO_V2)
    ob = bc.load(root).openbao

    assert ob.storage_group('default') == 'nyle'
    assert ob.storage_group('with') == 'with'


def test_version_2_roundtrips_through_save(root):
    write_yaml(root, OPENBAO_V2)
    config = bc.load(root)

    bc.save(root, config)
    text = (root / 'secrets' / 'backend.yml').read_text()

    assert 'path_team_global' not in text and 'path_team_project_prefix' not in text
    assert 'layout: group' in text and 'path_team_prefix: team' in text
    assert bc.load(root) == config


def test_version_1_save_does_not_write_the_group_keys(root):
    write_yaml(root, OPENBAO_MINIMAL)

    bc.save(root, bc.load(root))
    text = (root / 'secrets' / 'backend.yml').read_text()

    for key in ('layout', 'path_team_prefix', 'group_aliases'):
        assert key not in text


def test_version_2_without_layout_is_rejected(root):
    """決定 1: 版の番号から並びを思い出さなくて済むよう ``layout`` を必須にする"""
    write_yaml(root, OPENBAO_V2.replace("  layout: group\n", ""))

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert 'openbao.layout' in str(exc.value)


def test_version_2_with_the_flat_layout_is_rejected(root):
    write_yaml(root, OPENBAO_V2.replace("layout: group", "layout: flat"))

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert 'openbao.layout' in str(exc.value) and 'group' in str(exc.value)


@pytest.mark.parametrize('key', ['path_team_global', 'path_team_project_prefix'])
def test_version_2_rejects_the_flat_path_keys(root, key):
    write_yaml(root, OPENBAO_V2 + f"  {key}: team/x\n")

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert f'openbao.{key}' in str(exc.value) and '2' in str(exc.value)


@pytest.mark.parametrize('line', ['  layout: group\n', '  layout: flat\n',
                                  '  path_team_prefix: team\n',
                                  '  group_aliases:\n    default: nyle\n'])
def test_version_1_rejects_the_group_keys(root, line):
    write_yaml(root, OPENBAO_MINIMAL + line)

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    key = line.strip().split(':')[0]
    assert f'openbao.{key}' in str(exc.value) and '1' in str(exc.value)


@pytest.mark.parametrize('pair', ['ubuntu: nyle', '"1": nyle', '"bad name": nyle',
                                  'default: ubuntu', 'default: "1"', 'default: "a/b"'])
def test_aliases_must_be_valid_group_names(root, pair):
    write_yaml(root, OPENBAO_V2.replace('default: nyle', pair))

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert 'group_aliases' in str(exc.value)
    assert 'DEVBASE_ACCOUNT_GROUP' in str(exc.value)


@pytest.mark.parametrize('target', ['global', 'projects'])
def test_alias_target_cannot_be_a_reserved_storage_name(root, target):
    """決定 1: ``team/projects/global`` などの ``version: 1`` のパスと重なる名前は拒む"""
    write_yaml(root, OPENBAO_V2.replace('default: nyle', f'default: {target}'))

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert target in str(exc.value)


@pytest.mark.parametrize('group', ['global', 'projects'])
def test_reserved_storage_name_without_an_alias_is_rejected(root, group):
    write_yaml(root, OPENBAO_V2)
    ob = bc.load(root).openbao

    with pytest.raises(bc.BackendConfigError) as exc:
        ob.storage_group(group)
    assert group in str(exc.value)


def test_alias_from_a_reserved_storage_name_is_accepted(root):
    """``global`` という名前のグループを別の置き場へ向ける対応は成り立つ"""
    write_yaml(root, OPENBAO_V2.replace('default: nyle', 'global: nyle'))

    assert bc.load(root).openbao.storage_group('global') == 'nyle'


def test_version_3_is_still_rejected(root):
    write_yaml(root, OPENBAO_V2.replace('version: 2', 'version: 3'))

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert 'version' in str(exc.value)


def test_config_with_mismatched_version_and_layout_is_not_saved(root):
    """``use`` が版を引き継ぎ損ねた設定を書かない (版とレイアウトは 1 対 1)"""
    config = bc.BackendConfig(
        backend='openbao', version=1,
        openbao=bc.OpenBaoSettings(url='https://x.example.com', user='me', layout='group'))

    with pytest.raises(bc.BackendConfigError):
        bc.save(root, config)
    assert not (root / 'secrets' / 'backend.yml').exists()


@pytest.mark.parametrize('alias', ["default: ' with '", "' default ': with", "default: 'global '"])
def test_alias_with_surrounding_spaces_is_rejected(root, alias):
    """前後の空白を黙って落とすと、パスに空白が入るか読み替えが効かない"""
    write_yaml(root, OPENBAO_V2.replace('default: nyle', alias))

    with pytest.raises(bc.BackendConfigError) as exc:
        bc.load(root)
    assert '空白' in str(exc.value)
