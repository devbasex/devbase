"""devbase 専用 age 鍵と受信者リストの管理

``devbase env export`` / ``import`` が使う ``~/.ssh`` の鍵とは別に、devbase が
機密の保存に使う専用鍵を扱う。署名用の SSH 鍵とは失効・保管・バックアップの
扱いが異なるため、鍵を分けて管理する (plan35 §5.1)。

鍵ファイルの場所は ``DEVBASE_AGE_KEY_FILE`` で上書きでき、既定は
``~/.config/devbase/age/keys.txt`` (``XDG_CONFIG_HOME`` があればそれを尊重)。
"""

from __future__ import annotations

import os
import stat
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
    """ディレクトリを用意し、**自分が新規作成した階層だけ** ``0700`` にする。

    既存ディレクトリまで chmod すると、``DEVBASE_AGE_KEY_FILE=/tmp/devbase-key``
    のように共有ディレクトリを鍵の置き場に指定されたとき、その共有ディレクトリ
    ごと他ユーザーやサービスから読めなくしてしまう。devbase が作っていない
    ディレクトリの権限はその所有者の管轄なので触らず、緩い場合は警告に留める。

    ``mkdir(parents=True)`` で一括作成してから chmod すると、作成から chmod まで
    の間だけ umask 依存の緩い権限 (例 0755) が見えてしまう。その隙に開いた fd は
    後から chmod しても閉じないため、**作成前に** 未存在の階層を控えておき、親→子
    の順に ``mkdir(mode=0o700)`` で 1 階層ずつ作る。こうすれば最初から 0700 で、
    緩い権限が一瞬も露出しない。

    ``mode`` は umask でビットが削られることはあっても広がることはなく、``0o700``
    には group / other ビットが無いので umask の影響を受けない。「umask で緩く
    なるのでは」と後追いの chmod を足す必要は無い。
    """
    path = Path(path)
    missing: List[Path] = []
    probe = path
    while not probe.exists():
        missing.append(probe)
        parent = probe.parent
        if parent == probe:   # ルートまで到達 (通常は起こらない)
            break
        probe = parent

    if not missing:
        _warn_if_world_accessible(path)
        return

    # missing は子→親の順に積んであるので、逆順 (親→子) に作る
    for target in reversed(missing):
        try:
            target.mkdir(mode=0o700)
        except FileExistsError:
            # 並行して他プロセスが先に作った場合。既存ディレクトリは
            # 所有者の管轄として権限を触らない方針に合わせ、chmod しない。
            continue


def _warn_if_world_accessible(path: Path) -> None:
    """既存ディレクトリの権限が緩ければ警告する (権限は変更しない)"""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return
    if mode & 0o077:
        logger.warning(
            "%s は他ユーザーからアクセスできます (mode %04o)。"
            "devbase が作成したディレクトリではないため権限は変更しません。"
            "機密を置く場所なら chmod 700 を検討してください",
            path, mode,
        )


def generate_key_file(path: Optional[Path] = None, *,
                      force: bool = False) -> Tuple[Path, str]:
    """devbase 専用の age 鍵を生成して ``0600`` で保存する。

    ``force`` で既存鍵を作り直す場合も、同一ディレクトリの一時ファイルへ書いて
    fsync してから atomic に差し替える。直接 ``O_TRUNC`` で上書きすると、書き込み
    途中の失敗 (ディスク枯渇・強制終了など) で旧鍵だけが失われ、既存の暗号文を
    誰も復号できなくなるため。差し替えに成功するまで旧鍵はそのまま残る。

    Returns:
        ``(鍵ファイルのパス, 公開鍵文字列)``

    Raises:
        AgeKeyError: 既存の鍵があり ``force`` が偽のとき
    """
    path = Path(path) if path is not None else key_file_path()
    if path.exists() and not force:
        raise AgeKeyError(
            f"鍵ファイルが既に存在します: {path}\n"
            "上書きすると既存の暗号化ファイルを復号できなくなります。"
            "意図的に作り直す場合のみ --force を指定してください"
        )

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
    _io_common.write_secure_bytes_atomic(path, content.encode('utf-8'))
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
