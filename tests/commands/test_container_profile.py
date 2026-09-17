"""プロファイルの解決と操作 (PLAN58)

実 docker には触れない。``subprocess.run`` を偽の Compose に差し替え、組み立てた
コマンド列・終了コード・出力を検査する。dev の Container ID が変わらないことは実
コンテナが要るため手動確認で見る (要求仕様「検証手段」)。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from devbase.commands import container
from devbase.errors import DevbaseError
from devbase.utils.docker import NO_PROFILE

COMPOSE = Path('.docker-compose.scale.yml')
_REAL_RUN = subprocess.run


class FakeCompose:
    """``docker compose`` の問い合わせに答え、呼び出しを記録する偽物。

    ``profiles`` はプロファイル名 → サービス名 (宣言順)。``defaults`` は既定の
    サービス。``running`` は ``ps`` が ``running`` と答えるサービス名。
    ``fail`` は subcommand 名 → 終了コード (``ps`` / ``up`` / ``stop`` / ``rm``)。
    """

    def __init__(self, profiles=None, defaults=('dev-1',), running=(), fail=None):
        self.profiles = dict(profiles or {})
        self.defaults = list(defaults)
        self.running = set(running)
        self.fail = dict(fail or {})
        self.calls: list[dict] = []

    @property
    def compose_calls(self):
        return [c for c in self.calls if c['cmd'][:2] == ['docker', 'compose']]

    def _args(self, cmd):
        """``-f <file>`` と ``--profile X`` を取り除いた引数と、指定されたプロファイル"""
        rest, profiles, i = [], [], 2
        while i < len(cmd):
            if cmd[i] in ('-f', '--profile'):
                if cmd[i] == '--profile':
                    profiles.append(cmd[i + 1])
                i += 2
                continue
            rest.append(cmd[i])
            i += 1
        return rest, profiles

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append({'cmd': cmd, **kwargs})
        if cmd[:2] != ['docker', 'compose']:
            return _REAL_RUN(cmd, **kwargs)     # フック (bash ./deploy) は実際に走らせる
        args, profiles = self._args(cmd)
        sub = args[0]
        if sub in self.fail:
            return subprocess.CompletedProcess(cmd, self.fail[sub], '', 'boom')
        out = ''
        if args[:2] == ['config', '--profiles']:
            out = ''.join(f'{name}\n' for name in sorted(self.profiles))
        elif args[:2] == ['config', '--services']:
            names = list(self.defaults)
            for p in profiles:
                names += self.profiles.get(p, [])
            out = ''.join(f'{name}\n' for name in sorted(set(names)))
        elif sub == 'ps':
            out = ''.join(json.dumps({'Service': s, 'State': 'running'}) + '\n'
                          for s in sorted(self.running))
        return subprocess.CompletedProcess(cmd, 0, out, '')


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(container, '_prepare_compose', lambda context=None: None)
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
    return tmp_path


def use(monkeypatch, fake: FakeCompose) -> FakeCompose:
    monkeypatch.setattr(container.subprocess, 'run', fake)
    return fake


# ---------------------------------------------------------------------------
# Task 2: プロファイルの解決 (決定 1)
# ---------------------------------------------------------------------------

def test_default_services_come_from_config_with_reserved_profile(project, monkeypatch):
    fake = use(monkeypatch, FakeCompose(profiles={'test': ['app']},
                                        defaults=['dev-1', 'dev-2', 'redis']))
    monkeypatch.setenv('COMPOSE_PROFILES', 'test')

    assert container.default_services(COMPOSE) == ['dev-1', 'dev-2', 'redis']

    call = fake.calls[0]
    assert call['cmd'] == ['docker', 'compose', '-f', str(COMPOSE), 'config', '--services']
    assert call['env']['COMPOSE_PROFILES'] == NO_PROFILE


def test_profile_services_subtract_default_services(project, monkeypatch):
    fake = use(monkeypatch, FakeCompose(
        profiles={'test': ['app', 'mysql'], 'cache': ['valkey']},
        defaults=['dev-1', 'redis']))

    assert container.profile_services(COMPOSE) == {
        'cache': ['valkey'],
        'test': ['app', 'mysql'],
    }
    # --profile は subcommand より前に置く (docker_compose は -f の後にそのまま並べる)
    profiled = [c['cmd'] for c in fake.calls if '--profile' in c['cmd']]
    assert ['docker', 'compose', '-f', str(COMPOSE), '--profile', 'test',
            'config', '--services'] in profiled
    assert all(c['env']['COMPOSE_PROFILES'] == NO_PROFILE for c in fake.calls)


def test_profile_services_use_names_expanded_by_compose(project, monkeypatch):
    # profiles: ["${TEST_PROFILE:-test}"] の展開は Compose が行う。devbase は
    # config --profiles が返した名前をそのまま使う
    use(monkeypatch, FakeCompose(profiles={'demo': ['app']}))

    assert list(container.profile_services(COMPOSE)) == ['demo']


def test_profile_services_empty_without_profiles(project, monkeypatch):
    use(monkeypatch, FakeCompose())

    assert container.profile_services(COMPOSE) == {}


def test_resolution_failure_raises(project, monkeypatch):
    use(monkeypatch, FakeCompose(fail={'config': 15}))

    with pytest.raises(DevbaseError):
        container.profile_services(COMPOSE)
    with pytest.raises(DevbaseError):
        container.default_services(COMPOSE)


# ---------------------------------------------------------------------------
# Task 5: 起動・停止・一覧 (F1〜F3、決定 2・3・5)
# ---------------------------------------------------------------------------

def write_generated(root: Path, dev: str = 'dev', scale: int = 1, extra=('app', 'mysql')):
    lines = ['services:']
    lines += [f'  {name}:\n    image: alpine:3\n    profiles: [test]' for name in extra]
    lines += [f'  {dev}-{i}:\n    image: alpine:3' for i in range(1, scale + 1)]
    (root / COMPOSE).write_text('\n'.join(lines) + '\n')


TEST_PROFILES = {'test': ['app', 'mysql'], 'cache': ['valkey']}


def creating_calls(fake: FakeCompose):
    return [c['cmd'] for c in fake.compose_calls
            if {'up', 'stop', 'rm', 'down', 'create', 'start'} & set(c['cmd'])]


@pytest.mark.parametrize('op', ['up', 'down', 'list'])
def test_missing_generated_compose_stops_before_compose(project, monkeypatch, caplog, op):
    fake = use(monkeypatch, FakeCompose(profiles=TEST_PROFILES))

    if op == 'list':
        rc = container.cmd_profile_list()
    else:
        rc = getattr(container, f'cmd_profile_{op}')('test')

    assert rc == 1
    assert fake.calls == []
    assert 'devbase up' in caplog.text


@pytest.mark.parametrize('op', ['up', 'down'])
def test_unknown_profile_lists_known_names(project, monkeypatch, caplog, op):
    write_generated(project)
    fake = use(monkeypatch, FakeCompose(profiles=TEST_PROFILES))

    rc = getattr(container, f'cmd_profile_{op}')('nope')

    assert rc == 1
    assert creating_calls(fake) == []
    assert 'nope' in caplog.text
    assert 'cache' in caplog.text and 'test' in caplog.text


def test_profile_up_names_every_service_with_no_deps(project, monkeypatch, caplog):
    write_generated(project)
    fake = use(monkeypatch, FakeCompose(profiles=TEST_PROFILES, defaults=['dev-1']))
    monkeypatch.setenv('COMPOSE_PROFILES', 'test')
    caplog.set_level('INFO')

    assert container.cmd_profile_up('test') == 0

    ups = creating_calls(fake)
    assert ups == [['docker', 'compose', '-f', str(COMPOSE), '--profile', 'test',
                    'up', '-d', '--no-deps', 'app', 'mysql']]
    assert 'dev-1' not in ups[0]
    assert all(c['env']['COMPOSE_PROFILES'] == NO_PROFILE for c in fake.compose_calls)
    assert "app, mysql" in caplog.text


def test_profile_up_returns_compose_exit_code(project, monkeypatch):
    # デーモンへ接続できないとき Compose は 1 で落ちる。その値をそのまま返す
    write_generated(project)
    use(monkeypatch, FakeCompose(profiles=TEST_PROFILES, fail={'up': 1}))
    (project / 'deploy').write_text('#!/bin/bash\ntouch ran\n')

    assert container.cmd_profile_up('test') == 1
    assert not (project / 'ran').exists()


def write_project_yml(root: Path, scale: int = 1):
    (root / 'project.yml').write_text(
        f"version: 1\nscale: {scale}\nrepos:\n  - owner: volareinc\n    repo: carmo\n")


DEPLOY_DUMP = ('#!/bin/bash\n'
               'echo "$DEVBASE_INSTANCE_INDEX $DEVBASE_ACTIVE_PROFILES" >> deploy.txt\n')


def test_profile_up_runs_deploy_for_running_instances_of_generated_compose(project, monkeypatch):
    # up を scale 2 で通した後に project.yml を 1 へ書き換えても、稼働中の 2 台へ走る
    write_generated(project, scale=2)
    write_project_yml(project, scale=1)
    (project / 'deploy').write_text(DEPLOY_DUMP)
    (project / 'pre-up').write_text('#!/bin/bash\ntouch pre-up-ran\n')
    use(monkeypatch, FakeCompose(profiles=TEST_PROFILES))

    assert container.cmd_profile_up('test') == 0

    assert (project / 'deploy.txt').read_text().splitlines() == ['1 test', '2 test']
    assert not (project / 'pre-up-ran').exists()


def test_profile_up_follows_dev_service_name(project, monkeypatch):
    write_generated(project, dev='workspace', scale=2)
    write_project_yml(project, scale=2)
    (project / 'deploy').write_text(DEPLOY_DUMP)
    use(monkeypatch, FakeCompose(profiles=TEST_PROFILES, defaults=['workspace-1', 'workspace-2']))
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'workspace')

    assert container.cmd_profile_up('test') == 0

    assert (project / 'deploy.txt').read_text().splitlines() == ['1 test', '2 test']


def test_profile_up_fails_when_deploy_fails(project, monkeypatch):
    write_generated(project)
    write_project_yml(project)
    (project / 'deploy').write_text('#!/bin/bash\nexit 3\n')
    use(monkeypatch, FakeCompose(profiles=TEST_PROFILES))

    assert container.cmd_profile_up('test') != 0


def test_profile_up_fails_when_project_config_raises_devbase_error(project, monkeypatch):
    write_generated(project)
    (project / 'deploy').write_text('#!/bin/bash\ntouch ran\n')
    fake = use(monkeypatch, FakeCompose(profiles=TEST_PROFILES))

    def failing_config():
        raise DevbaseError("broken config")

    monkeypatch.setattr(container.project_runtime, 'current_project_config', failing_config)

    assert container.cmd_profile_up('test') == 1
    assert creating_calls(fake) == []
    assert not (project / 'ran').exists()


def test_profile_down_stops_then_removes_without_volumes(project, monkeypatch, caplog):
    write_generated(project)
    fake = use(monkeypatch, FakeCompose(profiles=TEST_PROFILES))
    (project / 'deploy').write_text('#!/bin/bash\ntouch ran\n')
    caplog.set_level('INFO')

    assert container.cmd_profile_down('test') == 0

    base = ['docker', 'compose', '-f', str(COMPOSE), '--profile', 'test']
    assert creating_calls(fake) == [base + ['stop', 'app', 'mysql'],
                                    base + ['rm', '-f', 'app', 'mysql']]
    flat = [arg for cmd in creating_calls(fake) for arg in cmd]
    assert 'down' not in flat and '-v' not in flat and '--volumes' not in flat
    assert not (project / 'ran').exists()          # 停止はフックを呼ばない
    assert "app, mysql" in caplog.text


def test_profile_down_skips_rm_when_stop_fails(project, monkeypatch):
    write_generated(project)
    fake = use(monkeypatch, FakeCompose(profiles=TEST_PROFILES, fail={'stop': 1}))

    assert container.cmd_profile_down('test') == 1

    assert [cmd[6] for cmd in creating_calls(fake)] == ['stop']


def test_profile_down_fails_when_rm_fails(project, monkeypatch):
    write_generated(project)
    use(monkeypatch, FakeCompose(profiles=TEST_PROFILES, fail={'rm': 1}))

    assert container.cmd_profile_down('test') == 1


@pytest.mark.parametrize('running, expected', [
    ({'app', 'mysql', 'dev-1'}, '2/2 running'),
    ({'app', 'dev-1'}, '1/2 partial'),
    ({'dev-1'}, '0/2 stopped'),
])
def test_profile_list_shows_services_and_running_state(project, monkeypatch, capsys,
                                                       running, expected):
    write_generated(project)
    use(monkeypatch, FakeCompose(profiles=TEST_PROFILES, running=running))

    assert container.cmd_profile_list() == 0

    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ['PROFILE', 'SERVICES', 'RUNNING']
    rows = {line.split()[0]: line for line in out[1:]}
    assert 'app,mysql' in rows['test'] and expected in rows['test']
    assert 'valkey' in rows['cache'] and '0/1 stopped' in rows['cache']


def test_profile_list_asks_ps_for_all_profiles(project, monkeypatch):
    """非アクティブなプロファイルのサービスを ps に出さない版があるため ``--profile '*'`` を付ける"""
    write_generated(project)
    fake = use(monkeypatch, FakeCompose(profiles=TEST_PROFILES, running={'app'}))

    assert container.cmd_profile_list() == 0

    ps_calls = [c['cmd'] for c in fake.compose_calls if 'ps' in c['cmd']]
    assert len(ps_calls) == 1
    cmd = ps_calls[0]
    assert cmd[cmd.index('ps') - 2:cmd.index('ps')] == ['--profile', '*']


def test_profile_list_marks_running_unknown_without_daemon(project, monkeypatch, capsys):
    write_generated(project)
    use(monkeypatch, FakeCompose(profiles=TEST_PROFILES, fail={'ps': 1}))

    assert container.cmd_profile_list() == 0

    out = capsys.readouterr().out.splitlines()
    rows = {line.split()[0]: line for line in out[1:]}
    assert 'app,mysql' in rows['test'] and rows['test'].split()[-1] == '不明'


@pytest.mark.parametrize('ps_failure', ['invalid_json', 'os_error'])
def test_profile_list_marks_running_unknown_when_ps_fails(project, monkeypatch, capsys,
                                                         ps_failure):
    write_generated(project, extra=('app',))
    fake = FakeCompose(profiles={'test': ['app']})

    def failing_ps(cmd, **kwargs):
        if 'ps' in cmd:
            if ps_failure == 'os_error':
                raise OSError('cannot execute docker compose ps')
            return subprocess.CompletedProcess(cmd, 0, '{broken json', '')
        return fake(cmd, **kwargs)

    monkeypatch.setattr(container.subprocess, 'run', failing_ps)

    assert container.cmd_profile_list() == 0

    rows = {line.split()[0]: line.split()
            for line in capsys.readouterr().out.splitlines()[1:]}
    assert rows['test'][1] == 'app'
    assert rows['test'][-1] == '不明'


def test_profile_list_accepts_json_array_from_older_compose(project, monkeypatch, capsys):
    write_generated(project)
    fake = FakeCompose(profiles={'test': ['app']})
    use(monkeypatch, fake)
    original = fake.__call__

    def array_ps(cmd, **kwargs):
        result = original(cmd, **kwargs)
        if 'ps' in cmd:
            result.stdout = json.dumps([{'Service': 'app', 'State': 'running'}])
        return result

    monkeypatch.setattr(container.subprocess, 'run', array_ps)

    assert container.cmd_profile_list() == 0
    assert '1/1 running' in capsys.readouterr().out


def test_profile_list_without_profiles_prints_header_only(project, monkeypatch, capsys):
    write_generated(project, extra=())
    use(monkeypatch, FakeCompose())

    assert container.cmd_profile_list() == 0

    assert capsys.readouterr().out.split() == ['PROFILE', 'SERVICES', 'RUNNING']
