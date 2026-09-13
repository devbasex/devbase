"""生成物の bind mount の ``~`` をリモート側の HOME で展開する (PLAN52)。"""

from __future__ import annotations

import copy

from devbase.volume import bind_mounts


def _services():
    return {
        'dev-1': {'volumes': [
            '~/.aws:/home/ubuntu/.aws',
            '~:/mnt/home:ro',
            '/var/run/docker.sock:/var/run/docker.sock',
            'devbase_work_1:/work',
            {'type': 'bind', 'source': '~/devbase', 'target': '/work/devbase'},
            {'type': 'volume', 'source': 'named', 'target': '/data'},
        ]},
        'db': {'volumes': ['./init.sql:/docker-entrypoint-initdb.d/init.sql',
                           '~alice/x:/x']},
        'nothing': {},
    }


def test_expand_home_rewrites_tilde_forms():
    services = _services()
    warnings = bind_mounts.expand_home(services, '/home/takemi')
    assert services['dev-1']['volumes'][0] == '/home/takemi/.aws:/home/ubuntu/.aws'
    assert services['dev-1']['volumes'][1] == '/home/takemi:/mnt/home:ro'
    assert services['dev-1']['volumes'][4]['source'] == '/home/takemi/devbase'
    # 触らないもの
    assert services['dev-1']['volumes'][2] == '/var/run/docker.sock:/var/run/docker.sock'
    assert services['dev-1']['volumes'][3] == 'devbase_work_1:/work'
    assert services['dev-1']['volumes'][5]['source'] == 'named'
    # 書き換えず警告に載せるもの
    assert warnings == ['db: ./init.sql:/docker-entrypoint-initdb.d/init.sql', 'db: ~alice/x:/x']


def test_expand_home_strips_trailing_slash():
    services = {'s': {'volumes': ['~/x:/x']}}
    bind_mounts.expand_home(services, '/home/t/')
    assert services['s']['volumes'][0] == '/home/t/x:/x'


def test_collect_remote_warnings_without_home_lists_tilde_and_relative():
    services = _services()
    before = copy.deepcopy(services)
    warnings = bind_mounts.collect_remote_warnings(services)
    assert services == before                      # 書き換えない
    assert warnings == [
        'dev-1: ~/.aws:/home/ubuntu/.aws',
        'dev-1: ~:/mnt/home:ro',
        'dev-1: ~/devbase:/work/devbase',
        'db: ./init.sql:/docker-entrypoint-initdb.d/init.sql',
        'db: ~alice/x:/x',
    ]


def test_no_warnings_when_nothing_to_report():
    services = {'s': {'volumes': ['/abs:/x', 'named:/y']}}
    assert bind_mounts.expand_home(services, '/h') == []
    assert bind_mounts.collect_remote_warnings(services) == []
