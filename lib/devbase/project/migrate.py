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

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from devbase.errors import ConfigError
from devbase.log import get_logger
from devbase.project.config import (
    PROJECT_CONFIG_FILENAME,
    load_project_config,
    parse_project_config,
)

logger = get_logger(__name__)

#: ``project.yml`` へ移すキー (これ以外は env に残す)
MIGRATED_KEYS: Tuple[str, ...] = (
    "GIT_HOST", "GIT_USER", "GIT_REPO", "WORK_DIR",
    "CONTAINER_SCALE", "DEVBASE_OPEN_EDITOR",
)

#: ``project.yml`` へ**文字列スカラー**として書き出すキー (残りは数値・真偽値)
_STRING_KEYS: Tuple[str, ...] = ("GIT_HOST", "GIT_USER", "GIT_REPO", "WORK_DIR")

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
    """1 プロジェクトを ``project.yml`` 方式へ移行する。

    136 件を 1 回で回すため、**1 件の失敗で全体を止めない**。不正な UTF-8・権限
    エラー・書き込み失敗といった想定内の例外はここで ``failed`` の
    :class:`MigrationResult` に畳み、残りのプロジェクトの移行と集計を続行する。
    """
    project_dir = Path(project_dir)
    try:
        return _migrate_project(project_dir.resolve(), dry_run=dry_run)
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ConfigError) as e:
        logger.warning("migrate-config: %s の移行に失敗しました: %s", project_dir, e)
        return MigrationResult(project_dir.name, project_dir, "failed", reason=str(e))


def _migrate_project(project_dir: Path, dry_run: bool) -> MigrationResult:
    """:func:`migrate_project` の本体 (例外はそのまま送出し、呼び出し側で畳む)。"""
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
        # 既存 project.yml が壊れている / 読めない場合は env を触らない。旧キーは
        # 唯一の復旧元なので、設定を読めない状態で掃除すると構成が完全に消える。
        try:
            load_project_config(project_dir)
        except ConfigError as e:
            return MigrationResult(
                name, project_dir, "failed",
                reason=(f"既存 {PROJECT_CONFIG_FILENAME} を読めないため env を"
                        f"変更しませんでした: {e}"))
        if env_changed and not dry_run:
            _atomic_write(env_path, new_env_text)
        return MigrationResult(
            name, project_dir, "already",
            reason=f"{PROJECT_CONFIG_FILENAME} が既にあります (上書きしません)",
            env=new_env_text, changed_env=env_changed)

    missing = [key for key in ("GIT_USER", "GIT_REPO") if not values.get(key)]
    if missing:
        return MigrationResult(
            name, project_dir, "skipped",
            reason=f"env に {' / '.join(missing)} がないため変換できません")

    # host / owner / repo / 作業ディレクトリに引用符が残るのは、``GIT_REPO="carmo``
    # のように env 側の引用符が閉じていない場合だけ。YAML では正しく引用して
    # 書けてしまう (= 引用符込みのリポジトリ名として通ってしまう) ため、
    # 生成前に malformed な env として弾く。
    quoted = [key for key in _STRING_KEYS
              if any(c in values.get(key, "") for c in "\"'")]
    if quoted:
        return MigrationResult(
            name, project_dir, "failed",
            reason=(f"env の {' / '.join(quoted)} の引用符が閉じていません "
                    "(値に引用符が残っています)"))

    document = _build_project_yml(values)
    try:
        parse_project_config(_load_yaml(document, config_path),
                             source=str(config_path))
    except ConfigError as e:
        return MigrationResult(name, project_dir, "failed",
                               reason=str(e), project_yml=document)

    if not dry_run:
        # project.yml を完全に永続化してから env を掃除する。逆順や非 atomic な
        # 書き込みだと、中断・ディスクフル時に「壊れた project.yml + 旧キーの無い
        # env」が残り、再実行しても復旧できなくなる。
        _atomic_write(config_path, document)
        if env_changed:
            _atomic_write(env_path, new_env_text)

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

def _yaml_scalar(value: str) -> str:
    """文字列を YAML の暗黙型変換に食われないスカラーとして書き出す。

    ``GIT_REPO=123`` / ``GIT_REPO=on`` / ``GIT_REPO=2026-08-22`` は env では
    ただの文字列だが、素で埋め込むと YAML 1.1 の暗黙タグで int / bool / date に
    なり、ローダの「文字列で指定してください」で移行が失敗する。
    ``yaml.safe_dump`` に判断を任せることで、引用が要る値だけが引用され、
    ``carmo-web`` のような通常の値は素のまま (= 生成物の見た目は変わらない)。
    """
    return yaml.safe_dump(
        value, default_flow_style=True, width=10 ** 6, allow_unicode=True,
    ).strip().removesuffix("...").strip()


def _load_yaml(text: str, source: Path) -> dict:
    """生成した YAML を読み戻す。壊れていたら :class:`ConfigError` に揃える。

    ``env`` に ``CONTAINER_SCALE="1`` のような閉じられていない引用符があると、値が
    そのまま YAML へ流れて ``yaml.YAMLError`` になる。ローダと同じ
    :class:`ConfigError` に変換して、その 1 件だけを ``failed`` に倒す。
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"{source} 用に生成した YAML を解釈できません: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(
            f"{source} 用に生成した YAML がマッピングになりません "
            "(env の値に YAML の構文が混ざっている可能性があります)")
    return data


def _atomic_write(path: Path, text: str) -> None:
    """同一ディレクトリの一時ファイルへ書いてから ``os.replace`` で差し替える。

    直接 ``write_text`` すると、ディスクフルや中断でファイルが truncate された
    まま残りうる。移行では ``project.yml`` が壊れたまま ``env`` の旧キーだけが
    消えると復旧元が無くなるため、読み手からは常に旧内容か新内容のどちらかしか
    見えない atomic な差し替えにする。
    """
    path = Path(os.path.realpath(path))      # symlink 自体を置き換えない
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


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
        lines.append(f"work_dir: {_yaml_scalar(work_dir)}")

    lines.append("repos:")
    if host != _DEFAULT_HOST:
        lines.append(f"  - host: {_yaml_scalar(host)}")
        lines.append(f"    owner: {_yaml_scalar(owner)}")
    else:
        lines.append(f"  - owner: {_yaml_scalar(owner)}")
    lines.append(f"    repo: {_yaml_scalar(repo)}")
    return "\n".join(lines) + "\n"


__all__ = ["MIGRATED_KEYS", "MigrationResult", "migrate_project", "migrate_projects"]
