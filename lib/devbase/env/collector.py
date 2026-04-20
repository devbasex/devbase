"""コレクターの基底クラスと自動検出レジストリ"""

import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Callable, List

from devbase.log import get_logger

logger = get_logger(__name__)


@dataclass
class Collector:
    """認証情報・設定のコレクター定義"""
    name: str
    display_name: str
    collect_fn: Callable
    source_files: List[str] = field(default_factory=list)
    source_type: str = ""  # "tar_base64", "file_base64", "named_profiles", ""


class CollectorRegistry:
    """env/collectors/ 配下のコレクターモジュールを自動検出・登録する"""

    def __init__(self):
        self._collectors: List[Collector] = []

    def discover(self) -> None:
        """devbase.env.collectors パッケージ内のモジュールを走査し、
        COLLECTOR定数を持つモジュールを登録する"""
        import devbase.env.collectors as pkg

        for importer, modname, ispkg in pkgutil.iter_modules(pkg.__path__):
            if modname.startswith('_'):
                continue
            try:
                module = importlib.import_module(f'devbase.env.collectors.{modname}')
                if hasattr(module, 'COLLECTOR'):
                    self._collectors.append(module.COLLECTOR)
            except Exception as e:
                logger.warning("コレクター '%s' の読み込みに失敗: %s", modname, e)

    @property
    def collectors(self) -> List[Collector]:
        return list(self._collectors)
