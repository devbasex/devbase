"""``projects/<name>/project.local.yml`` (個人・機材ごとの設定) の読み込み (PLAN52)。

``project.yml`` はチームで共有される正であり、devbase-samples / devbase-ext の
リポジトリに載る。「このプロジェクトのコンテナは別ホストの Docker に立てる」は
個人の事情で、リモート側の HOME や docker グループの gid に至っては機材そのものに
依存する。共有ファイルに混ぜると同じ ``project.yml`` を使う他の人の ``up`` が壊れる
ため、gitignore 対象の別ファイルへ分ける。

スキーマ::

    docker:                 # 任意
      context: gpu-wsl      # 任意。`docker context ls` に出る名前
      home: /home/takemi    # 任意。リモート側の HOME (絶対パス)。bind mount の ~ を展開する
      gid: 999              # 任意。リモート側の docker グループ gid

``project.yml`` へ深くマージはしない (決定 2)。初期スコープの ``docker`` 節は
``project.yml`` に存在しないキーであり、マージする対象が無い。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from devbase.errors import ConfigError

#: 設定ファイル名 (プロジェクトディレクトリ直下)
PROJECT_LOCAL_CONFIG_FILENAME = "project.local.yml"

_TOP_LEVEL_KEYS = frozenset({"docker"})
_DOCKER_KEYS = frozenset({"context", "home", "gid"})


@dataclass(frozen=True)
class DockerSettings:
    """``docker`` 節 1 つ分。すべて省略可。"""

    context: Optional[str] = None
    home: Optional[str] = None
    gid: Optional[int] = None


@dataclass(frozen=True)
class ProjectLocalConfig:
    """``project.local.yml`` 1 ファイル分。いまは ``docker`` だけを持つ。"""

    docker: DockerSettings = DockerSettings()


def local_config_path(project_dir: Path) -> Path:
    return Path(project_dir) / PROJECT_LOCAL_CONFIG_FILENAME


def load_project_local_config(project_dir: Path) -> ProjectLocalConfig:
    """``<project_dir>/project.local.yml`` を読む。無い・空なら既定値を返す。

    Raises:
        ConfigError: YAML が壊れている / スキーマ違反。
    """
    path = local_config_path(project_dir)
    if not path.is_file():
        return ProjectLocalConfig()

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as e:
        raise ConfigError(f"{path} を UTF-8 として読めません ({e})") from e
    except OSError as e:
        raise ConfigError(f"{path} を読み込めません: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} の YAML を解釈できません: {e}") from e

    if raw is None:
        return ProjectLocalConfig()
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path} の最上位はマッピングである必要があります。")
    return parse_project_local_config(raw, source=str(path))


def parse_project_local_config(data: Mapping[str, Any], source: str) -> ProjectLocalConfig:
    """読み込み済みのマッピングを検証する (I/O を伴わない)。"""
    _reject_unknown_keys(data, _TOP_LEVEL_KEYS, source, "最上位")

    docker = data.get("docker")
    if docker is None:
        return ProjectLocalConfig()
    if not isinstance(docker, Mapping):
        raise ConfigError(f"{source}: docker はマッピングである必要があります。")
    _reject_unknown_keys(docker, _DOCKER_KEYS, source, "docker")

    return ProjectLocalConfig(docker=DockerSettings(
        context=_parse_context(docker.get("context"), source),
        home=_parse_home(docker.get("home"), source),
        gid=_parse_gid(docker.get("gid"), source),
    ))


def _reject_unknown_keys(data: Mapping[str, Any], allowed: frozenset,
                         source: str, where: str) -> None:
    unknown = sorted(str(key) for key in data if key not in allowed)
    if unknown:
        raise ConfigError(
            f"{source}: {where}に未知のキーがあります: {', '.join(unknown)} "
            f"(使えるキー: {', '.join(sorted(allowed))})")


def _parse_context(value: Any, source: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(
            f"{source}: docker.context は docker context の名前 (空でない文字列) です "
            f"({value!r})")
    if any(c.isspace() or not c.isprintable() for c in value):
        raise ConfigError(
            f"{source}: docker.context に空白文字・制御文字は使えません ({value!r})")
    return value


def _parse_home(value: Any, source: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("/"):
        raise ConfigError(
            f"{source}: docker.home はリモート側の絶対パス (/ で始まる文字列) です "
            f"({value!r})")
    return value


def _parse_gid(value: Any, source: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(
            f"{source}: docker.gid は 0 以上の整数です ({value!r})")
    return value


__all__ = [
    "PROJECT_LOCAL_CONFIG_FILENAME",
    "DockerSettings",
    "ProjectLocalConfig",
    "load_project_local_config",
    "local_config_path",
    "parse_project_local_config",
]
