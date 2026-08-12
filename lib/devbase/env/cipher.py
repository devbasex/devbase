"""age (pyrage) を用いた env バンドルの暗号化・復号"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

import pyrage

from devbase.errors import DevbaseError
from devbase.log import get_logger

logger = get_logger(__name__)


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
        # 空行と '#' で始まるコメント行を除いた有効行のみを取り出す。
        valid = [
            line.strip() for line in content.splitlines()
            if line.strip() and not line.strip().startswith('#')
        ]
        if not valid:
            raise CipherError(f"recipient ファイルに有効な行がありません: {path}")
        if len(valid) > 1:
            # 複数公開鍵を 1 ファイルに列挙したケース (team_keys.txt 等)。
            # 暗黙に「最初の 1 人」だけ採用するとチーム運用で暗号化が壊れるため、
            # 明示的に複数 `--recipient` で指定するよう要求する (PR #13 gemini 指摘)。
            raise CipherError(
                f"recipient ファイルに複数行の鍵が含まれています ({path}, {len(valid)} 件)。"
                "複数の公開鍵で暗号化したい場合は `--recipient @file_a.pub --recipient @file_b.pub` "
                "のように 1 ファイルにつき 1 鍵で指定してください"
            )
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

    # age-keygen が生成する秘密鍵ファイルは先頭に `# created: ...` などの
    # コメント行を含むため、`raw.strip().startswith(b'AGE-SECRET-KEY-1')` では
    # 検出できない。`_resolve_recipient` と同様に行単位で走査して、コメント /
    # 空行を除いた最初の有効行が AGE-SECRET-KEY-1 で始まるかで判定する
    # (PR #13 gemini 指摘)。
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        text = None
    if text is not None:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if stripped.startswith('AGE-SECRET-KEY-1'):
                try:
                    # pyrage.x25519.Identity.from_str は単独の AGE-SECRET-KEY-1
                    # 行のみを受け付けるため、ファイル全体ではなく該当行を渡す。
                    return pyrage.x25519.Identity.from_str(stripped)
                except Exception as e:
                    raise CipherError(
                        f"age 秘密鍵の解釈に失敗しました ({path}): {e}"
                    ) from e
            break  # 最初の有効行が AGE-SECRET-KEY-1 でなければ age 鍵ではない

    # ヘッダから判別できなかった場合のフォールバック。OpenSSH 互換の他形式
    # (rsa 以外の PEM など) を pyrage に任せて受け付ける。
    try:
        return pyrage.ssh.Identity.from_buffer(raw)
    except Exception as e:
        raise CipherError(
            f"秘密鍵の解釈に失敗しました ({path}): {e}\n"
            "対応形式: AGE-SECRET-KEY-1... / OpenSSH (ed25519, rsa)"
        ) from e


def validate_recipient(spec: str) -> None:
    """recipient 仕様文字列が解釈可能かを検証する (不正なら CipherError)。

    受信者リストへの登録時など、実際に暗号化する前に形式不正を弾くために使う。
    """
    _resolve_recipient(spec)


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

    # identities は「devbase 専用鍵 → ~/.ssh の既定鍵」のように複数候補を並べて
    # 渡される (agekeys.resolve_identities)。ここで 1 つでも解決に失敗した時点で
    # 例外にすると、壊れた / 読めない鍵ファイルが 1 つ混ざっているだけで後続の
    # 有効な鍵を試せず、旧来 ~/.ssh の鍵で暗号化した暗号文を移行期間中に復号
    # できるという意図が壊れる。そこで候補ごとに解決を試し、失敗した候補は
    # 理由を warning に残したうえで読み飛ばす。黙って捨てると「鍵を指定したのに
    # 復号できない」原因を利用者が追えなくなるため、ログは必須。
    resolved = []
    failures: List[str] = []
    for spec in identities:
        try:
            resolved.append(_resolve_identity(spec))
        except Exception as e:
            # 想定外の例外もここで握り潰さず失敗理由として蓄積する。全滅時には
            # 下で CipherError に含めて送出するので、情報は失われない。
            failures.append(f"{spec}: {e}")
            logger.warning(
                "identity を解決できなかったため復号候補から除外します (%s): %s",
                spec, e,
            )

    # 全候補が解決できなかったときだけ失敗させる。どの候補がなぜ駄目だったかを
    # 並べて示し、鍵の置き場所・権限・形式のどれが原因かを切り分けられるようにする。
    if not resolved:
        detail = '\n'.join(f"  - {f}" for f in failures)
        raise CipherError(
            "復号に使える identity がありません "
            f"(候補 {len(failures)} 件をいずれも解決できませんでした):\n{detail}"
        )

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
