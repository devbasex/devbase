"""起動の待ち (``wait_for_containers_ready``) の結果 (#224・#371)。

1 台が起動できなくても残りを制限まで待ち、起動できた番号と、起動できなかった番号ごとの
理由 (終了した・見つからない・時間切れ) を例外で返す。実 docker には触れない。
``docker_compose`` を差し替え、巡 (``time.sleep`` の回数) ごとの状態を台本で返す。
時計 (``time.monotonic``) も差し替え、休みと docker の呼び出しにかかる秒数を台本で進める。
"""

from __future__ import annotations

import json
import subprocess

import pytest

from devbase.errors import DevbaseError, DockerError
from devbase.utils import docker


class Script:
    """番号ごとの状態を巡の関数で返す ``docker_compose`` の代わり。

    ``states[n](round)`` は ``(状態, 完了の印があるか)``。状態が ``None`` なら見つからない。
    """

    def __init__(self, states, logs=None, call_seconds=0.0):
        self.states = states
        self.logs = logs or {}
        self.call_seconds = call_seconds
        self.clock = 1000.0
        self.round = 0
        self.calls: list = []

    def sleep(self, seconds):
        self.clock += seconds
        self.round += 1

    def monotonic(self):
        return self.clock

    def __call__(self, command, compose_file=None, check=True, capture_output=False,
                 silent_error=False):
        command = list(command)
        service = command[-1] if command[0] in ('ps', 'logs') else command[2]
        index = int(service.rsplit('-', 1)[1])
        self.calls.append((self.round, command[0], index))
        self.clock += self.call_seconds
        state, ready = self.states[index](self.round)
        if command[0] == 'ps':
            stdout = '' if state is None else json.dumps({'State': state})
            return subprocess.CompletedProcess(command, 0, stdout, '')
        if command[0] == 'logs':
            return subprocess.CompletedProcess(command, 0, self.logs.get(index, ''), '')
        if command[0] == 'exec':
            if ready:
                return subprocess.CompletedProcess(command, 0, '', '')
            raise subprocess.CalledProcessError(1, command)
        raise AssertionError(command)


def ready_at(n):
    return lambda r: ('running', r >= n)


def exited_at(n):
    return lambda r: (('exited', False) if r >= n else ('running', False))


def never_ready(_r):
    return ('running', False)


def missing(_r):
    return (None, False)


@pytest.fixture
def run(monkeypatch):
    def _run(states, scale=None, timeout=60, logs=None, call_seconds=0.0):
        script = Script(states, logs, call_seconds)
        monkeypatch.setattr(docker, 'docker_compose', script)
        monkeypatch.setattr(docker.time, 'sleep', script.sleep)
        monkeypatch.setattr(docker.time, 'monotonic', script.monotonic)
        scale = scale or len(states)
        try:
            result = docker.wait_for_containers_ready('dev', scale, timeout=timeout)
        except docker.ContainerStartupError as e:
            return script, e
        return script, result
    return _run


def test_all_ready_returns_true(run):
    script, result = run({1: ready_at(0), 2: ready_at(2)})

    assert result is True
    assert script.round == 2


def test_exited_instances_are_all_reported_with_logs(run):
    """受け入れ条件 12・11: 最初の巡で 2 台が終了していれば、両方の名前・理由・ログが入る"""
    _script, error = run({1: ready_at(0), 2: exited_at(0), 3: exited_at(0)},
                         logs={2: 'ln: Already exists', 3: 'boom'})

    assert isinstance(error, DockerError) and isinstance(error, DevbaseError)
    assert error.ready == (1,)
    assert [(f.index, f.service, f.reason) for f in error.failures] == [
        (2, 'dev-2', 'exited'), (3, 'dev-3', 'exited')]
    text = str(error)
    assert text.startswith('Container startup failed: 2 of 3 instances did not become ready.')
    assert '  - dev-2: exited unexpectedly' in text
    assert '  - dev-3: exited unexpectedly' in text
    assert '    ln: Already exists' in text and '    boom' in text
    assert text.count('Last logs:') == 2


