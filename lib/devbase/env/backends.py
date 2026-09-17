"""backend の登録簿 — 名前から実装を作る

``secrets/backend.yml`` の ``backend`` に書かれた名前を、``SecretBackend`` の実装へ
引く (PLAN51 設計 1)。未知の名前は、利用できる名前の一覧を添えて拒む。

``PlaintextBackend`` と ``AgeBackend`` は :mod:`devbase.env.secret_store` に置いた
ままで、登録簿はそれらを参照するだけである。このモジュールは ``secret_store`` から
関数内 import で呼ばれる (モジュール先頭で相互に import すると循環する)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from devbase.env.backend_config import BACKEND_NAMES
from devbase.env.secret_store import SecretStoreError

if TYPE_CHECKING:  # pragma: no cover
    from devbase.env.secret_store import SecretBackend, SecretStore

__all__ = ['BACKEND_NAMES', 'require_known', 'create_backend']


def require_known(name: str) -> str:
    """登録簿にある名前であることを確かめる (無ければ一覧を添えて拒む)"""
    if name not in BACKEND_NAMES:
        raise SecretStoreError(
            f"backend '{name}' は登録されていません "
            f"(利用できる backend: {', '.join(BACKEND_NAMES)})")
    return name


def create_backend(name: str, store: 'SecretStore') -> 'SecretBackend':
    """名前に対応する backend を返す。

    ``auto`` はここでは扱わない。存在による判定は ``SecretStore.backend_for`` の
    仕事であり、1 つの backend オブジェクトで表せないため。
    """
    require_known(name)
    if name == 'plaintext':
        return store.plaintext
    if name == 'age':
        return store.age
    if name == 'openbao':
        from devbase.env.openbao import OpenBaoBackend

        return OpenBaoBackend(store)
    raise SecretStoreError(f"backend '{name}' は登録簿にありますが、生成方法が定義されていません")
