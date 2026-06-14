"""エディタ動作設定コレクター (VS Code 自動オープン)。

``devbase up`` / ``devbase list`` 後に dev コンテナへ接続した VS Code を自動で
開くか (``DEVBASE_OPEN_EDITOR``) を ``devbase env init`` 時に対話的に設定する。
既定は有効 (``1``)。プロジェクト個別の ``env`` で ``DEVBASE_OPEN_EDITOR=0`` を
指定すれば、このグローバル既定を上書きして個別に無効化できる
(プロジェクト env はグローバル ``.env`` より後に読まれるため優先される)。
"""

from devbase.log import get_logger
from devbase.env import keys
from devbase.env.store import EnvFile, safe_input
from devbase.env.collector import Collector

logger = get_logger(__name__)

# 応答の真偽解釈 (大小無視)。空入力は default に倒れる (safe_input が処理)。
_YES = {"1", "y", "yes", "true", "on"}
_NO = {"0", "n", "no", "false", "off"}


def _normalize(answer: str, default: str) -> str:
    """ユーザー応答を ``"1"`` / ``"0"`` に正規化する。未知の値は default。"""
    a = answer.strip().lower()
    if a in _YES:
        return "1"
    if a in _NO:
        return "0"
    return default


def collect_open_editor(env_file: EnvFile) -> None:
    """``DEVBASE_OPEN_EDITOR`` を対話的に設定する (既定: ``1`` = 有効)。

    既存値 (``0`` / ``1``) があればそれを既定として提示し、空入力で維持する。
    非対話 (EOF) 環境では default が確定する。
    """
    existing = env_file.get(keys.DEVBASE_OPEN_EDITOR)
    default = existing if existing in ("0", "1") else "1"
    answer = safe_input(
        f"{keys.DEVBASE_OPEN_EDITOR}: devbase up/list 後に VS Code を自動オープンしますか? "
        f"[Y/n] (既定={default}): ",
        default,
    )
    value = _normalize(answer, default)
    env_file.set(keys.DEVBASE_OPEN_EDITOR, value)
    logger.info("%s = %s", keys.DEVBASE_OPEN_EDITOR, value)


COLLECTOR = Collector(
    name="editor",
    display_name="VS Code 自動オープン (DEVBASE_OPEN_EDITOR)",
    collect_fn=collect_open_editor,
)
