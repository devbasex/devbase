"""``projects/<name>/project.yml`` の読み込み・正規化・検証 (PLAN32)。

1 プロジェクト = 1 コンテナ = **複数リポジトリ**構成の設定ファイルを扱う。
人間が編集する正は YAML であり、コンテナへは正規化した「clone プラン」を
base64 TSV (:func:`encode_repo_plan`) にして渡す。YAML の解釈をホスト側の
Python に閉じ込めることで、entrypoint (bash) は ``base64 -d`` と ``while read``
だけで済み、コンテナイメージへ YAML パーサ依存を持ち込まずに済む。

スキーマ::

    version: 1              # 必須
    scale: 1                # 任意。旧 CONTAINER_SCALE
    open_editor: true       # 任意。旧 DEVBASE_OPEN_EDITOR
    work_dir: /work/carmo   # 任意。既定は primary repo の /work/<dir>
    defaults:               # 任意。repos の各要素へ継承させる既定値
      host: github.com
      owner: volareinc
    repos:
      - repo: carmo         # 必須
        primary: true       # 任意。未指定なら先頭要素が primary
      - repo: carmo-batch
        dir: batch          # 任意。/work 配下の clone 先名 (既定 repo 名)
        branch: develop     # 任意。clone 後に checkout
        init: false         # 任意 (既定 true)。clone 後の ./init.sh 実行有無

旧方式 (``env`` の ``GIT_USER`` / ``GIT_REPO``) への後方互換は持たない。
``project.yml`` が無いプロジェクトは移行手順を案内して :class:`ConfigError`
を送出する (黙って単一 repo として動かすと、移行漏れが検出できないため)。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

import yaml

from devbase.errors import ConfigError

#: 設定ファイル名 (プロジェクトディレクトリ直下)
PROJECT_CONFIG_FILENAME = "project.yml"

#: 対応するスキーマ版
SUPPORTED_VERSION = 1

_TOP_LEVEL_KEYS = frozenset(
    {"version", "scale", "open_editor", "work_dir", "defaults", "repos"})
_REPO_KEYS = frozenset({"host", "owner", "repo", "dir", "branch", "init", "primary"})
#: ``defaults`` に書けるのは repo ごとに異なるとは限らない項目だけ。
#: ``dir`` / ``primary`` は repo 固有 (継承すると必ず重複・複数 primary になる)。
_DEFAULTS_KEYS = frozenset({"host", "owner", "branch", "init"})

_DEFAULT_HOST = "github.com"

_WIRE_FIELD_SEPARATOR = "\t"


@dataclass(frozen=True)
class RepoSpec:
    """正規化済みの 1 リポジトリ分の clone 指定。"""

    host: str
    owner: str
    repo: str
    dir: str
    branch: Optional[str]
    init: bool
    primary: bool

    @property
    def url(self) -> str:
        """clone 先 URL。認証は既存の git 資格情報機構に委ねる (URL に含めない)。"""
        return f"https://{self.host}/{self.owner}/{self.repo}.git"


@dataclass(frozen=True)
class RepoPlanEntry:
    """wire format を復号した 1 行分 (entrypoint が受け取る情報と同じ)。"""

    url: str
    dir: str
    branch: Optional[str]
    init: bool


@dataclass(frozen=True)
class ProjectConfig:
    """``project.yml`` 1 ファイル分の正規化済み設定。"""

    version: int
    repos: Tuple[RepoSpec, ...]
    scale: Optional[int] = None
    open_editor: Optional[bool] = None
    work_dir: Optional[str] = None

    @property
    def primary(self) -> RepoSpec:
        """``cd`` 先・エディタの既定フォルダになる repo (常にちょうど 1 件)。"""
        return next(repo for repo in self.repos if repo.primary)

    def resolved_work_dir(self) -> str:
        """コンテナ内で開く既定フォルダ。明示指定が無ければ primary repo の dir。"""
        return self.work_dir or f"/work/{self.primary.dir}"


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------

def config_path(project_dir: Path) -> Path:
    """プロジェクトディレクトリ内の ``project.yml`` のパス。"""
    return Path(project_dir) / PROJECT_CONFIG_FILENAME


def load_project_config(project_dir: Path) -> ProjectConfig:
    """``<project_dir>/project.yml`` を読み込む。

    Raises:
        ConfigError: ファイルが無い / YAML が壊れている / スキーマ違反。
            旧 ``env`` 形式へのフォールバックはしない (PLAN32 は後方互換なし)。
    """
    path = config_path(project_dir)
    if not path.is_file():
        raise ConfigError(
            f"{path} がありません。PLAN32 以降、プロジェクトのリポジトリ構成は "
            f"{PROJECT_CONFIG_FILENAME} で指定します。"
            "旧 env 形式 (GIT_USER / GIT_REPO) からの移行は "
            "`devbase project migrate-config` を実行してください。"
        )

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as e:
        raise ConfigError(
            f"{path} を UTF-8 として読めません ({e})。"
            f"{PROJECT_CONFIG_FILENAME} は UTF-8 で保存してください。") from e
    except OSError as e:
        raise ConfigError(f"{path} を読み込めません: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} の YAML を解釈できません: {e}") from e

    if raw is None:
        raise ConfigError(f"{path} が空です。version と repos が必要です。")
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path} の最上位はマッピングである必要があります。")

    return parse_project_config(raw, source=str(path))


def parse_project_config(data: Mapping[str, Any], source: str) -> ProjectConfig:
    """読み込み済みのマッピングを正規化・検証する (I/O を伴わない)。

    Args:
        data: YAML を読み込んだマッピング
        source: エラーメッセージに出す出所 (ファイルパス等)
    """
    _reject_unknown_keys(data, _TOP_LEVEL_KEYS, source, "最上位")

    version = data.get("version")
    # YAML の ``true`` は Python では ``1`` と等価なので bool を明示的に弾く。
    if isinstance(version, bool) or version != SUPPORTED_VERSION:
        raise ConfigError(
            f"{source}: version は {SUPPORTED_VERSION} である必要があります "
            f"(現在: {version!r})")

    defaults = data.get("defaults")
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, Mapping):
        raise ConfigError(f"{source}: defaults はマッピングである必要があります。")
    _reject_unknown_keys(defaults, _DEFAULTS_KEYS, source, "defaults")

    raw_repos = data.get("repos")
    if not isinstance(raw_repos, Sequence) or isinstance(raw_repos, (str, bytes)):
        raise ConfigError(f"{source}: repos はリストである必要があります。")
    if not raw_repos:
        raise ConfigError(f"{source}: repos が空です。1 件以上指定してください。")

    repos = [_parse_repo(entry, defaults, source, i)
             for i, entry in enumerate(raw_repos)]
    _validate_dirs(repos, source)
    repos = _assign_primary(repos, source)

    return ProjectConfig(
        version=SUPPORTED_VERSION,
        repos=tuple(repos),
        scale=_parse_scale(data.get("scale"), source),
        open_editor=_parse_open_editor(data.get("open_editor"), source),
        work_dir=_parse_work_dir(data.get("work_dir"), source),
    )


# ---------------------------------------------------------------------------
# wire format (entrypoint との契約)
# ---------------------------------------------------------------------------

def encode_repo_plan(repos: Iterable[RepoSpec]) -> str:
    """clone プランを base64 TSV へ符号化する。

    1 行 1 repo で ``url<TAB>dir<TAB>branch<TAB>init`` (``init`` は ``1``/``0``、
    ``branch`` 未指定は空文字)。base64 にするのは、compose の変数展開 (``$``) や
    改行を含む値で構成ファイルが壊れないようにするため。primary は列に含めず
    ``DEVBASE_PRIMARY_DIR`` で別に渡す (entrypoint の ``cd`` 先判定を単純に保つ)。
    """
    lines = [
        _WIRE_FIELD_SEPARATOR.join(
            [repo.url, repo.dir, repo.branch or "", "1" if repo.init else "0"])
        for repo in repos
    ]
    return base64.b64encode("\n".join(lines).encode()).decode()


def decode_repo_plan(encoded: str) -> Tuple[RepoPlanEntry, ...]:
    """:func:`encode_repo_plan` の逆変換 (契約テストと診断用)。"""
    try:
        text = base64.b64decode(encoded, validate=True).decode()
    except (ValueError, UnicodeDecodeError) as e:
        raise ConfigError(f"clone プランを復号できません: {e}") from e

    entries = []
    for line in text.splitlines():
        if not line:
            continue
        fields = line.split(_WIRE_FIELD_SEPARATOR)
        if len(fields) != 4:
            raise ConfigError(f"clone プランの列数が不正です: {line!r}")
        url, directory, branch, init = fields
        entries.append(RepoPlanEntry(
            url=url, dir=directory, branch=branch or None, init=init == "1"))
    return tuple(entries)


# ---------------------------------------------------------------------------
# 内部: 検証
# ---------------------------------------------------------------------------

def _reject_unknown_keys(data: Mapping[str, Any], allowed: frozenset,
                         source: str, where: str) -> None:
    """未知キーは黙って無視せずエラーにする (typo が設定漏れとして表れないように)。"""
    unknown = sorted(str(key) for key in data if key not in allowed)
    if unknown:
        raise ConfigError(
            f"{source}: {where}に未知のキーがあります: {', '.join(unknown)} "
            f"(使えるキー: {', '.join(sorted(allowed))})")


def _parse_repo(entry: Any, defaults: Mapping[str, Any], source: str,
                index: int) -> RepoSpec:
    where = f"repos[{index}]"
    if not isinstance(entry, Mapping):
        raise ConfigError(f"{source}: {where} はマッピングである必要があります。")
    _reject_unknown_keys(entry, _REPO_KEYS, source, where)

    merged = {**defaults, **entry}

    repo = _require_token(merged.get("repo"), "repo", source, where,
                          allow_slash=False)
    owner = _require_token(merged.get("owner"), "owner", source, where,
                           allow_slash=True)
    host = _require_token(merged.get("host", _DEFAULT_HOST), "host", source, where,
                          allow_slash=False)

    directory = merged.get("dir", repo)
    directory = _require_token(directory, "dir", source, where, allow_slash=False)
    if directory in (".", ".."):
        raise ConfigError(
            f"{source}: {where} の dir は /work 直下の名前である必要があります "
            f"({directory!r})")

    branch = merged.get("branch")
    if branch is not None:
        branch = _require_token(branch, "branch", source, where, allow_slash=True)

    init = merged.get("init", True)
    if not isinstance(init, bool):
        raise ConfigError(f"{source}: {where} の init は真偽値です ({init!r})")

    primary = entry.get("primary", False)
    if not isinstance(primary, bool):
        raise ConfigError(f"{source}: {where} の primary は真偽値です ({primary!r})")

    return RepoSpec(host=host, owner=owner, repo=repo, dir=directory,
                    branch=branch, init=init, primary=primary)


def _require_token(value: Any, field: str, source: str, where: str,
                   allow_slash: bool) -> str:
    """URL 組み立てと TSV を壊さない文字列であることを確かめる。

    空白・タブ・改行は wire format (タブ区切り・行区切り) を壊し、``/`` は
    ``https://<host>/<owner>/<repo>.git`` の構造や ``/work/<dir>`` の階層を
    壊すため、項目ごとに許可を分ける (gitlab のサブグループやブランチ名の
    ``feature/x`` は ``/`` を含むため許可する)。
    """
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{source}: {where} の {field} は必須です ({value!r})")
    if any(c.isspace() for c in value):
        raise ConfigError(
            f"{source}: {where} の {field} に空白文字は使えません ({value!r})")
    if not allow_slash and "/" in value:
        raise ConfigError(
            f"{source}: {where} の {field} に / は使えません ({value!r})")
    if allow_slash and (value.startswith("/") or value.endswith("/")):
        raise ConfigError(
            f"{source}: {where} の {field} は / で始まる・終わることはできません "
            f"({value!r})")
    return value


def _validate_dirs(repos: Sequence[RepoSpec], source: str) -> None:
    """同じ ``/work/<dir>`` を 2 つの repo が奪い合わないこと。"""
    seen = set()
    for repo in repos:
        if repo.dir in seen:
            raise ConfigError(
                f"{source}: clone 先の dir が重複しています: {repo.dir!r}")
        seen.add(repo.dir)


def _assign_primary(repos: Sequence[RepoSpec], source: str) -> list:
    """primary をちょうど 1 件に確定する (未指定なら先頭)。"""
    explicit = [repo for repo in repos if repo.primary]
    if len(explicit) > 1:
        names = ", ".join(repo.dir for repo in explicit)
        raise ConfigError(
            f"{source}: primary: true は 1 件だけ指定できます ({names})")
    if explicit:
        return list(repos)
    first, *rest = repos
    return [replace(first, primary=True), *rest]


def _parse_scale(value: Any, source: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{source}: scale は 1 以上の整数です ({value!r})")
    return value


def _parse_open_editor(value: Any, source: str) -> Optional[bool]:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ConfigError(f"{source}: open_editor は真偽値です ({value!r})")
    return value


def _parse_work_dir(value: Any, source: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{source}: work_dir は文字列です ({value!r})")
    return value.strip()