def test_one_exited_does_not_stop_waiting_for_the_rest(run):
    """受け入れ条件 A2・I2: dev-1 が終了しても、後から完了する dev-2 を起動できた側へ入れる"""
    script, error = run({1: exited_at(0), 2: ready_at(5)})

    assert error.ready == (2,)
    assert [f.index for f in error.failures] == [1]
    assert script.round == 5


def test_timeout_names_the_instances_not_ready(run):
    """受け入れ条件 13・A6: 制限までに完了しない番号は時間切れとして名前が入る"""
    script, error = run({1: ready_at(1), 2: never_ready}, timeout=5)

    assert error.ready == (1,)
    (failure,) = error.failures
    assert (failure.index, failure.reason) == (2, 'timeout')
    assert ('  - dev-2: timeout after 5 rounds (5s elapsed) '
            'waiting for the entrypoint to complete') in str(error)
    assert script.round == 5


def test_timeout_reports_rounds_and_real_elapsed_seconds(run):
    """#374: 時間切れの文言は巡の回数と、docker の呼び出しを含む実際の経過の秒数 (整数) を出す"""
    # 1 巡に ps と exec が 1 回ずつ、各 0.65 秒。60 巡で 60 + 60 * 1.3 = 138 秒
    script, error = run({1: never_ready}, call_seconds=0.65)

    assert script.round == 60
    (failure,) = error.failures
    assert (failure.timeout, failure.elapsed) == (60, 138)
    assert ('  - dev-1: timeout after 60 rounds (138s elapsed) '
            'waiting for the entrypoint to complete') in str(error)
    assert 'timeout (60s)' not in str(error)


def test_elapsed_seconds_are_truncated_to_an_integer(run):
    """#374: 経過の秒数は整数の秒で出す (端数は切り捨てる)"""
    # 3 巡 + 3 巡 * (ps + exec) * 0.3 = 3 + 1.8 = 4.8 秒
    _script, error = run({1: never_ready}, timeout=3, call_seconds=0.3)

    (failure,) = error.failures
    assert failure.elapsed == 4
    assert 'timeout after 3 rounds (4s elapsed)' in str(error)


def test_missing_container_is_not_found(run):
    _script, error = run({1: missing, 2: ready_at(0)})

    assert error.ready == (2,)
    assert [(f.index, f.reason) for f in error.failures] == [(1, 'not_found')]
    assert '  - dev-1: container not found' in str(error)
    assert 'Last logs' not in str(error)


def test_every_index_lands_on_exactly_one_side(run):
    """I1: 待った番号は、起動できた側とできなかった側のどちらか一方にだけ現れる"""
    _script, error = run({1: ready_at(0), 2: exited_at(1), 3: never_ready, 4: missing,
                          5: ready_at(2)}, timeout=4)

    failed = [f.index for f in error.failures]
    assert sorted(list(error.ready) + failed) == [1, 2, 3, 4, 5]
    assert not set(error.ready) & set(failed)


def test_decided_instances_are_not_asked_again(run):
    """I3: 1 巡の呼び出しは番号あたり状態 1 回と完了 1 回まで。決まった番号は問い合わせない"""
    script, _error = run({1: ready_at(0), 2: exited_at(0), 3: ready_at(3)},
                         logs={2: 'x'})

    per_round: dict = {}
    for rnd, kind, index in script.calls:
        per_round.setdefault((rnd, index), []).append(kind)
    for kinds in per_round.values():
        assert kinds.count('ps') <= 1 and kinds.count('exec') <= 1
    assert {i for (rnd, i) in per_round if rnd > 0} == {3}
    assert [c for c in script.calls if c[1] == 'logs'] == [(0, 'logs', 2)]


def test_rounds_do_not_exceed_the_limit(run):
    script, _error = run({1: never_ready}, timeout=7)

    assert script.round == 7
    assert max(rnd for rnd, _k, _i in script.calls) == 6
