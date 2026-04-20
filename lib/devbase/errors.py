"""devbase カスタム例外"""


class DevbaseError(Exception):
    """全devbaseエラーの基底"""


class PluginError(DevbaseError):
    """プラグイン操作エラー"""


class RepositoryError(DevbaseError):
    """リポジトリ操作エラー"""


class DockerError(DevbaseError):
    """Docker操作エラー"""


class ConfigError(DevbaseError):
    """設定エラー"""


class SnapshotError(DevbaseError):
    """スナップショット操作エラー"""
