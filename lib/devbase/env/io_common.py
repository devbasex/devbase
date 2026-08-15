"""env export / import で共通利用する I/O ヘルパ

io_export / io_import の両方で必要になる「ファイル不在を許容する passphrase 読み取り」
「省略時の既定 age 鍵 fallback」「0600 でセキュアにバイト列を書き出す」処理を
1 箇所に集約する。

書き出し先ディレクトリを掘る ``ensure_private_dir`` もここに置く。鍵ファイル
(``agekeys``) と機密の保存先 (``secrets/`` など) で同じ規則を使う必要があり、
実装が二重にあると片方だけ緩むため。
"""

from __future__ import annotations

import getpass
import os
import stat
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


def ensure_private_dir(path: Path, *, warn_if_permissive: bool = False) -> None:
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

    ``warn_if_permissive`` は既定で無効。鍵ファイルのように置き場を利用者が
    明示的に選ぶ経路では警告する価値があるが、``write_secure_bytes`` は
    ``$DEVBASE_ROOT`` 直下や export 先の CWD のような「緩くて当たり前」の
    ディレクトリにも書くため、常に鳴らすと本当の警告が埋もれる。
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
        if warn_if_permissive:
            warn_if_world_accessible(path)
        return

    # missing は子→親の順に積んであるので、逆順 (親→子) に作る
    for target in reversed(missing):
        try:
            target.mkdir(mode=0o700)
        except FileExistsError:
            # 並行して他プロセスが先に作った場合。既存ディレクトリは
            # 所有者の管轄として権限を触らない方針に合わせ、chmod しない。
            continue


def warn_if_world_accessible(path: Path) -> None:
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


def write_secure_bytes(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """``path`` に ``data`` を書き出す (新規・既存どちらも ``mode`` を強制)。

    ``open(..., 'wb')`` 直後に ``chmod`` する素朴な実装では、umask が緩い環境で
    作成→chmod の間にパーミッションが一瞬広がるウィンドウがある。これを避けるため:

      - 既存ファイルは書き込み前に ``chmod`` で権限を絞ってから ``O_TRUNC`` で上書き
      - ``os.open(..., flags, mode)`` で作成時点から ``mode`` を適用
      - mode 引数が無視される環境 (Windows 等) のため後追いでも ``chmod`` を試みる

    ``chmod`` が失敗するプラットフォームでは例外を握りつぶす (主に Windows)。

    親ディレクトリを新規に掘る場合は ``ensure_private_dir`` に任せて ``0700`` に
    する。``mkdir`` の既定は umask 依存で、``secrets/`` のような機密の置き場が
    0755 で生まれうるため (ファイルが 0600 でも、ディレクトリが読めると
    ファイル名の一覧から何を保存しているかは漏れる)。
    """
    ensure_private_dir(path.parent)
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

    親ディレクトリの扱いは ``write_secure_bytes`` と同じく ``ensure_private_dir``
    に任せる (新規に掘る階層だけ ``0700``、既存の権限は変えない)。
    """
    ensure_private_dir(path.parent)
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
