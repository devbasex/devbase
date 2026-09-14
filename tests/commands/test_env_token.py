"""`devbase env token [--print] [--context NAME]` (PLAN54 / #169)

起動中の dev コンテナの ``~/.vault-token`` を、再起動せずに新しい token で置き換える。
docker は runner のスタブで置き換え、設計「`devbase env token`」の状況表を行ごとに固定する。
"""

from __future__ import annotations

import subprocess

import pytest

from devbase.commands import env as env_cmd
from devbase.env import container_token

TEAM_GLOBAL = 'team/global'


class Docker:
    """``docker ps`` と ``docker exec`` を記録するスタブ"""

    def __init__(self, ps_lines=(), fail_exec=()):
        self.ps_lines = list(ps_lines)
        self.fail_exec = set(fail_exec)
        self.calls = []   # (argv, DOCKER_CONTEXT)

    def __call__(self, argv, **kwargs):
        import os

        self.calls.append((list(argv), os.environ.get('DOCKER_CONTEXT')))
        if argv[:2] == ['docker', 'ps']:
            return subprocess.CompletedProcess(argv, 0, stdout='\n'.join(self.ps_lines) + '\n',
                                               stderr='')
        if argv[:2] == ['docker', 'exec']:
            rc = 1 if argv[3] in self.fail_exec else 0
            return subprocess.CompletedProcess(argv, rc, stdout='', stderr='')
        raise AssertionError(f'unexpected: {argv}')

    def of(self, verb):
        return [c for c in self.calls if c[0][1] == verb]


@pytest.fixture
def web(openbao_root, openbao, monkeypatch):
    project = openbao_root / 'projects' / 'web'
    (project / 'env').write_text('')
    openbao.put(TEAM_GLOBAL, {'A': '1'})
    # cmd_env_token はプロジェクトの env と docker context を os.environ へ載せる。
    # 元が未設定の変数は delenv だけでは戻す控えが残らないため、先に setenv して控えを作る
    for name in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DEVBASE_DOCKER_CONTEXT', 'DEV_SERVICE_NAME',
                 'COMPOSE_PROJECT_NAME'):
        monkeypatch.setenv(name, '')
        monkeypatch.delenv(name)
    monkeypatch.chdir(project)
    monkeypatch.setenv('PWD', str(project))
    return project


def _run(root, docker, **kw):
    return env_cmd.cmd_env_token(root, runner=docker, **kw)


def test_writes_the_token_into_running_dev_containers(openbao_root, openbao, web, capsys):
    docker = Docker(['web-dev-1\tdev-1', 'web-dev-2\tdev-2'])

    assert _run(openbao_root, docker) == 0

    ps, = docker.of('ps')
    assert '--filter' in ps[0] and 'label=com.docker.compose.project=web' in ps[0]
    execs = docker.of('exec')
    assert [c[0][3] for c in execs] == ['web-dev-1', 'web-dev-2']
    out = capsys.readouterr().out
    assert out.split() == ['web-dev-1', 'web-dev-2']
    assert openbao.token not in out
    assert openbao.logins == 1


def test_non_dev_services_of_the_project_are_skipped(openbao_root, web):
    docker = Docker(['web-db-1\tdb', 'web-snapshot-1\tsnapshot', 'web-dev-1\tdev-1',
                     'web-devtools-1\tdevtools-1'])

    assert _run(openbao_root, docker) == 0

    assert [c[0][3] for c in docker.of('exec')] == ['web-dev-1']


def test_dev_service_name_comes_from_the_project_env_even_in_a_subdirectory(
        openbao_root, web, monkeypatch):
    (web / 'env').write_text('DEV_SERVICE_NAME=workspace\n')
    sub = web / 'src'
    sub.mkdir()
    monkeypatch.chdir(sub)
    monkeypatch.setenv('PWD', str(sub))
    docker = Docker(['web-workspace-1\tworkspace-1', 'web-dev-1\tdev-1'])

    assert _run(openbao_root, docker) == 0

    assert [c[0][3] for c in docker.of('exec')] == ['web-workspace-1']


def test_print_outputs_only_the_token_without_looking_at_containers(
        openbao_root, openbao, monkeypatch, capsys):
    monkeypatch.chdir(openbao_root)
    monkeypatch.setenv('PWD', str(openbao_root))
    docker = Docker()

    assert _run(openbao_root, docker, print_only=True) == 0

    assert capsys.readouterr().out == openbao.token + '\n'
    assert docker.calls == []


