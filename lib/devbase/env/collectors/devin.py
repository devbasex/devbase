"""Devin API設定コレクター"""

from devbase.log import get_logger
from devbase.env.keys import DEVIN_KEYS
from devbase.env.store import EnvFile, safe_input, collect_key
from devbase.env.collector import Collector

logger = get_logger(__name__)


def collect_devin_settings(env_file: EnvFile) -> None:
    """Devin API設定を対話的に収集する"""
    print("\n=== Devin API設定 ===")

    existing_api_key = env_file.get(DEVIN_KEYS[0])
    if existing_api_key:
        logger.info("%s: 設定済み (長さ: %d)", DEVIN_KEYS[0], len(existing_api_key))
        for key in [k for k in DEVIN_KEYS[1:] if env_file.get(k)]:
            logger.info("%s: 設定済み", key)

        update = safe_input("Devin設定を更新しますか? [y/N]: ", "n")
        if update.lower() != 'y':
            return
        # 更新する場合は既存値を削除してからcollect_key()で再収集
        for key in DEVIN_KEYS:
            env_file.delete(key)

    configure = safe_input("\nDevin APIを設定しますか? [y/N]: ", "n")
    if configure.lower() != 'y':
        logger.info("Devin設定をスキップしました")
        return

    for key in DEVIN_KEYS:
        collect_key(env_file, key, mask_after=4)

    if env_file.get(DEVIN_KEYS[0]):
        logger.info("Devin API設定が完了しました")
    else:
        logger.info("Devin設定をスキップしました")


COLLECTOR = Collector(
    name="devin",
    display_name="Devin",
    collect_fn=collect_devin_settings,
)
