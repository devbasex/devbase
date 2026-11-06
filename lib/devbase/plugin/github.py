"""GitHub の参照の解決と ``gh`` の呼び出し (#514)。

引数の解決 (:class:`GitHubRef`) と、権限の確認・リポジトリの作成・PR・マージ
(:class:`GhClient`) を持つ。認証は利用者の ``gh`` の設定に任せ、トークンを扱わない
(設計の決定 11)。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from devbase.errors import RepositoryError

_HTTPS_RE = re.compile(r'https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?')
_SSH_RE = re.compile(r'git@github\.com:([^/]+)/([^/]+?)(?:\.git)?')
_SHORT_RE = re.compile(r'([^/:@]+)/([^/:@]+?)(?:\.git)?')

_OWNER_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]*')
_REPO_RE = re.compile(r'[A-Za-z0-9._][A-Za-z0-9._-]*')
_RESERVED_REPOS = frozenset({'.', '..', '.git'})

_PUSH_PERMISSIONS = frozenset({'ADMIN', 'MAINTAIN', 'WRITE'})
_MISSING_REPO_MARK = 'Could not resolve to a Repository'
_NOT_ACCESSIBLE_MARKS = ('HTTP 403', 'HTTP 404')

#: マージの方式。リポジトリが許す順に選ぶ (設計の決定 4)
_MERGE_METHODS = (
    ('mergeCommitAllowed', '--merge'),
    ('squashMergeAllowed', '--squash'),
    ('rebaseMergeAllowed', '--rebase'),
)
#: ``mergeable`` が ``UNKNOWN`` の間の待ち (2 秒 × 15 回で最長 30 秒。設計の決定 4)
_MERGEABLE_POLL_SECONDS = 2
_MERGEABLE_POLLS = 15


@dataclass(frozen=True)
class GitHubRef:
    """GitHub のリポジトリの参照。``clone_url`` は入力の形を保つ (``git@`` は ``git@`` のまま)"""
    owner: str
    repo: str
    clone_url: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @classmethod
    def parse(cls, value: str) -> 'GitHubRef':
        """``owner/repo``・``https://github.com/...``・``git@github.com:...`` を解決する。

        Raises:
            RepositoryError: 形が違う・GitHub 以外のとき
        """
        text = (value or '').strip()
        ssh = _SSH_RE.fullmatch(text)
        match = ssh or _HTTPS_RE.fullmatch(text) or _SHORT_RE.fullmatch(text)
        if match is None:
            raise RepositoryError(
                f"GitHub のリポジトリとして読めません: '{value}'\n"
                "owner/repo・https://github.com/owner/repo・git@github.com:owner/repo.git の"
                "どれかで指定してください"
            )
        owner, repo = match.groups()
        if (_OWNER_RE.fullmatch(owner) is None or _REPO_RE.fullmatch(repo) is None
                or repo in _RESERVED_REPOS):
            raise RepositoryError(f"GitHub の owner/repo の形ではありません: '{value}'")
        clone_url = (f"git@github.com:{owner}/{repo}.git" if ssh
                     else f"https://github.com/{owner}/{repo}.git")
        return cls(owner, repo, clone_url)


@dataclass(frozen=True)
class RepoView:
    """``gh repo view`` の結果"""
    empty: bool
    default_branch: str
    can_push: bool


class GhClient:
    """``gh`` の呼び出し。読むものと書くものをメソッドで分ける"""

    def __init__(self) -> None:
        self._login: Optional[str] = None

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(['gh', *args], capture_output=True, text=True, check=False)

    def _json(self, *args: str, what: str):
        result = self._run(*args)
        if result.returncode != 0:
            raise RepositoryError(f"{what}に失敗しました: {result.stderr.strip()}")
        return json.loads(result.stdout)

    def ensure_ready(self) -> None:
        """``gh`` が入っていて認証済みであることを確かめる (読むだけ)"""
        if shutil.which('gh') is None:
            raise RepositoryError(
                "gh が見つかりません。https://cli.github.com/ から入れて、"
                "gh auth login でログインしてください"
            )
        self.login()

    def login(self) -> str:
        """認証中のアカウントの ``login``"""
        if self._login is None:
            result = self._run('api', 'user')
            if result.returncode != 0:
                raise RepositoryError(
                    "gh の認証を確かめられません。gh auth login でログインしてください: "
                    f"{result.stderr.strip()}"
                )
            self._login = json.loads(result.stdout)['login']
        return self._login

    def view_repo(self, ref: GitHubRef) -> Optional[RepoView]:
        """リポジトリの状態を返す。無い (読めない private を含む) ときは ``None``"""
        result = self._run('repo', 'view', ref.full_name,
                           '--json', 'isEmpty,defaultBranchRef,viewerPermission')
        if result.returncode != 0:
            if _MISSING_REPO_MARK in result.stderr:
                return None
            raise RepositoryError(
                f"{ref.full_name} の確認に失敗しました: {result.stderr.strip()}")
        data = json.loads(result.stdout)
        return RepoView(
            empty=bool(data.get('isEmpty')),
            default_branch=(data.get('defaultBranchRef') or {}).get('name') or '',
            can_push=data.get('viewerPermission') in _PUSH_PERMISSIONS,
        )

    def can_create_in(self, owner: str) -> bool:
        """``owner`` の下にリポジトリを作れるか (読むだけ)

        組織の ``members_can_create_repositories`` は組織の owner にしか返らないので、値が無い
        ときは判定できない。作れると見て進め、作れなければ ``gh repo create`` の失敗で止める。
        """
        if owner.lower() == self.login().lower():
            return True
        membership = self._read_api(f'user/memberships/orgs/{owner}')
        if membership is None or membership.get('state') != 'active':
            return False
        if membership.get('role') == 'admin':
            return True
        org = self._read_api(f'orgs/{owner}')
        return (org or {}).get('members_can_create_repositories') is not False

    def _read_api(self, path: str) -> Optional[dict]:
        """権限が無い・無いものは ``None``、それ以外の失敗は例外にする"""
        result = self._run('api', path)
        if result.returncode == 0:
            return json.loads(result.stdout)
        if any(mark in result.stderr for mark in _NOT_ACCESSIBLE_MARKS):
            return None
        raise RepositoryError(f"gh api {path} に失敗しました: {result.stderr.strip()}")

    def read_file(self, ref: GitHubRef, path: str) -> Optional[str]:
        """既定ブランチのファイルの中身。無いときは ``None``。ディレクトリは一覧の JSON が返る"""
        result = self._run('api', f'repos/{ref.full_name}/contents/{path}',
                           '-H', 'Accept: application/vnd.github.raw+json')
        if result.returncode == 0:
            return result.stdout
        if 'HTTP 404' in result.stderr:
            return None
        raise RepositoryError(
            f"{ref.full_name} の {path} を読めません: {result.stderr.strip()}")

    def create_repo(self, ref: GitHubRef, *, public: bool) -> None:
        visibility = '--public' if public else '--private'
        result = self._run('repo', 'create', ref.full_name, visibility)
        if result.returncode != 0:
            raise RepositoryError(
                f"{ref.full_name} を作れませんでした: {result.stderr.strip()}")

    def create_pr(self, ref: GitHubRef, *, base: str, head: str,
                  title: str, body: str) -> str:
        result = self._run('pr', 'create', '--repo', ref.full_name, '--base', base,
                           '--head', head, '--title', title, '--body', body)
        if result.returncode != 0:
            raise RepositoryError(
                f"{ref.full_name} へ PR を作れませんでした: {result.stderr.strip()}\n"
                f"ブランチ {head} は GitHub に残っています。"
                "消してから同じコマンドを打ち直してください"
            )
        return result.stdout.strip().splitlines()[-1]

    def find_open_pr(self, ref: GitHubRef, branch: str) -> Optional[str]:
        pulls = self._json('pr', 'list', '--repo', ref.full_name, '--head', branch,
                           '--state', 'open', '--json', 'url',
                           what=f"{ref.full_name} の PR の確認")
        return pulls[0]['url'] if pulls else None

    def merge_pr(self, ref: GitHubRef, url: str) -> None:
        """PR を 1 回だけマージする。CI を待たず、``--auto``・``--admin`` を使わない (決定 4)"""
        mergeable = self._wait_mergeable(ref, url)
        if mergeable != 'MERGEABLE':
            raise RepositoryError(f"マージできる状態ではありません (mergeable: {mergeable})")
        result = self._run('pr', 'merge', url, '--repo', ref.full_name,
                           self._merge_method(ref), '--delete-branch')
        if result.returncode != 0:
            raise RepositoryError(f"マージに失敗しました: {result.stderr.strip()}")

    def _wait_mergeable(self, ref: GitHubRef, url: str) -> str:
        mergeable = 'UNKNOWN'
        for attempt in range(_MERGEABLE_POLLS):
            if attempt:
                time.sleep(_MERGEABLE_POLL_SECONDS)
            mergeable = self._json('pr', 'view', url, '--repo', ref.full_name,
                                   '--json', 'mergeable',
                                   what="PR の状態の確認")['mergeable']
            if mergeable != 'UNKNOWN':
                break
        return mergeable

    def _merge_method(self, ref: GitHubRef) -> str:
        allowed = self._json('repo', 'view', ref.full_name, '--json',
                             ','.join(key for key, _ in _MERGE_METHODS),
                             what=f"{ref.full_name} のマージの方式の確認")
        for key, flag in _MERGE_METHODS:
            if allowed.get(key):
                return flag
        raise RepositoryError(f"{ref.full_name} はどのマージの方式も許していません")
