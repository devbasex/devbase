"""env export / import で共通利用する I/O ヘルパ

io_export / io_import の両方で必要になる「ファイル不在を許容する passphrase 読み取り」
「省略時の既定 age 鍵 fallback」「0600 でセキュアにバイト列を書き出す」処理を
1 箇所に集約する。
"""

from __future__ import annotations

import getpass
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Type

from devbase.errors import DevbaseError
from devbase.log import get_logger

from devbase.env import cipher as _cipher

logger = get_logger(__name__)


def read_passphrase(
    passphrase_env: Optional[str],
    passphrase_stdin: bool,
    error_class: Type[DevbaseError],
) -> Optional[str]:
    """env 変数 / stdin から passphrase を読み取る。どちらも指定が無ければ ``None``。

    両方指定済みかなどの組み合わせ検証は呼び出し側の責務 (エラーメッセージを
    文脈に合わせるため)。tty 入力時は ``getpass.getpass`` でエコー抑止、
    パイプ入力時は ``stdin.readline()`` で 1 行読む。
    """
    if passphrase_env:
        value = os.environ.get(passphrase_env)
        if not value:
            raise error_class(f"環境変数 {passphrase_env} が空または未設定です")
        return value
    if passphrase_stdin:
        if sys.stdin.isatty():
            try:
                return getpass.getpass("passphrase: ", stream=sys.stderr)
            except EOFError as e:
                raise error_class("stdin からパスフレーズを読み取れませんでした") from e
        line = sys.stdin.readline()
        if not line:
            raise error_class("stdin からパスフレーズを読み取れませんでした")
        # CRLF (Windows/WSL からのパイプ) を考慮して \r も剥がす。
        # パスフレーズ末尾に \r が残ると複合化が一致せず原因不明の失敗になる。
        return line.rstrip('\r\n')
    return None


def resolve_recipient_specs(specs: Sequence[str]) -> List[str]:
    """recipient 指定の解決。

    明示指定があればそのまま返す。空なら ``~/.ssh/id_ed25519.pub`` → ``id_rsa.pub``
    の順で存在する公開鍵を探し、最初に見つかったものを ``@PATH`` 参照として返す。
    """
    if specs:
        return list(specs)
    for path in _cipher.default_recipient_paths():
        if path.exists():
            logger.info("recipient 既定鍵を使用: %s", path)
            return [f'@{path}']
    return []


def resolve_identity_specs(specs: Sequence[str]) -> List[str]:
    """identity 指定の解決。

    明示指定があればそのまま返す。空なら ``~/.ssh/id_ed25519`` / ``id_rsa`` の
    うち **存在するものをすべて** 返す。``pyrage.decrypt`` は複数 identity を
    受け付け、バンドル内の暗号化対象と一致した identity だけ復号に使われるため、
    両方を渡しておけば「どの鍵で暗号化されたか分からない」状況でも復号できる
    (PR #13 gemini 指摘)。一方 ``resolve_recipient_specs`` は明確に「どの鍵で
    暗号化するか」を選ぶ必要があるため最初の 1 つだけを返す (非対称な仕様)。
    """
    if specs:
        return list(specs)
    found: List[str] = []
    for path in _cipher.default_identity_paths():
        if path.exists():
            logger.info("identity 既定鍵を使用: %s", path)
            found.append(str(path))
    return found


def write_secure_bytes(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """``path`` に ``data`` を書き出す (新規・既存どちらも ``mode`` を強制)。

    ``open(..., 'wb')`` 直後に ``chmod`` する素朴な実装では、umask が緩い環境で
    作成→chmod の間にパーミッションが一瞬広がるウィンドウがある。これを避けるため:

      - 既存ファイルは書き込み前に ``chmod`` で権限を絞ってから ``O_TRUNC`` で上書き
      - ``os.open(..., flags, mode)`` で作成時点から ``mode`` を適用
      - mode 引数が無視される環境 (Windows 等) のため後追いでも ``chmod`` を試みる

    ``chmod`` が失敗するプラットフォームでは例外を握りつぶす (主に Windows)。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, mode)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _fsync_dir(directory: Path) -> None:
    """ディレクトリエントリを fsync する (対応しない環境では黙って諦める)。

    ``os.replace`` 自体は atomic でも、rename の記録がディスクへ届く前に電源断
    すると差し替えが失われうる。ディレクトリを fsync して rename を永続化する。
    Windows などディレクトリを開けない環境では何もしない。
    """
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_secure_bytes_atomic(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """``path`` の中身を ``data`` へ **atomic に** 差し替える (``mode`` を強制)。

    ``write_secure_bytes`` は既存ファイルを ``O_TRUNC`` で直接上書きするため、
    ディスク枯渇やプロセス中断が起きると「旧内容は消えたが新内容も揃っていない」
    中途半端なファイルが残る。age 鍵のように失うと復旧不能なファイルでは、

      - 同一ディレクトリの一時ファイルへ ``0600`` で書く (別 FS だと rename が
        atomic にならないため、必ず同じディレクトリに作る)
      - ``fsync`` して中身をディスクへ確定させる
      - ``os.replace`` で差し替え、ディレクトリも ``fsync`` する

    という順序にして、途中のどこで失敗しても旧内容がそのまま残るようにする。
    失敗時は一時ファイルを掃除してから例外を送出する。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp は 0600 で作成するため、作成時点から権限が広がらない。
    fd, tmp_name = tempfile.mkstemp(
        prefix=f'.{path.name}.', suffix='.tmp', dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # mkstemp の mode が無視される環境 (Windows 等) に備えて明示的に揃える
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)
