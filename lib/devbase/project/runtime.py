"""``project.yml`` をコンテナ・エディタが使う形へ変換する層 (PLAN32)。

:mod:`devbase.project.config` が「読んで検証する」までを担い、ここは
「コンテナへ何を渡すか」「エディタに何を開かせるか」「``scale`` をどう書き戻すか」
という実行時の関心を持つ。
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any, Dict, Mapping

from devbase.errors import ConfigError
from devbase.project.config import (
    PROJECT_CONFIG_FILENAME,
    ProjectConfig,
    config_path,
    encode_repo_plan,
    load_project_config,
)

#: ``scale`` 未指定時のコンテナ数 (従来の ``CONTAINER_SCALE`` 既定値と同じ)
DEFAULT_SCALE = 2


def workspace_path(project_name: str) -> str:
    """複数 repo をまとめて開く workspace ファイルのコンテナ内パス。"""
    return f"/work/{project_name}.code-workspace"


def build_workspace_document(config: ProjectConfig) -> Dict[str, Any]:
    """VS Code の multi-root workspace ファイル (JSON) の中身を組み立てる。

    primary repo を先頭に置く。エディタのエクスプローラは並び順どおりに出るため、
    作業の起点になる repo が一番上に来る方が探しやすい。
    """
    repos = sorted(config.repos, key=lambda repo: not repo.primary)
    return {"folders": [{"name": repo.dir, "path": f"/work/{repo.dir}"}
                        for repo in repos]}


def container_env(config: ProjectConfig, project_name: str) -> Dict[str, str]:
    """dev コンテナへ渡す環境変数を組み立てる。

    - ``DEVBASE_REPOS``: clone プラン (base64)
    - ``DEVBASE_PRIMARY_DIR``: 起動後に ``cd`` する ``/work`` 配下のディレクトリ名
    - ``DEVBASE_WORKSPACE`` / ``DEVBASE_WORKSPACE_B64``: repo が 2 件以上のときだけ。
      1 件のときは従来どおりフォルダを開かせたいので付けない。

    値は base64 と検証済みの名前だけなので、``$`` や改行を含まず compose の
    変数展開に食われない。
    """
    env = {
        "DEVBASE_REPOS": encode_repo_plan(config.repos),
        "DEVBASE_PRIMARY_DIR": config.primary.dir,
    }
    if len(config.repos) > 1:
        document = json.dumps(build_workspace_document(config),
                              ensure_ascii=False, indent=2)
        env["DEVBASE_WORKSPACE"] = workspace_path(project_name)
        env["DEVBASE_WORKSPACE_B64"] = base64.b64encode(document.encode()).decode()
    return env


# ---------------------------------------------------------------------------
# scale (旧 CONTAINER_SCALE)
# ---------------------------------------------------------------------------

_SCALE_LINE = re.compile(r'^scale:.*$', re.M)
_VERSION_LINE = re.compile(r'^version:.*$', re.M)


def read_scale(project_dir: Path) -> int:
    """``project.yml`` の ``scale`` (未指定なら既定値)。"""
    config = load_project_config(project_dir)
    return config.scale if config.scale is not None else DEFAULT_SCALE


def write_scale(project_dir: Path, scale: int) -> None:
    """``project.yml`` の ``scale`` を書き換える (無ければ ``version`` の直後へ追加)。

    YAML を読み直して書き戻すとコメントと並び順が失われるため、行単位で置き換える。
    書き換えた結果は読み直して検証し、壊れていれば元へ戻す。
    """
    if scale < 1:
        raise ConfigError(f"scale は 1 以上の整数です ({scale!r})")

    path = config_path(project_dir)
    original = path.read_text(encoding="utf-8")

    if _SCALE_LINE.search(original):
        updated = _SCALE_LINE.sub(f"scale: {scale}", original, count=1)
    elif _VERSION_LINE.search(original):
        updated = _VERSION_LINE.sub(
            lambda m: f"{m.group(0)}\nscale: {scale}", original, count=1)
    else:
        raise ConfigError(
            f"{path}: version 行が見つからないため scale を書き込めません")

    path.write_text(updated, encoding="utf-8")
    try:
        load_project_config(project_dir)
    except ConfigError:
        path.write_text(original, encoding="utf-8")
        raise


def current_project_config(project_dir: Path = None) -> ProjectConfig:
    """カレントプロジェクト (既定は CWD) の設定を読む。

    wrapper (``bin/devbase``) と ``_resolve_project_name`` が対象プロジェクトへ
    cd 済みである前提。見つからなければ移行手順を含むエラーになる。
    """
    return load_project_config(Path(project_dir or Path.cwd()))


__all__ = [
    "DEFAULT_SCALE",
    "PROJECT_CONFIG_FILENAME",
    "build_workspace_document",
    "container_env",
    "current_project_config",
    "read_scale",
    "workspace_path",
    "write_scale",
]
