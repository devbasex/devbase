"""devbase env export の高レベル実装"""

from __future__ import annotations

import getpass  # noqa: F401  (tests monkey-patch devbase.env.io_export.getpass)
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from devbase.errors import DevbaseError
from devbase.log import get_logger

from devbase.env import bundle as _bundle
from devbase.env import cipher as _cipher
from devbase.env import io_common as _io_common
from devbase.env import storage as _storage

logger = get_logger(__name__)

# 平文出力時に "機密キーが含まれます" の警告を出す判定パターン
_SENSITIVE_PATTERNS = ('KEY', 'SECRET', 'TOKEN', 'PASSWORD', 'CREDENTIALS', 'BASE64')
_ENV_KEY_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=', re.MULTILINE)


class ExportError(DevbaseError):
    """export エラー"""


@dataclass
class ExportOptions:
    dest: Optional[str] = None
    include_global: bool = True
    include_metadata: bool = True
    include_projects: Optional[List[str]] = None
    exclude_projects: List[str] = field(default_factory=list)
    recipients: List[str] = field(default_factory=list)
    passphrase_env: Optional[str] = None
    passphrase_stdin: bool = False
    force_unencrypted: bool = False
    # S3 backend 専用: バケット既定暗号化が未設定でも export を許可するか
    # (オブジェクト単位の SSE はこのフラグに関係なく常に付与される)
    unsafe_allow_unencrypted_bucket: bool = False


def _default_dest(force_unencrypted: bool) -> str:
    # microsecond まで含めて衝突を回避する (PR #22 codex round 3 指摘)
    ts = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    suffix = '.dbenv.tar.gz' if force_unencrypted else '.dbenv'
    return f'./devbase-env-{ts}{suffix}'


def _default_filename(force_unencrypted: bool) -> str:
    """`_default_dest` の `./` prefix を除いたファイル名部分のみを返す。
    dest がディレクトリ的なときに append する用途。"""
    return _default_dest(force_unencrypted).removeprefix('./')


def _complete_dir_dest(dest: str, force_unencrypted: bool) -> str:
    """dest が「ディレクトリ的」なら既定ファイル名を補完する (`aws s3 cp` 互換、#24)。

    - S3 URI で末尾が `/`: `s3://bucket/prefix/` → `s3://bucket/prefix/<default>`
    - ローカルで既存ディレクトリ: `/tmp/out/` (または末尾 `/` なし) → `/tmp/out/<default>`
    - それ以外 (フルキー / 通常ファイルパス / stdio `-`) はそのまま返す。
    """
    if _storage.is_stdio(dest):
        return dest
    name = _default_filename(force_unencrypted)
    if _storage.is_s3(dest):
        return dest + name if dest.endswith('/') else dest
    # ローカル: 既存ディレクトリか末尾 `/` ならディレクトリ扱い
    p = Path(dest)
    if dest.endswith('/') or dest.endswith(os.sep) or p.is_dir():
        return str(p / name)
    return dest


def _read_passphrase(opts: ExportOptions) -> Optional[str]:
    """既存テストとの互換のために残している thin wrapper。
    実体は :mod:`devbase.env.io_common.read_passphrase`。"""
    return _io_common.read_passphrase(
        opts.passphrase_env, opts.passphrase_stdin, ExportError
    )


def _sensitive_keys(entries: Sequence[_bundle.BundleEntry]) -> List[str]:
    """平文出力に含まれる機密キー候補を返す (警告表示用、.env エントリのみ走査)"""
    hits: set[str] = set()
    for entry in entries:
        if not entry.arcname.endswith('.env'):
            continue
        try:
            text = entry.data.decode('utf-8', errors='ignore')
        except Exception:
            continue
        for key in _ENV_KEY_RE.findall(text):
            if any(p in key.upper() for p in _SENSITIVE_PATTERNS):
                hits.add(key)
    return sorted(hits)


def _validate_options(opts: ExportOptions) -> None:
    # NOTE: DEST='-' (stdout) と --passphrase-stdin の併用は許可する。
    # export は stdin (passphrase) と stdout (bundle) で別ストリームを使うため
    # `echo "pass" | devbase env export - --passphrase-stdin > out` は適法。
    # (import 側は両方 stdin なので併用不可。io_import._validate_options 参照)
    if opts.passphrase_env and opts.passphrase_stdin:
        raise ExportError("--passphrase-env と --passphrase-stdin は併用できません")
    if (opts.passphrase_env or opts.passphrase_stdin) and opts.recipients:
        raise ExportError(
            "--recipient と --passphrase-env/--passphrase-stdin は併用できません"
        )
    if opts.force_unencrypted and (
        opts.recipients or opts.passphrase_env or opts.passphrase_stdin
    ):
        raise ExportError(
            "--force-unencrypted は recipient / passphrase と併用できません"
        )


