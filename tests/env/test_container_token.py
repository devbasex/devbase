"""container_token.push: token を起動中のコンテナの ~/.vault-token へ届ける (PLAN54)

docker は呼ばず、runner のスタブで次を固定する。

- token は ``docker exec -i`` の stdin だけで渡し、argv にもログにも出さない
- コンテナの中では ``mktemp`` で毎回新しい一時ファイルへ書き、``mv -f`` で置き換える
  (固定名だと既存の inode へ書いて ``umask 077`` が効かない)
- 書けたコンテナ名を返し、書けなかったものは警告にとどめる
"""

from __future__ import annotations

import logging
import subprocess

import pytest

from devbase.env import container_token

TOKEN = 's.fake-token-1234567890'


class Recorder:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        container = argv[3]
        rc = 1 if container in self.fail else 0
        return subprocess.CompletedProcess(argv, rc, stdout='', stderr='boom' if rc else '')


def test_push_writes_through_docker_exec_stdin():
    runner = Recorder()

    written = container_token.push(['web-dev-1'], TOKEN, runner=runner)

    assert written == ['web-dev-1']
    (argv, kwargs), = runner.calls
    assert argv[:4] == ['docker', 'exec', '-i', 'web-dev-1']
    assert argv[4:6] == ['sh', '-c']
    assert kwargs['input'] == TOKEN
    assert all(TOKEN not in part for part in argv)


def test_shell_uses_a_fresh_tempfile_then_moves_it_into_place():
    script = container_token.WRITE_COMMAND

    assert 'umask 077' in script
    assert 'mktemp "$HOME/.vault-token.XXXXXX"' in script
    assert 'chmod 0600' in script
    assert 'mv -f "$tmp" "$HOME/.vault-token"' in script
    assert 'rm -f "$tmp"' in script
    assert '.vault-token.tmp' not in script


def test_push_repeats_for_each_container_and_reports_failures(caplog):
    runner = Recorder(fail={'web-dev-2'})

    with caplog.at_level(logging.DEBUG):
        written = container_token.push(['web-dev-1', 'web-dev-2', 'web-dev-3'], TOKEN,
                                       runner=runner)

    assert written == ['web-dev-1', 'web-dev-3']
    assert [c[0][3] for c in runner.calls] == ['web-dev-1', 'web-dev-2', 'web-dev-3']
    assert 'web-dev-2' in caplog.text
    assert TOKEN not in caplog.text


def test_push_treats_a_missing_docker_as_a_failure(caplog):
    def runner(argv, **kwargs):
        raise FileNotFoundError('docker')

    with caplog.at_level(logging.DEBUG):
        assert container_token.push(['web-dev-1'], TOKEN, runner=runner) == []
    assert TOKEN not in caplog.text


def test_push_with_no_containers_does_nothing():
    runner = Recorder()
    assert container_token.push([], TOKEN, runner=runner) == []
    assert runner.calls == []


@pytest.mark.parametrize('error_type', [subprocess.TimeoutExpired, subprocess.CalledProcessError])
def test_push_continues_after_a_subprocess_exception_without_logging_token(caplog, error_type):
    """現状固定: 途中の例外を警告にとどめ、後続にも stdin で token を届ける。"""
    deliveries = []

    def runner(argv, **kwargs):
        name = argv[3]
        deliveries.append((name, kwargs['input']))
        if name == 'web-dev-2':
            if error_type is subprocess.TimeoutExpired:
                raise error_type(argv, 30, output=TOKEN, stderr=TOKEN)
            raise error_type(1, argv, output=TOKEN, stderr=TOKEN)
        return subprocess.CompletedProcess(argv, 0, stdout='', stderr='')

    with caplog.at_level(logging.DEBUG):
        written = container_token.push(['web-dev-1', 'web-dev-2', 'web-dev-3'], TOKEN,
                                       runner=runner)

    assert written == ['web-dev-1', 'web-dev-3']
    assert deliveries == [('web-dev-1', TOKEN), ('web-dev-2', TOKEN), ('web-dev-3', TOKEN)]
    assert any(record.levelno == logging.WARNING and 'web-dev-2' in record.getMessage()
               for record in caplog.records)
    assert TOKEN not in caplog.text
