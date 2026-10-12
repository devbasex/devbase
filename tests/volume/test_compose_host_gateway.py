"""コンテナからホストの loopback へ届かせる設定 (#295)

``host_gateway_environment`` を渡すと、dev のインスタンスだけが

- その環境変数で ``dev_environment`` の値を上書きし
- ``extra_hosts`` に ``host.docker.internal:host-gateway`` を (元の形を保って) 足す

``host.docker.internal`` の行が元からあれば足さず、``network_mode: host`` の dev サービスには
何も当てない。渡さなければ生成物は今と同じ。
"""

from __future__ import annotations

import pytest

from devbase.volume.compose import generate_scaled_compose
from tests.volume.conftest import load_scaled, write_compose

RAW = {'BAO_ADDR': 'http://127.0.0.1:8200'}
GATEWAY = {'BAO_ADDR': 'http://host.docker.internal:8200'}


def _env(service: dict) -> dict:
    environment = service.get('environment') or {}
    if isinstance(environment, dict):
        return environment
    return dict(entry.split('=', 1) for entry in environment)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _generate(project, dev: dict, scale: int = 2, **kwargs) -> dict:
    write_compose(project, {'dev': dev, 'db': {'image': 'mysql'}})
    generate_scaled_compose(scale, dev_environment=RAW, **kwargs)
    return load_scaled(project)['services']


def test_dev_instances_get_the_gateway_address_and_the_host_line(project):
    services = _generate(project, {'image': 'alpine'}, host_gateway_environment=GATEWAY)

    for name in ('dev-1', 'dev-2'):
        assert _env(services[name])['BAO_ADDR'] == 'http://host.docker.internal:8200'
        assert services[name]['extra_hosts'] == ['host.docker.internal:host-gateway']
    # dev でないサービスへは足さない
    assert 'extra_hosts' not in services['db']
    assert 'BAO_ADDR' not in _env(services['db'])


def test_without_gateway_the_output_is_unchanged(project):
    services = _generate(project, {'image': 'alpine'})

    assert _env(services['dev-1'])['BAO_ADDR'] == 'http://127.0.0.1:8200'
    assert 'extra_hosts' not in services['dev-1']


def test_list_extra_hosts_keeps_its_entries_and_shape(project):
    dev = {'image': 'alpine', 'extra_hosts': ['db.local:10.0.0.5']}

    services = _generate(project, dev, host_gateway_environment=GATEWAY)

    assert services['dev-1']['extra_hosts'] == [
        'db.local:10.0.0.5', 'host.docker.internal:host-gateway']


def test_dict_extra_hosts_keeps_its_entries_and_shape(project):
    dev = {'image': 'alpine', 'extra_hosts': {'db.local': '10.0.0.5'}}

    services = _generate(project, dev, host_gateway_environment=GATEWAY)

    assert services['dev-1']['extra_hosts'] == {
        'db.local': '10.0.0.5', 'host.docker.internal': 'host-gateway'}


@pytest.mark.parametrize('extra_hosts', [
    ['host.docker.internal:192.168.65.254'],
    ['host.docker.internal=192.168.65.254'],
    {'host.docker.internal': '192.168.65.254'},
])
def test_an_existing_host_line_is_not_duplicated(project, extra_hosts):
    dev = {'image': 'alpine', 'extra_hosts': extra_hosts}

    services = _generate(project, dev, scale=1, host_gateway_environment=GATEWAY)

    assert services['dev-1']['extra_hosts'] == extra_hosts
    # 接続先の置き換えは行う (利用者の行で host.docker.internal が引ける)
    assert _env(services['dev-1'])['BAO_ADDR'] == 'http://host.docker.internal:8200'


def test_unknown_extra_hosts_shape_is_left_untouched(project):
    dev = {'image': 'alpine', 'extra_hosts': 'db.local:10.0.0.5'}

    services = _generate(project, dev, scale=1, host_gateway_environment=GATEWAY)

    assert services['dev-1']['extra_hosts'] == 'db.local:10.0.0.5'


def test_host_network_dev_service_keeps_the_loopback_address(project):
    dev = {'image': 'alpine', 'network_mode': 'host'}

    services = _generate(project, dev, scale=1, host_gateway_environment=GATEWAY)

    assert _env(services['dev-1'])['BAO_ADDR'] == 'http://127.0.0.1:8200'
    assert 'extra_hosts' not in services['dev-1']


def test_list_environment_is_overwritten_in_place(project):
    dev = {'image': 'alpine', 'environment': ['FEATURE=on']}

    services = _generate(project, dev, scale=1, host_gateway_environment=GATEWAY)

    entries = services['dev-1']['environment']
    assert 'FEATURE=on' in entries
    assert [e for e in entries if e.startswith('BAO_ADDR=')] == [
        'BAO_ADDR=http://host.docker.internal:8200']
