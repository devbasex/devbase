"""SSH publish 用のホストポートを決定的に算出する (PLAN33)。

Orca は publish された `127.0.0.1:<port>` を known_hosts / SSH config で参照するため、
同じ `(project_name, index)` は **常に同じポート** に解決されなければならない
(`down` → `up` を跨いでも一定であること)。

そのため Python 組み込みの `hash()` は使わない。CPython は起動ごとに文字列ハッシュへ
salt を混ぜる (PYTHONHASHSEED) ため、プロセスを跨ぐと値が変わり決定性が崩れる。
代わりに `hashlib.sha1` ベースの安定ハッシュ (`_stable_hash`) を用いる。

異なるプロジェクト / index はほぼ衝突しないようオフセットを分散させる。
"""

import hashlib
from typing import Set


def _stable_hash(value: str) -> int:
    """プロセスや実行を跨いで一定な非負整数ハッシュを返す。

    組み込み `hash()` は salt されるため使わず、SHA-1 ダイジェストを整数化する。
    """
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()
    return int(digest, 16)


def ssh_host_port(project_name: str, index: int, base: int = 2200) -> int:
    """`(project_name, index)` から publish 先ホストポートを決定的に算出する。

    Args:
        project_name: プロジェクト名 (COMPOSE_PROJECT_NAME)。
        index: dev インスタンス番号 (1 始まり)。
        base: ポート算出の起点 (既定 2200)。

    Returns:
        `base + offset` のホストポート。同じ引数は常に同じ値を返す。
        offset = (stable_hash(project_name) % 100) * 10 + (index - 1)
        により、プロジェクト間は 10 刻みで分散し、同一プロジェクト内の
        index 差分は +1 ずつずれる (0..990 + 0..9 の範囲)。
    """
    offset = (_stable_hash(project_name) % 100) * 10 + (index - 1)
    return base + offset


def allocate_ssh_host_port(
    project_name: str, index: int, base: int, used_ports: Set[int],
) -> int:
    """決定的ポートを起点に、未使用の publish 先ホストポートを確保する。

    :func:`ssh_host_port` が返す決定的な値を **優先** して返す (`down`→`up` を
    跨いでも一定であることを保つ)。ただしその値が既に ``used_ports`` に含まれる場合
    (= 同一生成内の他インスタンス、または他プロジェクトが稼働 publish 済みのポート)
    は、空きが見つかるまで +1 ずつ線形探索して衝突を回避する。

    衝突が無ければ決定性は完全に保たれる。100 バケットへの縮約や index≥11 の
    バケット重複による同時起動時の bind 失敗を、この確保段階で解消する。

    Args:
        project_name: プロジェクト名 (COMPOSE_PROJECT_NAME)。
        index: dev インスタンス番号 (1 始まり)。
        base: ポート算出の起点。
        used_ports: 既に割り当て済み / 使用中のホストポート集合。

    Returns:
        ``used_ports`` に含まれない確保済みホストポート。
    """
    port = ssh_host_port(project_name, index, base)
    while port in used_ports:
        port += 1
    return port
