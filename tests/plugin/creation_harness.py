"""作成フローのテストの土台: 偽の ``gh`` と手元の bare リポジトリ (#514)。

実の GitHub へ触らない。``git`` は本物を使い、``https://github.com/`` と
``git@github.com:`` を ``url.<base>.insteadOf`` で手元の bare リポジトリへ向ける。``gh`` は
PATH の先頭に置いたこのファイル自身で、状態は ``state.json`` に持つ。
``python creation_harness.py <world> <gh の引数...>`` で偽の ``gh`` として動く。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

GIT = ['git', '-c', 'protocol.file.allow=always']


def _bare_git(bare, *args, check=True):
    return subprocess.run([*GIT, '--git-dir', str(bare), *args], capture_output=True,
                          text=True, check=check)


def init_bare(world: Path, full_name: str) -> Path:
    bare = world / 'git' / f'{full_name}.git'
    bare.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '--bare', '-q', '-b', 'main', str(bare)], check=True)
    hook = bare / 'hooks' / 'post-receive'
    hook.write_text(f'#!/bin/sh\ncat >> "{world}/pushes-{full_name.replace("/", "--")}.log"\n')
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    return bare


# ── 偽の gh ─────────────────────────────────────────────────────


class _Fail(Exception):
    pass


def _option(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def _repo_entry(state, full_name):
    entry = state['repos'].get(full_name)
    if entry is None or entry.get('hidden'):
        raise _Fail(f"GraphQL: Could not resolve to a Repository with the name '{full_name}'.")
    return entry


def _gh_api(world, state, argv):
    path = argv[0]
    if path == 'user':
        return json.dumps({'login': state['user']})
    if path.startswith('user/memberships/orgs/'):
        org = state['orgs'].get(path.rsplit('/', 1)[-1])
        if org is None:
            raise _Fail('gh: Not Found (HTTP 404)')
        return json.dumps({'state': org['state'], 'role': org['role']})
    if path.startswith('orgs/'):
        org = state['orgs'].get(path.split('/', 1)[1])
        if org is None:
            raise _Fail('gh: Not Found (HTTP 404)')
        # 実の GitHub はこの値を組織の owner にしか返さない
        return json.dumps({'members_can_create_repositories': True}
                          if org['role'] == 'admin' else {'login': path.split('/', 1)[1]})
    if path.startswith('repos/'):
        _, owner, repo, _, file_path = path.split('/', 4)
        bare = world / 'git' / owner / f'{repo}.git'
        found = _bare_git(bare, 'show', f'HEAD:{file_path}', check=False)
        if found.returncode != 0:
            raise _Fail('gh: Not Found (HTTP 404)')
        return found.stdout
    raise _Fail(f'unexpected api path: {path}')


def _gh_repo_view(world, state, argv):
    full_name = argv[0]
    entry = _repo_entry(state, full_name)
    bare = world / 'git' / f'{full_name}.git'
    has_commits = bool(_bare_git(bare, 'for-each-ref', 'refs/heads').stdout.strip())
    allowed = entry.get('merge_methods', ['merge', 'squash', 'rebase'])
    values = {
        'isEmpty': not has_commits,
        'defaultBranchRef': {'name': 'main'} if has_commits else None,
        'viewerPermission': entry.get('permission', 'ADMIN'),
        'mergeCommitAllowed': 'merge' in allowed,
        'squashMergeAllowed': 'squash' in allowed,
        'rebaseMergeAllowed': 'rebase' in allowed,
    }
    return json.dumps({k: values[k] for k in _option(argv, '--json').split(',')})


def _gh_repo_create(world, state, argv):
    full_name = argv[0]
    if full_name in state['repos']:
        raise _Fail('GraphQL: Name already exists on this account (createRepository)')
    org = state['orgs'].get(full_name.split('/')[0])
    if org is not None and org['role'] != 'admin' and not org['can_create']:
        raise _Fail('GraphQL: owner cannot create repositories (createRepository)')
    state['repos'][full_name] = {
        'permission': 'ADMIN',
        'visibility': 'public' if '--public' in argv else 'private',
    }
    init_bare(world, full_name)
    return f'https://github.com/{full_name}'


def _gh_pr_create(world, state, argv):
    full_name = _option(argv, '--repo')
    if state['repos'][full_name].get('pr_error'):
        raise _Fail(state['repos'][full_name]['pr_error'])
    number = len(state['prs']) + 1
    url = f'https://github.com/{full_name}/pull/{number}'
    state['prs'].append({
        'url': url, 'repo': full_name, 'base': _option(argv, '--base'),
        'head': _option(argv, '--head'), 'title': _option(argv, '--title'), 'state': 'OPEN',
    })
    return url


def _find_pr(state, url):
    return next(pr for pr in state['prs'] if pr['url'] == url)


def _gh_pr_view(state, argv):
    entry = state['repos'][_option(argv, '--repo')]
    return json.dumps({'mergeable': entry.get('mergeable', 'MERGEABLE')})


def _gh_pr_merge(world, state, argv):
    pr = _find_pr(state, argv[0])
    entry = state['repos'][pr['repo']]
    if entry.get('merge_error'):
        raise _Fail(entry['merge_error'])
    bare = world / 'git' / f"{pr['repo']}.git"
    head = _bare_git(bare, 'rev-parse', f"refs/heads/{pr['head']}").stdout.strip()
    _bare_git(bare, 'update-ref', f"refs/heads/{pr['base']}", head)
    if '--delete-branch' in argv:
        _bare_git(bare, 'update-ref', '-d', f"refs/heads/{pr['head']}")
    pr['state'] = 'MERGED'
    return ''


def _gh_pr_list(state, argv):
    head = _option(argv, '--head')
    return json.dumps([{'url': pr['url']} for pr in state['prs']
                       if pr['head'] == head and pr['state'] == 'OPEN'])


def _gh(world, state, argv):
    if not state['authed']:
        raise _Fail('To get started with GitHub CLI, please run:  gh auth login')
    group, rest = argv[0], argv[1:]
    if group == 'api':
        return _gh_api(world, state, rest)
    action, rest = rest[0], rest[1:]
    handlers = {
        ('repo', 'view'): lambda: _gh_repo_view(world, state, rest),
        ('repo', 'create'): lambda: _gh_repo_create(world, state, rest),
        ('pr', 'create'): lambda: _gh_pr_create(world, state, rest),
        ('pr', 'view'): lambda: _gh_pr_view(state, rest),
        ('pr', 'merge'): lambda: _gh_pr_merge(world, state, rest),
        ('pr', 'list'): lambda: _gh_pr_list(state, rest),
    }
    return handlers[(group, action)]()


def _gh_main(argv):
    world = Path(argv[0])
    args = argv[1:]
    with open(world / 'calls.jsonl', 'a') as f:
        f.write(json.dumps(args) + '\n')
    state = json.loads((world / 'state.json').read_text())
    try:
        out = _gh(world, state, args)
    except _Fail as e:
        print(str(e), file=sys.stderr)
        return 1
    (world / 'state.json').write_text(json.dumps(state))
    print(out)
    return 0


# ── テストから使う側 ────────────────────────────────────────────


WRITE_CALLS = (('repo', 'create'), ('pr', 'create'), ('pr', 'merge'))


class FakeGitHub:
    def __init__(self, world: Path):
        self.world = world
        self.bin = world / 'bin'
        self.bin.mkdir(parents=True)
        self.update(user='me', authed=True, orgs={}, repos={}, prs=[])
        shim = self.bin / 'gh'
        shim.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{__file__}" "{world}" "$@"\n')
        shim.chmod(0o755)
        for tool in ('docker', 'docker-compose'):
            spy = self.bin / tool
            spy.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{world}/docker.log"\n')
            spy.chmod(0o755)
        self.gitconfig = world / 'gitconfig'
        self.gitconfig.write_text(
            '[user]\n\tname = tester\n\temail = tester@example.com\n'
            '[init]\n\tdefaultBranch = main\n'
            '[protocol "file"]\n\tallow = always\n'
            f'[url "file://{world}/git/"]\n'
            '\tinsteadOf = https://github.com/\n'
            '\tinsteadOf = git@github.com:\n'
        )

    # 状態
    @property
    def _state_file(self):
        return self.world / 'state.json'

    def state(self) -> dict:
        return json.loads(self._state_file.read_text())

    def update(self, **values) -> None:
        state = self.state() if self._state_file.exists() else {}
        state.update(values)
        self._state_file.write_text(json.dumps(state))

    def ssh_only(self) -> None:
        """https の URL を使えなくする (SSH の認証だけの利用者)。"""
        text = self.gitconfig.read_text().replace('\tinsteadOf = https://github.com/\n', '')
        self.gitconfig.write_text(
            f'{text}[url "file://{self.world}/no-https/"]\n\tinsteadOf = https://github.com/\n')

    def update_repo(self, full_name: str, **values) -> None:
        state = self.state()
        state['repos'][full_name].update(values)
        self._state_file.write_text(json.dumps(state))

    def add_org(self, name, *, state='active', role='member', can_create=False):
        orgs = self.state()['orgs']
        orgs[name] = {'state': state, 'role': role, 'can_create': can_create}
        self.update(orgs=orgs)

    def add_repo(self, full_name: str, files=None, **values) -> Path:
        """``files`` が空なら空のリポジトリを作る。"""
        state = self.state()
        state['repos'][full_name] = {'permission': 'ADMIN', **values}
        self._state_file.write_text(json.dumps(state))
        bare = init_bare(self.world, full_name)
        if files:
            self.commit(full_name, files, message='seed')
            pushes = self.world / f'pushes-{full_name.replace("/", "--")}.log'
            pushes.unlink(missing_ok=True)
        return bare

    def commit(self, full_name: str, files: dict, message='change', remove=()) -> None:
        """既定ブランチへ別の人が入れたコミットを作る。値が ``Path`` なら symlink にする。"""
        bare = self.world / 'git' / f'{full_name}.git'
        work = self.world / 'seed'
        env = {**os.environ, 'GIT_CONFIG_GLOBAL': str(self.gitconfig)}
        subprocess.run([*GIT, 'clone', '-q', str(bare), str(work)], check=True, env=env,
                       capture_output=True)
        subprocess.run(['git', 'checkout', '-q', '-B', 'main'], cwd=work, check=True, env=env)
        for path, text in files.items():
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(text, Path):
                target.symlink_to(text)
            else:
                target.write_text(text)
        for path in remove:
            subprocess.run(['git', 'rm', '-rqf', path], cwd=work, check=True, env=env)
        subprocess.run(['git', 'add', '-A'], cwd=work, check=True, env=env)
        subprocess.run(['git', 'commit', '-q', '-m', message], cwd=work, check=True, env=env)
        subprocess.run([*GIT, 'push', '-q', 'origin', 'main'], cwd=work, check=True, env=env,
                       capture_output=True)
        subprocess.run(['rm', '-rf', str(work)], check=True)

    # 観察
    def calls(self) -> list:
        path = self.world / 'calls.jsonl'
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def write_calls(self) -> list:
        return [c for c in self.calls() if tuple(c[:2]) in WRITE_CALLS]

    def docker_calls(self) -> list:
        path = self.world / 'docker.log'
        return path.read_text().splitlines() if path.exists() else []

    def exists(self, full_name: str) -> bool:
        return (self.world / 'git' / f'{full_name}.git').is_dir()

    def _bare(self, full_name):
        return self.world / 'git' / f'{full_name}.git'

    def file(self, full_name: str, path: str):
        found = _bare_git(self._bare(full_name), 'show', f'HEAD:{path}', check=False)
        return found.stdout if found.returncode == 0 else None

    def subjects(self, full_name: str) -> list:
        """既定ブランチのコミットの題 (古い順)。"""
        out = _bare_git(self._bare(full_name), 'log', '--reverse', '--format=%s', 'HEAD').stdout
        return out.splitlines()

    def branches(self, full_name: str) -> list:
        out = _bare_git(self._bare(full_name), 'for-each-ref', '--format=%(refname:short)',
                        'refs/heads').stdout
        return out.splitlines()

    def pushed_refs(self, full_name: str) -> list:
        """push された ref (順)。マージは含まない。"""
        path = self.world / f'pushes-{full_name.replace("/", "--")}.log'
        if not path.exists():
            return []
        return [line.split()[2] for line in path.read_text().splitlines()]

    def prs(self) -> list:
        return self.state()['prs']


if __name__ == '__main__':
    sys.exit(_gh_main(sys.argv[1:]))
