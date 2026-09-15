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

どちらを使うかは、既定では**ファイルの存在で自動判定**する。暗号化ファイルがあればそれを
使い、無ければ平文を使う。同じ参照に対して両方が存在する状態は、どちらが正なのか判断できない
ため明示的なエラーにして利用者に解消させる (plan35 §9)。

``secrets/backend.yml`` (:mod:`devbase.env.backend_config`) で backend を明示的に
選ぶこともできる (PLAN51)。設定があるときは ``backend_for`` がその backend を返し、
サーバ backend (``openbao``) もここへ載る。設定が無ければ ``auto`` = 上記の自動判定で、
設定を持たない端末の挙動は変わらない。

参照には**持ち主** (``owner``) の軸がある。``team`` はチームの全員が同じ値を使う機密、
``user`` は利用者ごとに値が違う機密を指す。ファイル backend は 1 台の端末に閉じている
ため個人単位の参照を持たず、``user`` に対しては存在しない参照として振る舞う。

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


OWNER_TEAM = 'team'
OWNER_USER = 'user'
_OWNERS = (OWNER_TEAM, OWNER_USER)


def _validate_owner(owner: str) -> str:
    if owner not in _OWNERS:
        raise SecretStoreError(
            f"参照の持ち主は {' / '.join(_OWNERS)} のいずれかです: {owner!r}")
    return owner


def _validate_group(group: Optional[str]) -> Optional[str]:
    """参照のグループ名を ``DEVBASE_ACCOUNT_GROUP`` と同じ規則で検証する (空は ``None``)。

    グループ名はパスの 1 要素になるため、区切り文字や予約語を参照の時点で弾く。
    規則は :func:`devbase.volume.manager.resolve_account_group` を使い、写さない。
    """
    if group is None or not str(group).strip():
        return None
    from devbase.volume.manager import resolve_account_group

    try:
        return resolve_account_group(str(group))
    except DevbaseError as e:
        raise SecretStoreError(str(e)) from None


@dataclass(frozen=True)
class SecretRef:
    """機密の参照 (共通 / プロジェクト × チーム単位 / 個人単位)

    ``owner`` を末尾の既定値付きフィールドにしているのは、``name`` を位置引数で
    渡している既存の生成をそのまま残すため。既存の呼び出しはすべてチーム単位を指す。
    ファクトリは ``for_global`` / ``for_project`` の 2 つのままにし、持ち主は
    キーワード引数 ``owner`` で受ける (PLAN51 設計 1「参照の形」)。

    ``group`` はアカウントグループ (PLAN56 決定 2)。``backend.yml`` が ``version: 2``
    (``openbao.layout: group``) のときだけ入り、それ以外では常に ``None`` で、参照の値・
    等価性・キャッシュの位置は今と同じになる (決定 5)。グループを参照が持つため、
    1 つの ``SecretStore`` の中でグループが変わっても控え (``_seen``) を取り違えない。
    読み替え (``group_aliases``) の前の名前を持ち、読み替えはパスを組むときに行う。
    """
    kind: str                      # 'global' | 'project'
    name: Optional[str] = None
    owner: str = OWNER_TEAM        # 'team' | 'user'
    group: Optional[str] = None

    @staticmethod
    def for_global(*, owner: str = OWNER_TEAM, group: Optional[str] = None) -> 'SecretRef':
        return SecretRef(kind='global', owner=_validate_owner(owner),
                         group=_validate_group(group))

    @staticmethod
    def for_project(name: str, *, owner: str = OWNER_TEAM,
                    group: Optional[str] = None) -> 'SecretRef':
        return SecretRef(kind='project', name=_validate_project_name(name),
                         owner=_validate_owner(owner), group=_validate_group(group))

    @property
    def is_user(self) -> bool:
        return self.owner == OWNER_USER

    def label(self) -> str:
        # チーム単位の文字列は変えない。誤りの伝達や桁揃えに埋め込まれており、
        # 変えると既存の表示とテストが一斉に動く。
        base = 'グローバル' if self.kind == 'global' else f"プロジェクト '{self.name}'"
        text = f'個人の{base}' if self.is_user else base
        return f'{text}（グループ {self.group}）' if self.group else text


