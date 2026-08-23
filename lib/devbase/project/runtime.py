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
    return {"folders": [_workspace_folder(repo) for repo in _workspace_repos(config)]}


def encode_workspace_folders(config: ProjectConfig) -> str:
    r"""workspace の folder を 1 行 1 件へ直列化する (PLAN37 の wire format)。

    ``<dir><US><folder オブジェクトの JSON>`` の行を LF で連ね、全体を base64 化する。
    entrypoint は ``<dir>`` で clone の成否を確かめ、生き残った行の JSON だけを
    連結して workspace を書き出す。**ホストが JSON を直列化しておくことが要点**で、
    ``dir`` に ``"`` や ``\`` が入っていてもシェル側でエスケープを考えずに済む。

    ``dir`` は ``project.yml`` のローダが空白・制御文字を弾いているため US / LF が
    フィールドを割ることはなく、JSON 側も ``json.dumps`` が制御文字を
    エスケープするので 1 行に収まる。
    """
    lines = []
    for repo in _workspace_repos(config):
        folder = json.dumps(_workspace_folder(repo), ensure_ascii=False)
        lines.append(f"{repo.dir}\x1f{folder}\n")
    return base64.b64encode("".join(lines).encode()).decode()


def _workspace_repos(config: ProjectConfig):
    """workspace へ並べる順 (primary が先頭)。"""
    return sorted(config.repos, key=lambda repo: not repo.primary)


def _workspace_folder(repo) -> Dict[str, str]:
    return {"name": repo.dir, "path": f"/work/{repo.dir}"}


def container_env(config: ProjectConfig, project_name: str) -> Dict[str, str]:
    """dev コンテナへ渡す環境変数を組み立てる。

    - ``DEVBASE_REPOS``: clone プラン (base64)
    - ``DEVBASE_PRIMARY_DIR``: 起動後に ``cd`` する ``/work`` 配下のディレクトリ名
    - ``DEVBASE_WORKSPACE`` / ``DEVBASE_WORKSPACE_B64`` / ``DEVBASE_WORKSPACE_FOLDERS``:
      repo が 2 件以上のときだけ。1 件のときは従来どおりフォルダを開かせたいので付けない。
      entrypoint は ``DEVBASE_WORKSPACE_FOLDERS`` から clone できた repo だけを選んで
      書き出す (PLAN37)。``DEVBASE_WORKSPACE_B64`` は**この変数を知らない古いイメージ**の
      ための完成品で、新しいホスト + 古いイメージでも workspace が消えないよう残している。

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
        env["DEVBASE_WORKSPACE_FOLDERS"] = encode_workspace_folders(config)
    return env


def hook_env(config: ProjectConfig) -> Dict[str, str]:
    """``pre-up`` / ``deploy`` フックへ渡す環境変数を組み立てる。

    フックはホスト側で動き、clone 先のパスやリポジトリ URL を必要とすることが
    ある (共有ボリュームへの populate、Laravel の ``.env`` 配置など)。以前は
    ``env`` の ``GIT_REPO`` / ``WORK_DIR`` を ``source ./env`` で読んでいたが、
    それらは ``project.yml`` へ移ったため devbase 側から明示的に渡す。

    - ``DEVBASE_PRIMARY_DIR`` : primary repo の ``/work`` 配下ディレクトリ名
    - ``DEVBASE_PRIMARY_URL`` : primary repo の clone URL
    - ``DEVBASE_WORK_DIR``    : コンテナ内の既定の作業ディレクトリ
    - ``DEVBASE_REPO_DIRS``   : 全 repo のディレクトリ名 (空白区切り、宣言順)
    """
    return {
        "DEVBASE_PRIMARY_DIR": config.primary.dir,
        "DEVBASE_PRIMARY_URL": config.primary.url,
        "DEVBASE_WORK_DIR": config.resolved_work_dir(),
        "DEVBASE_REPO_DIRS": " ".join(repo.dir for repo in config.repos),
    }


# ---------------------------------------------------------------------------
# scale (旧 CONTAINER_SCALE)
# ---------------------------------------------------------------------------

#: ``scale: 1  # 並列数`` の値部分と行内コメントを別々に捕まえる。
#: 値だけを差し替えてコメントをそのまま残すため。
_SCALE_LINE = re.compile(r'^scale:(?P<value>[^#\n]*)(?P<comment>#[^\n]*)?$', re.M)
_VERSION_LINE = re.compile(r'^version:.*$', re.M)


def _rewrite_scale_line(match: "re.Match[str]", scale: int) -> str:
    """``scale`` 行の値だけを差し替え、行内コメントは元の間隔ごと残す。"""
    comment = match.group("comment")
    if not comment:
        return f"scale: {scale}"
    value = match.group("value")
    gap = value[len(value.rstrip()):] or " "
    return f"scale: {scale}{gap}{comment}"


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
        updated = _SCALE_LINE.sub(
            lambda m: _rewrite_scale_line(m, scale), original, count=1)
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
    "hook_env",
    "read_scale",
    "workspace_path",
    "write_scale",
]
