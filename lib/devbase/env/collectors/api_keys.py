"""APIキーコレクター"""

from devbase.env.keys import API_KEYS
from devbase.env.store import EnvFile, collect_key
from devbase.env.collector import Collector


def collect_api_keys(env_file: EnvFile) -> None:
    """各種APIキーを対話的に収集する"""
    print("\n=== API Keys ===")

    for key in API_KEYS:
        collect_key(env_file, key, mask_after=4)


COLLECTOR = Collector(
    name="api_keys",
    display_name="APIキー",
    collect_fn=collect_api_keys,
)
