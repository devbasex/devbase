"""#425: 空の DEV_SERVICE_NAME を未設定と同じに扱い、開発サービス名を dev にする

``bin/devbase`` の ``${DEV_SERVICE_NAME:-dev}`` と同じ読み方に揃える。空白だけの値は
空として扱わない (前提 2)。
"""

from __future__ import annotations

import yaml
import pytest

from devbase.volume import compose


@pytest.mark.parametrize('value, expected', [
    (None, 'dev'),
    ('', 'dev'),
    ('app', 'app'),
    (' ', ' '),
])
def test_get_dev_service_name(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv('DEV_SERVICE_NAME', raising=False)
    else:
        monkeypatch.setenv('DEV_SERVICE_NAME', value)

    assert compose.get_dev_service_name() == expected


def test_empty_name_scales_dev_service(tmp_path, monkeypatch):
    """空の値でも generate_scaled_compose は dev を開発サービスとして複製する"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('DEV_SERVICE_NAME', '')
    (tmp_path / 'compose.yml').write_text(yaml.safe_dump({'services': {
        'dev': {'image': 'dev:latest'},
        'redis': {'image': 'redis:7'},
    }}, sort_keys=False), encoding='utf-8')

    compose.generate_scaled_compose(scale=2)

    scaled = yaml.safe_load(
        (tmp_path / '.docker-compose.scale.yml').read_text())['services']
    assert {'dev-1', 'dev-2', 'redis'} <= set(scaled)
    assert 'dev' not in scaled
