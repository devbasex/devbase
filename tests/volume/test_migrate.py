"""旧既定のボリュームの移行 (``devbase project migrate-volume``。#315 決定 6・I7)

Docker は呼ばず、``docker`` の呼び出しを記録する偽の runner で振る舞いを縛る。実機での中身の
比べ合わせ (持ち主・権限・シンボリックリンク) は手動確認に残す。
"""

from __future__ import annotations

import subprocess
import types

import pytest

from devbase.errors import DevbaseError
from devbase.volume import manager
from devbase.volume.migrate import VolumeMigration, VolumeMigrationError

#: 自前の fixture が subprocess.run を差し替える前に取っておく (数える式を実機の sh で確かめる)
_REAL_RUN = subprocess.run

SOURCE = 'devbase_home_default'
TARGET = 'devbase_home_acme'


class FakeDocker:
    """``docker`` の呼び出しを記録し、ボリュームと稼働中のコンテナの状態で答える"""

    #: ``VolumeManager.create_volume`` で作られたボリューム (fixture ``created`` が差し込む)
    created: list = []

    def __init__(self, *, volumes=None, entries=None, running=None, reachable=True,
                 copy_rc=0, counts=None):
        self.volumes = set(volumes if volumes is not None else {SOURCE})
        #: ボリューム → 最上位のエントリ
        self.entries = dict(entries or {})
        #: ボリューム → それをマウントした稼働中のコンテナ
        self.running = dict(running or {})
        self.reachable = reachable
        self.copy_rc = copy_rc
        #: ボリューム → 数えたエントリの数 (既定は SOURCE 3・TARGET はコピー後 3)
        self.counts = dict(counts or {})
        self.copied = False
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        args = argv[1:]
        if args[0] == 'info':
            return self._proc(0 if self.reachable else 1, '27.0\n',
                              '' if self.reachable else 'Cannot connect')
        if args[:2] == ['volume', 'inspect']:
            return self._proc(0 if args[2] in self.volumes else 1)
        if args[0] == 'ps':
            name = args[args.index('--filter') + 1].split('=', 1)[1]
            return self._proc(0, ''.join(f'{c}\n' for c in self.running.get(name, [])))
        if args[0] == 'run':
            return self._run(args)
        raise AssertionError(f'想定外の docker の呼び出し: {argv}')

    def _run(self, args):
        mounts = [args[i + 1] for i, a in enumerate(args) if a == '-v']
        command = args[args.index('devbase-snapshot:latest') + 1:]
        for mount in mounts:
            volume = mount.split(':', 1)[0]
            assert volume in self.volumes or volume in self.created, \
                f'無いボリュームをマウントした: {mount}'
        if command[:2] == ['ls', '-A']:
            volume = mounts[0].split(':', 1)[0]
            return self._proc(0, ''.join(f'{e}\n' for e in self.entries.get(volume, [])))
        if command[0] == 'sh':
            volume = mounts[0].split(':', 1)[0]
            default = 3 if volume == SOURCE or self.copied else 0
            return self._proc(0, f'{self.counts.get(volume, default)}\n')
        if command[0] == 'cp':
            self.copied = self.copy_rc == 0
            return self._proc(self.copy_rc, '', 'cp: failed' if self.copy_rc else '')
        raise AssertionError(f'想定外のヘルパーの命令: {command}')

    @staticmethod
    def _proc(rc, out='', err=''):
        return subprocess.CompletedProcess([], rc, out, err)

    def runs(self):
        return [c for c in self.calls if c[1] == 'run']


@pytest.fixture(autouse=True)
def _no_real_docker(monkeypatch):
    """実機の Docker へ届かないようにする。偽の runner を渡さない呼び出しはここで落ちる"""
    from devbase.volume import migrate

    def refuse(argv, **kwargs):
        raise AssertionError(f'実機の docker を呼んだ: {argv}')

    monkeypatch.setattr(migrate.subprocess, 'run', refuse)


@pytest.fixture
def created(monkeypatch):
    """``VolumeManager.create_volume`` の呼び出しを記録する (先はここでだけ作られる)"""
    names: list[str] = []

    def create(self, name):
        names.append(name)
        return True

    monkeypatch.setattr(manager.VolumeManager, 'create_volume', create)
    monkeypatch.setattr(FakeDocker, 'created', names)
    return names


