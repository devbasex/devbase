"""一時の浅い clone から既定ブランチへ変更を入れる (#514 の E6・E7)。

手元の ``repos/`` の clone には触らない。未登録のリポジトリには手元の clone が無く、登録済みの
clone には利用者の変更があるので、どちらも一時の clone で同じ手順にする (設計の決定 1)。
既定ブランチへの直接の push は、空のリポジトリの最初のコミットだけである (不変条件 I7)。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Dict, Optional

from devbase.errors import RepositoryError
from devbase.log import get_logger

from . import scaffold
from .github import GhClient, GitHubRef
from .installer import git_clone
from .repo_manager import _run_git

logger = get_logger("devbase.plugin.publish")

_PR_BODY = "devbase の作成フローが足しました。"


class MergeFailed(RepositoryError):
    """PR を作ったがマージできなかった。``pr_url`` に PR が残っている"""

    def __init__(self, message: str, pr_url: str):
        super().__init__(message)
        self.pr_url = pr_url


def branch_name(kind: str, name: str) -> str:
    return f"devbase/new-{kind}-{name}"


def pr_title(kind: str, name: str) -> str:
    return f"devbase: {kind} {name} を足す"


def _git(clone: Path, *args: str) -> str:
    result = _run_git(clone, *args)
    if result.returncode != 0:
        raise RepositoryError(f"git {args[0]} に失敗しました: {result.stderr.strip()}")
    return result.stdout.strip()


def _write_files(clone: Path, files: Dict[str, str]) -> None:
    for relative, text in files.items():
        target = clone / relative
        if target.exists():
            raise RepositoryError(f"{relative} は既にあります。上書きしません")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def _add_to_registry(clone: Path, name: str) -> None:
    path = clone / scaffold.REGISTRY_FILE
    path.write_text(scaffold.add_registry_entry(path.read_text(), name))


def _commit(clone: Path, title: str) -> None:
    _git(clone, 'add', '-A')
    _git(clone, 'commit', '-m', title)


def push_initial(ref: GitHubRef, files: Dict[str, str], *, title: str) -> None:
    """空のリポジトリへ最初のコミットを入れる (GitHub は空のリポジトリに PR を作れない)"""
    with tempfile.TemporaryDirectory(prefix='devbase-publish-') as tmp:
        clone = Path(tmp) / 'repo'
        git_clone(ref.clone_url, clone)
        branch = _git(clone, 'symbolic-ref', '--short', 'HEAD')
        _write_files(clone, files)
        _commit(clone, title)
        _git(clone, 'push', 'origin', f'HEAD:refs/heads/{branch}')
    logger.info("最初のコミットを入れました: %s (%s)", ref.full_name, branch)


def _reject_existing_branch(gh: GhClient, ref: GitHubRef, clone: Path, branch: str) -> None:
    if _run_git(clone, 'ls-remote', '--exit-code', '--heads', 'origin', branch).returncode != 0:
        return
    url = gh.find_open_pr(ref, branch)
    if url:
        raise RepositoryError(
            f"ブランチ {branch} と開いている PR が既にあります: {url}\n"
            "PR を片付けてから同じコマンドを打ち直してください"
        )
    raise RepositoryError(
        f"ブランチ {branch} が {ref.full_name} に既にあります。"
        "消してから同じコマンドを打ち直してください"
    )


def publish_change(
    gh: GhClient,
    ref: GitHubRef,
    *,
    kind: str,
    name: str,
    files: Dict[str, str],
    registry_entry: Optional[str] = None,
) -> str:
    """ブランチへ変更を push し、PR を作ってマージする。PR の URL を返す。

    ``registry_entry`` があれば ``registry.yml`` へその名前の項目を足す。

    Raises:
        MergeFailed: PR を作ったがマージできなかったとき
    """
    branch = branch_name(kind, name)
    title = pr_title(kind, name)
    with tempfile.TemporaryDirectory(prefix='devbase-publish-') as tmp:
        clone = Path(tmp) / 'repo'
        git_clone(ref.clone_url, clone)
        base = _git(clone, 'branch', '--show-current')
        _reject_existing_branch(gh, ref, clone, branch)
        _git(clone, 'checkout', '-b', branch)
        _write_files(clone, files)
        if registry_entry is not None:
            _add_to_registry(clone, registry_entry)
        _commit(clone, title)
        _git(clone, 'push', 'origin', f'{branch}:refs/heads/{branch}')
    logger.info("ブランチを push しました: %s", branch)

    url = gh.create_pr(ref, base=base, head=branch, title=title, body=_PR_BODY)
    logger.info("PR を作りました: %s", url)
    try:
        gh.merge_pr(ref, url)
    except RepositoryError as e:
        raise MergeFailed(f"{url} をマージできませんでした: {e}", url) from e
    logger.info("PR をマージしました: %s", url)
    return url