def _encrypt_payload(tar_blob: bytes, opts: ExportOptions) -> bytes:
    """``opts`` の鍵指定に従って tar.gz を暗号化する。鍵が無ければ既定鍵を試す"""
    passphrase = _read_passphrase(opts)
    # NOTE: --recipient と --passphrase-* の排他チェックは _validate_options で
    # fail-fast 済み。cipher.encrypt 側にも防御的チェックがある。
    recipients = (
        [] if passphrase is not None
        else _io_common.resolve_recipient_specs(opts.recipients)
    )
    if not recipients and not passphrase:
        raise ExportError(
            "暗号化キーが指定されていません。次のいずれかを指定してください:\n"
            "  --recipient KEY            age / OpenSSH 公開鍵\n"
            "  --passphrase-env VAR       環境変数からパスフレーズ取得\n"
            "  --passphrase-stdin         stdin の最初の行をパスフレーズとして使用\n"
            "  --force-unencrypted        平文 tar.gz として書き出す (機密キー検知時は警告)\n"
            "  ~/.ssh/id_ed25519.pub または ~/.ssh/id_rsa.pub があれば "
            "--recipient 省略時の既定として使用されます (ed25519 優先)"
        )
    return _cipher.encrypt(tar_blob, recipients=recipients, passphrase=passphrase)


def _warn_if_plaintext_sensitive(entries: Sequence[_bundle.BundleEntry]) -> None:
    sensitive = _sensitive_keys(entries)
    if not sensitive:
        return
    head = ', '.join(sensitive[:10])
    suffix = ' ...' if len(sensitive) > 10 else ''
    logger.warning("平文 export に機密キーが含まれます: %s%s", head, suffix)
    logger.warning(
        "ファイルパーミッションは 0600 で書き出されますが、保管・転送時の暗号化を強く推奨します"
    )


def export(devbase_root: Path, opts: ExportOptions) -> int:
    """export 本体。CLI ハンドラから呼ばれる"""
    _validate_options(opts)

    entries = _bundle.make_entries_from_disk(
        devbase_root,
        include_global=opts.include_global,
        include_metadata=opts.include_metadata,
        include_projects=opts.include_projects,
        exclude_projects=opts.exclude_projects,
    )
    if not entries:
        raise ExportError(
            "export 対象のファイルがありません "
            "(--no-global / --exclude-project の指定や DEVBASE_ROOT を確認してください)"
        )

    logger.info("export 対象 %d 件:", len(entries))
    for entry in entries:
        logger.info("  - %s (%d bytes) <- %s",
                    entry.arcname, len(entry.data), entry.origin)

    tar_blob = _bundle.pack(entries)
    logger.debug("tar.gz サイズ: %d bytes", len(tar_blob))

    if opts.force_unencrypted:
        # NOTE: --force-unencrypted と鍵指定の排他チェックは _validate_options で
        # fail-fast 済み。ここでは平文出力の警告のみ。
        _warn_if_plaintext_sensitive(entries)
        payload = tar_blob
    else:
        payload = _encrypt_payload(tar_blob, opts)
        logger.debug("暗号化後サイズ: %d bytes", len(payload))

    dest = opts.dest or _default_dest(opts.force_unencrypted)
    # dest が「ディレクトリ的」なら `aws s3 cp` 互換でファイル名を自動補完する (#24)。
    # 末尾 `/` の S3 URI で空キーオブジェクトが作られる事故と、ローカル既存
    # ディレクトリへの OSError fail-fast の両方を救う。
    if opts.dest:
        completed = _complete_dir_dest(dest, opts.force_unencrypted)
        if completed != dest:
            logger.info("dest がディレクトリ的なためファイル名を補完: %s", completed)
            dest = completed
    # 既定名 (opts.dest 未指定) かつローカルパスの場合、既存ファイルの上書きを拒否する
    # (microsecond 精度でも理論上は衝突しうるため防御的にチェック)
    if not opts.dest and not _storage.is_s3(dest) and not _storage.is_stdio(dest):
        if Path(dest).exists():
            raise ExportError(
                f"既定出力先 {dest} が既に存在します。"
                "出力先を明示的に指定するか、既存ファイルを移動してください"
            )
    # S3 など backend 固有のオプションを渡したい場合は s3_options を組み立てる。
    # それ以外 (local/stdio) では未使用なので無害。
    s3_options = (_storage.S3Options.from_env(
        unsafe_allow_unencrypted_bucket=opts.unsafe_allow_unencrypted_bucket,
    ) if _storage.is_s3(dest) else None)
    backend = _storage.resolve(dest, s3_options=s3_options)
    backend.write_bytes(dest, payload)

    if _storage.is_stdio(dest):
        logger.info("export 完了 (stdout, %d bytes)", len(payload))
    else:
        logger.info("export 完了: %s (%d bytes)", dest, len(payload))
    return 0
