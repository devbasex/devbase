"""ホストの userns の制限を読み、外し、戻す (#533)

Ubuntu 23.10 以降のカーネルは ``kernel.apparmor_restrict_unprivileged_userns = 1`` で、
AppArmor のプロファイルを持たない特権の無いプロセスが user namespace を作ることを止める。
dev コンテナの中の bubblewrap (codex のサンドボックスなど) もこれで止まる。

このモジュールは 3 つの値を読む。

- ホストの値 (``/proc/sys/kernel/apparmor_restrict_unprivileged_userns``)
- 設定ファイルの上の値 (sysctl.d の読む順で最後にキーを書くファイルの値。sysctl.d(5))
- コンテナが動くカーネルの値 (手元の ``devbase-base:latest`` を ``--pull never`` で起動して読む)

変えるのは devbase の sysctl の設定ファイル (``/etc/sysctl.d/60-devbase-userns.conf``) の
1 つだけで、1 行目の目印 (``# devbase:``) で devbase が置いたものと見分ける。

**ホストのパスと子プロセスの起動は差し替えられる。** 関数は ``paths`` と ``runner`` を
引数で受け、省けばモジュールの既定 (:data:`DEFAULT_PATHS` / :data:`DEFAULT_RUNNER`) を
呼ぶたびに読む。テストの autouse の fixture が既定を tmp と「呼ばれたら落ちる runner」へ
差し替える (設計の決定 7)。子プロセスには標準入力を渡さない (``stdin=DEVNULL``)。
``curl | bash`` の残りのスクリプトを読ませないためである (決定 3)。

表示は持たない。出す行は :class:`Report` に入れ、``emit`` を渡されたら足すたびに呼ぶ。
sudo がパスワードを尋ねる前に説明を出すため、行は返り値を待たずに出す。
"""

from __future__ import annotations

import enum
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from devbase.log import get_logger

logger = get_logger(__name__)

#: ``subprocess.run`` と同じ形の呼び出し (``docker_context.Runner`` と同じ形)
Runner = Callable[..., subprocess.CompletedProcess]
#: 行を出す呼び出し。``(レベル, 行)`` で、レベルは ``info`` か ``warning``
Emit = Callable[[str, str], None]

KEY = 'kernel.apparmor_restrict_unprivileged_userns'
MARKER = '# devbase:'
#: devbase の sysctl の設定ファイルの中身 (2 行。1 行目が目印)
FILE_CONTENT = (
    f'{MARKER} dev コンテナの中で bubblewrap などが user namespace を作れるようにする。'
    '戻すには devbase userns revert\n'
    f'{KEY} = 0\n'
)
PROBE_IMAGE = 'devbase-base:latest'
PROBE_TIMEOUT = 20.0


@dataclass(frozen=True)
class HostPaths:
    """読む・書くホストのパス。テストは tmp の下へ向けた値を渡す"""

    proc_key: Path = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')
    #: sysctl.d(5) の読む順。同じ名前のファイルは先のディレクトリのものが勝つ
    sysctl_dirs: Tuple[Path, ...] = (
        Path('/etc/sysctl.d'),
        Path('/run/sysctl.d'),
        Path('/usr/local/lib/sysctl.d'),
        Path('/usr/lib/sysctl.d'),
    )
    devbase_file: Path = Path('/etc/sysctl.d/60-devbase-userns.conf')
    container_markers: Tuple[Path, ...] = (Path('/.dockerenv'), Path('/run/.containerenv'))
    tty: Path = Path('/dev/tty')


class Privilege(enum.Enum):
    ROOT = 'root'
    SUDO = 'sudo'
    SUDO_PROMPT = 'sudo-prompt'
    NONE = 'none'


@dataclass(frozen=True)
class ContainerKernel:
    """コンテナが動くカーネルの値

    ``source`` が ``none`` なら未確認である。``image`` / ``docker-info`` で ``value`` が
    ``None`` なら、キーが無い・AppArmor が無いので制限は効かない。
    """

    value: Optional[int]
    source: str
    reason: str = ''