def test_outside_projects_fails_without_logging_in(openbao_root, openbao, monkeypatch):
    monkeypatch.chdir(openbao_root)
    monkeypatch.setenv('PWD', str(openbao_root))
    docker = Docker()

    assert _run(openbao_root, docker) == 1

    assert docker.calls == []
    assert openbao.logins == 0


def test_no_running_dev_container_fails_without_logging_in(openbao_root, openbao, web, caplog):
    docker = Docker(['web-db-1\tdb'])

    assert _run(openbao_root, docker) == 1

    assert docker.of('exec') == []
    assert openbao.logins == 0
    assert '起動中の dev コンテナがありません' in caplog.text


def test_partial_failure_is_non_zero(openbao_root, web):
    docker = Docker(['web-dev-1\tdev-1', 'web-dev-2\tdev-2'], fail_exec={'web-dev-2'})

    assert _run(openbao_root, docker) == 1


def test_rejected_login_is_non_zero(openbao_root, openbao, web):
    openbao.reject_login = True
    docker = Docker(['web-dev-1\tdev-1'])

    assert _run(openbao_root, docker) == 1
    assert docker.of('exec') == []


def test_local_yml_context_applies_to_both_ps_and_exec(openbao_root, web):
    (web / 'project.local.yml').write_text("docker:\n  context: gpu-wsl\n")
    docker = Docker(['web-dev-1\tdev-1'])

    assert _run(openbao_root, docker) == 0

    assert [ctx for _argv, ctx in docker.calls] == ['gpu-wsl', 'gpu-wsl']


def test_cli_context_wins(openbao_root, web):
    (web / 'project.local.yml').write_text("docker:\n  context: gpu-wsl\n")
    docker = Docker(['web-dev-1\tdev-1'])

    assert _run(openbao_root, docker, context='other') == 0

    assert {ctx for _argv, ctx in docker.calls} == {'other'}


def test_non_openbao_backend_fails(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    docker = Docker()

    assert env_cmd.cmd_env_token(tmp_path, runner=docker) == 1

    assert docker.calls == []
    assert 'openbao' in caplog.text


def test_cli_parses_env_token():
    from devbase import cli

    parser = cli._create_parser()
    args = parser.parse_args(['env', 'token', '--print', '--context', 'x'])
    assert (args.command, args.subcommand, args.print_only, args.context) == \
        ('env', 'token', True, 'x')
    assert cli._skip_secret_injection('env', 'token')


def test_push_is_the_shared_writer(openbao_root, web, monkeypatch):
    """書き込みは up と同じ container_token.push を通る"""
    seen = {}
    monkeypatch.setattr(container_token, 'push',
                        lambda names, token, runner=None: seen.setdefault('names', names) or names)

    assert _run(openbao_root, Docker(['web-dev-1\tdev-1'])) == 0
    assert seen['names'] == ['web-dev-1']


@pytest.mark.parametrize('failure', [
    'nonzero', FileNotFoundError('docker'),
    subprocess.TimeoutExpired(['docker', 'ps'], 30),
], ids=['nonzero', 'missing-docker', 'timeout'])
def test_docker_ps_failure_does_not_issue_or_distribute_a_token(
        tmp_path, monkeypatch, capsys, failure):
    """現状固定: 列挙の失敗は終了値 1 となり、認証にも配布にも進まない。"""
    from devbase.env import runtime

    class Backend:
        def __init__(self):
            self.issued_tokens = []

        def issue_token(self):
            token = 's.fake-issued-token'
            self.issued_tokens.append(token)
            return token

    backend = Backend()

    class Store:
        backend_name = 'openbao'

        def backend_for(self, ref):
            return backend

    monkeypatch.setattr(runtime, 'store_for', lambda root: Store())
    project = tmp_path / 'projects' / 'web'
    project.mkdir(parents=True)
    (project / 'env').write_text('')
    monkeypatch.chdir(project)
    monkeypatch.setenv('PWD', str(project))
    for name in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DEVBASE_DOCKER_CONTEXT',
                 'DEV_SERVICE_NAME', 'COMPOSE_PROJECT_NAME'):
        monkeypatch.setenv(name, '')
        monkeypatch.delenv(name)
    calls = []

    def runner(argv, **kwargs):
        calls.append(list(argv))
        assert argv[:2] == ['docker', 'ps']
        if isinstance(failure, Exception):
            raise failure
        return subprocess.CompletedProcess(argv, 1, stdout='', stderr='docker unavailable')

    assert env_cmd.cmd_env_token(tmp_path, runner=runner) == 1

    assert capsys.readouterr().out == ''
    assert backend.issued_tokens == []
    assert any(argv[:2] == ['docker', 'ps'] for argv in calls)
    assert not any(argv[:2] == ['docker', 'exec'] for argv in calls)
