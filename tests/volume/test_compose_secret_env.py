"""生成する構成ファイルへの機密の渡し方 (変数名のみの列挙)"""

from __future__ import annotations

import pytest
import yaml

from devbase.volume.compose import generate_scaled_compose


COMPOSE = """services:
  dev:
    image: alpine
    env_file:
      - ${DEVBASE_ROOT}/.env
      - env
    environment:
      FEATURE_FLAG: enabled
      DB_PASSWORD: has-a-value
    volumes:
      - x:/work
  db:
    image: mysql
volumes:
  x: {}
"""

COMPOSE_LIST_ENV = """services:
  dev:
    image: alpine
    environment:
      - FEATURE_FLAG=enabled
      - DB_PASSWORD=has-a-value
      - PASSTHROUGH
    volumes:
      - x:/work
volumes:
  x: {}
"""

COMPOSE_NO_ENV = """services:
  dev:
    image: alpine
    volumes:
      - x:/work
volumes:
  x: {}
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / 'compose.yml').write_text(COMPOSE)
    (tmp_path / 'env').write_text('GIT_REPO=web\n')
    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path / 'root'))
    (tmp_path / 'root').mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def project_factory(tmp_path, monkeypatch):
    """任意の compose.yml でプロジェクトを組み立てる"""
    def build(compose_text):
        (tmp_path / 'compose.yml').write_text(compose_text)
        monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path / 'root'))
        (tmp_path / 'root').mkdir(exist_ok=True)
        monkeypatch.chdir(tmp_path)
        return tmp_path
    return build


def generated(path):
    return yaml.safe_load((path / '.docker-compose.scale.yml').read_text())


def test_secret_names_are_listed_without_values(project):
    generate_scaled_compose(1, secret_env_names=['ANTHROPIC_API_KEY', 'DB_PASSWORD'])

    # 元が map 形式なら map のまま。機密キーだけ値なし参照 (None) になる
    assert generated(project)['services']['dev-1']['environment'] == {
        'FEATURE_FLAG': 'enabled',
        'DB_PASSWORD': None,
        'ANTHROPIC_API_KEY': None,
    }


def test_non_secret_environment_is_preserved_in_list_form(project_factory):
    path = project_factory(COMPOSE_LIST_ENV)

    generate_scaled_compose(1, secret_env_names=['DB_PASSWORD', 'ANTHROPIC_API_KEY'])

    # 元が list 形式なら list のまま。機密キーは裸のキー名へ落とす
    assert generated(path)['services']['dev-1']['environment'] == [
        'FEATURE_FLAG=enabled',
        'DB_PASSWORD',
        'PASSTHROUGH',
        'ANTHROPIC_API_KEY',
    ]
    assert 'has-a-value' not in (path / '.docker-compose.scale.yml').read_text()


def test_generated_file_contains_no_secret_values(project):
    generate_scaled_compose(1, secret_env_names=['ANTHROPIC_API_KEY', 'DB_PASSWORD'])

    text = (project / '.docker-compose.scale.yml').read_text()
    assert 'ANTHROPIC_API_KEY' in text
    # 機密キーの値は生成物に残さない。非機密の固定値はそのまま残す
    assert 'has-a-value' not in text
    assert 'enabled' in text


def test_every_instance_gets_the_names(project):
    generate_scaled_compose(3, secret_env_names=['TOKEN'])

    config = generated(project)
    for index in (1, 2, 3):
        assert config['services'][f'dev-{index}']['environment']['TOKEN'] is None


def test_no_environment_section_without_secrets(project_factory):
    path = project_factory(COMPOSE_NO_ENV)

    generate_scaled_compose(1, secret_env_names=[])

    assert 'environment' not in generated(path)['services']['dev-1']


def test_names_are_listed_when_original_has_no_environment(project_factory):
    path = project_factory(COMPOSE_NO_ENV)

    generate_scaled_compose(1, secret_env_names=['ANTHROPIC_API_KEY', 'TOKEN'])

    assert generated(path)['services']['dev-1']['environment'] == [
        'ANTHROPIC_API_KEY', 'TOKEN']


def test_missing_env_file_entries_are_dropped(project):
    """暗号化で平文が無くなった参照を残すと Compose が起動時に落ちる"""
    generate_scaled_compose(1, secret_env_names=['TOKEN'])

    config = generated(project)
    assert config['services']['dev-1']['env_file'] == ['env']


def test_missing_non_secret_env_file_entries_are_kept(project_factory):
    """機密以外の欠落は隠さない (タイプミスや未配置を Compose に知らせる)"""
    path = project_factory("""services:
  dev:
    image: alpine
    env_file:
      - ${DEVBASE_ROOT}/.env
      - config/app.env
      - .env
