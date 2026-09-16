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
            return subprocess.CompletedProcess(cmd, 0, '', '')
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
