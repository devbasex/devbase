"""作成フロー: リポジトリ・プラグイン・プロジェクトを足す一連の操作 (#514)。

``plan_*`` は読むだけで、書き込みの前に作成の計画を確定する (不変条件 I1)。``execute`` が
GitHub と手元へ書く。手元の登録は、既定ブランチへ入れる変更がすべて反映された後に既存の
``add_repository``・``install_plugin``・``refresh_repository`` で行う (不変条件 I2・決定 2・
決定 8)。``devbase up`` は実行しない (不変条件 I6)。
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, List, Optional, Protocol, Tuple

from devbase.env import groups
from devbase.errors import DevbaseError, RepositoryError
from devbase.log import get_logger
from devbase.utils import names
from devbase.volume.manager import validate_account_group

from . import publish, scaffold
from .github import GhClient, GitHubRef, RepoView
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
KIND_PROJECT = 'project'


class Prompter(Protocol):
    """入れ先とグループを人へ聞く口。TTY が無いときは渡さない"""

    def choose(self, message: str, options: List[str]) -> Optional[str]:
        """``options`` から 1 つ選ばせる。選ばなければ ``None``"""

    def ask(self, message: str) -> Optional[str]:
        """1 行を入力させる。入れなければ ``None``"""


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
    #: 計画の文面に足す行 (入れ先のプラグインなど)
    details: Tuple[str, ...] = ()
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
        lines.extend(f"  {detail}" for detail in self.details)
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


@dataclass(frozen=True)
class PluginTarget:
    """プロジェクトの入れ先のプラグイン"""
    plugin: str
    repo: RegisteredRepository
    ref: GitHubRef
    path: str
    default_branch: str = ''

    @property
    def label(self) -> str:
        return f"{self.plugin} ({self.ref.full_name})"


def _installed_targets(registry: PluginRegistry) -> List[PluginTarget]:
    """導入済みで、登録済みの GitHub のリポジトリに項目があるプラグイン (GitHub を見ない)"""
    targets = []
    for plugin in registry.list_installed():
        repo = None if plugin.linked else _find_registered(registry, plugin.source)
        entry = repo.find_plugin(plugin.name) if repo else None
        if entry is None:
            continue
        try:
            ref = GitHubRef.parse(repo.url)
        except RepositoryError:
            continue
        targets.append(PluginTarget(plugin.name, repo, ref,
                                    (entry.path or plugin.name).rstrip('/')))
    return targets


def _pushable(gh: GhClient, target: PluginTarget, views: Dict[str, Optional[RepoView]],
              ) -> Optional[PluginTarget]:
    """push できる入れ先に既定ブランチを埋めて返す。``gh`` はリポジトリごとに 1 回"""
    key = target.ref.full_name
    if key not in views:
        views[key] = gh.view_repo(target.ref)
    view = views[key]
    if view is None or view.empty or not view.can_push:
        return None
    return replace(target, default_branch=view.default_branch)


def plugin_candidates(
    registry: PluginRegistry, gh: GhClient, ref: GitHubRef,
) -> List[PluginTarget]:
    """入れ先の候補 (読むだけ)。対象と同じ owner の候補があれば、それに絞る"""
    views: Dict[str, Optional[RepoView]] = {}
    pushable = [found for target in _installed_targets(registry)
                if (found := _pushable(gh, target, views)) is not None]
    owner = ref.owner.lower()
    same_owner = [t for t in pushable if t.ref.owner.lower() == owner]
    return same_owner or pushable


def _named_target(registry: PluginRegistry, gh: GhClient, plugin: str) -> PluginTarget:
    target = next((t for t in _installed_targets(registry) if t.plugin == plugin), None)
    if target is None:
        raise RepositoryError(
            f"プラグイン {plugin} は、導入済みの GitHub のプラグインの中にありません")
    found = _pushable(gh, target, {})
    if found is None:
        raise RepositoryError(f"{target.ref.full_name} へ push する権限がありません")
    return found


def _decide_target(
    registry: PluginRegistry, gh: GhClient, ref: GitHubRef,
    plugin: Optional[str], prompter: Optional[Prompter],
) -> PluginTarget:
    """入れ先のプラグインを決める (受け入れ条件 10)"""
    if plugin:
        return _named_target(registry, gh, plugin)
    candidates = plugin_candidates(registry, gh, ref)
    if not candidates:
        raise RepositoryError(
            "入れ先のプラグインがありません。push できるプラグインを導入するか、"
            "devbase plugin new で足してください"
        )
    if len(candidates) == 1:
        return candidates[0]
    labels = [c.label for c in candidates]
    if prompter is None:
        listed = '\n'.join(f"  {label}" for label in labels)
        raise RepositoryError(f"入れ先のプラグインを --plugin で指定してください:\n{listed}")
    chosen = prompter.choose("入れ先のプラグインを選んでください", labels)
    if chosen is None:
        raise RepositoryError("入れ先のプラグインが選ばれませんでした")
    return candidates[labels.index(chosen)]


def suggest_group(registry: PluginRegistry, target: PluginTarget) -> Optional[str]:
    """入れ先の既存プロジェクトで最多のグループ (読むだけ)。無い・同数なら ``None``"""
    projects = registry.devbase_root / target.repo.local_path / target.path / 'projects'
    counts: Counter = Counter()
    for project in names.project_dirs(projects):
        try:
            found = groups._read_declaration(project / groups.ENV_FILENAME)
        except DevbaseError as e:
            logger.debug("グループの案から外します (%s): %s", project.name, e)
            continue
        if found is not None and found[0].strip():
            counts[found[0].strip()] += 1
    ranked = counts.most_common(2)
    if not ranked or (len(ranked) == 2 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


def _decide_group(
    registry: PluginRegistry, target: PluginTarget,
    group: Optional[str], prompter: Optional[Prompter],
) -> str:
    """グループを決めて ``validate_account_group`` を通す (受け入れ条件 11・不変条件 I9)"""
    value = group or suggest_group(registry, target)
    if not value and prompter is not None:
        value = prompter.ask("アカウントグループを入力してください")
    if not value:
        raise RepositoryError(
            f"アカウントグループを決められません。--group で指定してください ({target.plugin})")
    return validate_account_group(value)


def plan_project(
    registry: PluginRegistry, gh: GhClient, value: str, *,
    plugin: Optional[str] = None, group: Optional[str] = None,
    name: Optional[str] = None, prompter: Optional[Prompter] = None,
) -> CreationPlan:
    """``project new`` の計画 (読むだけ)"""
    ref = GitHubRef.parse(value)
    gh.ensure_ready()
    project = _checked_name(name or ref.repo, "プロジェクト")
    if gh.view_repo(ref) is None:
        raise RepositoryError(f"{ref.full_name} が見つからないか読めません")
    local = registry.get_projects_dir() / project
    if os.path.lexists(local):
        raise RepositoryError(f"{local} が既にあります")

    target = _decide_target(registry, gh, ref, plugin, prompter)
    _require_clone(registry, target.repo)
    destination = f"{target.path}/projects/{project}"
    if gh.read_file(target.ref, destination) is not None:
        raise RepositoryError(f"{target.ref.full_name} に {destination} が既にあります")
    decided_group = _decide_group(registry, target, group, prompter)

    return CreationPlan(
        kind=KIND_PROJECT, ref=target.ref, name=project, state=RepoState.HAS_REGISTRY,
        default_branch=target.default_branch, registered=target.repo,
        files=scaffold.project_files(target.path, project, ref, decided_group),
        details=(f"対象: {ref.full_name}",
                 f"入れ先のプラグイン: {target.plugin}",
                 f"アカウントグループ: {decided_group}"),
    )


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
    logger.info("%s %s を足しました", plan.kind, plan.name)


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
