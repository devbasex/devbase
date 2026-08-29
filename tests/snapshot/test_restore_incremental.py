"""差分スナップショットの復元 (PLAN40)

GNU tar の incremental は、ディレクトリを ``(dev, ino)`` で追跡して rename を検出する。
ディレクトリが削除され作り直されると **inode 番号が再利用される**ため、tar は無関係な
ディレクトリを「rename された」と誤判定し、dumpdir に偽の ``R``/``T`` レコードを書く。
復元側の ``tar --listed-incremental=/dev/null`` はそれを ``rename()`` として実行して失敗し、
終了コード 2 を返す。従来はここで ``restore()`` が中断し、ボリュームが中途半端なまま残った。

**tar は rename に失敗しても展開自体は完遂している。** そのため復元は続行してよい。
このテストは「rename エラーだけの失敗は警告にして続ける」「それ以外の失敗は従来どおり
止める」の両方を固定する。
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from devbase.errors import SnapshotCommandError, SnapshotError
from devbase.snapshot.manager import (
    SnapshotManager, rename_only_failure, rename_targets,
)

@pytest.fixture(autouse=True)
def _clean_group_env(monkeypatch):
    """復元前の自動バックアップがグループを解決するので、環境で揺らさない。"""
    monkeypatch.delenv("DEVBASE_ACCOUNT_GROUP", raising=False)


# Task 1 で実際に採取した stderr
BOGUS_RENAME_STDERR = (
    "tar: Cannot rename './.claude/plugins/cache/B9/unknown/commands/unknown/commands' "
    "to './.claude/plugins/cache/B10': No such file or directory\n"
    "tar: Exiting with failure status due to previous errors\n"
)
# 本番の backups/20260829-182126 で観測した形 (Directory not empty)
PRODUCTION_RENAME_STDERR = (
    "tar: Cannot rename './ai/.codex/.tmp/marketplaces/ai-plugins/plugins/playwright-kit' "
    "to './ai/.claude/plugins/cache/temp_git_1788002903410_i7hzgk/.git/objects': "
    "Directory not empty\n"
    "tar: Exiting with failure status due to previous errors\n"
)
REAL_ERROR_STDERR = (
    "tar: ./ai/.claude/history.jsonl: Cannot write: No space left on device\n"
    "tar: Exiting with failure status due to previous errors\n"
)


# ---------------------------------------------------------------------------
# stderr の判定 (純粋関数)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stderr", [BOGUS_RENAME_STDERR, PRODUCTION_RENAME_STDERR])
def test_rename_only_failure_is_detected(stderr):
    """rename エラーだけの失敗は、失敗した rename の一覧を返す。"""
    assert rename_only_failure(stderr) is not None
    assert len(rename_only_failure(stderr)) == 1


def test_rename_targets_extracts_the_destination():
    """欠落の判定に使うのは rename の**宛先**である。"""
    lines = rename_only_failure(BOGUS_RENAME_STDERR)
    assert rename_targets(lines) == ['./.claude/plugins/cache/B10']


def test_rename_targets_extracts_the_destination_with_spaces():
    """パスに空白が入っていても宛先を取り違えない。"""
    stderr = ("tar: Cannot rename './a b/old dir' to './a b/new dir': "
              "Directory not empty\n")
    assert rename_targets(rename_only_failure(stderr)) == ['./a b/new dir']


def test_real_error_is_not_treated_as_rename_failure():
    assert rename_only_failure(REAL_ERROR_STDERR) is None


def test_rename_error_mixed_with_a_real_error_is_not_tolerated():
    """1 行でも別のエラーが混ざれば見逃さない。"""
    assert rename_only_failure(BOGUS_RENAME_STDERR + REAL_ERROR_STDERR) is None


def test_empty_stderr_is_not_treated_as_rename_failure():
    """終了コードが 0 でないのに stderr が空なら、理由が分からないので止める。"""
    assert rename_only_failure("") is None
    assert rename_only_failure("   \n") is None


# ---------------------------------------------------------------------------
# restore() の振る舞い (Docker を起動しない)
# ---------------------------------------------------------------------------

# 新レイアウト (PLAN39 以降) と旧レイアウト (共通ボリューム 1 本) の両方を通す。
NEW_LAYOUT = {'volumes': {'ai': 'devbase_home_ubuntu', 'group': 'devbase_home_default'}}
OLD_LAYOUT = {'volume': 'devbase_home_ubuntu'}


def _write_generation(root: Path, name: str, incrementals: int,
                      layout: dict | None = None) -> Path:
    snap_dir = root / 'backups' / name
    snap_dir.mkdir(parents=True)
    (snap_dir / 'full.tar.zst').write_text('archive')
    (snap_dir / 'snapshot.snar').write_text('snar')
    files = ['full.tar.zst']
    for i in range(1, incrementals + 1):
        incr = f'incr-{i:03d}.tar.zst'
        (snap_dir / incr).write_text('archive')
        files.append(incr)
    (snap_dir / 'meta.yml').write_text(yaml.dump({
        'name': name, 'type': 'incremental', 'files': files,
        'incremental_count': incrementals,
        **(layout if layout is not None else NEW_LAYOUT),
    }))
    return snap_dir


class StubManager(SnapshotManager):
    """``docker run`` を起こさず、指定したアーカイブでだけ失敗させる。"""

    def __init__(self, root: Path, failures: dict[str, str]):
        super().__init__(root)
        self._failures = failures
        self.restored: list[str] = []
        self.checked: str | None = None

    def _run_docker_tar(self, snap_dir, mode, command, volumes=None):
        if mode == 'backup':
            (snap_dir / 'full.tar.zst').write_text('archive')
            (snap_dir / 'snapshot.snar').write_text('snar')
            return
        archive = self._archive_in(command)
        if archive is None:
            # 展開ではなく、復元後の rename 宛先の検証コマンド
            self.checked = command
            return
        self.restored.append(archive)
        if archive in self._failures:
            stderr = self._failures[archive]
            raise SnapshotCommandError(
                f"Dockerでのtar操作に失敗しました: {stderr}", stderr=stderr)

    @staticmethod
    def _archive_in(command: str):
        """復元コマンドから、いま展開しているアーカイブ名を取り出す。

        展開以外のコマンド (rename 宛先の検証) なら ``None`` を返す。
        """
        match = re.search(r'full\.tar\.zst|incr-\d+\.tar\.zst', command)
        return match.group(0) if match else None


def test_bogus_rename_does_not_stop_the_restore(tmp_path):
    """AC1: 偽 rename で落ちても、後続の差分まで適用しきる。"""
    _write_generation(tmp_path, 'gen', incrementals=3)
    mgr = StubManager(tmp_path, {'incr-001.tar.zst': BOGUS_RENAME_STDERR})

    mgr.restore('gen')

    assert mgr.restored == [
        'full.tar.zst', 'incr-001.tar.zst', 'incr-002.tar.zst', 'incr-003.tar.zst']


def test_bogus_rename_is_reported_as_a_warning(tmp_path, caplog):
    """見逃しを可視化する: 失敗した rename のパスを警告に出す。"""
    _write_generation(tmp_path, 'gen', incrementals=1)
    mgr = StubManager(tmp_path, {'incr-001.tar.zst': BOGUS_RENAME_STDERR})

    with caplog.at_level('WARNING'):
        mgr.restore('gen')

    warnings = '\n'.join(r.getMessage()
                         for r in caplog.records if r.levelname == 'WARNING')
    assert 'incr-001.tar.zst' in warnings
    assert './.claude/plugins/cache/B10' in warnings


def test_a_failure_without_stderr_is_not_tolerated(tmp_path):
    """イメージのビルド失敗など、stderr を持たない失敗は見逃さない。"""
    _write_generation(tmp_path, 'gen', incrementals=2)

    class NoStderrManager(StubManager):
        def _run_docker_tar(self, snap_dir, mode, command, volumes=None):
            if mode == 'backup':
                return super()._run_docker_tar(snap_dir, mode, command, volumes)
            raise SnapshotError("devbase-snapshotのビルドに失敗")

    mgr = NoStderrManager(tmp_path, {})
    with pytest.raises(SnapshotError) as e:
        mgr.restore('gen')
    assert 'full.tar.zst' in str(e.value)


@pytest.mark.parametrize("layout, label", [
    (NEW_LAYOUT, "新レイアウト (ai + group)"),
    (OLD_LAYOUT, "旧レイアウト (共通ボリューム 1 本)"),
])
def test_both_layouts_survive_a_bogus_rename(tmp_path, layout, label):
    """AC5: 対象ボリュームのレイアウトに関係なく、偽 rename では止まらない。"""
    _write_generation(tmp_path, 'gen', incrementals=2, layout=layout)
    mgr = StubManager(tmp_path, {'incr-001.tar.zst': BOGUS_RENAME_STDERR})

    mgr.restore('gen')

    assert mgr.restored == [
        'full.tar.zst', 'incr-001.tar.zst', 'incr-002.tar.zst'], label


def test_skipped_rename_targets_are_checked_after_the_restore(tmp_path):
    """飲み込んだ rename の宛先は、全アーカイブ適用後にまとめて検証する。"""
    _write_generation(tmp_path, 'gen', incrementals=2)
    mgr = StubManager(tmp_path, {'incr-001.tar.zst': BOGUS_RENAME_STDERR})

    mgr.restore('gen')

    # 宛先が渡り、空ディレクトリだけを拾うコマンドになっている
    assert mgr.checked is not None
    assert './.claude/plugins/cache/B10' in mgr.checked
    assert 'ls -A' in mgr.checked


def test_rename_targets_are_shell_quoted(tmp_path):
    """宛先は tar の出力由来なので、シェルへ素通しにしない。"""
    _write_generation(tmp_path, 'gen', incrementals=1)
    nasty = ("tar: Cannot rename './x' to './a b; touch /tmp/pwned': "
             "Directory not empty\n")
    mgr = StubManager(tmp_path, {'incr-001.tar.zst': nasty})

    mgr.restore('gen')

    assert mgr.checked is not None
    assert 'touch /tmp/pwned' not in mgr.checked.replace(
        shlex.quote('./a b; touch /tmp/pwned'), '')
    assert shlex.quote('./a b; touch /tmp/pwned') in mgr.checked


def test_no_check_runs_when_no_rename_was_skipped(tmp_path):
    """rename を飲み込んでいなければ、余計なコンテナを起こさない。"""
    _write_generation(tmp_path, 'gen', incrementals=1)
    mgr = StubManager(tmp_path, {})

    mgr.restore('gen')

    assert mgr.checked is None


def test_an_empty_rename_target_is_warned_as_possible_data_loss(tmp_path, caplog):
    """AC4: 宛先が空なら、正当な rename を取りこぼした可能性として警告する。"""
    _write_generation(tmp_path, 'gen', incrementals=1)

    class EmptyTargetManager(StubManager):
        def _run_docker_tar(self, snap_dir, mode, command, volumes=None):
            result = super()._run_docker_tar(snap_dir, mode, command, volumes)
            if self._archive_in(command) is None and mode == 'restore':
                return subprocess.CompletedProcess(
                    [], 0, stdout='./.claude/plugins/cache/B10\n', stderr='')
            return result

    mgr = EmptyTargetManager(tmp_path, {'incr-001.tar.zst': BOGUS_RENAME_STDERR})
    with caplog.at_level('WARNING'):
        mgr.restore('gen')

    warnings = '\n'.join(r.getMessage() for r in caplog.records
                          if r.levelname == 'WARNING')
    assert '中身が復元されていない可能性があります' in warnings
    assert './.claude/plugins/cache/B10' in warnings


def test_a_real_tar_error_still_stops_the_restore(tmp_path):
    """AC4: rename 以外の失敗は従来どおり止める。"""
    _write_generation(tmp_path, 'gen', incrementals=3)
    mgr = StubManager(tmp_path, {'incr-002.tar.zst': REAL_ERROR_STDERR})

    with pytest.raises(SnapshotError) as e:
        mgr.restore('gen')

    assert 'incr-002.tar.zst' in str(e.value)
    assert 'incr-003.tar.zst' not in mgr.restored


def test_the_failure_message_says_how_to_get_back(tmp_path):
    """AC4: どの差分で落ちたかと、pre-restore から戻せることを示す。"""
    _write_generation(tmp_path, 'gen', incrementals=2)
    mgr = StubManager(tmp_path, {'incr-001.tar.zst': REAL_ERROR_STDERR})

    with pytest.raises(SnapshotError) as e:
        mgr.restore('gen')

    message = str(e.value)
    assert 'incr-001.tar.zst' in message
    assert 'pre-restore-' in message
    assert 'devbase snapshot restore' in message


def test_a_full_restore_failure_also_says_how_to_get_back(tmp_path):
    """フルの展開で落ちた場合も同じ案内を出す。"""
    _write_generation(tmp_path, 'gen', incrementals=1)
    mgr = StubManager(tmp_path, {'full.tar.zst': REAL_ERROR_STDERR})

    with pytest.raises(SnapshotError) as e:
        mgr.restore('gen')

    assert 'full.tar.zst' in str(e.value)
    assert 'pre-restore-' in str(e.value)


# ---------------------------------------------------------------------------
# 実機 (Docker が要る)
# ---------------------------------------------------------------------------

def _docker_available() -> bool:
    if shutil.which('docker') is None:
        return False
    try:
        subprocess.run(['docker', 'info'], capture_output=True, timeout=30,
                       check=True)
    except (subprocess.SubprocessError, OSError):
        return False
    try:
        out = subprocess.run(
            ['docker', 'image', 'inspect', 'devbase-snapshot:latest'],
            capture_output=True, timeout=30)
        return out.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


# group 側だけを対象にする。共通側 (ai) は devbase_home_ubuntu しか許されず、
# 実データのボリュームを消してしまうため実機テストでは使わない。
TEST_VOLUME = 'devbase_home_plan40test'


def _docker(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(['docker', *args], capture_output=True, text=True,
                          **kwargs)


@pytest.fixture
def throwaway_volume():
    # 収集時ではなくこのテストを実行するときだけ Docker を叩く
    if not _docker_available():
        pytest.skip("Docker と devbase-snapshot:latest イメージが要る")
    _docker('volume', 'rm', '-f', TEST_VOLUME)
    _docker('volume', 'create', TEST_VOLUME)
    yield TEST_VOLUME
    _docker('volume', 'rm', '-f', TEST_VOLUME)


def _in_volume(script: str) -> subprocess.CompletedProcess:
    return _docker('run', '--rm', '-v', f'{TEST_VOLUME}:/work',
                   'devbase-snapshot:latest', 'bash', '-c', script)


def _swap_directories(generation: str) -> None:
    """ディレクトリを総入れ替えして inode を再利用させる。"""
    _in_volume(
        'rm -rf /work/.claude/plugins/cache; '
        'mkdir -p /work/.claude/plugins/cache; '
        f'for i in $(seq 1 40); do '
        f'  d=/work/.claude/plugins/cache/{generation}$i/unknown/commands; '
        f'  mkdir -p $d; echo {generation}$i > $d/file.txt; '
        f'done; '
        f'echo {generation} > /work/marker.txt')


def _listing() -> str:
    return _in_volume('cd /work && find . | sort').stdout


def test_a_generation_with_swapped_directories_restores_completely(
        tmp_path, throwaway_volume):
    """AC1/AC2/AC3: 総入れ替えを挟んだ差分 3 個の世代が、最後まで復元でき内容も一致する。"""
    mgr = SnapshotManager(tmp_path)
    # 作成側の対象を使い捨てボリュームへ差し替える。こうしないと復元前の自動バックアップが
    # 実データのボリューム (devbase_home_ubuntu) を対象にしてしまう。
    mgr._volumes = {'group': TEST_VOLUME}

    _swap_directories('A')
    mgr.create(name='plan40gen', full=True)
    for generation in ('B', 'C', 'D'):
        _swap_directories(generation)
        mgr.create(name='plan40gen')

    snap_dir = tmp_path / 'backups' / 'plan40gen'
    assert sorted(p.name for p in snap_dir.glob('incr-*.tar.zst')) == [
        'incr-001.tar.zst', 'incr-002.tar.zst', 'incr-003.tar.zst']

    expected = _listing()
    assert './marker.txt' in expected

    mgr.restore('plan40gen')

    assert _listing() == expected
