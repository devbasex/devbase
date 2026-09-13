"""生成物の bind mount の ``~`` をリモート側の HOME で展開する (PLAN52)。

compose クライアントは bind mount の ``~`` を**手元の** HOME へ展開してから daemon へ
渡す。daemon が別ホストにあると、そのパスはリモートに無く、Linux の daemon は存在しない
パスを空ディレクトリとして作ってしまう (黙って空になる)。起動時に ``-f`` で渡すのは
生成物 ``.docker-compose.scale.yml`` だけなので、生成の段階で絶対パスへ書き換えれば
展開は起きず、そのままリモートへ渡る (決定 7)。

書き換えるのは ``~`` と ``~/...`` だけである。``~user/...`` は別のユーザの HOME を指し、
``./`` / ``../`` の相対パスは compose が手元の絶対パスへ解決するもので、どちらも
devbase が「正しい値」を推測できないため、黙って書き換えるより一覧で示す (決定 8)。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def expand_home(services: Dict[str, Any], home: str) -> List[str]:
    """各サービスの bind mount の ``~`` を ``home`` に置き換える。

    Returns:
        置き換えられなかった mount (``~user`` / 相対パス) の一覧 (``"<service>: <mount>"``)。
    """
    home = home.rstrip('/') or '/'
    warnings: List[str] = []
    for name, service in services.items():
        volumes = _volumes(service)
        for i, vol in enumerate(volumes):
            source, rest = _split(vol)
            if source is None:
                continue
            if source == '~' or source.startswith('~/'):
                new_source = home + source[1:]
                volumes[i] = _join(vol, new_source, rest)
            elif _needs_remote_path(source):
                warnings.append(f"{name}: {_display(vol)}")
    return warnings


def collect_remote_warnings(services: Dict[str, Any]) -> List[str]:
    """``home`` が無いリモート扱いで、リモートに存在しないパスを指す mount を集める。

    書き換えは行わない。``~`` 系と相対パスの両方を載せる。
    """
    warnings: List[str] = []
    for name, service in services.items():
        for vol in _volumes(service):
            source, _ = _split(vol)
            if source is None:
                continue
            if source == '~' or source.startswith('~') or _needs_remote_path(source):
                warnings.append(f"{name}: {_display(vol)}")
    return warnings


def _volumes(service: Any) -> list:
    if not isinstance(service, dict):
        return []
    volumes = service.get('volumes')
    return volumes if isinstance(volumes, list) else []


def _split(vol: Any) -> Tuple[Optional[str], Any]:
    """bind mount なら (source, 残り) を返す。named volume や不明な形は (None, None)。"""
    if isinstance(vol, str):
        parts = vol.split(':')
        if len(parts) < 2:
            return None, None
        source = parts[0]
        if not _looks_like_path(source):
            return None, None
        return source, parts[1:]
    if isinstance(vol, dict):
        if vol.get('type') not in (None, 'bind'):
            return None, None
        source = vol.get('source')
        if isinstance(source, str) and _looks_like_path(source):
            return source, None
    return None, None


def _join(vol: Any, new_source: str, rest: Any) -> Any:
    if isinstance(vol, str):
        return ':'.join([new_source, *rest])
    vol['source'] = new_source
    return vol


def _looks_like_path(source: str) -> bool:
    """compose が bind mount の source と解釈する形 (``/`` ``.`` ``~`` 始まり)。"""
    return source.startswith(('/', '.', '~'))


def _needs_remote_path(source: str) -> bool:
    """手元でしか解決できないパス: ``~user/...`` と相対パス。"""
    if source.startswith('~') and source != '~' and not source.startswith('~/'):
        return True
    return source.startswith(('./', '../')) or source in ('.', '..')


def _display(vol: Any) -> str:
    if isinstance(vol, str):
        return vol
    return f"{vol.get('source')}:{vol.get('target')}"


__all__ = ["collect_remote_warnings", "expand_home"]
