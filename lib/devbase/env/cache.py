"""サーバ backend の機密の控え — ``$DEVBASE_ROOT/secrets/cache/`` (PLAN51)

サーバから取得できた (または devbase 自身が書いた) 機密を age で暗号化して控え、
サーバへ到達できないときにそこから読む。控えは**サーバの内容の写し**であって
記録ではなく、最後にサーバと一致すると確かめられた 1 世代だけを持つ (決定 5)。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from devbase.env.secret_store import SECRETS_DIRNAME
from devbase.log import get_logger

logger = get_logger(__name__)

CACHE_DIRNAME = 'cache'
INDEX_FILENAME = 'index.json'


def cache_dir(devbase_root: Path) -> Path:
    return Path(devbase_root) / SECRETS_DIRNAME / CACHE_DIRNAME


def index_path(devbase_root: Path) -> Path:
    return cache_dir(devbase_root) / INDEX_FILENAME


def read_index(devbase_root: Path) -> Dict[str, Dict[str, Any]]:
    """``index.json`` の ``entries`` を返す (壊れていても空として扱う)。

    表示のためだけの情報であり、キャッシュを使えるかの判定には使わない。
    """
    path = index_path(devbase_root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    entries = data.get('entries') if isinstance(data, dict) else None
    return entries if isinstance(entries, dict) else {}
