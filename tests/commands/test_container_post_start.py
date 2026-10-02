"""起動の後の処理を ``up`` / ``scale`` / ``project post-start`` で共有する (#224・#371)。

- ``scale`` は増やしたインスタンスへ、``up`` と同じ並び (不足リポジトリの報告 → ``./deploy`` →
  token の配布 → 窓のタイトルの設定) を行う
- 起動できなかったインスタンスがあっても、起動できたインスタンス (``scale`` は増やしたもののうち
  起動できたもの) へ後処理を行い、名前・理由・補う手順を出して 1 で終える
- ``devbase project post-start`` は、動いていて entrypoint の完了を確かめられたインスタンスへ、
  ``./deploy`` を除く 3 つをやり直す

実 docker と実 ``DEVBASE_ROOT`` には触れない。``DEVBASE_ROOT`` は tmp へ向け、backend は
偽の ``openbao`` の置き場に差し替える。docker は ``subprocess.run`` を差し替えて答える。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devbase.commands import container
from devbase.editor import opener, window_title
from devbase.env import container_token
from devbase.utils import docker_context as dc
from devbase.utils.docker import ContainerStartupError, StartupFailure

LOCAL = dc.DockerTarget(context=None, source='none', remote=False, home=None, gid=None)
TOKEN = 'hvs.SECRET-TOKEN-VALUE'
POST_START_LINE = 'devbase project post-start proj'


def _project_yml(scale: int) -> str:
    return (f"version: 1\nscale: {scale}\nrepos:\n"
            "  - owner: example-org\n    repo: myapp\n"
            "  - owner: example-org\n    repo: lib\n")


class FakeStore:
    class _Backend:
        def issue_token(self):
            return TOKEN

    def backend_for(self, ref):
        return self._Backend()


class FakeDocker:
    """``subprocess.run`` の代わり。docker への呼び出しと ``./deploy`` を記録して答える。"""

    def __init__(self, events: list):
        self.events = events
        self.work: dict = {}            # 番号 -> /work の一覧 (無ければ揃っている)
        self.running: list = []         # docker ps の (番号, コンテナ名)
        self.ps_returncode = 0
        self.not_ready: set = set()     # 完了の印が無いコンテナ名
        self.services = ['dev-1']

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.events.append(('run', cmd))
        if cmd == ['bash', 'deploy']:
            self.events.append(('deploy', int(kwargs['env']['DEVBASE_INSTANCE_INDEX'])))
            return subprocess.CompletedProcess(cmd, 0, '', '')
        if 'config' in cmd and '--services' in cmd:
            return subprocess.CompletedProcess(cmd, 0, '\n'.join(self.services) + '\n', '')
        if cmd[:2] == ['docker', 'ps']:
            out = ''.join(f'proj-dev-{i}\tdev-{i}\n' for i, _name in self.running)
            return subprocess.CompletedProcess(cmd, self.ps_returncode, out, 'daemon down')
        if cmd[:2] == ['docker', 'exec'] and cmd[-3:] == ['test', '-f', '/tmp/entrypoint-ready']:
            self.events.append(('ready?', cmd[2]))
            return subprocess.CompletedProcess(cmd, 1 if cmd[2] in self.not_ready else 0, '', '')
        if 'ls' in cmd and '/work' in cmd:
            service = cmd[cmd.index('ls') - 1]
            index = int(service.rsplit('-', 1)[1])
            self.events.append(('repos', index))
            listing = self.work.get(index, ['myapp', 'lib'])
            return subprocess.CompletedProcess(cmd, 0, '\n'.join(listing) + '\n', '')
        return subprocess.CompletedProcess(cmd, 0, '', '')


@pytest.fixture
def harness(tmp_path, monkeypatch, caplog):
    caplog.set_level('INFO')
    root = tmp_path / 'root'
    project = root / 'projects' / 'proj'
    project.mkdir(parents=True)
    monkeypatch.setenv('DEVBASE_ROOT', str(root))
    monkeypatch.chdir(project)
    for name in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DEVBASE_DOCKER_CONTEXT',
                 'DEVBASE_WINDOW_TITLE', 'DEVBASE_OPEN_INDEX', 'DEVBASE_OPEN_EDITOR'):
        monkeypatch.delenv(name, raising=False)
    dc.reset()

    events: list = []
    fake = FakeDocker(events)
    monkeypatch.setattr(subprocess, 'run', fake)

    monkeypatch.setattr(container, 'get_project_name', lambda: 'proj')
    monkeypatch.setattr(container, 'get_dev_service_name', lambda: 'dev')
    monkeypatch.setattr(container, '_require_group_declaration', lambda project=None: True)
    monkeypatch.setattr(container, '_resolve_docker_target', lambda context=None: LOCAL)
    monkeypatch.setattr(container, '_apply_context', lambda context=None: None)
    for name in ('_ensure_env_files', '_run_pre_up_hook', '_ensure_images'):
        monkeypatch.setattr(container, name, lambda *a, **k: True)
    monkeypatch.setattr(container, '_auto_snapshot', lambda *a, **k: None)
    monkeypatch.setattr(container, '_inject_secrets', lambda *, required: None)
    monkeypatch.setattr(container, 'ensure_volumes', lambda *a, **k: None)
    monkeypatch.setattr(container, 'ensure_network', lambda *a, **k: None)
    monkeypatch.setattr(container, 'docker_compose_down',
                        lambda compose_file=None: events.append(('down', None)))
    monkeypatch.setattr(container, 'docker_compose_up',
                        lambda **k: events.append(('up', None)))
    monkeypatch.setattr(container, 'default_services', lambda *a, **k: ['dev-1'])
    monkeypatch.setattr(container, '_openbao_store', lambda: FakeStore())

    def build(scale, config, project_name, target):
        container._SCALE_COMPOSE_FILE.write_text('services: {}\n')
        return container._SCALE_COMPOSE_FILE

    monkeypatch.setattr(container, '_build_scaled_override', build)

    wait = {'error': None}

    def fake_wait(**kwargs):
        events.append(('wait', kwargs['scale']))
        if wait['error'] is not None:
            raise wait['error']
        return True

    monkeypatch.setattr(container, 'wait_for_containers_ready', fake_wait)
    monkeypatch.setattr(opener, '_query_container_name',
                        lambda dev, index, compose_file=None, runner=None: f'real-{dev}-{index}')
    monkeypatch.setattr(container_token, 'push',
                        lambda names, token, runner=None:
                        events.append(('token', list(names), token)) or list(names))
    monkeypatch.setattr(window_title, 'apply_to_container',
                        lambda name, template=None, **k: events.append(('title', name, template)))
    monkeypatch.setattr(container, '_open_editor_at',
                        lambda project_name, index, config, **k:
                        events.append(('editor', index)) or 'launch')

    def setup(scale=2, deploy=False):
        (project / 'project.yml').write_text(_project_yml(scale))
        if deploy:
            (project / 'deploy').write_text('#!/bin/sh\n')

    def fail(ready, *failures):
        wait['error'] = ContainerStartupError(
            ready, [StartupFailure(i, f'dev-{i}', reason, logs) for i, reason, logs in failures])

    return {'events': events, 'fake': fake, 'setup': setup, 'fail': fail,
            'project': project}


def _hooks(events):
    """インスタンスごとの処理の呼び出しを (処理, 番号) で並べる。"""
    out = []
    for event in events:
        kind = event[0]
        if kind in ('repos', 'deploy'):
            out.append((kind, event[1]))
        elif kind == 'token':
            out.extend(('token', int(name.rsplit('-', 1)[1])) for name in event[1])
        elif kind == 'title':
            out.append(('title', int(event[1].rsplit('-', 1)[1])))
    return out


def _indices(events, kind):
    return [i for k, i in _hooks(events) if k == kind]


def _messages(caplog):
    return [r.getMessage() for r in caplog.records]


ORDER = ['repos', 'deploy', 'token', 'title']


# ---------------------------------------------------------------------------
# #224: scale で増やしたインスタンスの後処理
# ---------------------------------------------------------------------------

def test_scale_sets_window_titles_on_the_added_instances(harness):
    """受け入れ条件 1・2: dev-2 と dev-3 に実コンテナ名始まりのタイトル。dev-1 には何もしない"""
    harness['setup'](scale=1, deploy=True)

    assert container.cmd_scale(3) == 0

    titles = [(name, template) for kind, name, template in
              (e for e in harness['events'] if e[0] == 'title')]
    assert [name for name, _t in titles] == ['real-dev-2', 'real-dev-3']
    for name, template in titles:
        assert window_title.render(template, name).startswith(name)
    assert all(i != 1 for _k, i in _hooks(harness['events']))


def test_scale_reports_missing_repos_like_up(harness, caplog):
    """受け入れ条件 3: 増やした dev-3 の /work に無いリポジトリを up と同じ文言で出す"""
    harness['setup'](scale=1)
    harness['fake'].work[3] = ['myapp']

    assert container.cmd_scale(3) == 0

    messages = _messages(caplog)
    assert 'Repositories missing in /work of dev-3 (clone may have failed):' in messages
    assert any(m.strip().startswith('- lib (') for m in messages)
    assert not any('of dev-2' in m or 'of dev-1' in m for m in messages)


def test_scale_without_missing_repos_says_nothing(harness, caplog):
    """受け入れ条件 4"""
    harness['setup'](scale=1)

    assert container.cmd_scale(3) == 0

    assert not any('Repositories missing' in m for m in _messages(caplog))
    assert _indices(harness['events'], 'repos') == [2, 3]


def test_scale_window_title_off_sets_no_title(harness, monkeypatch):
    """受け入れ条件 5"""
    harness['setup'](scale=1)
    monkeypatch.setenv('DEVBASE_WINDOW_TITLE', 'off')

    assert container.cmd_scale(3) == 0

    assert _indices(harness['events'], 'title') == []


def test_scale_runs_the_hooks_in_the_up_order(harness):
    """受け入れ条件 6・7・I5: scale (1→3) と up (台数 2) は同じ処理を同じ順で行う"""
    harness['setup'](scale=1, deploy=True)
    assert container.cmd_scale(3) == 0
    scale_hooks = _hooks(harness['events'])
    assert scale_hooks == [(k, i) for k in ORDER for i in (2, 3)]

    harness['events'].clear()
    harness['setup'](scale=2, deploy=True)
    assert container.cmd_up() == 0
    up_hooks = _hooks(harness['events'])
    assert ([k for k, i in up_hooks if i == 2] == [k for k, i in scale_hooks if i == 2]
            == ORDER)


def test_scale_success_output_is_unchanged(harness, caplog):
    """受け入れ条件 8"""
    harness['setup'](scale=1)

    assert container.cmd_scale(3) == 0

    messages = _messages(caplog)
    assert '=== Scale completed successfully ===' in messages
    assert '  devbase login 2' in messages and '  devbase login 3' in messages
    assert not any(e[0] == 'editor' for e in harness['events'])


# ---------------------------------------------------------------------------
# #371: 起動できなかったインスタンスがあるとき
# ---------------------------------------------------------------------------

def test_up_all_ready_keeps_the_order_and_opens_the_editor(harness, caplog):
    """受け入れ条件 9"""
    harness['setup'](scale=2, deploy=True)

    assert container.cmd_up(open_editor=True) == 0

    assert _hooks(harness['events']) == [(k, i) for k in ORDER for i in (1, 2)]
    assert [e for e in harness['events'] if e[0] == 'editor'] == [('editor', 1)]
    assert '=== Deploy completed successfully ===' in _messages(caplog)


def test_up_with_a_failed_instance_post_starts_the_ready_one(harness, caplog):
    """受け入れ条件 A1・10・11・14: dev-1 に 4 つとも行い、dev-2 には行わず、1 で終える"""
    harness['setup'](scale=2, deploy=True)
    harness['fail']([1], (2, 'exited', 'ln: Already exists'))

    assert container.cmd_up() == 1

    events = harness['events']
    assert _hooks(events) == [(k, 1) for k in ORDER]
    assert [e for e in events if e[0] == 'deploy'] == [('deploy', 1)]
    text = caplog.text
    assert 'Deploy failed' in text
    assert '  - dev-2: exited unexpectedly' in text and 'ln: Already exists' in text
    assert '=== Deploy completed successfully ===' not in text


def test_up_failure_report_names_every_failed_instance(harness, caplog):
    """受け入れ条件 12・13"""
    harness['setup'](scale=3)
    harness['fail']([1], (2, 'exited', ''), (3, 'timeout', ''))

    assert container.cmd_up() == 1

    assert 'dev-2: exited unexpectedly' in caplog.text
    assert 'dev-3: timeout' in caplog.text


def test_failure_output_gives_the_post_start_command_on_its_own_line(harness, caplog):
    """受け入れ条件 15・B7: コマンドだけの行と、docker start では行われない旨の文"""
    harness['setup'](scale=2)
    harness['fail']([1], (2, 'exited', ''))

    assert container.cmd_up() == 1

    messages = [m.strip() for m in _messages(caplog)]
    assert POST_START_LINE in messages
    assert any('docker start' in m and '行われません' in m for m in messages)
    # 報告は出力の最後に置く (決定 12)
    last_hook = max(i for i, r in enumerate(caplog.records) if 'bao の token' in r.getMessage())
    assert messages.index(POST_START_LINE) > last_hook


@pytest.mark.parametrize('command', ['up', 'scale'])
def test_failure_never_stops_or_removes_containers(harness, command):
    """受け入れ条件 16・I8: 起動の待ちより後に down / stop / rm を呼ばない"""
    harness['setup'](scale=1 if command == 'scale' else 2)
    harness['fail']([1], (2, 'exited', ''))

    assert (container.cmd_up() if command == 'up' else container.cmd_scale(2)) == 1

    events = harness['events']
    after = events[next(i for i, e in enumerate(events) if e[0] == 'wait'):]
    assert not any(e[0] in ('down', 'up') for e in after)
    assert not any(e[0] == 'run' and {'down', 'stop', 'rm', 'start'} & set(e[1])
                   for e in after)


def test_scale_with_a_failed_added_instance_post_starts_the_other(harness, caplog):
    """受け入れ条件 17・A5: dev-2 にだけ行い、dev-1 と dev-3 には行わない"""
    harness['setup'](scale=1, deploy=True)
    harness['fail']([1, 2], (3, 'exited', ''))

    assert container.cmd_scale(3) == 1

    assert _hooks(harness['events']) == [(k, 2) for k in ORDER]
    text = caplog.text
    assert 'Scale failed' in text and 'dev-3' in text
    assert '=== Scale completed successfully ===' not in text


def test_scale_with_a_failed_existing_instance_fails(harness, caplog):
    """受け入れ条件 18: 既存の dev-1 が落ちても 1 で終わり、名前が出る。増やした番号へは行う"""
    harness['setup'](scale=1)
    harness['fail']([2, 3], (1, 'exited', ''))

    assert container.cmd_scale(3) == 1

    assert 'dev-1: exited unexpectedly' in caplog.text
    assert sorted({i for _k, i in _hooks(harness["events"])}) == [2, 3]


def test_up_with_no_ready_instance_does_nothing(harness, caplog):
    """受け入れ条件 A4・I6: 処理もエディタも行わず、token も発行しない"""
    harness['setup'](scale=2, deploy=True)
    harness['fail']([], (1, 'exited', ''), (2, 'exited', ''))

    assert container.cmd_up(open_editor=True) == 1

    assert _hooks(harness['events']) == []
    assert not any(e[0] == 'editor' for e in harness['events'])
    first = next(m for m in _messages(caplog) if m.startswith('起動できなかったインスタンス'))
    assert '行いません' in first and '続けます' not in first


def test_scale_with_only_the_added_instance_failed_does_nothing(harness, caplog):
    """受け入れ条件 A4・I6: 既存の dev-1 は起動できていても処理先に入れない"""
    harness['setup'](scale=1, deploy=True)
    harness['fail']([1], (2, 'exited', ''))

    assert container.cmd_scale(2) == 1

    assert _hooks(harness['events']) == []
    first = next(m for m in _messages(caplog) if m.startswith('起動できなかったインスタンス'))
    assert 'dev-1' not in first and '行いません' in first


@pytest.mark.parametrize('command, scale, new_scale, ready, failed, target', [
    ('up', 2, None, [1], [2], 'dev-1'),
    ('scale', 1, 3, [1, 2], [3], 'dev-2'),
])
def test_first_warning_names_only_the_targets(harness, caplog, command, scale, new_scale,
                                              ready, failed, target):
    """出力の契約: 1 行目の処理先の名前は後処理の対象と一致する"""
    harness['setup'](scale=scale)
    harness['fail'](ready, *[(i, 'exited', '') for i in failed])

    assert (container.cmd_up() if command == 'up' else container.cmd_scale(new_scale)) == 1

    first = next(m for m in _messages(caplog) if m.startswith('起動できなかったインスタンス'))
    head, _sep, tail = first.partition('。')
    assert [f'dev-{i}' for i in failed] == [n.strip() for n in head.split(':')[1].split(',')]
    assert tail.startswith(f'{target} へ')


def test_up_does_not_open_the_editor_on_a_failed_index(harness, caplog):
    """受け入れ条件 A3・I9: 開く番号が落ちたら開かず、ほかの番号へ替えない"""
    harness['setup'](scale=2)
    harness['fail']([1], (2, 'exited', ''))

    assert container.cmd_up(open_editor=True, open_index=2) == 1

    assert not any(e[0] == 'editor' for e in harness['events'])
    assert any('dev-2' in r.getMessage() for r in caplog.records
               if r.levelname == 'WARNING' and 'エディタ' in r.getMessage())


def test_up_opens_the_editor_when_its_index_is_ready(harness):
    """受け入れ条件 A3: 開く番号が起動できていれば、ほかが落ちていても開く"""
    harness['setup'](scale=2)
    harness['fail']([1], (2, 'exited', ''))

    assert container.cmd_up(open_editor=True) == 1

    assert [e for e in harness['events'] if e[0] == 'editor'] == [('editor', 1)]


def test_timeout_post_starts_the_instances_ready_so_far(harness):
    """受け入れ条件 A6"""
    harness['setup'](scale=3)
    harness['fail']([1, 3], (2, 'timeout', ''))

    assert container.cmd_up() == 1

    assert sorted({i for _k, i in _hooks(harness["events"])}) == [1, 3]


def test_token_goes_only_to_the_targets_and_is_never_logged(harness, caplog):
    """I11"""
    harness['setup'](scale=2)
    harness['fail']([1], (2, 'exited', ''))

    assert container.cmd_up() == 1

    tokens = [e for e in harness['events'] if e[0] == 'token']
    assert tokens == [('token', ['real-dev-1'], TOKEN)]
    assert TOKEN not in caplog.text
    assert not any(TOKEN in arg for e in harness['events'] if e[0] == 'run' for arg in e[1])


# ---------------------------------------------------------------------------
# devbase project post-start (やり直し)
# ---------------------------------------------------------------------------

def _running(harness, *indices):
    harness['fake'].running = [(i, f'proj-dev-{i}') for i in indices]


def test_post_start_reapplies_to_running_ready_instances(harness, caplog):
    """受け入れ条件 B1・B6: 両方へタイトルと token。不足を up と同じ文言で。./deploy は走らない"""
    harness['setup'](scale=2, deploy=True)
    _running(harness, 1, 2)
    harness['fake'].work[2] = ['myapp']

    assert container.cmd_post_start() == 0

    assert _hooks(harness['events']) == [(k, i) for k in ('repos', 'token', 'title')
                                         for i in (1, 2)]
    assert 'Repositories missing in /work of dev-2 (clone may have failed):' in _messages(caplog)
    assert '=== Post-start completed ===' in _messages(caplog)
    assert not any(e[0] == 'editor' for e in harness['events'])


def test_post_start_twice_writes_the_same(harness):
    """受け入れ条件 B5: 2 回目に渡るタイトルと token の書き込みが 1 回目と同じ"""
    harness['setup'](scale=2)
    _running(harness, 1, 2)

    assert container.cmd_post_start() == 0
    first = [e for e in harness['events'] if e[0] in ('token', 'title')]
    harness['events'].clear()
    assert container.cmd_post_start() == 0
    second = [e for e in harness['events'] if e[0] in ('token', 'title')]

    assert first == second


def test_post_start_skips_instances_not_running(harness, caplog):
    """受け入れ条件 B3"""
    harness['setup'](scale=3)
    _running(harness, 1, 3)

    assert container.cmd_post_start() == 0

    assert sorted({i for _k, i in _hooks(harness["events"])}) == [1, 3]
    assert any('dev-2' in r.getMessage() for r in caplog.records if r.levelname == 'WARNING')


def test_post_start_skips_instances_without_the_ready_mark(harness, caplog):
    """I4・前提 4: 完了の印が無い dev-2 には token も書かず、確かめは 1 台 1 回"""
    harness['setup'](scale=3)
    _running(harness, 1, 2, 3)
    harness['fake'].not_ready = {'proj-dev-2'}

    assert container.cmd_post_start() == 0

    assert sorted({i for _k, i in _hooks(harness["events"])}) == [1, 3]
    checks = [e[1] for e in harness['events'] if e[0] == 'ready?']
    assert checks == ['proj-dev-1', 'proj-dev-2', 'proj-dev-3']
    warnings = [r.getMessage() for r in caplog.records if r.levelname == 'WARNING']
    assert any('dev-2' in m and 'entrypoint' in m for m in warnings)


def test_post_start_without_any_ready_mark_fails(harness):
    harness['setup'](scale=2)
    _running(harness, 1, 2)
    harness['fake'].not_ready = {'proj-dev-1', 'proj-dev-2'}

    assert container.cmd_post_start() == 1

    assert _hooks(harness['events']) == []
    assert not any(e[0] == 'token' for e in harness['events'])


def test_post_start_with_nothing_running_does_not_start(harness, caplog):
    """受け入れ条件 B4・I8"""
    harness['setup'](scale=2)

    assert container.cmd_post_start() == 1

    assert _hooks(harness['events']) == []
    assert not any(e[0] in ('up', 'down') for e in harness['events'])
    assert not any(e[0] == 'run' and {'up', 'start', 'down', 'stop', 'rm'} & set(e[1])
                   for e in harness['events'])
    assert 'devbase up' in caplog.text


def test_post_start_fails_when_the_state_is_unknown(harness):
    harness['setup'](scale=2)
    _running(harness, 1)
    harness['fake'].ps_returncode = 1

    assert container.cmd_post_start() == 1

    assert _hooks(harness['events']) == []


def test_post_start_uses_the_scale_compose_file(harness):
    harness['setup'](scale=1)
    _running(harness, 1)
    Path(container._SCALE_COMPOSE_FILE).write_text('services: {}\n')

    assert container.cmd_post_start() == 0

    ls = [e[1] for e in harness['events'] if e[0] == 'run' and 'ls' in e[1]]
    assert ls and all(str(container._SCALE_COMPOSE_FILE) in cmd for cmd in ls)