class SecretBackend(Protocol):
    name: str
    #: 保存先をファイルとして直接エディタで開いてよいか。平文だけ真。
    direct_edit: bool
    #: 個人単位の参照 (``owner='user'``) を持つか。ファイル backend は持たない。
    has_user_refs: bool

    def path(self, ref: SecretRef) -> Path: ...
    def exists(self, ref: SecretRef) -> bool: ...
    def load(self, ref: SecretRef) -> Dict[str, str]: ...
    def save(self, ref: SecretRef, data: Dict[str, str]) -> Path: ...
    def load_bytes(self, ref: SecretRef) -> bytes: ...
    def save_bytes(self, ref: SecretRef, data: bytes) -> Path: ...
    def remove(self, ref: SecretRef) -> bool: ...


def _reject_user_ref(backend_name: str, ref: SecretRef) -> None:
    """ファイル backend への個人単位の書き込みを拒む。

    黙ってチーム単位の置き場へ落とすと、個人の資格情報が全員から見える場所へ入る。
    """
    if ref.is_user:
        raise SecretStoreError(
            f"{backend_name} backend は個人単位の機密を扱えません "
            f"({ref.label()})。個人単位の機密を置くにはサーバ backend を設定してください")


class PlaintextBackend:
    """従来どおり平文の ``.env`` を読み書きする"""

    name = MODE_PLAINTEXT
    direct_edit = True
    has_user_refs = False

    def __init__(self, devbase_root: Path):
        self._root = Path(devbase_root)

    def path(self, ref: SecretRef) -> Path:
        if ref.kind == 'global':
            return self._root / '.env'
        return self._root / 'projects' / _validate_project_name(ref.name or '') / '.env'

    def exists(self, ref: SecretRef) -> bool:
        return not ref.is_user and self.path(ref).is_file()

    def load_bytes(self, ref: SecretRef) -> bytes:
        """``.env`` の中身を **原文のバイト列のまま** 返す (不在なら空)"""
        path = self.path(ref)
        if ref.is_user or not path.is_file():
            return b''
        try:
            return path.read_bytes()
        except OSError as e:
            raise SecretStoreError(f"読み込みに失敗しました ({path}): {e}") from e

    def save_bytes(self, ref: SecretRef, data: bytes) -> Path:
        """バイト列を **加工せずそのまま** ``.env`` へ書き出す"""
        _reject_user_ref(self.name, ref)
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
        # 個人単位の参照は持たない。path() はチーム単位のファイルを指すため、
        # ここで止めないとチームの機密を消してしまう。
        if ref.is_user:
            return False
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
    direct_edit = False
    has_user_refs = False

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
        return not ref.is_user and self.path(ref).is_file()

    # -- 読み書き -----------------------------------------------------------

    def load_bytes(self, ref: SecretRef) -> bytes:
        """復号した **生バイト列** を返す (不在なら空)。

        中身を ``KEY=VALUE`` として解釈しないので、コメント・空行・``export``
        表記を含む原文をそのまま取り出せる。
        """
        path = self.path(ref)
        if ref.is_user or not path.is_file():
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
        _reject_user_ref(self.name, ref)
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
        if ref.is_user:
            return False
        path = self.path(ref)
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError as e:
            raise SecretStoreError(f"削除に失敗しました ({path}): {e}") from e
        return True


