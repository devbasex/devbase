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
    generate_scaled_compose(1, secret_env_names=['TOKEN'])

    config = generated(project)
    assert 'environment' not in config['services']['db']
