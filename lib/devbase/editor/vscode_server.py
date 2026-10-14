"""VS Code Server の本体の置き場をコンテナの外から書ける形にする (#463)

``~/.vscode-server/bin`` には全コンテナ共通のボリューム ``devbase_vscode-server-bin``
が付く。空のボリュームは root の所有で付き、#463 より前の entrypoint は
``~/.vscode-server`` だけを開発ユーザーへ直すため、``bin`` は root のままで
VS Code Server が本体を置けない (attach が失敗する)。

entrypoint の変更は base と派生イメージを建て直すまで効かないため、起動の後の処理
(``devbase up`` / ``scale`` / ``project post-start``) で ``docker exec -u 0`` を 1 回打ち、
``bin`` の所有者を ``~/.vscode-server`` の所有者へ揃える。揃っていれば何もしない
ため、新しい entrypoint のコンテナでは何も変えない。``chown`` は ``bin`` 1 つにだけ
打ち、再帰しない。パスは ``/home/ubuntu`` の絶対パスで書く (root の ``$HOME`` は
``/root``)。
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Callable, Optional

from devbase.log import get_logger

logger = get_logger(__name__)

#: 開発ユーザーのホーム。生成する compose のマウント先と同じ
HOME_ROOT = "/home/ubuntu"

# docker exec の待ち時間上限 (秒)。ローカルの docker への 1 コマンドなので即答する。
_EXEC_TIMEOUT = 10


def bin_fix_script(home_root: str = HOME_ROOT) -> str:
    """``bin`` の所有者を ``~/.vscode-server`` の所有者へ揃えるシェルの手順。

    ``bin`` が無ければ何もしない。所有者は GNU の ``stat -c`` で読み、使えなければ
    BSD の ``stat -f`` で読む (コンテナは Linux。テストは手元でも走らせる)。
    """
    server = shlex.quote(f"{home_root}/.vscode-server")
    bin_dir = shlex.quote(f"{home_root}/.vscode-server/bin")
    return (
        "owner_of() { stat -c %u:%g \"$1\" 2>/dev/null || stat -f %u:%g \"$1\"; }; "
        f"[ -d {bin_dir} ] || exit 0; "
        f"o=$(owner_of {server}) || exit 1; "
        f"b=$(owner_of {bin_dir}) || exit 1; "
        f"[ \"$b\" = \"$o\" ] || chown \"$o\" {bin_dir}"
    )


def prepare_bin_dir(container_name: str,
                    runner: Optional[Callable] = None) -> bool:
    """コンテナの ``~/.vscode-server/bin`` を開発ユーザーが書ける形にする。

    直せた・直す必要が無かったら True。docker の失敗・タイムアウトは False を
    返し、例外を出さない (起動の後の処理を止めない)。
    """
    run = runner or subprocess.run
    try:
        result = run(
            ["docker", "exec", "-u", "0", container_name,
             "sh", "-c", bin_fix_script()],
            capture_output=True, text=True, timeout=_EXEC_TIMEOUT, check=False,
        )
    except Exception as e:  # noqa: BLE001 - docker 不在/タイムアウト等で起動を倒さない
        logger.debug("VS Code Server の bin の所有者を直せませんでした (%s): %s",
                     container_name, e)
        return False
    if result.returncode != 0:
        logger.debug("VS Code Server の bin の所有者を直せませんでした (%s): %s",
                     container_name, (result.stderr or "").strip())
        return False
    return True
