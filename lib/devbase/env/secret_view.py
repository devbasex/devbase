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

    def __init__(self, store: SecretStore, ref: SecretRef):
        self._store = store
        self._ref = ref
        self._data: Dict[str, str] = {}
        self._loaded = False

    # -- 読み書き -----------------------------------------------------------

    def load(self) -> Dict[str, str]:
        self._data = self._store.load(self._ref)
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

    def file_exists(self) -> bool:
        return self._store.exists(self._ref)

    def backup(self) -> Optional[Path]:
        """保存先ファイルを ``.backup`` 付きで複製する。

        暗号化されている場合は暗号文のまま複製されるため、複製が新たな平文の
        滞留を生むことはない。
        """
        import shutil

        if not self.file_exists():
            return None
        source = self.path
        backup_path = Path(str(source) + '.backup')
        try:
            shutil.copy2(source, backup_path)
        except OSError as e:
            logger.warning("バックアップを作成できませんでした (%s): %s", backup_path, e)
            return None
        return backup_path

    def __repr__(self) -> str:
        return f"SecretEnvFile({self._ref!r} -> {self.path})"