@dataclass
class Report:
    ok: bool = True
    changed: bool = False
    lines: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    emit: Optional[Emit] = field(default=None, repr=False, compare=False)

    def add(self, line: str) -> None:
        self.lines.append(line)
        if self.emit is not None:
            self.emit('info', line)

    def warn(self, line: str) -> None:
        self.warnings.append(line)
        if self.emit is not None:
            self.emit('warning', line)


DEFAULT_PATHS = HostPaths()
DEFAULT_RUNNER: Runner = subprocess.run


def _euid() -> int:
    """実効ユーザー ID。テストが差し替える"""
    return os.geteuid()


def logger_emit(log) -> Emit:
    """``Report`` の行を logger の ``info`` / ``warning`` で出す ``emit`` を作る"""
    def _emit(level: str, line: str) -> None:
        (log.warning if level == 'warning' else log.info)('%s', line)
    return _emit


def _defaults(paths: Optional[HostPaths], runner: Optional[Runner]) -> Tuple[HostPaths, Runner]:
    # 既定は呼ぶたびにモジュールから読む (fixture の差し替えを効かせるため)
    return (paths if paths is not None else DEFAULT_PATHS,
            runner if runner is not None else DEFAULT_RUNNER)


def _run(runner: Runner, cmd: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault('capture_output', True)
    kwargs.setdefault('text', True)
    logger.debug('run: %s', ' '.join(str(c) for c in cmd))
    proc = runner(list(cmd), stdin=subprocess.DEVNULL, **kwargs)
    logger.debug('exit: %s', proc.returncode)
    return proc


def _first_line(text: Optional[str]) -> str:
    for line in (text or '').splitlines():
        if line.strip():
            return line.strip()
    return ''


# --- 値を読む ---------------------------------------------------------------

def _read_runtime(paths: HostPaths) -> Tuple[Optional[int], str]:
    """proc のキーを読む。読めなければ ``(None, 理由)``"""
    try:
        text = paths.proc_key.read_text()
    except FileNotFoundError:
        return None, 'キーが無い'
    except OSError as e:
        return None, f'読めない: {e.strerror or e}'
    try:
        value = int(text.strip())
    except ValueError:
        return None, f'読めない: 値が {text.strip()!r}'
    if value not in (0, 1):
        return None, f'読めない: 値が {value}'
    return value, ''


def read_runtime_value(paths: HostPaths) -> Optional[int]:
    """proc のキーを読み、0 / 1 を返す。キーが無い・読めないなら ``None``"""
    return _read_runtime(paths)[0]


def _normalize_key(key: str) -> str:
    return key.strip().replace('/', '.')


def _file_value(path: Path) -> Optional[int]:
    """1 つの sysctl.d のファイルがキーへ最後に書く値。書かない・読めないなら ``None``"""
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return None
    value: Optional[int] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in '#;':
            continue
        if line.startswith('-'):
            line = line[1:]
        if '=' not in line:
            continue
        key, _, val = line.partition('=')
        if _normalize_key(key) != KEY:
            continue
        try:
            value = int(val.strip())
        except ValueError:
            continue
    return value


def _ordered_files(paths: HostPaths) -> List[Path]:
    """sysctl.d の ``*.conf`` を読む順に並べる (同じ名前は先のディレクトリのものだけ)"""
    chosen: dict = {}
    for directory in paths.sysctl_dirs:
        try:
            entries = sorted(directory.glob('*.conf'))
        except OSError:
            continue
        for entry in entries:
            chosen.setdefault(entry.name, entry)
    return [chosen[name] for name in sorted(chosen)]


def config_value(paths: HostPaths, skip: Optional[Path] = None
                 ) -> Tuple[Optional[int], Optional[Path]]:
    """設定ファイルの上の値と、それを書くファイル。``skip`` のパスは無いものとして読む"""
    result: Tuple[Optional[int], Optional[Path]] = (None, None)
    for path in _ordered_files(paths):
        if skip is not None and path == skip:
            continue
        value = _file_value(path)
        if value is not None:
            result = (value, path)
    return result


def later_overrides(paths: HostPaths) -> List[Path]:
    """devbase のファイルより辞書順で後にあり、キーへ 0 以外を書くファイル"""
    name = paths.devbase_file.name
    return [p for p in _ordered_files(paths)
            if p.name > name and _file_value(p) not in (None, 0)]


def in_container(paths: HostPaths) -> bool:
    return any(marker.exists() for marker in paths.container_markers)


def _devbase_file_state(paths: HostPaths) -> Tuple[bool, bool, Optional[str]]:
    """devbase のパスのファイルの ``(ある, 目印がある, 中身)``"""
    try:
        text = paths.devbase_file.read_text()
    except FileNotFoundError:
        return False, False, None
    except (OSError, UnicodeDecodeError):
        return True, False, None
    first = text.splitlines()[0] if text else ''
    return True, first.startswith(MARKER), text


# --- 権限 -------------------------------------------------------------------

def choose_privilege(paths: HostPaths, runner: Runner) -> Privilege:
    """``ROOT`` / ``SUDO`` / ``SUDO_PROMPT`` / ``NONE`` を選ぶ。パスワードは尋ねない"""
    if _euid() == 0:
        return Privilege.ROOT
    try:
        proc = _run(runner, ['sudo', '-n', 'true'])
    except FileNotFoundError:
        return Privilege.NONE
    if proc.returncode == 0:
        return Privilege.SUDO
    try:
        fd = os.open(str(paths.tty), os.O_RDWR)
    except OSError:
        return Privilege.NONE
    os.close(fd)
    return Privilege.SUDO_PROMPT


def _prefix(priv: Privilege) -> List[str]:
    return [] if priv is Privilege.ROOT else ['sudo']


def _ask_sudo(runner: Runner, report: Report) -> bool:
    """``sudo -v`` でパスワードを尋ねる。通らない・中断したら ``False``"""
    try:
        proc = _run(runner, ['sudo', '-v'], capture_output=False, text=False)
    except KeyboardInterrupt:
        report.warn('sudo のパスワードの入力を中断した。何も変えていない')
        return False
    except FileNotFoundError:
        report.warn('sudo が無い')
        return False
    if proc.returncode != 0:
        report.warn('sudo の認証が通らなかった。何も変えていない')
        return False
    return True


# --- コンテナが動くカーネル -------------------------------------------------

def probe_container_kernel(runner: Runner, timeout: float = PROBE_TIMEOUT) -> ContainerKernel:
    """コンテナが動くカーネルの値を、手元のイメージから読む。イメージは取得しない"""
    def call(cmd: List[str]) -> subprocess.CompletedProcess:
        return _run(runner, cmd, timeout=timeout)

    try:
        inspect = call(['docker', 'image', 'inspect', '--format', '{{.Id}}', PROBE_IMAGE])
        if inspect.returncode == 0:
            proc = call(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none',
                         '--entrypoint', 'cat', PROBE_IMAGE, '/proc/sys/kernel/'
                         'apparmor_restrict_unprivileged_userns'])
            if proc.returncode == 0:
                text = (proc.stdout or '').strip()
                if text in ('0', '1'):
                    return ContainerKernel(int(text), 'image')
                return ContainerKernel(None, 'none', f'値が読めない: {text!r}')
            err = proc.stderr or ''
            if 'No such file' in err:
                return ContainerKernel(None, 'image', 'キーが無い（制限は効かない）')
            return ContainerKernel(None, 'none',
                                   f'docker run が失敗した: {_first_line(err) or proc.returncode}')
        info = call(['docker', 'info', '--format', '{{json .SecurityOptions}}'])
    except FileNotFoundError:
        return ContainerKernel(None, 'none', 'docker が無い')
    except subprocess.TimeoutExpired:
        return ContainerKernel(None, 'none', f'docker が {int(timeout)} 秒で返らない')
    if info.returncode != 0:
        return ContainerKernel(None, 'none',
                               f'docker の daemon に届かない: {_first_line(info.stderr)}'.rstrip(': '))
    if 'apparmor' not in (info.stdout or ''):
        return ContainerKernel(None, 'docker-info', 'AppArmor が無い（制限は効かない）')
    return ContainerKernel(None, 'none',
                           f'{PROBE_IMAGE} が無い。devbase build の後に devbase userns status で確かめる')


