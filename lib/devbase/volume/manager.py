"""Volume management functions for devbase"""

import os
import re
import subprocess
from typing import Optional

from devbase.env import keys
from devbase.errors import DevbaseError, DockerError
from devbase.log import get_logger

logger = get_logger("devbase.volume.manager")

# 共有ボリューム名のプレフィックス
SHARED_VOLUME_PREFIX = "devbase_home_"
WORK_VOLUME_PREFIX = "devbase_work_"
# 全コンテナで共有するホームディレクトリボリューム
HOME_UBUNTU_VOLUME = "devbase_home_ubuntu"
#: コンテナの中の利用者 ubuntu のホーム
CONTAINER_HOME = '/home/ubuntu'

# --- アカウントグループ (PLAN39 / #315) --------------------------------------
# アカウントグループは「使用する Google / AWS アカウントの単位」。グループごとに
# devbase_home_<group> を作り、/persistent/group としてマウントする。認証情報や
# 会話履歴のようにテナントへ紐づくデータ (分類 B) の置き場になる。
# グループは既定の値を持たない。プロジェクトの env での宣言が必須である (#315)。
#: #315 より前に宣言の無いプロジェクトが使っていたボリューム (旧既定のボリューム)。
#: ボリュームの移行の元で、スナップショットの系列にも残る。新しくは作らない
LEGACY_GROUP_VOLUME = "devbase_home_default"
# Docker のボリューム名として使える文字種
_GROUP_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
# 数字のみは devbase_home_<index> (get_volume_for_index) と同じ名前になる
_NUMERIC_NAME_RE = re.compile(r"^[0-9]+$")
# 共通ボリューム devbase_home_ubuntu と同じ名前になる
_SHARED_RESERVED_GROUP = "ubuntu"
#: 旧既定のグループ名。予約語で、グループ名にも置き場の読み替えにも使えない (#315)
LEGACY_GROUP_NAME = "default"

# --- VS Code Server (PLAN36 / #463) -----------------------------------------
# 本体 (~/.vscode-server/bin/<commit>) だけを全コンテナで 1 本のボリュームに置く。
# 版ごとに中身が変わらないため共有して壊れず、プロジェクトとインスタンスの数だけ
# 同じ版を重ねて持たずに済む。版の数の上限はコンテナの起動 (entrypoint) が保つ。
# extensions/・extensionsCache/・data/ はボリュームに置かず、コンテナの書き込み
# レイヤーに残す (#463 の利用者の決定)。
#
# 名前は旧形式 (devbase_vscode_<project>_<index>) の接頭辞 devbase_vscode_ と
# 15 文字目の "-" で必ず違い、旧形式の案内が共通のボリュームを選ばない。
VSCODE_SERVER_VOLUME = "devbase_vscode-server-bin"
#: VS Code Server の置き場。生成する compose のマウント先と、起動の後の処理が
#: 所有者を直す先の両方がこれを使う
VSCODE_SERVER_TARGET = f'{CONTAINER_HOME}/.vscode-server'
#: 本体の置き場。:data:`VSCODE_SERVER_VOLUME` を付ける先
VSCODE_BIN_TARGET = f'{VSCODE_SERVER_TARGET}/bin'
#: #463 より前の、~/.vscode-server を丸ごと持つコンテナごとのボリュームの接頭辞
LEGACY_VSCODE_VOLUME_PREFIX = "devbase_vscode_"

# --- Dev Containers の共有の置き場 (#507) ------------------------------------
# Dev Containers 拡張は、コンテナに名前が "vscode" のボリュームが付いていれば、
# 拡張のキャッシュと本体の新しい版をその中 (<マウント先>/vscode-server) に置く。
# 付いていなければ、VS Code を動かしている手元の端末のキャッシュを使い、CPU の
# 種類を見ずに拡張を送る。名前は Dev Containers の決め打ちで、devbase_ で始められない。
VSCODE_CACHE_VOLUME = "vscode"
#: :data:`VSCODE_CACHE_VOLUME` を付ける先。Dev Containers が自分で作るコンテナと同じ
VSCODE_CACHE_TARGET = '/vscode'
#: 共有の置き場の中で Dev Containers が使う置き場。これが無いコンテナでは bin のリンクを片付けない
VSCODE_CACHE_SERVER_DIR = f'{VSCODE_CACHE_TARGET}/vscode-server'
#: 共有の置き場の中の拡張のキャッシュ。起動の後の処理が片付ける
VSCODE_CACHE_EXTENSIONS_DIR = f'{VSCODE_CACHE_SERVER_DIR}/extensionsCache'
_LEGACY_VSCODE_VOLUME_RE = re.compile(r"^devbase_vscode_.+_[0-9]+$")