def migration(tmp_path, docker, group='acme'):
    return VolumeMigration(tmp_path, group, runner=docker,
                           image_provider=lambda root: 'devbase-snapshot:latest')


def test_copies_into_a_missing_target_and_keeps_the_source(tmp_path, created):
    docker = FakeDocker()

    count = migration(tmp_path, docker).run()

    assert count == 3
    assert created == [TARGET]
    [copy] = [c for c in docker.runs() if 'cp' in c]
    # 元は読み取り専用でマウントし、リンクを辿らない cp -a で写す
    assert f'{SOURCE}:/src:ro' in copy
    assert f'{TARGET}:/dst' in copy
    assert copy[-4:] == ['cp', '-a', '/src/.', '/dst/']
    # 元を消す・書き換える呼び出しは無い
    assert not any(c[1:3] == ['volume', 'rm'] for c in docker.calls)


def test_check_of_a_missing_target_does_not_create_it(tmp_path, created):
    """先の有無は inspect で確かめ、無い先をヘルパーでマウントしない (docker が暗黙に作るため)"""
    docker = FakeDocker()

    checked = migration(tmp_path, docker).check()

    assert checked.ok and checked.target_exists is False
    assert checked.source_count == 3
    assert created == []
    assert not any(TARGET in ' '.join(c) for c in docker.runs())


def test_an_empty_existing_target_is_used_as_is(tmp_path, created):
    docker = FakeDocker(volumes={SOURCE, TARGET})

    assert migration(tmp_path, docker).run() == 3
    assert created == []


def test_a_non_empty_target_stops_without_writing(tmp_path, created):
    entries = [f'.e{i}' for i in range(7)]
    docker = FakeDocker(volumes={SOURCE, TARGET}, entries={TARGET: entries})

    with pytest.raises(VolumeMigrationError) as exc:
        migration(tmp_path, docker).run()

    message = str(exc.value)
    assert TARGET in message and '.e0' in message and 'ほか 2 件' in message
    assert f'docker volume rm {TARGET}' in message
    assert created == []
    assert not any('cp' in c for c in docker.runs())


def test_a_target_with_only_a_blank_named_entry_is_not_empty(tmp_path, created):
    """名前の中身に依らず、エントリが 1 つでもあれば止める"""
    docker = FakeDocker(volumes={SOURCE, TARGET}, entries={TARGET: [' ']})

    with pytest.raises(VolumeMigrationError, match='空ではありません'):
        migration(tmp_path, docker).run()
    assert not any('cp' in c for c in docker.runs())


def test_the_count_script_reports_a_find_failure(tmp_path):
    """パイプの終了コードが wc のものにならず、find の失敗で 0 以外になる"""
    from devbase.volume.migrate import _COUNT_SCRIPT

    (tmp_path / 'vol').mkdir()
    (tmp_path / 'vol' / 'a').write_text('')
    script = _COUNT_SCRIPT.replace('/tmp/entries', str(tmp_path / 'entries'))

    def run(path):
        return _REAL_RUN(['sh', '-c', script, 'sh', str(path)],
                         capture_output=True, text=True, check=False)

    ok = run(tmp_path / 'vol')
    assert ok.returncode == 0 and ok.stdout.strip() == '1'
    assert run(tmp_path / 'missing').returncode != 0


def test_a_missing_source_stops(tmp_path, created):
    docker = FakeDocker(volumes=set())

    with pytest.raises(VolumeMigrationError) as exc:
        migration(tmp_path, docker).run()

    assert SOURCE in str(exc.value)
    assert created == [] and docker.runs() == []


@pytest.mark.parametrize('volume', [SOURCE, TARGET])
def test_a_running_container_on_either_volume_stops(tmp_path, created, volume):
    docker = FakeDocker(volumes={SOURCE, TARGET}, running={volume: ['myapp-ai-dev-1']})

    with pytest.raises(VolumeMigrationError) as exc:
        migration(tmp_path, docker).run()

    assert 'myapp-ai-dev-1' in str(exc.value) and 'devbase down' in str(exc.value)
    assert created == []
    assert not any('cp' in c for c in docker.runs())


