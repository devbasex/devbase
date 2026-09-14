"""秘密ストア上の 1 参照を ``EnvFile`` と同じ操作性で扱うビュー

設定の収集処理 (``collectors/``) や ``devbase env`` の各コマンドは、``EnvFile`` の
``get`` / ``set`` / ``save`` という素朴な API に対して書かれている。保存先が平文か
暗号化かでこれらを書き分けると、収集処理まで暗号化を意識することになる。

そこで ``SecretStore`` の 1 参照を ``EnvFile`` と同じ形に見せるビューを挟み、
呼び出し側は保存先を知らないまま従来どおり書けるようにする。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from devbase.env.secret_store import SecretRef, SecretStore
from devbase.log import get_logger

logger = get_logger(__name__)


class SecretEnvFile:
    """``SecretStore`` の 1 参照を ``EnvFile`` 互換の操作で読み書きする"""

    def __init__(self, store: SecretStore, ref: SecretRef, *, fresh: bool = False):
        """``fresh`` が真なら読み出しに現物を使う (サーバ backend で控えへ落ちない)。

        書き込みを伴う操作 (``set`` / ``delete`` / ``edit``) のビューに使う。控えから
        読んだ内容を元に書き戻すと、不達の間の他の利用者の更新を上書きするため、
        不達ならエディタを開く前・値を変える前に止める。
        """
        self._store = store
        self._ref = ref
        self._fresh = fresh
        self._data: Dict[str, str] = {}
        self._loaded = False

    # -- 読み書き -----------------------------------------------------------

    def load(self) -> Dict[str, str]:
        self._data = (self._store.fetch(self._ref) if self._fresh
                      else self._store.load(self._ref))
        self._loaded = True
        return self._data

    def save(self) -> None:
        """現在の内容を保存する (保存形式は既存のものを維持する)"""
        if not self._loaded:
            # 一度も読んでいない状態で保存すると、既存の値を空で上書きしてしまう
            self.load()
        self._store.save(self._ref, self._data)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # -- 原文のまま扱う経路 --------------------------------------------------
    #
    # 辞書経由の load / save はコメント・空行・``export`` 表記を落とす。
    # エディタ編集のように利用者の書いた原文を保ちたい経路はこちらを使う。

    def load_bytes(self) -> bytes:
        """保存されている内容を **原文のバイト列のまま** 返す (不在なら空)"""
        if self._fresh:
            return self._store.fetch_bytes(self._ref)
        return self._store.load_bytes(self._ref)

    def save_bytes(self, data: bytes) -> None:
        """バイト列を **加工せずそのまま** 保存する"""
        from devbase.env.store import EnvFile

        self._store.save_bytes(self._ref, data)
        # 保存後に辞書側のキャッシュがずれないよう、書いた内容で作り直す
        self._data = EnvFile.parse_bytes(data)
        self._loaded = True

    # -- EnvFile 互換の操作 --------------------------------------------------

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        self._ensure_loaded()
        return self._data.get(key, default)

    def set(self, key: str, value: str) -> None:
        self._ensure_loaded()
        self._data[key] = value

    def exists(self, key: str) -> bool:
        self._ensure_loaded()
        return key in self._data

    def get_all(self) -> Dict[str, str]:
        self._ensure_loaded()
        return self._data.copy()

    def delete(self, key: str) -> bool:
        self._ensure_loaded()
        if key in self._data:
            del self._data[key]
            return True
        return False

    def count(self) -> int:
        self._ensure_loaded()
        return len(self._data)

    # -- 保存先の情報 --------------------------------------------------------

    @property
    def ref(self) -> SecretRef:
        return self._ref

    @property
    def path(self) -> Path:
        """実際の保存先パス (暗号化なら ``.age`` ファイル)"""
        return self._store.path(self._ref)

    @property
    def file_path(self) -> Path:
        """``EnvFile.file_path`` 互換のエイリアス"""
        return self.path

    def mode(self) -> str:
        """``'age'`` / ``'plaintext'`` / ``'absent'``"""
        return self._store.mode(self._ref)

    def is_encrypted(self) -> bool:
        return self._store.is_encrypted(self._ref)

    def direct_edit(self) -> bool:
        """保存先をファイルとして直接エディタで開いてよいか (平文だけ真)"""
        return self._store.direct_edit(self._ref)

    def mode_name(self) -> str:
        """保存先の backend 名 (存在の有無によらない。エラー文言用)"""
        return self._store.backend_for(self._ref).name

    def has_user_refs(self) -> bool:
        """保存先の backend が個人単位の参照を持つか"""
        return self._store.has_user_refs(self._ref)

    def file_exists(self) -> bool:
        return self._store.exists(self._ref)

    def backup(self) -> Optional[Path]:
        """保存先を退避する。

        ファイル backend では現行どおり ``.backup`` 付きで複製する。暗号化されている
        場合は暗号文のまま複製されるため、複製が新たな平文の滞留を生むことはない。

        サーバ backend では手元に複製できるファイルが無いため、読み出した値を age で
        暗号化して ``backups/env-init/<日時>/`` へ控える。**退避を作れなければ失敗
        させる** (``SecretStoreError``)。呼び出し側 (``env init --reset``) はそこで
        削除へ進まない (PLAN51 設計 2)。
        """
        import shutil

        if not self.file_exists():
            return None
        from devbase.env.secret_store import AgeBackend, PlaintextBackend

        backend = self._store.backend_for(self._ref)
        if not isinstance(backend, (AgeBackend, PlaintextBackend)):
            return self._backup_encrypted()
        source = self.path
        backup_path = Path(str(source) + '.backup')
        try:
            shutil.copy2(source, backup_path)
        except OSError as e:
            logger.warning("バックアップを作成できませんでした (%s): %s", backup_path, e)
            return None
        return backup_path

    def _backup_encrypted(self) -> Path:
        from datetime import datetime

        from devbase.env import io_common as _io_common
        from devbase.env.secret_store import SecretStoreError

        ref = self._ref
        stem = f"{ref.owner}-{ref.kind}" + (f"-{ref.name}" if ref.name else '')
        target = (Path(self._store.root) / 'backups' / 'env-init'
                  / datetime.now().strftime('%Y%m%d-%H%M%S-%f') / f'{stem}.env.age')
        try:
            blob = self._store.age.encrypt_bytes(self.load_bytes())
            _io_common.write_secure_bytes_atomic(target, blob)
        except OSError as e:
            raise SecretStoreError(f"退避を書き込めませんでした ({target}): {e}") from e
        return target

    def __repr__(self) -> str:
        return f"SecretEnvFile({self._ref!r} -> {self.path})"