class SecretStore:
    """保存先を選んで機密を読み書きする窓口

    backend の選択は ``secrets/backend.yml`` が決め、設定が無ければ ``auto``
    (ファイルの存在による自動判定) になる。設定はこのインスタンスで最初に必要に
    なったときに 1 度だけ読む。生成時に読まないのは、``SecretStore(...)`` の生成箇所
    (11 か所) を変えずに済ませ、設定ファイルが壊れていても設定を触らない経路
    (``env keygen`` など) を止めないため。
    """

    def __init__(self, devbase_root: Path, *,
                 recipients: Optional[Sequence[str]] = None,
                 identities: Optional[Sequence[str]] = None,
                 config=None):
        """``config`` を渡すと ``secrets/backend.yml`` を読まずにその設定で動く。

        移行 (``env backend migrate``) のように、設定ファイルが指す backend とは別の
        backend を相手にする処理のための入口。通常の呼び出しでは渡さない。
        """
        self.root = Path(devbase_root)
        self.plaintext = PlaintextBackend(self.root)
        self.age = AgeBackend(self.root, recipients=recipients,
                              identities=identities)
        self._config = config
        self._selected: Optional[SecretBackend] = None

    # -- 設定 ---------------------------------------------------------------

    @property
    def config(self):
        """読み込んだ backend 設定 (:class:`devbase.env.backend_config.BackendConfig`)"""
        if self._config is None:
            from devbase.env import backend_config as _bc

            try:
                self._config = _bc.load(self.root)
            except _bc.BackendConfigError as e:
                raise SecretStoreError(f"backend の設定を読めませんでした: {e}") from e
        return self._config

    @property
    def backend_name(self) -> str:
        """設定で選ばれている backend 名 (``auto`` を含む)"""
        return self.config.backend

    def ref_group(self, project: Optional[str]) -> Optional[str]:
        """参照に持たせるグループ。``openbao`` かつ ``layout: group`` のときだけ値を返す。

        グループは非機密の ``env`` ファイルから決める
        (:func:`devbase.env.groups.declared_group`。機密の置き場は読まない)。それ以外の
        設定では ``None`` を返し、参照は今と同じ値になる (PLAN56 決定 5)。
        """
        config = self.config
        settings = config.openbao
        if config.backend != 'openbao' or settings is None or not settings.grouped:
            return None
        from devbase.env.groups import declared_group

        return declared_group(self.root, project)

    def _selected_backend(self) -> Optional[SecretBackend]:
        """設定で明示的に選ばれた backend (``auto`` なら ``None``)"""
        if self.backend_name == 'auto':
            return None
        if self._selected is None:
            # 登録簿はこのモジュールの backend を参照するため、先頭で import すると循環する
            from devbase.env import backends as _backends

            self._selected = _backends.create_backend(self.backend_name, self)
        return self._selected

    # -- 判定 ---------------------------------------------------------------

    def backend_for(self, ref: SecretRef) -> SecretBackend:
        """参照に対して使うべき backend を返す。

        設定で選ばれていればそれを返す。``auto`` では、暗号化ファイルと平文ファイルが
        同時に存在する場合にどちらが最新なのか devbase 側では判断できない。黙って
        一方を採用すると「編集したはずの値が反映されない」形で事故になるため、
        明示的に停止して利用者に解消させる。
        """
        selected = self._selected_backend()
        if selected is not None:
            return selected
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
        """選択中の backend 名 (``'age'`` / ``'plaintext'`` / ``'openbao'``) か ``'absent'``"""
        selected = self._selected_backend()
        if selected is not None:
            return selected.name if selected.exists(ref) else MODE_ABSENT
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

    def direct_edit(self, ref: SecretRef) -> bool:
        """保存先をファイルとして直接エディタで開いてよいか"""
        return bool(self.backend_for(ref).direct_edit)

    def has_user_refs(self, ref: SecretRef) -> bool:
        """選択中の backend が個人単位の参照を持つか (ファイル backend は持たない)"""
        return bool(getattr(self.backend_for(ref), 'has_user_refs', False))

    # -- 読み書き -----------------------------------------------------------

    def exists(self, ref: SecretRef) -> bool:
        return self.mode(ref) != MODE_ABSENT

    def path(self, ref: SecretRef) -> Path:
        return self.backend_for(ref).path(ref)

    def load(self, ref: SecretRef) -> Dict[str, str]:
        return self.backend_for(ref).load(ref)

    def fetch(self, ref: SecretRef) -> Dict[str, str]:
        """現物から読む (サーバ backend では控えへ落ちない)。

        書き込みを伴う操作の読み出しに使う。控えから読んだ内容を元に書き戻すと、
        不達の間の他の利用者の更新を上書きするため。ファイル backend では ``load`` と
        同じである。
        """
        backend = self.backend_for(ref)
        fetch = getattr(backend, 'fetch', None)
        return fetch(ref) if callable(fetch) else backend.load(ref)

    def fetch_bytes(self, ref: SecretRef) -> bytes:
        """``fetch`` の原文のバイト列版"""
        backend = self.backend_for(ref)
        if callable(getattr(backend, 'fetch', None)):
            from devbase.env.store import EnvFile

            return EnvFile.dump_bytes(backend.fetch(ref))
        return backend.load_bytes(ref)

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