def test_an_unreachable_docker_stops(tmp_path, created):
    docker = FakeDocker(reachable=False)

    checked = migration(tmp_path, docker).check()

    assert not checked.ok and 'Docker に届きません' in checked.problems[0]


def test_a_count_mismatch_after_copying_names_how_to_retry(tmp_path, created):
    docker = FakeDocker(counts={TARGET: 2})

    with pytest.raises(VolumeMigrationError) as exc:
        migration(tmp_path, docker).run()

    assert '元 3 件・先 2 件' in str(exc.value)
    assert f'docker volume rm {TARGET}' in str(exc.value)


def test_a_failed_copy_says_the_source_is_unchanged(tmp_path, created):
    docker = FakeDocker(copy_rc=1)

    with pytest.raises(VolumeMigrationError) as exc:
        migration(tmp_path, docker).run()

    assert '変わっていません' in str(exc.value)


@pytest.mark.parametrize('group', ['default', 'ubuntu', '1', 'a/b', ''])
def test_unusable_target_groups_are_rejected_before_docker(tmp_path, group):
    docker = FakeDocker()

    with pytest.raises(DevbaseError):
        migration(tmp_path, docker, group=group)
    assert docker.calls == []


# ---------------------------------------------------------------------------
# 入口 (cmd_project_migrate_volume)
# ---------------------------------------------------------------------------

def _args(**kw):
    return types.SimpleNamespace(**{'to': None, 'dry_run': False, **kw})


def test_command_without_to_is_a_usage_error(tmp_path, caplog):
    from devbase.commands.project import cmd_project_migrate_volume

    assert cmd_project_migrate_volume(tmp_path, _args()) == 2
    assert '--to' in caplog.text


def test_command_refuses_the_reserved_default(tmp_path, caplog):
    from devbase.commands.project import cmd_project_migrate_volume

    assert cmd_project_migrate_volume(tmp_path, _args(to='default')) == 2


def test_command_dry_run_reports_without_writing(tmp_path, monkeypatch, created, capsys):
    from devbase.commands import project
    from devbase.volume import migrate

    docker = FakeDocker()
    monkeypatch.setattr(migrate.subprocess, 'run', docker)
    monkeypatch.setattr('devbase.snapshot.manager.ensure_snapshot_image',
                        lambda root: 'devbase-snapshot:latest')

    assert project.cmd_project_migrate_volume(tmp_path, _args(to='acme', dry_run=True)) == 0

    out = capsys.readouterr().out
    assert f'{SOURCE} → {TARGET} へ 3 件を写せます' in out
    assert created == []
    assert not any('cp' in c for c in docker.runs())


def test_command_reports_the_next_steps(tmp_path, monkeypatch, created, capsys):
    from devbase.commands import project
    from devbase.volume import migrate

    monkeypatch.setattr(migrate.subprocess, 'run', FakeDocker())
    monkeypatch.setattr('devbase.snapshot.manager.ensure_snapshot_image',
                        lambda root: 'devbase-snapshot:latest')

    assert project.cmd_project_migrate_volume(tmp_path, _args(to='acme')) == 0

    out = capsys.readouterr().out
    assert '3 件を写しました' in out and '残してあります' in out
    assert 'group_aliases' in out and 'DEVBASE_ACCOUNT_GROUP=acme' in out


def test_command_failure_is_one(tmp_path, monkeypatch, created, caplog):
    from devbase.commands import project
    from devbase.volume import migrate

    monkeypatch.setattr(migrate.subprocess, 'run', FakeDocker(volumes=set()))
    monkeypatch.setattr('devbase.snapshot.manager.ensure_snapshot_image',
                        lambda root: 'devbase-snapshot:latest')

    assert project.cmd_project_migrate_volume(tmp_path, _args(to='acme')) == 1
    assert SOURCE in caplog.text


def test_parser_accepts_migrate_volume():
    from devbase import cli

    args = cli._create_parser().parse_args(
        ['project', 'migrate-volume', '--to', 'acme', '--dry-run'])
    assert args.subcommand == 'migrate-volume' and args.to == 'acme' and args.dry_run is True