def _container_line(ck: ContainerKernel) -> str:
    label = 'コンテナが動くカーネル:  '
    if ck.value is not None:
        return f'{label}{ck.value}（{PROBE_IMAGE} から読んだ）'
    if ck.source == 'none':
        return f'{label}未確認（{ck.reason}）'
    return f'{label}{ck.reason}'


def _container_guide(report: Report) -> None:
    report.warn('コンテナが動くカーネル（VM かリモート）で userns の制限が効いている。'
                'devbase はそこを変えない')
    report.add('    変え方: VM の中（Colima なら colima ssh の中）で次を打つ')
    report.add(f"      echo '{KEY} = 0' | sudo tee /etc/sysctl.d/60-devbase-userns.conf"
               ' && sudo sysctl -p /etc/sysctl.d/60-devbase-userns.conf')
    report.add('    VM を作り直すと消えることがある')


def _finish_with_container(report: Report, host_value: Optional[int], runner: Runner) -> Report:
    ck = probe_container_kernel(runner)
    report.add(_container_line(ck))
    if ck.value == 1 and host_value in (0, None):
        _container_guide(report)
    return report


# --- status / apply / revert ------------------------------------------------

def _host_line(value: Optional[int], reason: str, cfg: Optional[int],
               cfg_path: Optional[Path]) -> str:
    label = 'ホスト:                  '
    if value is None:
        if reason == 'キーが無い':
            return f'{label}キーが無い（制限は効かない）'
        return f'{label}{reason}'
    if cfg is None:
        return f'{label}{value}（設定ファイルの上では値が無い）'
    return f'{label}{value}（設定ファイルの上では {cfg}: {cfg_path}）'


