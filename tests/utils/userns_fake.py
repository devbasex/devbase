"""userns のテストの部品: tmp のホストと、呼び出しを記録して振る舞いを写す runner (#533)

runner は実の sudo・sysctl・docker を起動しない。``install`` / ``rm`` / ``sysctl -p`` /
``sysctl -w`` を tmp のパスへの読み書きで写し、``docker`` は渡された筋書きで答える。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from devbase.utils import userns

DEVBASE_NAME = '60-devbase-userns.conf'


def make_paths(root: Path, *, value: Optional[int] = 1, tty: bool = False,
               container: bool = False) -> userns.HostPaths:
    """tmp の下にホストのパスを組む。``value`` が ``None`` なら proc のキーを作らない"""
    etc = root / 'etc' / 'sysctl.d'
    usr = root / 'usr' / 'lib' / 'sysctl.d'
    etc.mkdir(parents=True, exist_ok=True)
    usr.mkdir(parents=True, exist_ok=True)
    proc = root / 'proc' / 'apparmor_restrict_unprivileged_userns'
    proc.parent.mkdir(parents=True, exist_ok=True)
    if value is not None:
        proc.write_text(f'{value}\n')
    tty_path = root / 'tty'
    if tty:
        tty_path.write_text('')
    marker = root / '.dockerenv'
    if container:
        marker.write_text('')
    return userns.HostPaths(
        proc_key=proc,
        sysctl_dirs=(etc, root / 'run' / 'sysctl.d', root / 'usr' / 'local' / 'lib' / 'sysctl.d', usr),
        devbase_file=etc / DEVBASE_NAME,
        container_markers=(marker, root / 'run' / '.containerenv'),
        tty=tty_path,
    )


def write_conf(paths: userns.HostPaths, name: str, value: int, *, where: str = 'etc',
               marker: bool = False) -> Path:
    directory = paths.sysctl_dirs[0] if where == 'etc' else paths.sysctl_dirs[-1]
    path = directory / name
    head = f'{userns.MARKER} test\n' if marker else '# hand written\n'
    path.write_text(f'{head}{userns.KEY} = {value}\n')
    return path


def _done(cmd, rc=0, out='', err=''):
    return subprocess.CompletedProcess(cmd, rc, out, err)


@dataclass
class FakeHost:
    """呼び出しを記録し、tmp のホストへ振る舞いを写す runner"""

    paths: userns.HostPaths
    sudo_n: int = 1                 # ``sudo -n true`` の終了コード
    sudo_v: object = 0              # ``sudo -v`` の終了コード、または投げる例外
    sysctl_p_rc: int = 0
    sysctl_p_sets: Optional[int] = 0
    sysctl_w_rc: int = 0
    install_rc: int = 0
    image: bool = False
    container_value: Optional[str] = '1'
    docker_missing: bool = False
    docker_info_rc: int = 0
    security_options: str = '["name=apparmor","name=seccomp,profile=builtin"]'
    run_rc: int = 0
    run_err: str = ''
    calls: List[List[str]] = field(default_factory=list)
    kwargs: List[Dict] = field(default_factory=list)
    events: Optional[List[str]] = None
    hooks: Dict[str, Callable] = field(default_factory=dict)

    def __call__(self, cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        self.kwargs.append(kwargs)
        if self.events is not None:
            self.events.append('run: ' + ' '.join(cmd))
        args = cmd[1:] if cmd and cmd[0] == 'sudo' and cmd[1:2] not in (['-n'], ['-v']) else cmd
        if args[:3] == ['sudo', '-n', 'true']:
            return _done(cmd, self.sudo_n)
        if args[:2] == ['sudo', '-v']:
            if isinstance(self.sudo_v, BaseException) or (
                    isinstance(self.sudo_v, type) and issubclass(self.sudo_v, BaseException)):
                raise self.sudo_v
            return _done(cmd, self.sudo_v)
        if args[0] == 'install':
            if self.install_rc != 0:
                return _done(cmd, self.install_rc, err='install: Read-only file system\n')
            shutil.copyfile(args[-2], args[-1])
            return _done(cmd)
        if args[:2] == ['rm', '-f']:
            Path(args[2]).unlink(missing_ok=True)
            return _done(cmd)
        if args[:2] == ['sysctl', '-p']:
            if self.sysctl_p_rc != 0:
                return _done(cmd, self.sysctl_p_rc, err='sysctl: permission denied on key\n')
            if self.sysctl_p_sets is not None:
                self.paths.proc_key.write_text(f'{self.sysctl_p_sets}\n')
            return _done(cmd)
        if args[:2] == ['sysctl', '-w']:
            if self.sysctl_w_rc != 0:
                return _done(cmd, self.sysctl_w_rc, err='sysctl: denied\n')
            self.paths.proc_key.write_text(args[2].split('=', 1)[1] + '\n')
            return _done(cmd)
        if args[0] == 'docker':
            if self.docker_missing:
                raise FileNotFoundError('docker')
            if args[1:3] == ['image', 'inspect']:
                return _done(cmd, 0 if self.image else 1, out='sha256:abc\n' if self.image else '',
                             err='' if self.image else 'Error: No such image\n')
            if args[1] == 'run':
                if self.run_rc != 0:
                    return _done(cmd, self.run_rc, err=self.run_err)
                if self.container_value is None:
                    return _done(cmd, 1, err='cat: /proc/sys/kernel/apparmor_restrict_unprivileged_userns:'
                                             ' No such file or directory\n')
                return _done(cmd, 0, out=self.container_value + '\n')
            if args[1] == 'info':
                if self.docker_info_rc != 0:
                    return _done(cmd, self.docker_info_rc, err='Cannot connect to the Docker daemon\n')
                return _done(cmd, 0, out=self.security_options + '\n')
        raise AssertionError(f'想定していない呼び出し: {cmd}')

    def ran(self, *words: str) -> bool:
        return any(all(w in c for w in words) for c in self.calls)

    def non_docker(self) -> List[List[str]]:
        return [c for c in self.calls if c[0] != 'docker']
