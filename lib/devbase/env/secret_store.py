"""機密の保存先を抽象化する層 (平文 / age)

``devbase`` が扱う機密は、これまで平文の ``.env`` に直接置かれていた。本モジュールは
「どこに」「どの形式で」保存するかを 1 箇所に閉じ込め、上位の設定操作コマンドからは
``load`` / ``save`` だけを見えるようにする (plan35 §3.1)。

保存先の対応:

===================  ==================================  ==========================================
参照                 平文 (従来)                          age (暗号化)
===================  ==================================  ==========================================
共通                 ``$DEVBASE_ROOT/.env``               ``$DEVBASE_ROOT/secrets/global.env.age``
プロジェクト         ``projects/<name>/.env``             ``secrets/projects/<name>.env.age``
===================  ==================================  ==========================================

どちらを使うかは**ファイルの存在で自動判定**する。暗号化ファイルがあればそれを使い、
無ければ平文を使う。同じ参照に対して両方が存在する状態は、どちらが正なのか判断できない
ため明示的なエラーにして利用者に解消させる (plan35 §9)。

読み書きの経路は 2 つある:

- ``load`` / ``save``: ``KEY=VALUE`` の辞書として扱う。``devbase env set`` など
  「値を書き換える」操作はこちらを使う
- ``load_bytes`` / ``save_bytes``: **原文のバイト列をそのまま** 扱う。
  ``devbase env encrypt`` / ``decrypt`` の移行はこちらを使い、コメント・空行・
  ``export KEY=...`` のような表記を保ったまま往復させる

このため「**値を書き換えるまでは原文が保たれ、書き換えると正規化される**」という
性質になる。``env set`` で 1 つでも値を更新すると ``EnvFile.dump_bytes`` の書式
(キーの昇順・コメントの消失) へ揃うが、これは平文しか無かった頃と同じ挙動であり、
暗号化したからといって変わるものではない。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Sequence

from devbase.env import agekeys
from devbase.env import cipher as _cipher
from devbase.env import io_common as _io_common
from devbase.env.store import EnvFile
from devbase.errors import DevbaseError
from devbase.log import get_logger

logger = get_logger(__name__)


class SecretStoreError(DevbaseError):
    """秘密ストアの操作エラー"""


#: 暗号化された機密を置くディレクトリ名 (``$DEVBASE_ROOT`` 相対)
SECRETS_DIRNAME = 'secrets'

GLOBAL_ENCRYPTED_FILENAME = 'global.env.age'

MODE_AGE = 'age'
MODE_PLAINTEXT = 'plaintext'
MODE_ABSENT = 'absent'


def _validate_project_name(name: str) -> str:
    """プロジェクト名がパスを跨がないことを確認する。

    参照名はそのままファイル名に使われるため、``..`` や区切り文字を許すと
    ``secrets/`` の外側へ書き出せてしまう。
    """
    if not name:
        raise SecretStoreError("プロジェクト名が空です")
    if name != Path(name).name or name in ('.', '..'):
        raise SecretStoreError(
            f"プロジェクト名にパス区切りは使えません: {name!r}"
        )
    return name


@dataclass(frozen=True)
class SecretRef:
    """機密の参照 (共通 / プロジェクト)"""
    kind: str                      # 'global' | 'project'
    name: Optional[str] = None

    @staticmethod
    def for_global() -> 'SecretRef':
        return SecretRef(kind='global')

    @staticmethod
    def for_project(name: str) -> 'SecretRef':
        return SecretRef(kind='project', name=_validate_project_name(name))

    def label(self) -> str:
        return 'グローバル' if self.kind == 'global' else f"プロジェクト '{self.name}'"


class SecretBackend(Protocol):
    name: str

    def path(self, ref: SecretRef) -> Path: ...
    def exists(self, ref: SecretRef) -> bool: ...
    def load(self, ref: SecretRef) -> Dict[str, str]: ...
    def save(self, ref: SecretRef, data: Dict[str, str]) -> Path: ...
    def load_bytes(self, ref: SecretRef) -> bytes: ...
    def save_bytes(self, ref: SecretRef, data: bytes) -> Path: ...
    def remove(self, ref: SecretRef) -> bool: ...


class PlaintextBackend:
    """従来どおり平文の ``.env`` を読み書きする"""

    name = MODE_PLAINTEXT

    def __init__(self, devbase_root: Path):
        self._root = Path(devbase_root)

    def path(self, ref: SecretRef) -> Path:
        if ref.kind == 'global':
            return self._root / '.env'
        return self._root / 'projects' / _validate_project_name(ref.name or '') / '.env'

    def exists(self, ref: SecretRef) -> bool:
        return self.path(ref).is_file()

    def load_bytes(self, ref: SecretRef) -> bytes:
        """``.env`` の中身を **原文のバイト列のまま** 返す (不在なら空)"""
        path = self.path(ref)
        if not path.is_file():
            return b''
        try:
            return path.read_bytes()
        except OSError as e:
            raise SecretStoreError(f"読み込みに失敗しました ({path}): {e}") from e

    def save_bytes(self, ref: SecretRef, data: bytes) -> Path:
        """バイト列を **加工せずそのまま** ``.env`` へ書き出す"""
        path = self.path(ref)
        try:
            # 平文とはいえ機密の入れ物なので、暗号化側と同じく atomic に差し替える。
            # 直接 O_TRUNC すると書き込み途中の失敗で旧値も新値も失った空ファイルが
            # 残り、その状態で暗号化すると中身の無い機密を保存してしまう。
            _io_common.write_secure_bytes_atomic(path, data)
        except OSError as e:
            raise SecretStoreError(f"書き込みに失敗しました ({path}): {e}") from e
        return path

    def load(self, ref: SecretRef) -> Dict[str, str]:
        try:
            return EnvFile.parse_bytes(self.load_bytes(ref))
        except UnicodeDecodeError as e:
            raise SecretStoreError(
                f"{self.path(ref)} を UTF-8 として読めませんでした: {e}\n"
                "暗号化済みファイルを平文として読もうとしていないか確認してください"
            ) from e

    def save(self, ref: SecretRef, data: Dict[str, str]) -> Path:
        """辞書を ``EnvFile`` の書式へ整形して保存する。

        コメント・空行・``export`` 表記は辞書に載らないため、この経路を通ると
        内容が正規化される。原文を保ちたい移行系は :meth:`save_bytes` を使う。
        """
        return self.save_bytes(ref, EnvFile.dump_bytes(data))

    def remove(self, ref: SecretRef) -> bool:
        path = self.path(ref)
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError as e:
            raise SecretStoreError(f"削除に失敗しました ({path}): {e}") from e
        return True


class AgeBackend:
    """age で暗号化したファイルを読み書きする"""

    name = MODE_AGE

    def __init__(self, devbase_root: Path, *,
                 recipients: Optional[Sequence[str]] = None,
                 identities: Optional[Sequence[str]] = None):
        self._root = Path(devbase_root)
        self._recipients = list(recipients) if recipients is not None else None
        self._identities = list(identities) if identities is not None else None

    # -- 鍵の解決 -----------------------------------------------------------

    def recipients(self) -> List[str]:
        if self._recipients is not None:
            return self._recipients
        return agekeys.resolve_recipients(self._root)

    def identities(self) -> List[str]:
        if self._identities is not None:
            return self._identities
        found = agekeys.resolve_identities()
        if not found:
            raise SecretStoreError(
                "復号に使える秘密鍵が見つかりません。\n"
                f"  `devbase env keygen` で生成するか、{agekeys.KEY_FILE_ENV} "
                "で鍵ファイルの場所を指定してください"
            )
        return found

    # -- 保存先 -------------------------------------------------------------

    def path(self, ref: SecretRef) -> Path:
        base = self._root / SECRETS_DIRNAME
        if ref.kind == 'global':
            return base / GLOBAL_ENCRYPTED_FILENAME
        name = _validate_project_name(ref.name or '')
        return base / 'projects' / f'{name}.env.age'

    def exists(self, ref: SecretRef) -> bool:
        return self.path(ref).is_file()

    # -- 読み書き -----------------------------------------------------------

    def load_bytes(self, ref: SecretRef) -> bytes:
        """復号した **生バイト列** を返す (不在なら空)。

        中身を ``KEY=VALUE`` として解釈しないので、コメント・空行・``export``
        表記を含む原文をそのまま取り出せる。
        """
        path = self.path(ref)
        if not path.is_file():
            return b''
        try:
            blob = path.read_bytes()
        except OSError as e:
            raise SecretStoreError(f"読み込みに失敗しました ({path}): {e}") from e
        try:
            return _cipher.decrypt(blob, identities=self.identities())
        except _cipher.CipherError as e:
            raise SecretStoreError(
                f"{ref.label()}の機密を復号できませんでした ({path}): {e}"
            ) from e

    def encrypt_bytes(self, data: bytes) -> bytes:
        """バイト列を受信者宛に暗号化して返す (保存はしない)。

        書き出し先を自前で扱う処理 (``devbase env import`` の原子的な書き込み等)
        が、暗号化だけをこの層に任せられるようにする。
        """
        try:
            return _cipher.encrypt(data, recipients=self.recipients())
        except _cipher.CipherError as e:
            raise SecretStoreError(
                f"機密を暗号化できませんでした: {e}"
            ) from e

    def save_bytes(self, ref: SecretRef, data: bytes) -> Path:
        """バイト列を **加工せずそのまま** 暗号化して保存する"""
        path = self.path(ref)
        blob = self.encrypt_bytes(data)
        try:
            # 暗号文は失うと復旧不能なので、既存ファイルを直接 O_TRUNC せず
            # 一時ファイル → fsync → os.replace で差し替える。ディスク枯渇や中断が
            # 起きても旧 ciphertext はそのまま残り、書きかけの一時ファイルも消える。
            _io_common.write_secure_bytes_atomic(path, blob)
        except OSError as e:
            raise SecretStoreError(f"書き込みに失敗しました ({path}): {e}") from e
        return path

    def load(self, ref: SecretRef) -> Dict[str, str]:
        try:
            return EnvFile.parse_bytes(self.load_bytes(ref))
        except UnicodeDecodeError as e:
            # 復号は成功したのに中身が UTF-8 でない = 元々 .env ではない
            # バイナリを暗号化していた、というケース。PlaintextBackend.load と
            # 同じく SecretStoreError へ包み、呼び出し側が扱う例外を 1 種類に保つ。
            raise SecretStoreError(
                f"{ref.label()}の機密を復号しましたが、UTF-8 として読めませんでした "
                f"({self.path(ref)}): {e}\n"
                "KEY=VALUE 形式以外のファイルを暗号化していないか確認してください"
            ) from e

    def save(self, ref: SecretRef, data: Dict[str, str]) -> Path:
        """辞書を ``EnvFile`` の書式へ整形して暗号化する。

        ``PlaintextBackend.save`` と同じく、この経路を通ると内容が正規化される。
        原文を保ちたい移行系は :meth:`save_bytes` を使う。
        """
        return self.save_bytes(ref, EnvFile.dump_bytes(data))

    def remove(self, ref: SecretRef) -> bool:
        path = self.path(ref)
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError as e:
            raise SecretStoreError(f"削除に失敗しました ({path}): {e}") from e
        return True


class SecretStore:
    """保存先を自動判定して機密を読み書きする窓口"""

    def __init__(self, devbase_root: Path, *,
                 recipients: Optional[Sequence[str]] = None,
                 identities: Optional[Sequence[str]] = None):
        self.root = Path(devbase_root)
        self.plaintext = PlaintextBackend(self.root)
        self.age = AgeBackend(self.root, recipients=recipients,
                              identities=identities)

    # -- 判定 ---------------------------------------------------------------

    def backend_for(self, ref: SecretRef) -> SecretBackend:
        """参照に対して使うべき backend を返す。

        暗号化ファイルと平文ファイルが同時に存在する場合は、どちらが最新なのか
        devbase 側では判断できない。黙って一方を採用すると「編集したはずの値が
        反映されない」形で事故になるため、明示的に停止して利用者に解消させる。
        """
        age_exists = self.age.exists(ref)
        plain_exists = self.plaintext.exists(ref)
        if age_exists and plain_exists:
            raise SecretStoreError(
                f"{ref.label()}の機密が暗号化・平文の両方に存在します:\n"
                f"  暗号化: {self.age.path(ref)}\n"
                f"  平文:   {self.plaintext.path(ref)}\n"
                "どちらが正しいか判断できないため中止しました。"
                "不要な方を削除 (または退避) してから再実行してください"
            )
        return self.age if age_exists else self.plaintext

    def mode(self, ref: SecretRef) -> str:
        """``'age'`` / ``'plaintext'`` / ``'absent'`` のいずれかを返す"""
        if self.age.exists(ref):
            if self.plaintext.exists(ref):
                # backend_for と同じ理由でここでも停止させる
                self.backend_for(ref)
            return MODE_AGE
        if self.plaintext.exists(ref):
            return MODE_PLAINTEXT
        return MODE_ABSENT

    def is_encrypted(self, ref: SecretRef) -> bool:
        return self.mode(ref) == MODE_AGE

    # -- 読み書き -----------------------------------------------------------

    def exists(self, ref: SecretRef) -> bool:
        return self.mode(ref) != MODE_ABSENT

    def path(self, ref: SecretRef) -> Path:
        return self.backend_for(ref).path(ref)

    def load(self, ref: SecretRef) -> Dict[str, str]:
        return self.backend_for(ref).load(ref)

    def save(self, ref: SecretRef, data: Dict[str, str]) -> Path:
        """既存の保存形式を維持したまま保存する。

        まだ何も無い参照は平文に落とす。暗号化へ移すのは ``devbase env encrypt``
        の役目であり、``set`` や ``sync`` が暗黙に形式を変えるべきではない。

        辞書を経由するため、保存した時点で内容は ``EnvFile`` の書式へ正規化される
        (コメント・空行・``export`` 表記は残らない)。原文のまま運びたい場合は
        :meth:`save_bytes` を使う。
        """
        return self.backend_for(ref).save(ref, data)

    def load_bytes(self, ref: SecretRef) -> bytes:
        """保存形式を問わず、中身を **原文のバイト列のまま** 返す"""
        return self.backend_for(ref).load_bytes(ref)

    def save_bytes(self, ref: SecretRef, data: bytes) -> Path:
        """既存の保存形式を維持したまま、バイト列を **そのまま** 保存する"""
        return self.backend_for(ref).save_bytes(ref, data)

    def project_names(self) -> List[str]:
        """暗号化済みの機密を持つプロジェクト名を返す"""
        base = self.root / SECRETS_DIRNAME / 'projects'
        if not base.is_dir():
            return []
        return sorted(
            p.name[: -len('.env.age')]
            for p in base.iterdir()
            if p.is_file() and p.name.endswith('.env.age')
        )