def _file_line(paths: HostPaths) -> str:
    label = 'devbase のファイル:      '
    exists, marked, _ = _devbase_file_state(paths)
    if not exists:
        return f'{label}無し（{paths.devbase_file}）'
    if marked:
        return f'{label}あり・目印あり（{paths.devbase_file}）'
    return f'{label}あり・目印無し（{paths.devbase_file}。devbase のものではない）'


def status(paths: Optional[HostPaths] = None, runner: Optional[Runner] = None,
           emit: Optional[Emit] = None) -> Report:
    """3 つの値を読み、行を組む。何も変えない"""
    paths, runner = _defaults(paths, runner)
    report = Report(emit=emit)
    value, reason = _read_runtime(paths)
    cfg, cfg_path = config_value(paths)
    report.add(_host_line(value, reason, cfg, cfg_path))
    report.add(_file_line(paths))
    ck = probe_container_kernel(runner)
    report.add(_container_line(ck))
    if value is not None and in_container(paths):
        report.add('（コンテナの中で動いている。ホストの値はコンテナが動くカーネルの値である）')
    for path in later_overrides(paths):
        report.warn(f'{path} が 1 を書くので、再起動の後に 1 へ戻る')
    return report


def _manual_apply(paths: HostPaths, report: Report) -> None:
    marker_line, key_line = FILE_CONTENT.splitlines()
    report.add('次を打つと外せる:')
    report.add(f"    printf '%s\\n' '{marker_line}' '{key_line}' | sudo tee {paths.devbase_file} > /dev/null")
    report.add(f'    sudo sysctl -p {paths.devbase_file}')
    report.add('    （または端末で devbase userns apply を打つ）')


def _explain(paths: HostPaths, value: int, cfg: Optional[int], report: Report) -> None:
    after = '値が無い' if cfg is None else str(cfg)
    report.add('==> ホストの userns の制限を外します')
    if later_overrides(paths):
        # 後のファイルが 1 を書くので、再起動の後は 0 にならない (警告は書いた後に出す)
        report.add(f'    キー:     {KEY}（今 {value} → 0。再起動の後は後のファイルの値に戻る）')
        report.add(f'    ファイル: {paths.devbase_file}')
    else:
        report.add(f'    キー:     {KEY}（今 {value}・再起動の後 {after} → どちらも 0）')
        report.add(f'    ファイル: {paths.devbase_file}（再起動の後も 0 のまま）')
    report.add('    理由:     dev コンテナの中で bubblewrap（codex のサンドボックスなど）が '
               'user namespace を作るため')
    report.add('    緩むこと: AppArmor のプロファイルを持たない、ホストのすべての特権の無いプロセスが '
               'user namespace を作れるようになる')
    report.add(f'    戻し方:   devbase userns revert（または sudo rm {paths.devbase_file} '
               '&& sudo sysctl --system）')
    report.add('    止め方:   DEVBASE_SKIP_USERNS=1 で init を打つ')