def validate_account_group(name: str) -> str:
    """アカウントグループ名を検証して返す (前後の空白は外す)。グループ名の規則の唯一の置き場。

    名前はそのままボリューム名 ``devbase_home_<group>`` と置き場のパスの 1 要素になるため、
    次を弾く。(b)〜(d) は (a) を通過してしまうので、正規表現とは別のチェックとして持つ。

    (a) 空、または Docker のボリューム名にできない文字列
    (b) 予約語 ``ubuntu`` (共通ボリューム ``devbase_home_ubuntu`` と衝突)
    (c) 予約語 ``default`` (旧既定のボリューム ``devbase_home_default`` と衝突。#315)
    (d) 数字のみ (``devbase_home_<index>`` と衝突)

    Raises:
        DevbaseError: グループ名が使えない場合
    """
    name = (name or "").strip()
    if not name:
        raise DevbaseError(f"{keys.DEVBASE_ACCOUNT_GROUP} が空です")
    if not _GROUP_NAME_RE.match(name):
        raise DevbaseError(
            f"{keys.DEVBASE_ACCOUNT_GROUP} が不正です: '{name}'。"
            "Docker のボリューム名に使える文字 (英数字・ドット・ハイフン・"
            "アンダースコア、先頭は英数字) だけを使ってください"
        )
    if name == _SHARED_RESERVED_GROUP:
        raise DevbaseError(
            f"{keys.DEVBASE_ACCOUNT_GROUP} に予約語は使えません: '{name}'。"
            f"共通ボリューム {HOME_UBUNTU_VOLUME} と同じ名前になります"
        )
    if name == LEGACY_GROUP_NAME:
        from devbase.env.groups import EXAMPLE_GROUP

        raise DevbaseError(
            f"{keys.DEVBASE_ACCOUNT_GROUP} に予約語は使えません: '{name}'。"
            f"移し先のグループ名 ({EXAMPLE_GROUP} / personal など) を書いてください。"
            f"{LEGACY_GROUP_VOLUME} の中身は "
            "devbase project migrate-volume --to <グループ> で移せます"
        )
    if _NUMERIC_NAME_RE.match(name):
        raise DevbaseError(
            f"{keys.DEVBASE_ACCOUNT_GROUP} に数字だけの名前は使えません: "
            f"'{name}'。インスタンス番号のボリューム "
            f"{SHARED_VOLUME_PREFIX}<index> と同じ名前になります"
        )
    return name


def resolve_account_group(group: Optional[str] = None) -> str:
    """アカウントグループ名を引数か ``DEVBASE_ACCOUNT_GROUP`` から決めて検証する。

    既定の値は持たない (#315)。``devbase up`` / ``scale`` は起動の先頭でプロジェクトの
    宣言を読み、未設定なら環境変数へ置く (``commands/container.py``) ため、起動の経路では
    ここで値が決まる。規則は :func:`validate_account_group` に任せる。

    Raises:
        GroupDeclarationError: 引数も環境変数も空の場合
        DevbaseError: グループ名が使えない場合
    """
    raw = group if group is not None else os.environ.get(
        keys.DEVBASE_ACCOUNT_GROUP, "")
    if not (raw or "").strip():
        from devbase.env.groups import GroupDeclarationError

        raise GroupDeclarationError(
            f"アカウントグループが決まっていません。projects/<name>/env に "
            f"{keys.DEVBASE_ACCOUNT_GROUP}=<グループ> を書いてください"
        )
    return validate_account_group(raw)


def get_group_volume(group: Optional[str] = None) -> str:
    """アカウントグループのボリューム名 (``devbase_home_<group>``) を返す。

    検証を迂回する経路を作らないため、名前の解決は必ず
    :func:`resolve_account_group` を通す。
    """
    return f"{SHARED_VOLUME_PREFIX}{resolve_account_group(group)}"


