"""旧 ``env`` 形式から ``project.yml`` への移行 (PLAN32)。

PLAN32 で ``GIT_USER`` / ``GIT_REPO`` / ``GIT_HOST`` / ``WORK_DIR`` /
``CONTAINER_SCALE`` / ``DEVBASE_OPEN_EDITOR`` は ``project.yml`` へ移り、``env`` は
「コンテナへ渡す環境変数」だけを持つ。配布中のプロジェクト定義は 3 つの plugin
リポジトリに 136 件あり、手で書き換えると取りこぼしが混じるため機械的に変換する。

``env`` から読むキーは allowlist で限定し、それ以外 (``ENABLE_SSH`` 等) は
``env`` にそのまま残す。既に ``project.yml`` があるプロジェクトは**上書きしない**
(手で複数 repo 構成へ整えたものを壊さないため)。``env`` の旧キー掃除だけは行う
ので、何度実行しても同じ状態に収束する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from devbase.errors import ConfigError
from devbase.log import get_logger
from devbase.project.config import (
    PROJECT_CONFIG_FILENAME,
    parse_project_config,
)

logger = get_logger(__name__)

#: ``project.yml`` へ移すキー (これ以外は env に残す)
MIGRATED_KEYS: Tuple[str, ...] = (
    "GIT_HOST", "GIT_USER", "GIT_REPO", "WORK_DIR",
    "CONTAINER_SCALE", "DEVBASE_OPEN_EDITOR",
)

_DEFAULT_HOST = "github.com"
_TRUTHY = {"1", "true", "yes", "on"}

#: 旧キーを全部落として空になった env に残す説明 (compose が参照するのでファイルは消せない)
_EMPTY_ENV_TEXT = (
    "# コンテナへ渡す環境変数を書く。\n"
    "# devbase 自身の設定 (リポジトリ・scale・エディタ) は project.yml にある。\n"
)

_ASSIGNMENT = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$')
_VAR_REF = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)')


@dataclass
class MigrationResult:
    """1 プロジェクト分の移行結果 (``--dry-run`` でも同じものを返す)。"""

    name: str
    path: Path
    status: str            # migrated / already / skipped / failed
    reason: str = ""
    project_yml: str = ""
    env: str = ""
    changed_env: bool = False


def migrate_project(project_dir: Path, dry_run: bool = False) -> MigrationResult:
    """1 プロジェクトを ``project.yml`` 方式へ移行する。"""
    project_dir = Path(project_dir).resolve()
    name = project_dir.name
    env_path = project_dir / "env"
    config_path = project_dir / PROJECT_CONFIG_FILENAME

    if not env_path.is_file():
        return MigrationResult(name, project_dir, "skipped",
                               reason=f"env ファイルがありません ({env_path})")

    env_text = env_path.read_text(encoding="utf-8")
    values = _parse_env(env_text)
    new_env_text = _strip_migrated_keys(env_text)
    env_changed = new_env_text != env_text

    if config_path.is_file():
        if env_changed and not dry_run:
            env_path.write_text(new_env_text, encoding="utf-8")
        return MigrationResult(
            name, project_dir, "already",
            reason=f"{PROJECT_CONFIG_FILENAME} が既にあります (上書きしません)",
            env=new_env_text, changed_env=env_changed)

    missing = [key for key in ("GIT_USER", "GIT_REPO") if not values.get(key)]
    if missing:
        return MigrationResult(
            name, project_dir, "skipped",
            reason=f"env に {' / '.join(missing)} がないため変換できません")

    document = _build_project_yml(values)
    try:
        parse_project_config(_load_yaml(document), source=str(config_path))
    except ConfigError as e:
        return MigrationResult(name, project_dir, "failed",
                               reason=str(e), project_yml=document)

    if not dry_run:
        config_path.write_text(document, encoding="utf-8")
        if env_changed:
            env_path.write_text(new_env_text, encoding="utf-8")

    return MigrationResult(name, project_dir, "migrated",
                           project_yml=document, env=new_env_text,
                           changed_env=env_changed)


def migrate_projects(projects_dir: Path, dry_run: bool = False) -> List[MigrationResult]:
    """``projects/`` 配下の全プロジェクトを移行する (名前順)。

    ``projects/<name>`` は plugin リポジトリへの symlink であることが多い。
    :func:`migrate_project` が実体パスへ解決するため、書き換わるのは plugin
    リポジトリ側のファイルになる (そこが定義の正であるため意図どおり)。
    """
    projects_dir = Path(projects_dir)
    entries = sorted(
        (entry for entry in projects_dir.iterdir() if entry.is_dir()),
        key=lambda entry: entry.name)
    return [migrate_project(entry, dry_run=dry_run) for entry in entries]


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------

def _load_yaml(text: str) -> dict:
    import yaml
    return yaml.safe_load(text)


def _parse_env(text: str) -> Dict[str, str]:
    """``KEY=VALUE`` を読み、``$VAR`` は先行行の値で展開する (wrapper と同じ規則)。"""
    values: Dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _ASSIGNMENT.match(line)
        if not m:
            continue
        key, raw = m.group(1), m.group(2).strip()
        literal = len(raw) >= 2 and raw[0] == raw[-1] == "'"
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            raw = raw[1:-1]
        if not literal:
            raw = _VAR_REF.sub(
                lambda m: values.get(m.group(1) or m.group(2), ""), raw)
        values[key] = raw
    return values


def _strip_migrated_keys(text: str) -> str:
    """移行したキーの行を落とす (直前に付いていた説明コメントも一緒に落とす)。

    キーを消してコメントだけ残ると、何を説明しているのか分からない行になるため。
    全部消えて空になった場合は、``env`` の役割を書いた雛形を残す (compose が
    ``env_file`` で参照するのでファイル自体は消せない)。
    """
    kept: List[str] = []
    pending_comments: List[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            pending_comments.append(line)
            continue
        if not stripped:
            kept.extend(pending_comments)
            pending_comments = []
            kept.append(line)
            continue

        m = _ASSIGNMENT.match(line)
        if m and m.group(1) in MIGRATED_KEYS:
            pending_comments = []       # 直前の説明コメントごと落とす
            continue
        kept.extend(pending_comments)
        pending_comments = []
        kept.append(line)

    kept.extend(pending_comments)
    body = "\n".join(kept).strip("\n")
    if not body.strip():
        return _EMPTY_ENV_TEXT
    return body + "\n"


def _build_project_yml(values: Dict[str, str]) -> str:
    """``env`` の値から ``project.yml`` のテキストを組み立てる。

    ``yaml.dump`` ではなく手で組み立てるのは、キーの並び順とコメントを人が読む
    順序で固定したいため (この結果を人が編集して repo を足していく)。
    """
    repo = values["GIT_REPO"]
    owner = values["GIT_USER"]
    host = values.get("GIT_HOST") or _DEFAULT_HOST
    lines = [
        "# devbase プロジェクト設定 (PLAN32)",
        "# リポジトリを増やすときは repos に要素を足す。",
        "version: 1",
    ]

    scale = values.get("CONTAINER_SCALE")
    if scale:
        lines.append(f"scale: {scale}")

    open_editor = values.get("DEVBASE_OPEN_EDITOR")
    if open_editor:
        enabled = open_editor.strip().lower() in _TRUTHY
        lines.append(f"open_editor: {'true' if enabled else 'false'}")

    work_dir = values.get("WORK_DIR")
    if work_dir and work_dir != f"/work/{repo}":
        lines.append(f"work_dir: {work_dir}")

    lines.append("repos:")
    if host != _DEFAULT_HOST:
        lines.append(f"  - host: {host}")
        lines.append(f"    owner: {owner}")
    else:
        lines.append(f"  - owner: {owner}")
    lines.append(f"    repo: {repo}")
    return "\n".join(lines) + "\n"


__all__ = ["MIGRATED_KEYS", "MigrationResult", "migrate_project", "migrate_projects"]
