"""``devbase scale`` の Compose の呼び出しと正常系の手順 (PLAN65)。

``devbase scale`` の起動は ``devbase up`` と同じ共通経路 (``docker_compose``) を通り、子プロセスの
``COMPOSE_PROFILES`` を打ち消し用の名前にする。起動の対象は生成物の既定のサービスを明示する。

実 docker と実 ``DEVBASE_ROOT`` には触れない。``subprocess.run`` を差し替えて、組み立てた
コマンド列と子プロセスの環境を拾う。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from devbase.commands import container
from devbase.errors import DevbaseError
from devbase.utils import docker
from devbase.utils import docker_context as dc


PROJECT_YML = "version: 1\nscale: 1\nrepos:\n  - owner: volareinc\n    repo: carmo\n"

# 生成物のサービスとそのプロファイル。``config --services`` の応答はここから作る
NO_PROFILE_SERVICES = {'dev-1': [], 'dev-2': [], 'redis': []}
WITH_PROFILE_SERVICES = {'dev-1': [], 'dev-2': [], 'redis': [], 'testdb': ['test']}


class FakeCompose:
    """``subprocess.run`` の代わり。呼び出しを記録し、``config --services`` に答える。

    ``config --services`` は Compose と同じく、有効なプロファイル (``--profile`` と子プロセスの
    ``COMPOSE_PROFILES``) に属するサービスと、プロファイルを持たないサービスを返す。
    """

    def __init__(self, services: dict, calls: list, up_returncode: int = 0):
        self.services = services
        self.calls = calls
        self.up_returncode = up_returncode

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        env = kwargs.get('env')
        self.calls.append(('run', {'cmd': cmd, 'env': env}))
        if 'config' in cmd and '--services' in cmd:
            active = set()
            for i, arg in enumerate(cmd):
                if arg == '--profile':
                    active.add(cmd[i + 1])
            raw = (env if env is not None else os.environ).get('COMPOSE_PROFILES', '')
            active.update(p for p in raw.split(',') if p)
            names = [name for name, profiles in self.services.items()
                     if not profiles or active.intersection(profiles)]
            return subprocess.CompletedProcess(cmd, 0, '\n'.join(names) + '\n', '')
        returncode = self.up_returncode if 'up' in cmd else 0
        return subprocess.CompletedProcess(cmd, returncode, '', '')


@pytest.fixture
def scale_harness(tmp_path, monkeypatch):
    """``cmd_scale`` の外部作用を差し替え、呼び出しの順序を ``calls`` へ記録する。"""
    monkeypatch.chdir(tmp_path)
    for name in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DEVBASE_DOCKER_CONTEXT'):
        monkeypatch.delenv(name, raising=False)
    dc.reset()
    (tmp_path / 'project.yml').write_text(PROJECT_YML)
    override = tmp_path / '.docker-compose.scale.yml'
    calls: list = []

    monkeypatch.setattr(container, 'get_project_name', lambda: 'proj')
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
    monkeypatch.setattr(container, '_check_group_consistency',
                        lambda project=None: calls.append(('group', None)) or True)
    monkeypatch.setattr(container, '_resolve_docker_target', lambda context=None: dc.DockerTarget(
        context=None, source='none', remote=False, home=None, gid=None))

    real_write_scale = container.project_runtime.write_scale

    def write_scale(project_dir, scale):
        calls.append(('write_scale', scale))
        real_write_scale(project_dir, scale)

    monkeypatch.setattr(container.project_runtime, 'write_scale', write_scale)
    monkeypatch.setattr(container, 'ensure_volumes',
                        lambda scale, project: calls.append(('volumes', scale)))
    monkeypatch.setattr(container, 'ensure_network',
                        lambda name='devbase_net': calls.append(('network', name)))

    def build(scale, config, project_name, target):
        calls.append(('generate', scale))
        override.write_text("services:\n  dev-1: {}\n  dev-2: {}\n")
        return override

    monkeypatch.setattr(container, '_build_scaled_override', build)

    real_default_services = container.default_services

    def default_services(compose_file, environ=None):
        calls.append(('default_services', Path(compose_file)))
        return real_default_services(compose_file, environ)

    monkeypatch.setattr(container, 'default_services', default_services)
    monkeypatch.setattr(container, 'wait_for_containers_ready',
                        lambda **k: calls.append(('wait', k)))
    monkeypatch.setattr(container, '_push_bao_token',
                        lambda *a, **k: calls.append(('bao', {'args': a, **k})))
    real_deploy = container._run_deploy_script_for_instances
    monkeypatch.setattr(container, '_run_deploy_script_for_instances',
                        lambda script, indices, config=None, active_profiles=():
                        calls.append(('deploy', list(indices))) or True)

    fake = FakeCompose(NO_PROFILE_SERVICES, calls)
    monkeypatch.setattr(subprocess, 'run', fake)
    return {'calls': calls, 'override': override, 'fake': fake, 'root': tmp_path,
            'real_deploy': real_deploy}


def _runs(calls, *words):
    return [c for name, c in calls if name == 'run' and all(w in c['cmd'] for w in words)]


def _up_calls(calls):
    return [c for name, c in calls if name == 'run' and 'up' in c['cmd']]


# ---------------------------------------------------------------------------
# 起動の子プロセスの環境とコマンド列 (B-1〜B-5 / E-2)
# ---------------------------------------------------------------------------

def test_scale_start_passes_reserved_profile_to_child(scale_harness, monkeypatch):
    """B-1 / B-2: 利用者の COMPOSE_PROFILES は子プロセスで打ち消し、呼び出し側は変えない。"""
    monkeypatch.setenv('COMPOSE_PROFILES', 'web')

    assert container.cmd_scale(2) == 0

    up = _up_calls(scale_harness['calls'])
    assert len(up) == 1
    assert up[0]['env'] is not None
    assert up[0]['env']['COMPOSE_PROFILES'] == docker.NO_PROFILE
    assert os.environ['COMPOSE_PROFILES'] == 'web'


def test_scale_start_names_default_services_of_generated_compose(scale_harness, monkeypatch):
    """B-3: ``up -d --no-recreate`` に ``default_services(<生成物>)`` の全件を並びのまま渡す。"""
    monkeypatch.setattr(container, 'default_services',
                        lambda compose_file, environ=None: ['dev-1', 'dev-2', 'redis'])

    assert container.cmd_scale(2) == 0

    up = _up_calls(scale_harness['calls'])
    assert [c['cmd'] for c in up] == [[
        'docker', 'compose', '-f', str(scale_harness['override']),
        'up', '-d', '--no-recreate', 'dev-1', 'dev-2', 'redis']]
    assert '--profile' not in up[0]['cmd']


def test_scale_start_failure_returns_one_without_exception(scale_harness, caplog):
    """B-4: 起動の非 0 は ``Failed to start new containers`` と 1。例外を外へ出さない。"""
    scale_harness['fake'].up_returncode = 1

    assert container.cmd_scale(2) == 1

    assert 'Failed to start new containers' in caplog.text
    names = [name for name, _ in scale_harness['calls']]
    assert 'wait' not in names and 'bao' not in names and 'deploy' not in names


def test_scale_start_targets_every_service_without_profiles(scale_harness):
    """B-5: プロファイルを持たない生成物では、対象は ``config --services`` の全件。"""
    assert container.cmd_scale(2) == 0

    up = _up_calls(scale_harness['calls'])
    assert up[0]['cmd'][-3:] == ['dev-1', 'dev-2', 'redis']
    assert set(up[0]['cmd'][7:]) == set(NO_PROFILE_SERVICES)


def test_scale_start_leaves_out_profile_services(scale_harness, monkeypatch):
    """E-2: 利用者が COMPOSE_PROFILES でプロファイルを有効にしても、起動の対象に入れない。"""
    monkeypatch.setenv('COMPOSE_PROFILES', 'test')
    scale_harness['fake'].services = WITH_PROFILE_SERVICES

    assert container.cmd_scale(2) == 0

    up = _up_calls(scale_harness['calls'])
    assert 'testdb' not in up[0]['cmd']
    assert up[0]['cmd'][7:] == ['dev-1', 'dev-2', 'redis']
    config = _runs(scale_harness['calls'], 'config', '--services')
    assert config and all(c['env']['COMPOSE_PROFILES'] == docker.NO_PROFILE for c in config)


def test_scale_default_services_failure_is_a_scale_failure(scale_harness, monkeypatch, caplog):
    """既定のサービスの解決の失敗は ``Scale failed`` で 1。起動しない。"""
    def fail(compose_file, environ=None):
        raise DevbaseError('config --services failed')

    monkeypatch.setattr(container, 'default_services', fail)

    assert container.cmd_scale(2) == 1

    assert 'Scale failed: config --services failed' in caplog.text
    assert _up_calls(scale_harness['calls']) == []


# ---------------------------------------------------------------------------
# 正常系の手順の固定 (C-1 / C-2 / E-3)。cmd_scale の段階を分ける前の安全網
# ---------------------------------------------------------------------------

def _step(name, payload):
    if name != 'run':
        return name
    cmd = payload['cmd']
    if 'config' in cmd:
        return 'config'
    if 'up' in cmd:
        return 'up'
    return 'run:' + ' '.join(cmd[2:])


def test_scale_runs_the_steps_in_order(scale_harness):
    """C-1: グループの検査 → write_scale → ボリューム → network → 生成 → 既定のサービス → 起動
    → ready 待ち → bao → ./deploy。"""
    (scale_harness['root'] / 'deploy').write_text('#!/bin/sh\n')

    assert container.cmd_scale(3) == 0

    steps = [_step(name, payload) for name, payload in scale_harness['calls']]
    assert [s for s in steps if s != 'config'] == [
        'group', 'write_scale', 'volumes', 'network', 'generate', 'default_services',
        'up', 'wait', 'bao', 'deploy']
    # 既定のサービスの解決 (config --services) は生成の後、起動の前に行う
    assert steps.index('generate') < steps.index('config') < steps.index('up')
    up = _up_calls(scale_harness['calls'])[0]
    assert up['cmd'][4:7] == ['up', '-d', '--no-recreate']


def test_scale_passes_the_generated_compose_and_new_scale(scale_harness):
    """C-1: 各段に新しい scale と生成物が渡り、project.yml の scale が書き換わる。"""
    assert container.cmd_scale(3) == 0

    calls = dict((name, payload) for name, payload in scale_harness['calls'] if name != 'run')
    override = scale_harness['override']
    assert calls['write_scale'] == 3
    assert calls['volumes'] == 3
    assert calls['network'] == 'devbase_net'
    assert calls['generate'] == 3
    assert calls['default_services'] == override
    assert calls['wait'] == {'container_prefix': 'dev', 'scale': 3,
                             'compose_file': override, 'timeout': 60}
    assert 'scale: 3' in (scale_harness['root'] / 'project.yml').read_text()


def test_scale_hooks_cover_only_the_new_instances(scale_harness):
    """C-2: bao の token と ./deploy は current + 1 から new まで。既存のインスタンスを含めない。"""
    (scale_harness['root'] / 'deploy').write_text('#!/bin/sh\n')

    assert container.cmd_scale(4) == 0

    calls = dict((name, payload) for name, payload in scale_harness['calls'] if name != 'run')
    assert calls['bao'] == {'args': ('proj', 4, 'dev'),
                            'compose_file': scale_harness['override'], 'start': 2}
    assert calls['deploy'] == [2, 3, 4]


def test_scale_skips_deploy_without_the_script(scale_harness):
    """``./deploy`` が無ければ走らせない。bao の token は書く。"""
    assert container.cmd_scale(2) == 0

    names = [name for name, _ in scale_harness['calls']]
    assert 'bao' in names and 'deploy' not in names


def test_scale_continues_after_deploy_failure_and_returns_zero(scale_harness, monkeypatch):
    """現状固定: deploy の 2 が失敗しても 3 を実行し、scale 自体は 0 を返す。"""
    (scale_harness['root'] / 'deploy').write_text('#!/bin/sh\n')
    monkeypatch.setattr(container, '_run_deploy_script_for_instances',
                        scale_harness['real_deploy'])
    deployed_indices = []

    def run(cmd, **kwargs):
        if cmd == ['bash', 'deploy']:
            index = kwargs['env']['DEVBASE_INSTANCE_INDEX']
            deployed_indices.append(index)
            if index == '2':
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 0)
        return scale_harness['fake'](cmd, **kwargs)

    monkeypatch.setattr(subprocess, 'run', run)

    assert container.cmd_scale(3) == 0
    assert deployed_indices == ['2', '3']


def test_scale_never_stops_containers(scale_harness):
    """E-3: scale は停止の段を持たない。down / stop / rm を呼ばない。"""
    (scale_harness['root'] / 'deploy').write_text('#!/bin/sh\n')

    assert container.cmd_scale(3) == 0

    runs = [payload['cmd'] for name, payload in scale_harness['calls'] if name == 'run']
    assert runs
    for cmd in runs:
        assert not {'down', 'stop', 'rm'} & set(cmd)


@pytest.mark.parametrize('new_scale', [0, 1])
def test_scale_rejects_a_scale_not_above_current(scale_harness, new_scale):
    """現状固定: 1 未満と現在以下は 1 を返し、project.yml を書き換えず、何も起動しない。"""
    assert container.cmd_scale(new_scale) == 1

    names = [name for name, _ in scale_harness['calls']]
    assert names == ['group']
    assert (scale_harness['root'] / 'project.yml').read_text() == PROJECT_YML


def test_scale_passes_explicit_project_name(scale_harness, monkeypatch):
    """現状固定: 明示的に指定された project_name が ensure_volumes, _build_scaled_override, _push_bao_token に渡る。"""
    captured: dict = {}

    orig_volumes = container.ensure_volumes

    def fake_ensure_volumes(scale, project):
        captured['volumes_project'] = project
        return orig_volumes(scale, project)

    orig_build = container._build_scaled_override

    def fake_build(scale, config, project_name, target):
        captured['build_project'] = project_name
        return orig_build(scale, config, project_name, target)

    orig_push_bao = container._push_bao_token

    def fake_push_bao(project_name, *args, **kwargs):
        captured['bao_project'] = project_name
        return orig_push_bao(project_name, *args, **kwargs)

    monkeypatch.setattr(container, 'ensure_volumes', fake_ensure_volumes)
    monkeypatch.setattr(container, '_build_scaled_override', fake_build)
    monkeypatch.setattr(container, '_push_bao_token', fake_push_bao)

    assert container.cmd_scale(2, project_name='custom-proj') == 0

    assert captured['volumes_project'] == 'custom-proj'
    assert captured['build_project'] == 'custom-proj'
    assert captured['bao_project'] == 'custom-proj'



# ---------------------------------------------------------------------------
# 段階の関数の契約 (D-3 / D-4。設計の決定 8)
# ---------------------------------------------------------------------------

def _records(caplog):
    return [(r.levelname, r.getMessage()) for r in caplog.records]


def test_check_scale_request_rejects_below_one(caplog):
    """1 未満は False。error を 1 行だけ出す。"""
    caplog.set_level('INFO', logger=container.logger.name)

    assert container._check_scale_request(0, 1) is False
    assert _records(caplog) == [('ERROR', 'Scale must be at least 1')]


@pytest.mark.parametrize('new_scale', [1, 2])
def test_check_scale_request_rejects_not_above_current(caplog, new_scale):
    """現在以下は False。warning 1 行と案内の info 1 行を出す。"""
    caplog.set_level('INFO', logger=container.logger.name)

    assert container._check_scale_request(new_scale, 2) is False
    assert _records(caplog) == [
        ('WARNING', f'New scale ({new_scale}) is not greater than current scale (2)'),
        ('INFO', "To scale down, use 'devbase container down' first, "
                 "then 'devbase container up' with desired scale"),
    ]


def test_check_scale_request_accepts_above_current(caplog):
    """現在を上回れば True。何も出さない。"""
    caplog.set_level('INFO', logger=container.logger.name)

    assert container._check_scale_request(3, 2) is True
    assert _records(caplog) == []


def _run_pipeline(new_scale=3, current_scale=1):
    config = container.project_runtime.current_project_config()
    target = dc.DockerTarget(context=None, source='none', remote=False, home=None, gid=None)
    return container._run_scale_pipeline('proj', new_scale, current_scale, config, target, 'dev')


def test_run_scale_pipeline_runs_stages_one_to_five_and_returns_the_generated_compose(scale_harness):
    """[1/5]〜[5/5] を順に通し、生成物のパスを返す。後処理 (bao / ./deploy) は呼ばない。"""
    (scale_harness['root'] / 'deploy').write_text('#!/bin/sh\n')

    assert _run_pipeline() == scale_harness['override']

    names = [name for name, _ in scale_harness['calls'] if name != 'run']
    assert names == ['write_scale', 'volumes', 'network', 'generate', 'default_services', 'wait']
    assert len(_up_calls(scale_harness['calls'])) == 1


def test_run_scale_pipeline_returns_none_when_the_start_fails(scale_harness, caplog):
    """起動が 0 以外なら Failed to start new containers を出して None。ready 待ちへ進まない。"""
    scale_harness['fake'].up_returncode = 1
    caplog.set_level('INFO', logger=container.logger.name)

    assert _run_pipeline() is None

    assert ('ERROR', 'Failed to start new containers') in _records(caplog)
    assert 'wait' not in [name for name, _ in scale_harness['calls']]


def test_run_scale_pipeline_propagates_generation_failure(scale_harness, monkeypatch):
    """構成生成の失敗は DevbaseError のまま伝播する (Scale failed: は cmd_scale が出す)。"""
    def fail(*a, **k):
        raise DevbaseError('boom')

    monkeypatch.setattr(container, '_build_scaled_override', fail)

    with pytest.raises(DevbaseError, match='boom'):
        _run_pipeline()