""")

    generate_scaled_compose(1, secret_env_names=['TOKEN'])

    # 既知の機密参照 (${DEVBASE_ROOT}/.env, .env) だけが落ち、残りは残る
    assert generated(path)['services']['dev-1']['env_file'] == ['config/app.env']


def test_existing_env_file_entries_are_kept(project):
    (project / 'root' / '.env').write_text('TOKEN=x\n')

    generate_scaled_compose(1, secret_env_names=['TOKEN'])

    config = generated(project)
    assert config['services']['dev-1']['env_file'] == [
        '${DEVBASE_ROOT}/.env', 'env']


def test_env_file_key_is_removed_when_nothing_remains(tmp_path, monkeypatch):
    (tmp_path / 'compose.yml').write_text("""services:
  dev:
    image: alpine
    env_file:
      - ${DEVBASE_ROOT}/.env
""")
    monkeypatch.setenv('DEVBASE_ROOT', str(tmp_path / 'root'))
    (tmp_path / 'root').mkdir()
    monkeypatch.chdir(tmp_path)

    generate_scaled_compose(1, secret_env_names=['TOKEN'])

    config = yaml.safe_load((tmp_path / '.docker-compose.scale.yml').read_text())
    assert 'env_file' not in config['services']['dev-1']


def test_unresolvable_env_file_entries_are_left_alone(tmp_path, monkeypatch):
    """未定義の変数を含む参照は存在判定できないので触らない"""
    (tmp_path / 'compose.yml').write_text("""services:
  dev:
    image: alpine
    env_file:
      - ${SOME_UNDEFINED_ROOT}/.env
""")
    monkeypatch.delenv('SOME_UNDEFINED_ROOT', raising=False)
    monkeypatch.chdir(tmp_path)

    generate_scaled_compose(1)

    config = yaml.safe_load((tmp_path / '.docker-compose.scale.yml').read_text())
    assert config['services']['dev-1']['env_file'] == ['${SOME_UNDEFINED_ROOT}/.env']


def test_non_dev_services_are_untouched(project):
    """機密ファイルを参照していないサービスには余計な変数を注入しない"""
    generate_scaled_compose(1, secret_env_names=['TOKEN'])

    config = generated(project)
    assert 'environment' not in config['services']['db']


# ---------------------------------------------------------------------------
# 元々機密ファイルを参照していた非 dev サービスへの受け渡し
# ---------------------------------------------------------------------------

COMPOSE_DB_WITH_SECRET = """services:
  dev:
    image: alpine
    volumes:
      - x:/work
  db:
    image: mysql
    env_file:
      - ${DEVBASE_ROOT}/.env
    environment:
      MYSQL_DATABASE: app
  cache:
    image: redis
    env_file:
      - config/app.env
volumes:
  x: {}
"""


def test_non_dev_service_with_a_secret_reference_gets_the_names(project_factory):
    """DB パスワードを env_file から受け取っていたサービスに機密を渡す"""
    path = project_factory(COMPOSE_DB_WITH_SECRET)

    generate_scaled_compose(1, secret_env_names=['DB_PASSWORD'])

    config = generated(path)
    # 非機密の値は残したまま、機密は値なし参照として列挙される
    assert config['services']['db']['environment'] == {
        'MYSQL_DATABASE': 'app',
        'DB_PASSWORD': None,
    }
    # 機密を参照していないサービスには注入しない
    assert 'environment' not in config['services']['cache']


def test_commented_out_references_still_receive_the_secrets(project_factory):
    """移行後は参照がコメントアウトされる。YAML から消えても渡し先は変えない"""
    from devbase.env import compose_migrate

    disabled, _ = compose_migrate.disable(COMPOSE_DB_WITH_SECRET)
    path = project_factory(disabled)

    generate_scaled_compose(1, secret_env_names=['DB_PASSWORD'])

    config = generated(path)
    assert config['services']['db']['environment']['DB_PASSWORD'] is None
    assert 'environment' not in config['services']['cache']


# ---------------------------------------------------------------------------
# 由来 (共通 / プロジェクト) ごとの絞り込み
# ---------------------------------------------------------------------------

COMPOSE_MIXED_ORIGINS = """services:
  dev:
    image: alpine
    env_file:
      - ${DEVBASE_ROOT}/.env
      - env
      - .env
    volumes:
      - x:/work
  global_only:
    image: mysql
    env_file:
      - ${DEVBASE_ROOT}/.env
  project_only:
    image: redis
    env_file:
      - .env
  both:
    image: nginx
    env_file:
      - ${DEVBASE_ROOT}/.env
      - .env
  none:
    image: busybox