def _install(runner: Runner, prefix: List[str], content: str, dest: Path
             ) -> subprocess.CompletedProcess:
    """一時ファイルに書き、``install -m 0644`` で ``dest`` へ置く"""
    fd, tmp = tempfile.mkstemp(prefix='devbase-userns-', suffix='.conf')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
        return _run(runner, prefix + ['install', '-m', '0644', tmp, str(dest)])
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _rollback(runner: Runner, prefix: List[str], paths: HostPaths,
              backup: Optional[str], report: Report) -> None:
    try:
        if backup is None:
            proc = _run(runner, prefix + ['rm', '-f', str(paths.devbase_file)])
        else:
            proc = _install(runner, prefix, backup, paths.devbase_file)
    except OSError as e:
        report.warn(f'置く前の状態へ戻せなかった: {paths.devbase_file}（{e}）')
        return
    if proc.returncode == 0:
        report.add(f'置く前の状態へ戻した: {paths.devbase_file}')
    else:
        report.warn(f'置く前の状態へ戻せなかった: {paths.devbase_file}'
                    f'（{_first_line(proc.stderr)}）')


def _warn_overrides(overrides: List[Path], report: Report) -> None:
    for path in overrides:
        report.warn(f'{path} が 1 を書くので、再起動の後に 1 へ戻る。'
                    'このファイルは devbase のものではないので触らない')


def apply(paths: Optional[HostPaths] = None, runner: Optional[Runner] = None,
          emit: Optional[Emit] = None) -> Report:
    """ホストの userns の制限を外す (設計の「処理の流れ」の図)"""
    paths, runner = _defaults(paths, runner)
    report = Report(emit=emit)
    value, reason = _read_runtime(paths)

    if value is None:
        report.add(_host_line(value, reason, None, None) + '。変えない')
        return _finish_with_container(report, value, runner)
    if in_container(paths):
        report.add('コンテナの中で動いているので、ホストの設定は変えない。'
                   'ホストで devbase userns apply を打つ')
        return _finish_with_container(report, value, runner)

    cfg, cfg_path = config_value(paths)
    exists, marked, content = _devbase_file_state(paths)
    devbase_writes_zero = exists and _file_value(paths.devbase_file) == 0

    if value == 0 and cfg == 0:
        report.add(f'ホストの {KEY} は既に 0（設定ファイルの上でも 0: {cfg_path}）')
        return _finish_with_container(report, value, runner)
    if value == 0 and devbase_writes_zero:
        report.add(f'ホストの {KEY} は既に 0（{paths.devbase_file} が 0 を書く）')
        overrides = later_overrides(paths)
        if overrides:
            _warn_overrides(overrides, report)
        elif cfg_path is not None:
            report.warn(f'設定ファイルの上では {cfg}（{cfg_path}）なので、再起動の後に {cfg} へ戻る')
        return _finish_with_container(report, value, runner)

    if exists and not marked:
        report.ok = False
        report.warn(f'{paths.devbase_file} は devbase のファイルではない（1 行目に {MARKER} が無い）'
                    'ので書き換えない。直すか消してから devbase userns apply を打つ')
        return _finish_with_container(report, value, runner)

    _explain(paths, value, cfg, report)
    priv = choose_privilege(paths, runner)
    if priv is Privilege.NONE:
        report.ok = False
        report.warn('root でなく、パスワード無しの sudo も端末も無いので、変えなかった')
        _manual_apply(paths, report)
        return _finish_with_container(report, value, runner)
    if priv is Privilege.SUDO_PROMPT and not _ask_sudo(runner, report):
        report.ok = False
        _manual_apply(paths, report)
        return _finish_with_container(report, value, runner)

    prefix = _prefix(priv)
    backup = content if exists else None
    proc = _install(runner, prefix, FILE_CONTENT, paths.devbase_file)
    if proc.returncode != 0:
        report.ok = False
        report.warn(f'{paths.devbase_file} を書けなかった: {_first_line(proc.stderr)}')
        return _finish_with_container(report, value, runner)

    proc = _run(runner, prefix + ['sysctl', '-p', str(paths.devbase_file)])
    after, _ = _read_runtime(paths)
    if proc.returncode != 0 or after != 0:
        report.ok = False
        why = (f'sysctl -p が失敗した: {_first_line(proc.stderr)}' if proc.returncode != 0
               else f'sysctl -p の後も値が {after}')
        report.warn(why)
        _rollback(runner, prefix, paths, backup, report)
        return _finish_with_container(report, value, runner)

    report.changed = True
    report.add(f'ホストの {KEY} を 0 にした（{paths.devbase_file}）')
    overrides = later_overrides(paths)
    if overrides:
        _warn_overrides(overrides, report)
    else:
        report.add('再起動の後も 0 のまま')
    return _finish_with_container(report, after, runner)


