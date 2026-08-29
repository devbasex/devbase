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


class SnapshotCommandError(SnapshotError):
    """スナップショットのコンテナ内コマンドが失敗した。

    呼び出し側が**失敗の中身**で分岐できるよう ``stderr`` を持つ
    (復元は tar の rename エラーだけを警告として飲み込む — PLAN40)。
    """

    def __init__(self, message: str, stderr: str = ''):
        super().__init__(message)
        self.stderr = stderr
