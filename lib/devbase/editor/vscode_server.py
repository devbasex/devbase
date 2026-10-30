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
from devbase.volume.manager import (
    VSCODE_BIN_TARGET,
    VSCODE_CACHE_EXTENSIONS_DIR,
    VSCODE_CACHE_SERVER_DIR,
    VSCODE_SERVER_TARGET,
)

logger = get_logger(__name__)

# docker exec の待ち時間上限 (秒)。ローカルの docker への 1 コマンドなので即答する。
_EXEC_TIMEOUT = 10
# 片付けの待ち時間上限 (秒)。拡張のキャッシュのファイルを消すだけなので短い。
_PRUNE_TIMEOUT = 30


def bin_fix_script(home_root: Optional[str] = None) -> str:
    """``bin`` の所有者を ``~/.vscode-server`` の所有者へ揃えるシェルの手順。

    ``bin`` が無ければ何もしない。所有者は GNU の ``stat -c`` で読み、使えなければ
    BSD の ``stat -f`` で読む (コンテナは Linux。テストは手元でも走らせる)。
    パスの既定は compose のマウント先と同じ定数から組む。``home_root`` はテストが
    手元のディレクトリで試すためだけに渡す。
    """
    if home_root is None:
        server_path, bin_path = VSCODE_SERVER_TARGET, VSCODE_BIN_TARGET
    else:
        server_path = f"{home_root}/.vscode-server"
        bin_path = f"{home_root}/.vscode-server/bin"
    server = shlex.quote(server_path)
    bin_dir = shlex.quote(bin_path)
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


# --- Dev Containers の共有の置き場の片付け (#507) ------------------------------
# dev インスタンスには Dev Containers の共有の置き場 (名前 "vscode" のボリューム) が
# /vscode に付く。Dev Containers は拡張のキャッシュを 500 ファイルまで残し、本体の
# 古い版を消すと ~/.vscode-server/bin/<commit> のリンクが切れて残る。起動の後の処理で
# 両方を片付ける。entrypoint でなくここで行うのは、イメージを建て直さずに効かせるため。

#: 拡張のキャッシュに、拡張ごとに残す版の数
EXTENSIONS_CACHE_KEEP = 3


def shared_cache_prune_script(cache_dir: Optional[str] = None,
                              bin_dir: Optional[str] = None,
                              keep: int = EXTENSIONS_CACHE_KEEP,
                              shared_dir: Optional[str] = None) -> str:
    """共有の置き場の拡張のキャッシュと ``bin`` のリンク切れを片付けるシェルの手順。

    1. ``cache_dir`` の直下の通常のファイルを、ファイル名の最初の「``-`` と数字」より
       前 (拡張の ID) で分け、更新時刻の新しい順に ``keep`` 個を残して消す。
       リンクとディレクトリには触らない。``cache_dir`` が無い・リンクなら何もしない
    2. ``bin_dir`` の直下で、名前が 40 桁の 16 進のリンクのうち、リンク先が無いものを
       消す。リンクでないもの・リンク先があるものには触らない。``shared_dir``
       (共有の置き場の中の ``vscode-server``) が無いコンテナでは行わない。``bin`` は
       全 dev コンテナで共有のボリュームで、共有の置き場が付いていないコンテナ
       (作り直す前のもの) からは、他のコンテナが張ったリンクがすべて切れて見えるため

    どちらも失敗を無視し、終了コードは常に 0。パスの既定は compose のマウント先と同じ
    定数から組む。引数はテストが手元のディレクトリで試すためだけに渡す。
    """
    cache = shlex.quote(cache_dir or VSCODE_CACHE_EXTENSIONS_DIR)
    bin_path = shlex.quote(bin_dir or VSCODE_BIN_TARGET)
    shared = shlex.quote(shared_dir or VSCODE_CACHE_SERVER_DIR)
    return (
        f"if [ -d {cache} ] && [ ! -L {cache} ]; then "
        f"( cd {cache} && ls -t | "
        # 数える前に通常のファイルだけへ絞る (ディレクトリやリンクが残す枠を取らない)
        "while IFS= read -r f; do "
        "[ -f \"$f\" ] && [ ! -L \"$f\" ] && printf '%s\\n' \"$f\"; "
        "done | "
        f"awk -v keep={int(keep)} "
        "'{ id = $0; sub(/-[0-9].*$/, \"\", id); if (++n[id] > keep) print }' | "
        "while IFS= read -r f; do rm -f -- \"$f\"; done ) || true; "
        "fi; "
        f"if [ -d {shared} ] && [ -d {bin_path} ] && [ ! -L {bin_path} ]; then "
        f"for p in {bin_path}/*; do "
        "n=\"${p##*/}\"; "
        "case \"$n\" in *[!0-9a-f]*) continue ;; esac; "
        "[ \"${#n}\" -eq 40 ] || continue; "
        "[ -L \"$p\" ] && [ ! -e \"$p\" ] && rm -f -- \"$p\"; "
        "done; "
        "fi; "
        "exit 0"
    )


def prune_shared_cache(container_name: str,
                       runner: Optional[Callable] = None) -> bool:
    """コンテナの共有の置き場の拡張のキャッシュと ``bin`` のリンク切れを片付ける。

    Dev Containers は共有の置き場へ root で書くので、``docker exec -u 0`` で打つ。
    片付けられたら True。docker の失敗・タイムアウトは False を返し、例外を出さない
    (起動の後の処理を止めない)。
    """
    run = runner or subprocess.run
    try:
        result = run(
            ["docker", "exec", "-u", "0", container_name,
             "sh", "-c", shared_cache_prune_script()],
            capture_output=True, text=True, timeout=_PRUNE_TIMEOUT, check=False,
        )
    except Exception as e:  # noqa: BLE001 - docker 不在/タイムアウト等で起動を倒さない
        logger.debug("Dev Containers の共有の置き場を片付けられませんでした (%s): %s",
                     container_name, e)
        return False
    if result.returncode != 0:
        logger.debug("Dev Containers の共有の置き場を片付けられませんでした (%s): %s",
                     container_name, (result.stderr or "").strip())
        return False
    return True
