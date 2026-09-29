"""Volume management functions for devbase"""

import os
import re
import subprocess
from typing import Optional

from devbase.env import keys
from devbase.errors import DevbaseError, DockerError
from devbase.log import get_logger
from devbase.utils.config import get_project_name

logger = get_logger("devbase.volume.manager")

# 共有ボリューム名のプレフィックス
SHARED_VOLUME_PREFIX = "devbase_home_"
WORK_VOLUME_PREFIX = "devbase_work_"
# 全コンテナで共有するホームディレクトリボリューム
HOME_UBUNTU_VOLUME = "devbase_home_ubuntu"

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

# --- VS Code Server (PLAN36) -------------------------------------------------
# ~/.vscode-server はコンテナの書き込みレイヤ上にあり、devbase up の down → up で
# 消える。本体 (bin/<commit>) だけで 644MB あり、attach のたびに再取得が走る。
# コンテナ 1 つにつき 1 本の named volume を割り当てて再作成をまたいで保つ。
#
# 共有せずコンテナ単位にするのは、VS Code Server が「1 マシン 1 セット」の状態
# (data/Machine/.connection-token-<commit>・各種 marker・ログ) を持つため。
# 複数コンテナが同時に書くと接続トークンを奪い合う。
VSCODE_VOLUME_PREFIX = "devbase_vscode_"
# プロジェクト名のうち Docker のボリューム名に使えない文字
_VOLUME_UNSAFE_RE = re.compile(r"[^a-zA-Z0-9._-]")
# プロジェクト名が決まらないときの VS Code Server のボリューム名の一部。グループではない
_FALLBACK_PROJECT_NAME = "default"


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
        raise DevbaseError(
            f"{keys.DEVBASE_ACCOUNT_GROUP} に予約語は使えません: '{name}'。"
            "移し先のグループ名 (acme / personal など) を書いてください。"
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


def resolve_project_name(project_name: Optional[str] = None) -> str:
    """VS Code Server ボリュームに使うプロジェクト名を解決する。

    省略時は :func:`devbase.utils.config.get_project_name` (
    ``COMPOSE_PROJECT_NAME`` → カレントディレクトリ名) に委ねる。``devbase up``
    は同じ関数でプロジェクト名を決めてからボリュームを作る (``commands/container.py``)
    ため、ボリュームを作る側と生成 compose の双方がここを通れば必ず同じ名前になる。
    解決経路が 2 つあると、作った名前とマウントする名前がずれる。
    """
    name = (project_name or get_project_name() or "").strip()
    return name or _FALLBACK_PROJECT_NAME


def normalize_volume_component(name: str) -> str:
    """ボリューム名の一部として使えるようにプロジェクト名を正規化する。

    ``group/project`` のようにディレクトリ名由来の ``/`` が混じると
    ``docker volume create`` が弾く。使えない文字は ``_`` へ置き換える。

    置き換えの結果 ``a/b`` と ``a_b`` は同じ名前になるが、プロジェクト名は
    Compose のプロジェクト名としても使われる (``COMPOSE_PROJECT_NAME``) ため、
    実際にこの衝突が起きる名前は Docker 側で先に弾かれる。
    """
    normalized = _VOLUME_UNSAFE_RE.sub("_", name)
    return normalized or _FALLBACK_PROJECT_NAME


def get_vscode_volume_for(project_name: Optional[str], index: int) -> str:
    """VS Code Server ボリューム名 (``devbase_vscode_<project>_<index>``) を返す。

    Args:
        project_name: プロジェクト名 (省略時は ``COMPOSE_PROJECT_NAME``)
        index: インスタンス番号 (1 始まり)
    """
    project = normalize_volume_component(resolve_project_name(project_name))
    return f"{VSCODE_VOLUME_PREFIX}{project}_{index}"


class VolumeManager:
    """Manages Docker volumes for devbase projects"""

    def __init__(self, project_name: str = None):
        """
        Initialize VolumeManager

        Args:
            project_name: Project name. VS Code Server ボリュームの名前にだけ
                使う (PLAN36)。省略時は ``COMPOSE_PROJECT_NAME`` から解決する
        """
        self.project_name = project_name

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
        - devbase_vscode_{project}_{i}: VS Code Server state per instance (PLAN36)

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
        if self._volume_exists(HOME_UBUNTU_VOLUME):
            logger.info("  %s (shared home, exists)", HOME_UBUNTU_VOLUME)
        else:
            logger.info("  Creating %s (shared home)...", HOME_UBUNTU_VOLUME)
            if not self.create_volume(HOME_UBUNTU_VOLUME):
                raise DockerError(f"Failed to create volume {HOME_UBUNTU_VOLUME}")

        # Ensure account group volume (shared by all containers of the group)
        if self._volume_exists(group_volume):
            logger.info("  %s (account group, exists)", group_volume)
        else:
            logger.info("  Creating %s (account group)...", group_volume)
            if not self.create_volume(group_volume):
                raise DockerError(f"Failed to create volume {group_volume}")

        # Create or verify work volumes for each instance
        for i in range(1, scale + 1):
            work_volume = self.get_work_volume_for_index(i)

            # Ensure work volume
            if self._volume_exists(work_volume):
                logger.info("  %s (exists)", work_volume)
            else:
                logger.info("  Creating %s...", work_volume)
                if not self.create_volume(work_volume):
                    raise DockerError(f"Failed to create volume {work_volume}")

            # Ensure VS Code Server volume (PLAN36)
            vscode_volume = get_vscode_volume_for(self.project_name, i)
            if self._volume_exists(vscode_volume):
                logger.info("  %s (VS Code Server, exists)", vscode_volume)
            else:
                logger.info("  Creating %s (VS Code Server)...", vscode_volume)
                if not self.create_volume(vscode_volume):
                    raise DockerError(
                        f"Failed to create volume {vscode_volume}")


def ensure_volumes(scale: int, project_name: str = None,
                   group: Optional[str] = None) -> None:
    """
    Ensure required shared volumes exist for the specified scale

    All projects share the same home volume (devbase_home_ubuntu) and the
    volume of their account group (devbase_home_<group>); work volumes are
    per container index. VS Code Server volumes are per project and index
    (PLAN36).

    Args:
        scale: Number of container instances
        project_name: Project name (default: resolved from COMPOSE_PROJECT_NAME)
        group: Account group name (省略時は DEVBASE_ACCOUNT_GROUP。既定の値は無い)
    """
    manager = VolumeManager(project_name)
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
