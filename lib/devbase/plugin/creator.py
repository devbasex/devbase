"""作成フロー: リポジトリ・プラグイン・プロジェクトを足す一連の操作 (#514)。

``plan_*`` は読むだけで、書き込みの前に作成の計画を確定する (不変条件 I1)。``execute`` が
GitHub と手元へ書く。手元の登録は、既定ブランチへ入れる変更がすべて反映された後に既存の
``add_repository``・``install_plugin``・``refresh_repository`` で行う (不変条件 I2・決定 2・
決定 8)。``devbase up`` は実行しない (不変条件 I6)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from devbase.errors import DevbaseError, RepositoryError
from devbase.log import get_logger
from devbase.utils import names

from . import publish, scaffold
from .github import GhClient, GitHubRef
from .installer import install_plugin
from .models import RegisteredRepository
from .registry import PluginRegistry
from .repo_manager import (
    _find_registered,
    _pull_ff_only,
    _url_to_repos_dirname,
    add_repository,
    refresh_repository,
)

logger = get_logger("devbase.plugin.creator")

KIND_REPO = 'repo'
KIND_PLUGIN = 'plugin'


class RepoState(Enum):
    """対象のリポジトリの状態 (設計の「引数の解決と分岐」)"""
    ABSENT = 'absent'
    EMPTY = 'empty'
    NO_REGISTRY = 'no-registry'
    HAS_REGISTRY = 'has-registry'


@dataclass(frozen=True)
class CreationPlan:
    """作成の計画: 作るもの・ファイル・マージ先の組"""
    kind: str
    ref: GitHubRef
    name: str
    state: RepoState
    default_branch: str = ''
    public: bool = False
    registered: Optional[RegisteredRepository] = None
    files: Dict[str, str] = field(default_factory=dict)
    registry_entry: Optional[str] = None
    #: 空でなければ何もしないで終わる。その理由
    noop_reason: str = ''

    @property
    def writes_github(self) -> bool:
        return self.kind != KIND_REPO or self.state is not RepoState.HAS_REGISTRY

    def describe(self) -> str:
        """利用者へ示す計画の文面"""
        target = self.default_branch or 'GitHub の既定ブランチ'
        lines = [f"作成の計画: {self.kind} {self.name}",
                 f"  リポジトリ: {self.ref.full_name}"]
        if self.state is RepoState.ABSENT:
            visibility = 'public' if self.public else 'private'
            lines.append(f"  作成: {self.ref.full_name} を {visibility} で作る")
        if self.state in (RepoState.ABSENT, RepoState.EMPTY):
            lines.append(f"  最初のコミット: {scaffold.REGISTRY_FILE} を {target} へ直接入れる")
        elif self.state is RepoState.NO_REGISTRY:
            lines.append(f"  PR: {scaffold.REGISTRY_FILE} を足して {target} へマージする")
        if self.files or self.registry_entry:
            lines.append(f"  PR: 次を足して {target} へマージする")
            lines.extend(f"    - {path}" for path in self.files)
            if self.registry_entry:
                lines.append(f"    - {scaffold.REGISTRY_FILE} の項目 {self.registry_entry}")
        lines.append(f"  手元: {self._local_step()}")
        return '\n'.join(lines)

    def _local_step(self) -> str:
        if self.registered is None:
            return "repos/ へ clone して plugins.yml へ登録する"
        return f"{self.registered.local_path} を早送りで取り込む"


def _inspect(
    registry: PluginRegistry, gh: GhClient, ref: GitHubRef,
    registered: Optional[RegisteredRepository],
) -> tuple:
    """リポジトリの状態と既定ブランチを返す。使えないときは何も書かずに止める (読むだけ)"""
    view = gh.view_repo(ref)
    if view is None:
        if registered is not None:
            raise RepositoryError(
                f"{ref.full_name} は登録済みですが、GitHub で見つからないか読めません")
        if not gh.can_create_in(ref.owner):
            raise RepositoryError(
                f"{ref.full_name} が無く、{ref.owner} の下に作る権限もありません")
        _reject_stale_clone(registry, ref)
        return RepoState.ABSENT, ''
    if not view.can_push:
        raise RepositoryError(f"{ref.full_name} へ push する権限がありません")
    if registered is None:
        _reject_stale_clone(registry, ref)
    if view.empty:
        return RepoState.EMPTY, ''
    if gh.read_file(ref, scaffold.REGISTRY_FILE) is None:
        return RepoState.NO_REGISTRY, view.default_branch
    return RepoState.HAS_REGISTRY, view.default_branch


def _reject_stale_clone(registry: PluginRegistry, ref: GitHubRef) -> None:
    """未登録なのに clone の置き場が塞がっていると、マージの後の登録で止まるので先に止める"""
    clone_dir = registry.get_repos_dir() / _url_to_repos_dirname(ref.clone_url)
    if clone_dir.exists():
        raise RepositoryError(
            f"{clone_dir} が既にありますが、登録されていません。"
            "片付けてから打ち直してください"
        )


def plan_repo(
    registry: PluginRegistry, gh: GhClient, value: str, *, public: bool = False,
) -> CreationPlan:
    """``plugin repo new`` の計画 (読むだけ)"""
    ref = GitHubRef.parse(value)
    gh.ensure_ready()
    registered = _find_registered(registry, ref.clone_url)
    state, default_branch = _inspect(registry, gh, ref, registered)
    noop = (f"{ref.full_name} は登録済みです ({registered.name})。何もしません"
            if registered is not None else '')
    return CreationPlan(
        kind=KIND_REPO, ref=ref, name=ref.repo, state=state,
        default_branch=default_branch, public=public, registered=registered,
        noop_reason=noop,
    )


def _checked_name(name: str, what: str) -> str:
    """プラグインとプロジェクトの名前を 名前の形 で確かめる (不変条件 I9)"""
    if not names.is_single_segment_name(name):
        raise RepositoryError(
            f"{what}の名前にできません: '{name}' ({names.NAME_FORM_HINT})")
    return name


def _require_clone(registry: PluginRegistry, repo: RegisteredRepository) -> None:
    """登録の clone があることを確かめる。無ければ何も書かずに止める (読むだけ)

    ``local_path`` が空のまま進むと ``devbase_root`` そのものを指し、devbase 本体を取り込む。
    """
    if repo.local_path and (registry.devbase_root / repo.local_path).is_dir():
        return
    raise RepositoryError(
        f"登録 {repo.name} の手元の clone がありません。次のコマンドで登録し直してください:\n"
        f"  devbase plugin repo remove {repo.name}\n"
        f"  devbase plugin repo add {repo.url}\n"
        "repo remove はこのリポジトリから導入したプラグインも外すので、"
        "devbase plugin install も打ち直してください"
    )


def plan_plugin(
    registry: PluginRegistry, gh: GhClient, value: str, *,
    name: Optional[str] = None, public: bool = False,
) -> CreationPlan:
    """``plugin new`` の計画 (読むだけ)。未登録ならリポジトリの分岐も計画に入る"""
    ref = GitHubRef.parse(value)
    gh.ensure_ready()
    plugin = _checked_name(name or ref.repo, "プラグイン")
    registered = _find_registered(registry, ref.clone_url)
    if registered is not None:
        _require_clone(registry, registered)
    state, default_branch = _inspect(registry, gh, ref, registered)
    if registered is not None and state is not RepoState.HAS_REGISTRY:
        raise RepositoryError(
            f"{ref.full_name} は登録済みですが、既定ブランチに {scaffold.REGISTRY_FILE} が"
            "ありません")
    _reject_duplicate_plugin(registry, gh, ref, plugin, state)
    return CreationPlan(
        kind=KIND_PLUGIN, ref=ref, name=plugin, state=state,
        default_branch=default_branch, public=public, registered=registered,
        files=scaffold.plugin_files(plugin), registry_entry=plugin,
    )


def _reject_duplicate_plugin(
    registry: PluginRegistry, gh: GhClient, ref: GitHubRef, plugin: str, state: RepoState,
) -> None:
    if registry.get(plugin) is not None:
        raise RepositoryError(f"プラグイン {plugin} は既に導入されています")
    if state in (RepoState.ABSENT, RepoState.EMPTY):
        return
    if state is RepoState.HAS_REGISTRY:
        text = gh.read_file(ref, scaffold.REGISTRY_FILE) or ''
        if plugin in scaffold.registry_plugin_names(text):
            raise RepositoryError(
                f"プラグイン {plugin} は {ref.full_name} の {scaffold.REGISTRY_FILE} に既にあります")
    if gh.read_file(ref, plugin) is not None:
        raise RepositoryError(f"{ref.full_name} に {plugin} が既にあります")


def _reflect_repository(gh: GhClient, plan: CreationPlan) -> None:
    """リポジトリの分岐の分を既定ブランチへ入れる"""
    files = {scaffold.REGISTRY_FILE: scaffold.registry_yml(plan.ref.repo)}
    if plan.state in (RepoState.ABSENT, RepoState.EMPTY):
        publish.push_initial(plan.ref, files,
                             title=publish.pr_title(KIND_REPO, plan.ref.repo))
    elif plan.state is RepoState.NO_REGISTRY:
        publish.publish_change(gh, plan.ref, kind=KIND_REPO, name=plan.ref.repo, files=files)


def _source_url(plan: CreationPlan) -> str:
    """手元の呼び出しへ渡す URL。登録済みなら登録の値 (決定 9)"""
    return plan.ref.clone_url if plan.registered is None else plan.registered.url


def _follow_up(plan: CreationPlan) -> str:
    """マージの後に手で打つコマンド"""
    if plan.registered is None:
        commands = [f"devbase plugin repo add {plan.ref.clone_url}"]
    else:
        commands = [f"devbase plugin repo refresh {plan.registered.name}"]
    if plan.kind == KIND_PLUGIN:
        commands.append(f"devbase plugin install {_source_url(plan)}:{plan.name}")
    return '\n'.join(f"  {command}" for command in commands)


def execute(registry: PluginRegistry, gh: GhClient, plan: CreationPlan) -> None:
    """計画どおりに GitHub と手元へ書く。途中で止まったら、次のコマンドを例外の本文に載せる"""
    if plan.state is RepoState.ABSENT:
        gh.create_repo(plan.ref, public=plan.public)
        logger.info("リポジトリを作りました: %s", plan.ref.full_name)
    try:
        _reflect_repository(gh, plan)
        if plan.kind != KIND_REPO:
            publish.publish_change(gh, plan.ref, kind=plan.kind, name=plan.name,
                                   files=plan.files, registry_entry=plan.registry_entry)
    except publish.MergeFailed as e:
        raise RepositoryError(f"{e}\nマージの後に打つコマンド:\n{_follow_up(plan)}") from e
    except DevbaseError as e:
        if plan.state is RepoState.ABSENT:
            raise RepositoryError(
                f"{e}\n{plan.ref.full_name} は作成済みです。"
                "同じコマンドを打ち直すと続きから進みます"
            ) from e
        raise
    _apply_locally(registry, plan)


def _apply_locally(registry: PluginRegistry, plan: CreationPlan) -> None:
    """既定ブランチへの反映の後の、手元の取り込みと登録"""
    try:
        if plan.registered is None:
            add_repository(registry, plan.ref.clone_url)
        else:
            _pull_ff_only(registry.devbase_root / plan.registered.local_path)
            refresh_repository(registry, plan.registered.name)
        if plan.kind == KIND_PLUGIN:
            install_plugin(registry, f"{_source_url(plan)}:{plan.name}")
    except DevbaseError as e:
        if not plan.writes_github:
            raise
        raise RepositoryError(
            f"{e}\n既定ブランチへの反映は済んでいます。続けて打つコマンド:\n{_follow_up(plan)}"
        ) from e
