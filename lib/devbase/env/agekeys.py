"""devbase 専用 age 鍵と受信者リストの管理

``devbase env export`` / ``import`` が使う ``~/.ssh`` の鍵とは別に、devbase が
機密の保存に使う専用鍵を扱う。署名用の SSH 鍵とは失効・保管・バックアップの
扱いが異なるため、鍵を分けて管理する (plan35 §5.1)。

鍵ファイルの場所は ``DEVBASE_AGE_KEY_FILE`` で上書きでき、既定は
``~/.config/devbase/age/keys.txt`` (``XDG_CONFIG_HOME`` があればそれを尊重)。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import pyrage

from devbase.env import cipher as _cipher
from devbase.env import io_common as _io_common
from devbase.errors import DevbaseError
from devbase.log import get_logger

logger = get_logger(__name__)


class AgeKeyError(DevbaseError):
    """鍵ファイル / 受信者リストの操作エラー"""


#: 鍵ファイルの場所を明示するための環境変数。OS ごとの既定位置の違いを
#: 利用者が 1 箇所で吸収できるようにする (plan35 §5.1)。
KEY_FILE_ENV = 'DEVBASE_AGE_KEY_FILE'

#: 受信者リストのファイル名 (``$DEVBASE_ROOT/secrets/`` 配下)。
RECIPIENTS_FILENAME = 'recipients.txt'

_AGE_SECRET_PREFIX = 'AGE-SECRET-KEY-1'


# ---------------------------------------------------------------------------
# パス解決
# ---------------------------------------------------------------------------

def default_key_dir() -> Path:
    """既定の鍵ディレクトリ ``$XDG_CONFIG_HOME/devbase/age`` を返す。

    ``XDG_CONFIG_HOME`` が未設定なら ``~/.config`` を使う。
    """
    base = os.environ.get('XDG_CONFIG_HOME')
    root = Path(base).expanduser() if base else Path.home() / '.config'
    return root / 'devbase' / 'age'


def key_file_path() -> Path:
    """使用する鍵ファイルのパス (環境変数の指定を優先)"""
    override = os.environ.get(KEY_FILE_ENV)
    if override:
        return Path(override).expanduser()
    return default_key_dir() / 'keys.txt'


def recipients_file(devbase_root: Path) -> Path:
    """受信者リストのパス ``$DEVBASE_ROOT/secrets/recipients.txt``"""
    return Path(devbase_root) / 'secrets' / RECIPIENTS_FILENAME


# ---------------------------------------------------------------------------
# 鍵の生成・読み取り
# ---------------------------------------------------------------------------

def _ensure_private_dir(path: Path) -> None:
    """鍵 / 受信者リストの置き場を ``0700`` で用意する。

    実装は ``io_common.ensure_private_dir`` にある。機密ファイルの書き出し
    (``write_secure_bytes``) と同じ規則でディレクトリを掘る必要があり、実装を
    2 箇所に持つと片方だけ緩む。ここでは「置き場を利用者が明示的に選べる経路」
    なので、既存ディレクトリが緩いときの警告を有効にして呼ぶ
    (``DEVBASE_AGE_KEY_FILE`` に共有ディレクトリを指された場合に気づけるように)。
    """
    _io_common.ensure_private_dir(path, warn_if_permissive=True)


def _key_exists_error(path: Path) -> AgeKeyError:
    """「既に鍵がある」エラー。事前チェックと排他生成の両方から使う"""
    return AgeKeyError(
        f"鍵ファイルが既に存在します: {path}\n"
        "上書きすると既存の暗号化ファイルを復号できなくなります。"
        "意図的に作り直す場合のみ --force を指定してください"
    )


def _create_key_file_exclusive(path: Path, data: bytes) -> None:
    """新規鍵を ``O_CREAT|O_EXCL`` で **排他的に** 作成する。

    「存在チェック → 生成」を別々に行うと、その隙間に他プロセスが同じ判定を
    通り抜けられる。両者が生成へ進むと後発の書き込みが先発の鍵を消し、先発鍵で
    暗号化した機密がその瞬間から復号不能になる (TOCTOU)。``O_EXCL`` は
    「存在しなければ作る」をカーネル側で不可分に行うため、この隙間が原理的に
    消える。既存ファイルが無い状況では守るべき旧内容も無いので、一時ファイル +
    ``os.replace`` は不要なだけでなく有害 — ``os.replace`` は既存を無条件に
    置き換えてしまい、まさに塞ぎたい上書きを許すため。

    書き込み途中で失敗したら、中途半端な鍵ファイルを残さないよう自分で作った
    ファイルを消す。半端な鍵が残ると以後の生成が「既に存在します」で止まり、
    しかもその鍵では何も復号できない。
    """
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as e:
        raise _key_exists_error(path) from e
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            path.unlink()
        except OSError:
            pass
        raise
    # mode 引数が無視される環境 (Windows 等) に備えて明示的に揃える
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def generate_key_file(path: Optional[Path] = None, *,
                      force: bool = False) -> Tuple[Path, str]:
    """devbase 専用の age 鍵を生成して ``0600`` で保存する。

    書き込み方法は ``force`` で変える。守るべき旧内容の有無が違うため。

    - ``force=False`` (新規生成): ``O_CREAT|O_EXCL`` で直接排他作成する。
      判定と作成の隙間を閉じ、並行実行しても先に作った側の鍵が生き残る。
    - ``force=True`` (作り直し): 同一ディレクトリの一時ファイルへ書いて fsync
      してから atomic に差し替える。直接 ``O_TRUNC`` で上書きすると、書き込み
      途中の失敗 (ディスク枯渇・強制終了など) で旧鍵だけが失われ、既存の暗号文を
      誰も復号できなくなるため。差し替えに成功するまで旧鍵はそのまま残る。

    ``--force`` 同士の並行実行にはロックを掛けない。どちらも利用者が「既存鍵を
    捨てて作り直す」と明示的に要求した操作であり、後勝ちで最後の鍵が残ること自体が
    要求どおりの結果だから。ロックで直列化しても「先の鍵が消える」事実は変わらず、
    グローバルな鍵ファイルにロックの残骸 (stale lock) という別の詰まり方を持ち込む
    ぶん損になる。塞ぐべきだったのは「誰も上書きを要求していないのに上書きされる」
    新規生成側だけで、そこは ``O_EXCL`` で閉じている。

    Returns:
        ``(鍵ファイルのパス, 公開鍵文字列)``

    Raises:
        AgeKeyError: 既存の鍵があり ``force`` が偽のとき
    """
    path = Path(path) if path is not None else key_file_path()
    # 早期に弾いて無駄な鍵生成を避けるための事前チェック。ここを通り抜けた
    # 並行プロセスは下の O_EXCL で確実に止まるので、この判定は最適化にすぎない。
    if path.exists() and not force:
        raise _key_exists_error(path)

    identity = pyrage.x25519.Identity.generate()
    public = str(identity.to_public())
    created = datetime.now(timezone.utc).isoformat(timespec='seconds')
    content = (
        "# devbase age key file\n"
        f"# created: {created}\n"
        f"# public key: {public}\n"
        "# この鍵を失うと暗号化した機密は復旧できません。\n"
        "# パスワード管理ツール等へ必ず複製を保管してください。\n"
        f"{identity}\n"
    )

    _ensure_private_dir(path.parent)
    if force:
        _io_common.write_secure_bytes_atomic(path, content.encode('utf-8'))
    else:
        _create_key_file_exclusive(path, content.encode('utf-8'))
    return path, public


def read_public_key(path: Optional[Path] = None) -> str:
    """鍵ファイルから公開鍵を導出する。

    ファイル中のコメント (``# public key:``) は信頼せず、秘密鍵行から都度導出する。
    コメントは手で書き換えられうるため、そこを信じると「登録した受信者と実際の
    鍵が食い違ったまま暗号化してしまう」事故が起きる。
    """
    path = Path(path) if path is not None else key_file_path()
    if not path.exists():
        raise AgeKeyError(
            f"鍵ファイルが見つかりません: {path}\n"
            "`devbase env keygen` で生成してください"
        )
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as e:
        raise AgeKeyError(f"鍵ファイルを読み込めませんでした ({path}): {e}") from e

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if not stripped.startswith(_AGE_SECRET_PREFIX):
            break
        try:
            return str(pyrage.x25519.Identity.from_str(stripped).to_public())
        except Exception as e:
            raise AgeKeyError(f"age 秘密鍵の解釈に失敗しました ({path}): {e}") from e

    raise AgeKeyError(
        f"age 秘密鍵 ({_AGE_SECRET_PREFIX}...) が含まれていません: {path}\n"
        "OpenSSH 鍵など age 形式以外を使う場合は、対応する公開鍵を "
        "`devbase env keygen` ではなく受信者リストへ直接登録してください"
    )


# ---------------------------------------------------------------------------
# 受信者リスト
# ---------------------------------------------------------------------------

def load_recipients(devbase_root: Path) -> List[str]:
    """受信者リストの有効行 (コメント・空行を除く) を返す"""
    path = recipients_file(devbase_root)
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as e:
        raise AgeKeyError(f"受信者リストを読み込めませんでした ({path}): {e}") from e
    return [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.strip().startswith('#')
    ]


def save_recipients(devbase_root: Path, recipients: List[str]) -> Path:
    """受信者リストを書き出す。

    公開鍵そのものは秘密ではないが、ファイルは ``0600`` で保護する。第三者が
    自分の公開鍵をここへ追記できると、以後の暗号化がその相手にも復号可能に
    なるため、機密性ではなく**改竄防止**のために権限を絞る。

    書き込みは鍵ファイルと同じく atomic に行う。途中失敗で受信者が欠けたリストが
    残ると、以後の暗号化から一部の受信者が黙って外れてしまうため。

    置き場 (``secrets/``) の扱いも鍵ファイルと揃えて ``_ensure_private_dir`` に
    任せる。既に存在する ``secrets/`` — 例えば git clone 直後の 0755 — を勝手に
    0700 へ落とすと、ワークスペースを共有している他ユーザーの参照を壊すため。
    """
    path = recipients_file(devbase_root)
    _ensure_private_dir(path.parent)
    header = (
        "# devbase secret store recipients\n"
        "# 1 行に 1 つの公開鍵 (age1... / ssh-ed25519 ... / ssh-rsa ...)。\n"
        "# ここに列挙した全員が機密を復号できる。\n"
        "# 編集しても既存の暗号化ファイルは変わらないため、変更後は再暗号化すること。\n"
    )
    body = ''.join(f"{r}\n" for r in recipients)
    _io_common.write_secure_bytes_atomic(path, (header + body).encode('utf-8'))
    return path


def add_recipient(devbase_root: Path, spec: str) -> bool:
    """受信者を追加する。既に登録済みなら ``False`` を返して何もしない。"""
    spec = spec.strip()
    if not spec:
        raise AgeKeyError("受信者が空です")
    # 形式不正をここで弾いておく。登録後に初めて暗号化で落ちるより早い。
    _cipher.validate_recipient(spec)

    current = load_recipients(devbase_root)
    if spec in current:
        return False
    save_recipients(devbase_root, current + [spec])
    return True


def remove_recipient(devbase_root: Path, spec: str) -> bool:
    """受信者を削除する。登録が無ければ ``False`` を返す。"""
    spec = spec.strip()
    current = load_recipients(devbase_root)
    if spec not in current:
        return False
    save_recipients(devbase_root, [r for r in current if r != spec])
    return True


# ---------------------------------------------------------------------------
# 暗号化・復号に渡す鍵の解決
# ---------------------------------------------------------------------------

def resolve_recipients(devbase_root: Path) -> List[str]:
    """暗号化に使う受信者を解決する。

    受信者リストに登録があればそれを使い、無ければ専用鍵の公開鍵を使う。
    どちらも無ければ ``AgeKeyError``。
    """
    registered = load_recipients(devbase_root)
    if registered:
        return registered

    key_file = key_file_path()
    if key_file.exists():
        return [read_public_key(key_file)]

    raise AgeKeyError(
        "暗号化に使う公開鍵がありません。\n"
        "  `devbase env keygen` で devbase 専用鍵を生成するか、\n"
        f"  {recipients_file(devbase_root)} へ公開鍵を登録してください"
    )


def resolve_identities() -> List[str]:
    """復号に使う秘密鍵の候補を返す。

    専用鍵を先頭に置き、続けて ``~/.ssh`` の既定鍵を候補に加える。``pyrage`` は
    複数 identity を受け取り一致したものだけを使うため、旧来 ``~/.ssh`` の鍵で
    暗号化したファイルも移行期間中そのまま復号できる。
    """
    found: List[str] = []
    key_file = key_file_path()
    if key_file.exists():
        found.append(str(key_file))
    for path in _cipher.default_identity_paths():
        if path.exists() and str(path) not in found:
            found.append(str(path))
    return found
