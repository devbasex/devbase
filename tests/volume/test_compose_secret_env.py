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
      LEFTOVER: has-a-value
    volumes:
      - x:/work
  db:
    image: mysql
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


def generated(path):
    return yaml.safe_load((path / '.docker-compose.scale.yml').read_text())


def test_secret_names_are_listed_without_values(project):
    generate_scaled_compose(1, secret_env_names=['ANTHROPIC_API_KEY', 'DB_PASSWORD'])

    config = generated(project)
    assert config['services']['dev-1']['environment'] == [
        'ANTHROPIC_API_KEY', 'DB_PASSWORD']


def test_generated_file_contains_no_secret_values(project):
    generate_scaled_compose(1, secret_env_names=['ANTHROPIC_API_KEY'])

    text = (project / '.docker-compose.scale.yml').read_text()
    assert 'ANTHROPIC_API_KEY' in text
    # 値を持つ既存の environment は落とす (生成物に値を残さない)
    assert 'has-a-value' not in text


def test_every_instance_gets_the_names(project):
    generate_scaled_compose(3, secret_env_names=['TOKEN'])

    config = generated(project)
    for index in (1, 2, 3):
        assert config['services'][f'dev-{index}']['environment'] == ['TOKEN']


def test_no_environment_section_without_secrets(project):
    generate_scaled_compose(1, secret_env_names=[])

    config = generated(project)
    assert 'environment' not in config['services']['dev-1']


def test_missing_env_file_entries_are_dropped(project):
    """暗号化で平文が無くなった参照を残すと Compose が起動時に落ちる"""
    generate_scaled_compose(1, secret_env_names=['TOKEN'])

    config = generated(project)
    assert config['services']['dev-1']['env_file'] == ['env']


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
