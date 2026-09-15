"""起動中の dev コンテナへ OpenBao の token を届ける (PLAN54)

コンテナの中の ``bao`` は、環境変数 ``BAO_TOKEN`` が無ければ ``~/.vault-token`` を読む。
ここはその 1 ファイルを書くことだけを持つ。token の取得 (``OpenBaoBackend.issue_token``)
と backend の判定、届け先のコンテナの解決は呼び出し側 (``up`` / ``env token``) が持つ。

token を環境変数にしないのは、``docker inspect`` と子プロセスの環境に残るためである。
``docker exec`` の argv にも載せず (``ps`` から読める)、stdin だけで渡す。接続先
(``DOCKER_CONTEXT``) は呼び出し側が process の環境へ当てておく。
"""

from __future__ import annotations

import subprocess
from typing import Callable, List, Optional, Sequence

from devbase.log import get_logger

logger = get_logger(__name__)

#: コンテナの中で token を置くシェル。
#:
#: 一時ファイルは ``mktemp`` で毎回新しく作る。固定名だと、その名前のファイルが既に
#: あるとき ``cat >`` が既存の inode へ書き、``umask 077`` が効かない (0644 のまま残る)。
#: 同じディレクトリに書いてから ``mv -f`` で置き換えるので、途中で切れても空の
#: ``~/.vault-token`` は残らない。``chmod 0600`` は ``mktemp`` の実装差への保険。
WRITE_COMMAND = (
    'umask 077; '
    'tmp=$(mktemp "$HOME/.vault-token.XXXXXX") || exit 1; '
    'if cat > "$tmp" && chmod 0600 "$tmp" && mv -f "$tmp" "$HOME/.vault-token"; '
    'then exit 0; else rm -f "$tmp"; exit 1; fi'
)

_EXEC_TIMEOUT = 30


def push(container_names: Sequence[str], token: str,
         runner: Optional[Callable] = None) -> List[str]:
    """各コンテナの ``~/.vault-token`` を ``token`` で置き換え、書けたコンテナ名を返す。

    書けなかったコンテナは警告を 1 行ずつ出して続ける。失敗を上へ伝えるかどうか
    (``up`` は倒さない、``env token`` は非ゼロで終える) は呼び出し側が決める。
    """
    run = runner or subprocess.run
    written: List[str] = []
    for name in container_names:
        try:
            result = run(
                ['docker', 'exec', '-i', name, 'sh', '-c', WRITE_COMMAND],
                input=token, capture_output=True, text=True, encoding='utf-8',
                timeout=_EXEC_TIMEOUT, check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("%s へ token を書けませんでした: %s", name, type(e).__name__)
            continue
        if result.returncode != 0:
            detail = (result.stderr or '').strip().splitlines()
            logger.warning("%s へ token を書けませんでした (exit=%d)%s", name, result.returncode,
                           f": {detail[-1]}" if detail else "")
            continue
        written.append(name)
    return written
