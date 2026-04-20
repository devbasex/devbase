"""Slack認証情報コレクター"""

from devbase.env.keys import SLACK_KEYS
from devbase.env.store import EnvFile, collect_key
from devbase.env.collector import Collector


def collect_slack_credentials(env_file: EnvFile) -> None:
    """Slack認証情報を対話的に収集する"""
    print("\n=== Slack認証情報 ===")

    for key in SLACK_KEYS:
        collect_key(env_file, key)


COLLECTOR = Collector(
    name="slack",
    display_name="Slack",
    collect_fn=collect_slack_credentials,
)