def find_legacy_vscode_volumes() -> list[str]:
    """旧形式の VS Code Server ボリュームの名前を名前の順で返す (#463)。

    ``docker volume ls`` の名前を読むだけで、消す・付ける・中身を読むことはしない
    (C4)。一覧が取れなければ空を返す (案内のためだけに使い、起動を止めない)。
    """
    try:
        result = subprocess.run(
            ['docker', 'volume', 'ls', '--format', '{{.Name}}'],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as e:  # noqa: BLE001 - 案内のためだけの一覧。失敗は空として扱う
        logger.debug("Failed to list volumes: %s", e)
        return []
    if result.returncode != 0:
        logger.debug("Failed to list volumes: %s", result.stderr)
        return []
    names = {line.strip() for line in result.stdout.splitlines()}
    return sorted(n for n in names if _LEGACY_VSCODE_VOLUME_RE.match(n))


class VolumeManager:
    """Manages Docker volumes for devbase projects"""

    def _volume_exists(self, volume_name: str) -> bool:
        """Check if Docker volume exists"""
        try:
            result = subprocess.run(
                ['docker', 'volume', 'inspect', volume_name],
                capture_output=True,
                text=True,
                check=False
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning("Failed to check volume %s: %s", volume_name, e)
            return False

    def create_volume(self, volume_name: str) -> bool:
        """Docker のボリュームを作る。グループのボリュームを作る唯一の入口 (#315)"""
        try:
            subprocess.run(
                ['docker', 'volume', 'create', volume_name],
                capture_output=True,
                text=True,
                check=True
            )
            return True
        except subprocess.CalledProcessError as e:
            logger.error("Failed to create volume %s: %s", volume_name, e.stderr)
            return False

    def get_volume_for_index(self, index: int) -> str:
        """
        Get shared volume name for specified index

        Args:
            index: Container index (1-based)

        Returns:
            Shared volume name (devbase_home_{index})
        """
        return f"{SHARED_VOLUME_PREFIX}{index}"

    def get_work_volume_for_index(self, index: int) -> str:
        """
        Get work volume name for specified index

        Args:
            index: Container index (1-based)

        Returns:
            Work volume name (devbase_work_{index})
        """
        return f"{WORK_VOLUME_PREFIX}{index}"

    def get_ai_volume_for_index(self, index: int) -> str:
        """
        Get AI settings volume name for specified index

        Note: All containers share the same home directory volume (devbase_home_ubuntu)
        regardless of index.

        Args:
            index: Container index (1-based, unused)

        Returns:
            Home ubuntu volume name (devbase_home_ubuntu)
        """
        return HOME_UBUNTU_VOLUME

    def ensure_volumes(self, scale: int, group: Optional[str] = None) -> None:
        """
        Ensure required volumes exist for the specified scale

        Creates volumes:
        - devbase_home_ubuntu: Shared home directory for all containers
        - devbase_home_{group}: Per-account-group directory (PLAN39)
        - devbase_work_{i}: Project work directory per instance
        - devbase_vscode-server-bin: VS Code Server の本体, shared by all
          containers (#463)
        - vscode: Dev Containers の共有の置き場, shared by all containers (#507)

        Args:
            scale: Number of container instances
            group: Account group name (省略時は DEVBASE_ACCOUNT_GROUP。既定の値は無い)
        """
        logger.info("Ensuring volumes for %d container(s)", scale)

        # グループ名の検証は Docker を触る前に済ませる。あとに置くと、名前が
        # 不正なだけの入力エラーでも共有ボリュームが作られてから失敗して
        # しまい、Docker の状態が変わってしまう。
        group_volume = get_group_volume(group)

        # Ensure shared home directory volume (once for all containers)
        self._ensure_volume(HOME_UBUNTU_VOLUME, "shared home")

        # Ensure account group volume (shared by all containers of the group)
        self._ensure_volume(group_volume, "account group")

        # Ensure VS Code Server binary volume (once for all containers, #463)
        self._ensure_volume(VSCODE_SERVER_VOLUME, "VS Code Server")

        # Ensure the Dev Containers cache volume (once for all containers, #507)
        self._ensure_volume(VSCODE_CACHE_VOLUME, "Dev Containers cache")

        # Create or verify work volumes for each instance
        for i in range(1, scale + 1):
            self._ensure_volume(self.get_work_volume_for_index(i))

    def _ensure_volume(self, volume_name: str,
                       label: Optional[str] = None) -> None:
        """ボリュームが無ければ作る。label はログで名前の後ろに添える種類

        Raises:
            DockerError: ボリュームを作れなかった場合
        """
        if self._volume_exists(volume_name):
            if label:
                logger.info("  %s (%s, exists)", volume_name, label)
            else:
                logger.info("  %s (exists)", volume_name)
            return
        if label:
            logger.info("  Creating %s (%s)...", volume_name, label)
        else:
            logger.info("  Creating %s...", volume_name)
        if not self.create_volume(volume_name):
            raise DockerError(f"Failed to create volume {volume_name}")


def ensure_volumes(scale: int, group: Optional[str] = None) -> None:
    """
    Ensure required shared volumes exist for the specified scale

    All projects share the same home volume (devbase_home_ubuntu) and the
    volume of their account group (devbase_home_<group>); work volumes are
    per container index. The VS Code Server binary volume is shared by all
    containers (#463).

    Args:
        scale: Number of container instances
        group: Account group name (省略時は DEVBASE_ACCOUNT_GROUP。既定の値は無い)
    """
    manager = VolumeManager()
    manager.ensure_volumes(scale, group)


def get_volume_for_index(index: int, project_name: str = None) -> str:
    """
    Get shared volume name for specified index

    Args:
        index: Container index (1-based)
        project_name: Unused, kept for backward compatibility

    Returns:
        Shared volume name (devbase_home_{index})
    """
    return f"{SHARED_VOLUME_PREFIX}{index}"


def get_work_volume_for_index(index: int, project_name: str = None) -> str:
    """
    Get work volume name for specified index

    Args:
        index: Container index (1-based)
        project_name: Unused, kept for backward compatibility

    Returns:
        Work volume name (devbase_work_{index})
    """
    return f"{WORK_VOLUME_PREFIX}{index}"


def get_ai_volume_for_index(index: int, project_name: str = None) -> str:
    """
    Get AI settings volume name for specified index

    Note: All containers share the same home directory volume (devbase_home_ubuntu)
    regardless of index.

    Args:
        index: Container index (1-based, unused)
        project_name: Unused, kept for backward compatibility

    Returns:
        Home ubuntu volume name (devbase_home_ubuntu)
    """
    return HOME_UBUNTU_VOLUME
