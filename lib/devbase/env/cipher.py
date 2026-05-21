"""age (pyrage) を用いた env バンドルの暗号化・復号"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

import pyrage

from devbase.errors import DevbaseError


class CipherError(DevbaseError):
    """暗号化・復号エラー"""


_MAX_RECIPIENT_REF_DEPTH = 5


def _resolve_recipient(spec: str, _depth: int = 0):
    """recipient 仕様文字列を pyrage Recipient に解決する

    形式:
      'age1...'              -> X25519 公開鍵
      'ssh-ed25519 AAAA...'  -> OpenSSH ed25519 公開鍵
      'ssh-rsa AAAA...'      -> OpenSSH RSA 公開鍵
      '@PATH'                -> ファイル参照 (中身を再帰的に解釈, 深さ上限あり)
    """
    spec = spec.strip()
    if not spec:
        raise CipherError("recipient が空です")

    if spec.startswith('@'):
        if _depth >= _MAX_RECIPIENT_REF_DEPTH:
            raise CipherError(
                f"recipient の @PATH 参照が深すぎます (上限={_MAX_RECIPIENT_REF_DEPTH})。"
                "循環参照の可能性があります"
            )
        path = Path(spec[1:]).expanduser()
        if not path.exists():
            raise CipherError(f"recipient ファイルが見つかりません: {path}")
        try:
            content = path.read_text(encoding='utf-8')
        except UnicodeDecodeError as e:
            raise CipherError(
                f"recipient ファイルの UTF-8 デコードに失敗しました: {path}: {e}"
            ) from e
        except OSError as e:
            raise CipherError(
                f"recipient ファイルの読み込みに失敗しました ({path}): {e}"
            ) from e
        # ファイル中に複数行 / コメント / 空行が混在していても扱えるよう、
        # 空行と '#' で始まるコメント行を除いた最初の有効行を採用する。
        valid = [
            line.strip() for line in content.splitlines()
            if line.strip() and not line.strip().startswith('#')
        ]
        if not valid:
            raise CipherError(f"recipient ファイルに有効な行がありません: {path}")
        return _resolve_recipient(valid[0], _depth + 1)

    if spec.startswith('age1'):
        try:
            return pyrage.x25519.Recipient.from_str(spec)
        except Exception as e:
            raise CipherError(f"age 公開鍵の解釈に失敗しました: {e}") from e

    if spec.startswith('ssh-ed25519 ') or spec.startswith('ssh-rsa '):
        try:
            return pyrage.ssh.Recipient.from_str(spec)
        except Exception as e:
            raise CipherError(f"OpenSSH 公開鍵の解釈に失敗しました: {e}") from e

    if spec.startswith('ssh-'):
        raise CipherError(
            f"age は ssh-ed25519 / ssh-rsa のみ対応です (入力: {spec.split()[0]})。"
            "ssh-ecdsa / ssh-dss などは `age-keygen` で age 専用鍵を生成してください"
        )

    raise CipherError(
        f"recipient の形式を判別できません: {spec[:32]!r}... "
        "(対応形式: age1... / ssh-ed25519 ... / ssh-rsa ... / @PATH)"
    )


def _resolve_identity(path_spec: str):
    """秘密鍵ファイルパスを pyrage Identity に解決する"""
    path = Path(path_spec).expanduser()
    if not path.exists():
        raise CipherError(f"identity ファイルが見つかりません: {path}")

    try:
        raw = path.read_bytes()
    except OSError as e:
        raise CipherError(f"identity ファイルの読み込みに失敗しました ({path}): {e}") from e

    # OpenSSH 秘密鍵は PEM 風の決まったヘッダを持つため、age 鍵より先に
    # ヘッダで判別する。これにより鍵形式判別が明示的になり、将来の鍵形式
    # 追加時にも分岐を増やすだけで済む。
    if b'-----BEGIN OPENSSH PRIVATE KEY-----' in raw:
        try:
            return pyrage.ssh.Identity.from_buffer(raw)
        except Exception as e:
            raise CipherError(
                f"OpenSSH 秘密鍵の解釈に失敗しました ({path}): {e}"
            ) from e

    if raw.strip().startswith(b'AGE-SECRET-KEY-1'):
        try:
            text = raw.decode('utf-8').strip()
        except UnicodeDecodeError as e:
            raise CipherError(f"age 秘密鍵が UTF-8 でデコードできません ({path}): {e}") from e
        try:
            return pyrage.x25519.Identity.from_str(text)
        except Exception as e:
            raise CipherError(f"age 秘密鍵の解釈に失敗しました ({path}): {e}") from e

    # ヘッダから判別できなかった場合のフォールバック。OpenSSH 互換の他形式
    # (rsa 以外の PEM など) を pyrage に任せて受け付ける。
    try:
        return pyrage.ssh.Identity.from_buffer(raw)
    except Exception as e:
        raise CipherError(
            f"秘密鍵の解釈に失敗しました ({path}): {e}\n"
            "対応形式: AGE-SECRET-KEY-1... / OpenSSH (ed25519, rsa)"
        ) from e


def encrypt(data: bytes,
            recipients: Sequence[str] = (),
            passphrase: Optional[str] = None) -> bytes:
    """data を age で暗号化する

    recipients と passphrase のどちらか一方のみ指定する。両方指定はエラー。
    """
    if passphrase and recipients:
        raise CipherError("recipient と passphrase は同時に指定できません")

    if passphrase is not None:
        if not passphrase:
            raise CipherError("passphrase が空です")
        try:
            return pyrage.passphrase.encrypt(data, passphrase)
        except Exception as e:
            raise CipherError(f"passphrase 暗号化に失敗しました: {e}") from e

    if not recipients:
        raise CipherError("recipient または passphrase を指定してください")

    resolved = [_resolve_recipient(r) for r in recipients]
    try:
        return pyrage.encrypt(data, resolved)
    except Exception as e:
        raise CipherError(f"recipient 暗号化に失敗しました: {e}") from e


def decrypt(data: bytes,
            identities: Sequence[str] = (),
            passphrase: Optional[str] = None) -> bytes:
    """age 暗号化済みデータを復号する"""
    if passphrase and identities:
        raise CipherError("identity と passphrase は同時に指定できません")

    if passphrase is not None:
        if not passphrase:
            raise CipherError("passphrase が空です")
        try:
            return pyrage.passphrase.decrypt(data, passphrase)
        except Exception as e:
            raise CipherError(
                "passphrase 復号に失敗しました (パスフレーズが誤っている可能性があります)"
            ) from e

    if not identities:
        raise CipherError("identity または passphrase を指定してください")

    resolved = [_resolve_identity(p) for p in identities]
    try:
        return pyrage.decrypt(data, resolved)
    except Exception as e:
        raise CipherError(
            "復号に失敗しました (identity が一致しない / バンドルが破損している可能性があります)"
        ) from e


def default_recipient_paths() -> List[Path]:
    """recipient 省略時に試す既定の公開鍵パス候補

    ed25519 を優先し、次に rsa を試す。
    """
    ssh = Path.home() / '.ssh'
    return [ssh / 'id_ed25519.pub', ssh / 'id_rsa.pub']


def default_identity_paths() -> List[Path]:
    """identity 省略時に試す既定の秘密鍵パス候補

    ed25519 を優先し、次に rsa を試す。
    """
    ssh = Path.home() / '.ssh'
    return [ssh / 'id_ed25519', ssh / 'id_rsa']