volumes:
  x: {}
"""

ORIGINS = dict(
    secret_env_names=['SHARED_KEY', 'PROJECT_TOKEN'],
    global_env_names=['SHARED_KEY'],
    project_env_names=['PROJECT_TOKEN'],
)


def _env_names(service_config):
    """map / list どちらの記法でも、列挙された変数名を集合で返す"""
    environment = service_config.get('environment')
    if isinstance(environment, dict):
        return set(environment)
    return {item.split('=', 1)[0] for item in (environment or [])}


def test_global_only_service_gets_only_global_names(project_factory):
    """共通の .env だけを読んでいたサービスにプロジェクト固有の機密は渡さない"""
    path = project_factory(COMPOSE_MIXED_ORIGINS)

    generate_scaled_compose(1, **ORIGINS)

    assert _env_names(generated(path)['services']['global_only']) == {'SHARED_KEY'}


def test_project_only_service_gets_only_project_names(project_factory):
    path = project_factory(COMPOSE_MIXED_ORIGINS)

    generate_scaled_compose(1, **ORIGINS)

    assert _env_names(generated(path)['services']['project_only']) == {
        'PROJECT_TOKEN'}


def test_service_referencing_both_gets_every_name(project_factory):
    path = project_factory(COMPOSE_MIXED_ORIGINS)

    generate_scaled_compose(1, **ORIGINS)

    config = generated(path)
    assert _env_names(config['services']['both']) == {
        'SHARED_KEY', 'PROJECT_TOKEN'}
    # dev は従来どおり全件 (env_file を書いていない構成でも両方が要る)
    assert _env_names(config['services']['dev-1']) == {
        'SHARED_KEY', 'PROJECT_TOKEN'}
    # 機密を参照していないサービスには何も注入しない
    assert 'environment' not in config['services']['none']


def test_origins_are_respected_after_migration(project_factory):
    """移行で参照がコメントアウトされたあとも由来ごとの絞り込みを保つ"""
    from devbase.env import compose_migrate

    disabled, _ = compose_migrate.disable(COMPOSE_MIXED_ORIGINS)
    path = project_factory(disabled)

    generate_scaled_compose(1, **ORIGINS)

    config = generated(path)
    assert _env_names(config['services']['global_only']) == {'SHARED_KEY'}
    assert _env_names(config['services']['project_only']) == {'PROJECT_TOKEN'}


def test_without_the_split_every_receiver_gets_every_name(project_factory):
    """由来の内訳が渡されない場合は従来どおり全件 (渡し漏れで壊さない)"""
    path = project_factory(COMPOSE_MIXED_ORIGINS)

    generate_scaled_compose(1, secret_env_names=['SHARED_KEY', 'PROJECT_TOKEN'])

    config = generated(path)
    assert _env_names(config['services']['global_only']) == {
        'SHARED_KEY', 'PROJECT_TOKEN'}


def test_unreadable_compose_falls_back_to_dev_only(project_factory, monkeypatch):
    """生テキストを読めない場合は dev だけ・全件へフォールバックする"""
    from pathlib import Path as _Path

    path = project_factory(COMPOSE_MIXED_ORIGINS)

    original = _Path.read_text

    def fail_on_compose(self, *args, **kwargs):
        if self.name == 'compose.yml':
            raise OSError('boom')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(_Path, 'read_text', fail_on_compose)

    generate_scaled_compose(1, **ORIGINS)

    config = generated(path)
    assert _env_names(config['services']['dev-1']) == {
        'SHARED_KEY', 'PROJECT_TOKEN'}
    for name in ('global_only', 'project_only', 'both', 'none'):
        assert 'environment' not in config['services'][name]
