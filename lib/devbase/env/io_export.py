"""devbase env export の高レベル実装"""

from __future__ import annotations

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
from devbase.env import storage as _storage

logger = get_logger(__name__)

# 機密情報の検出パターン (平文出力時の警告用)
_SENSITIVE_PATTERNS = ('KEY', 'SECRET', 'TOKEN', 'PASSWORD', 'CREDENTIALS', 'BASE64')


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


def _default_dest(force_unencrypted: bool) -> str:
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    suffix = '.dbenv.tar.gz' if force_unencrypted else '.dbenv'
    return f'./devbase-env-{ts}{suffix}'


def _resolve_recipients(specs: Sequence[str]) -> List[str]:
    """recipient 指定の解決。空なら既定鍵 (~/.ssh/id_rsa.pub) を試みる"""
    if specs:
        return list(specs)
    for path in _cipher.default_recipient_paths():
        if path.exists():
            logger.info("recipient 既定鍵を使用: %s", path)
            return [f'@{path}']
    return []


def _read_passphrase(opts: ExportOptions) -> Optional[str]:
    if opts.passphrase_env:
        value = os.environ.get(opts.passphrase_env)
        if not value:
            raise ExportError(
                f"環境変数 {opts.passphrase_env} が空または未設定です"
            )
        return value
    if opts.passphrase_stdin:
        import sys
        line = sys.stdin.readline()
        if not line:
            raise ExportError("stdin からパスフレーズを読み取れませんでした")
        return line.rstrip('\n')
    return None


def _has_sensitive_keys(entries) -> List[str]:
    """env 形式のテキストから機密キーを抽出する (平文出力時の警告用)"""
    hits = set()
    key_re = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=', re.MULTILINE)
    for entry in entries:
        if not entry.arcname.endswith('.env'):
            continue
        try:
            text = entry.data.decode('utf-8', errors='ignore')
        except Exception:
            continue
        for key in key_re.findall(text):
            upper = key.upper()
            if any(p in upper for p in _SENSITIVE_PATTERNS):
                hits.add(key)
    return sorted(hits)


def export(devbase_root: Path, opts: ExportOptions) -> int:
    """export 本体。CLI ハンドラから呼ばれる"""
    # 引数組み合わせの早期検証
    if opts.passphrase_stdin and opts.dest == '-':
        raise ExportError(
            "DEST='-' (stdout) と --passphrase-stdin は併用できません "
            "(stdin/stdout が衝突します)"
        )
    if opts.passphrase_env and opts.passphrase_stdin:
        raise ExportError("--passphrase-env と --passphrase-stdin は併用できません")

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
        if opts.recipients or opts.passphrase_env or opts.passphrase_stdin:
            raise ExportError(
                "--force-unencrypted は recipient / passphrase と併用できません"
            )
        sensitive = _has_sensitive_keys(entries)
        if sensitive:
            logger.warning(
                "平文 export に機密キーが含まれます: %s",
                ', '.join(sensitive[:10]) + (' ...' if len(sensitive) > 10 else '')
            )
            logger.warning(
                "ファイルパーミッションは 0600 で書き出されますが、保管・転送時の暗号化を強く推奨します"
            )
        payload = tar_blob
    else:
        passphrase = _read_passphrase(opts)
        recipients = _resolve_recipients(opts.recipients) if passphrase is None else []
        if not recipients and not passphrase:
            raise ExportError(
                "暗号化キーが指定されていません。次のいずれかを指定してください:\n"
                "  --recipient KEY            age / OpenSSH 公開鍵\n"
                "  --passphrase-env VAR       環境変数からパスフレーズ取得\n"
                "  --passphrase-stdin         stdin の最初の行をパスフレーズとして使用\n"
                "  --force-unencrypted        平文 tar.gz として書き出す (機密キー検知時は警告)\n"
                "  ~/.ssh/id_rsa.pub があれば --recipient 省略時の既定として使用されます"
            )
        payload = _cipher.encrypt(tar_blob, recipients=recipients, passphrase=passphrase)
        logger.debug("暗号化後サイズ: %d bytes", len(payload))

    dest = opts.dest or _default_dest(opts.force_unencrypted)
    backend = _storage.resolve(dest)
    backend.write_bytes(dest, payload)

    if _storage.is_stdio(dest):
        logger.info("export 完了 (stdout, %d bytes)", len(payload))
    else:
        logger.info("export 完了: %s (%d bytes)", dest, len(payload))
    return 0