def _manual_revert(paths: HostPaths, restore: Optional[int], report: Report) -> None:
    report.add('次を打つと戻せる:')
    report.add(f'    sudo rm -f {paths.devbase_file}')
    if restore is not None:
        report.add(f'    sudo sysctl -w {KEY}={restore}')


def revert(paths: Optional[HostPaths] = None, runner: Optional[Runner] = None,
           emit: Optional[Emit] = None) -> Report:
    """目印のある devbase のファイルを消し、このキーだけを設定ファイルの上の値へ戻す"""
    paths, runner = _defaults(paths, runner)
    report = Report(emit=emit)
    exists, marked, _ = _devbase_file_state(paths)
    if not exists:
        report.add(f'{paths.devbase_file} は無い。何もしない')
        return report
    if not marked:
        report.ok = False
        report.warn(f'{paths.devbase_file} は devbase のファイルではない（1 行目に {MARKER} が無い）'
                    'ので消さない')
        return report

    value, _ = _read_runtime(paths)
    # 消した後に読む順で最後に当たるファイルの値。今の値はこのキーだけをこの値へ戻す
    restore, restore_path = config_value(paths, skip=paths.devbase_file)
    if value is None:
        restore = None
    report.add(f'消す: {paths.devbase_file}')

    priv = choose_privilege(paths, runner)
    if priv is Privilege.NONE:
        report.ok = False
        report.warn('root でなく、パスワード無しの sudo も端末も無いので、消さなかった')
        _manual_revert(paths, restore, report)
        return report
    if priv is Privilege.SUDO_PROMPT and not _ask_sudo(runner, report):
        report.ok = False
        _manual_revert(paths, restore, report)
        return report

    prefix = _prefix(priv)
    proc = _run(runner, prefix + ['rm', '-f', str(paths.devbase_file)])
    if proc.returncode != 0:
        report.ok = False
        report.warn(f'{paths.devbase_file} を消せなかった: {_first_line(proc.stderr)}')
        return report
    report.changed = True
    report.add(f'消した: {paths.devbase_file}')

    if value is None:
        report.add('ホストにキーが無いので、今の値は変えない')
    elif restore is None:
        report.add(f'設定ファイルの上の値が無いので、今の値（{value}）は変えない。'
                   '再起動の後はカーネルの既定になる')
    else:
        proc = _run(runner, prefix + ['sysctl', '-w', f'{KEY}={restore}'])
        if proc.returncode != 0:
            report.ok = False
            report.warn('ファイルは消したが、今の値を戻せなかった。再起動で反映される'
                        f'（{_first_line(proc.stderr)}）')
            return report
        report.add(f'{KEY} を設定ファイルの上の値 {restore}（{restore_path}）へ戻した')
    after, reason = _read_runtime(paths)
    report.add(f'ホスト: {after if after is not None else reason}')
    return report
