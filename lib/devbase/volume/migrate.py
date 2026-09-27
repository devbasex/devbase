"""旧既定のボリュームの中身をグループのボリュームへ写す (#315 決定 6)

#315 より前は、グループを宣言していないプロジェクトが ``devbase_home_default`` (旧既定の
ボリューム) を使っていた。``default`` は予約語になったため、その中身を利用者が指定した
グループのボリューム ``devbase_home_<group>`` へ写す。

- 元は ``devbase_home_default`` に固定する (指定させない)。元は読み取り専用でマウントし、
  書き換えず、消さない (I7)。元を消すのは利用者の手である
- 先が存在して空でない・元が無い・元か先をマウントした稼働中のコンテナがある、のどれかなら
  先を作らず、何も書かずに止まる。先の有無は ``docker volume inspect`` で先に確かめ、
  無ければ空と見なす (ヘルパーで無い先をマウントすると docker が先を暗黙に作るため)
- 写すのは Docker daemon の上のヘルパーのコンテナの中で、``cp -a`` (持ち主・権限・
  シンボリックリンクをリンクのまま) で写す。写した後に元と先のエントリの数を照らす
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from devbase.errors import DevbaseError
from devbase.log import get_logger
from devbase.volume.manager import (
    LEGACY_GROUP_VOLUME,
    VolumeManager,
    get_group_volume,
    validate_account_group,
)

logger = get_logger(__name__)

#: 先が空でないときに挙げる最上位のエントリの数
_LISTED_ENTRIES = 5
#: エントリの数え方 (元と先で同じ式を使う。最上位の自分自身は数えない)。
#: パイプにすると終了コードが ``wc`` のものになり、``find`` が途中で失敗しても部分の数を
#: 正常値として返すため、一時ファイルを挟んで ``find`` の失敗を伝える
_COUNT_SCRIPT = 'find "$1" -mindepth 1 > /tmp/entries && wc -l < /tmp/entries'


class VolumeMigrationError(DevbaseError):
    """移行の前提を満たさない、または写し損ねた"""


@dataclass
class MigrationCheck:
    """前提の検査の結果"""

    source: str
    target: str
    #: 止める理由 (空なら写せる)
    problems: List[str] = field(default_factory=list)
    #: 先のボリュームが既にあるか
    target_exists: bool = False
    #: 元のエントリの数 (検査を通ったときだけ数える)
    source_count: Optional[int] = None

    @property
    def ok(self) -> bool:
        return not self.problems


Runner = Callable[..., subprocess.CompletedProcess]


class VolumeMigration:
    """``devbase_home_default`` → ``devbase_home_<group>`` の検査と実行"""

    def __init__(self, devbase_root: Path, group: str, *,
                 runner: Optional[Runner] = None,
                 image_provider: Optional[Callable[[Path], str]] = None):
        #: 検証を通したグループ名。``default`` などの予約語はここで弾く (I3)
        self.group = validate_account_group(group)
        self.devbase_root = Path(devbase_root)
        self.source = LEGACY_GROUP_VOLUME
        self.target = get_group_volume(self.group)
        self._run = runner
        self._image_provider = image_provider
        self._image: Optional[str] = None

    # -- docker の呼び出し -------------------------------------------------

    def _docker(self, *args: str) -> subprocess.CompletedProcess:
        # 既定の runner は呼ぶときに引く (定義時に束ねると差し替えが効かない)
        run = self._run if self._run is not None else subprocess.run
        try:
            return run(['docker', *args], capture_output=True, text=True, check=False)
        except FileNotFoundError as e:
            raise VolumeMigrationError(f"docker コマンドが見つかりません: {e}") from e

    def _image_name(self) -> str:
        if self._image is None:
            provider = self._image_provider
            if provider is None:
                from devbase.snapshot.manager import ensure_snapshot_image

                provider = ensure_snapshot_image
            self._image = provider(self.devbase_root)
        return self._image

    def _helper(self, mounts: List[str], *command: str) -> subprocess.CompletedProcess:
        args = ['run', '--rm', '--network', 'none']
        for mount in mounts:
            args += ['-v', mount]
        return self._docker(*args, self._image_name(), *command)

    def _volume_exists(self, name: str) -> bool:
        return self._docker('volume', 'inspect', name).returncode == 0

    def _running_users(self, name: str) -> List[str]:
        result = self._docker('ps', '--filter', f'volume={name}', '--format', '{{.Names}}')
        if result.returncode != 0:
            raise VolumeMigrationError(
                f"稼働中のコンテナを確かめられません: {(result.stderr or '').strip()}")
        return [line for line in (result.stdout or '').splitlines() if line.strip()]

    def _top_entries(self, name: str) -> List[str]:
        result = self._helper([f'{name}:/vol:ro'], 'ls', '-A', '/vol')
        if result.returncode != 0:
            raise VolumeMigrationError(
                f"{name} の中身を確かめられません: {(result.stderr or '').strip()}")
        # 名前の中身で捨てない (空白だけの名前も 1 件)。何かあれば出力は空でない
        return (result.stdout or '').splitlines()

    def _count(self, name: str) -> int:
        result = self._helper([f'{name}:/vol:ro'], 'sh', '-c', _COUNT_SCRIPT, 'sh', '/vol')
        if result.returncode != 0:
            raise VolumeMigrationError(
                f"{name} のエントリを数えられません: {(result.stderr or '').strip()}")
        try:
            return int((result.stdout or '').strip())
        except ValueError as e:
            raise VolumeMigrationError(
                f"{name} のエントリの数を読めません: {result.stdout!r}") from e

    # -- 検査と実行 ---------------------------------------------------------

    def _running_problems(self) -> List[str]:
        problems = []
        for name in (self.source, self.target):
            users = self._running_users(name)
            if users:
                problems.append(
                    f"{name} を使うコンテナが動いています: {', '.join(users)}。"
                    "devbase down で止めてから打ち直してください")
        return problems

    def check(self) -> MigrationCheck:
        """前提を確かめる。先へは書かない (無い先を作らない)"""
        result = MigrationCheck(source=self.source, target=self.target)
        info = self._docker('info', '--format', '{{.ServerVersion}}')
        if info.returncode != 0:
            result.problems.append(
                f"Docker に届きません: {(info.stderr or '').strip()}")
            return result
        if not self._volume_exists(self.source):
            result.problems.append(
                f"元のボリューム {self.source} がありません。移すものはありません")
            return result
        result.problems += self._running_problems()
        result.target_exists = self._volume_exists(self.target)
        if result.target_exists:
            entries = self._top_entries(self.target)
            if entries:
                shown = ', '.join(entries[:_LISTED_ENTRIES])
                more = f" ほか {len(entries) - _LISTED_ENTRIES} 件" if len(entries) > _LISTED_ENTRIES else ''
                result.problems.append(
                    f"移し先 {self.target} が空ではありません ({shown}{more})。"
                    f"中身を確かめてから docker volume rm {self.target} で消し、打ち直してください")
        if result.ok:
            result.source_count = self._count(self.source)
        return result

    def run(self) -> int:
        """写して、写したエントリの数を返す。前提を満たさなければ何も書かずに止める"""
        checked = self.check()
        if not checked.ok:
            raise VolumeMigrationError('\n'.join(checked.problems))
        if not checked.target_exists:
            if not VolumeManager().create_volume(self.target):
                raise VolumeMigrationError(f"移し先 {self.target} を作れません")
        # 検査からここまでの間に起動されたコンテナを拾う (競合そのものは防がない)
        problems = self._running_problems()
        if problems:
            raise VolumeMigrationError('\n'.join(problems))
        copied = self._helper(
            [f'{self.source}:/src:ro', f'{self.target}:/dst'],
            'cp', '-a', '/src/.', '/dst/')
        if copied.returncode != 0:
            raise VolumeMigrationError(
                f"コピーに失敗しました: {(copied.stderr or '').strip()}。"
                f"元 {self.source} は変わっていません。"
                f"docker volume rm {self.target} で先を消してから打ち直してください")
        source_count = self._count(self.source)
        target_count = self._count(self.target)
        if source_count != target_count:
            raise VolumeMigrationError(
                f"写した後のエントリの数が合いません (元 {source_count} 件・先 {target_count} 件)。"
                f"元 {self.source} は変わっていません。"
                f"docker volume rm {self.target} で先を消してから打ち直してください")
        return target_count
