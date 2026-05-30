#!/usr/bin/env python3
"""devbase CLI entry point"""

import argparse
import os
import sys
from pathlib import Path

from devbase.errors import DevbaseError
from devbase.log import get_logger, setup

try:
    from . import __version__
except ImportError:
    __version__ = "2.2.0"

logger = get_logger("devbase.cli")

# Shortcuts: top-level command -> project subcommand
# 委譲先は共有の cmd_project (PLAN06 で container は非推奨化)。
# NOTE: `build` はここに含めない。配布入口 bin/devbase が `build` を shell の
# cmd_build (devbase-base 依存検出 + 2 段ビルド + --no-cache 対応) に委譲しており、
# Python の project build (単純な compose build) とは実装が異なるため。Python 側で
# `build` を project build ショートカットとして広告すると wrapper の実経路と乖離する。
# project build / container build サブコマンド自体は引き続き利用可能。
#
# 同期注意 (メンテナンス性): SHORTCUTS のキー集合と _add_project_parser の
# `name` positional 付きサブコマンドは bin/devbase の _NAME_RESOLVABLE_SHORTCUTS /
# _PROJECT_NAME_SUBCOMMANDS と対応している。サブコマンドを追加/削除する際は
# wrapper 側 (bin/devbase の該当リスト) の更新漏れに注意すること。
SHORTCUTS = {
    'up': 'up',
    'down': 'down',
    'login': 'login',
    'ps': 'ps',
    'scale': 'scale',
}

# Group aliases
GROUP_ALIASES = {
    'ct': 'container',
    'pl': 'plugin',
    'ss': 'snapshot',
}

# Subcommand map for prefix resolution: {(aliases...): [subcmds]}
SUBCMD_MAP = {
    ('project',):        ['up', 'down', 'ps', 'login', 'logs', 'scale', 'build'],
    ('container', 'ct'): ['up', 'down', 'ps', 'login', 'logs', 'scale', 'build'],
    ('env',):            ['init', 'sync', 'list', 'set', 'get', 'delete', 'edit', 'project', 'export', 'import'],
    ('plugin', 'pl'):    ['list', 'install', 'uninstall', 'update', 'info', 'sync', 'repo', 'migrate'],
    ('snapshot', 'ss'):  ['create', 'list', 'restore', 'copy', 'delete', 'rotate'],
}

# 後方互換: prefix が複数候補にマッチする場合に、特定の入力を特定のサブコマンドに
# 優先的に解決させる。例えば `devbase env e` は従来 `edit` のみに解決されていたが、
# `export` 追加後は ambiguous になるため、既存ショートカットを維持するために維持先を明示する。
SUBCMD_PREFIX_PREFERENCES = {
    ('env',): {
        'e': 'edit',
        # `import` 追加で `i` が `init` / `import` の両方にマッチして ambiguous に
        # なるため、既存ショートカット (`devbase env i` → `init`) を維持する。
        'i': 'init',
    },
}


def _require_devbase_root() -> Path:
    """Get DEVBASE_ROOT from environment, exiting if not set."""
    root = os.environ.get('DEVBASE_ROOT')
    if not root:
        logger.error("DEVBASE_ROOT environment variable not set")
        sys.exit(1)
    return Path(root)


def _add_login_subparser(sub):
    """`login` サブコマンドを登録する (project / container 共通)。

    単一 positional `index` の意味は両グループで完全に同一。`[name]` を足すと
    `project login 2` を name='2' と誤解釈して index=1 にログインしてしまう曖昧さ
    (旧 `container login <index>` との非互換) が生じるため、project でも name を
    受け付けない。PR2 で project name 解決を導入する際は曖昧さのない `--name`
    オプションで対応する方針。
    """
    p = sub.add_parser('login', help='Login to container')
    p.add_argument('index', nargs='?', default='1', help='Container index')


def _add_build_subparser(sub):
    """`build` サブコマンドを登録する (project / container 共通)。

    単一 positional `image` の意味は両グループで同一。`[name]` を許すと
    `project build web` が name='web', image=None となり image 指定ビルドが
    compose build に化けるため、project でも name を受け付けない (login 参照)。
    """
    p = sub.add_parser('build', help='Build container images')
    p.add_argument('image', nargs='?', default=None, help='Image name')


def _add_container_parser(subparsers):
    """Container group parser"""
    ct_parser = subparsers.add_parser('container', aliases=['ct'],
                                      help='Manage containers')
    ct_sub = ct_parser.add_subparsers(dest='subcommand')

    ct_sub.add_parser('up', help='Start containers')
    ct_sub.add_parser('down', help='Stop and remove containers')

    _add_login_subparser(ct_sub)

    ct_ps = ct_sub.add_parser('ps', help='Show container status')
    ct_ps.add_argument('--all', '-a', action='store_true', help='Show all containers')

    ct_logs = ct_sub.add_parser('logs', help='Show container logs')
    ct_logs.add_argument('--follow', '-f', action='store_true', help='Follow log output')
    ct_logs.add_argument('--tail', type=int, default=None, help='Number of lines')

    ct_scale = ct_sub.add_parser('scale', help='Scale containers online')
    ct_scale.add_argument('new_scale', type=int, help='New number of containers')

    _add_build_subparser(ct_sub)


def _add_project_parser(subparsers):
    """Project group parser (CWD 非依存のプロジェクト操作)。

    `container` と同じ subcommand 群に、省略可能な `[name]` positional を加える。
    name によるディレクトリ解決 / COMPOSE_PROJECT_NAME 上書きは PLAN06 Task 2 (PR2)
    で wrapper の cd + Python フォールバックとして実装する。PR1 では parser 構造と
    name のパースまでを用意する。

    例外: `login` / `build` は単一 positional が旧 `container` と同義 (index / image)
    であり、`[name]` を足すと `project login 2` / `project build web` が誤解釈される
    ため name を受け付けない。両者は project / container で定義が完全に一致するので
    `_add_login_subparser` / `_add_build_subparser` に共通化している。

    同期注意: ここで `name` positional を持つサブコマンド集合 (up/down/ps/logs/scale)
    は bin/devbase の `_PROJECT_NAME_SUBCOMMANDS` と一致させる必要がある。追加/削除時は
    wrapper 側リストの更新漏れに注意すること。
    """
    pj_parser = subparsers.add_parser('project', help='Manage projects (CWD-independent)')
    pj_sub = pj_parser.add_subparsers(dest='subcommand')

    pj_up = pj_sub.add_parser('up', help='Start containers')
    pj_up.add_argument('name', nargs='?', default=None, help='Project name')

    pj_down = pj_sub.add_parser('down', help='Stop and remove containers')
    pj_down.add_argument('name', nargs='?', default=None, help='Project name')

    _add_login_subparser(pj_sub)

    pj_ps = pj_sub.add_parser('ps', help='Show container status')
    pj_ps.add_argument('name', nargs='?', default=None, help='Project name')
    pj_ps.add_argument('--all', '-a', action='store_true', help='Show all containers')

    pj_logs = pj_sub.add_parser('logs', help='Show container logs')
    pj_logs.add_argument('name', nargs='?', default=None, help='Project name')
    pj_logs.add_argument('--follow', '-f', action='store_true', help='Follow log output')
    pj_logs.add_argument('--tail', type=int, default=None, help='Number of lines')

    # NOTE: `[name]` optional + `new_scale` 必須 int の順。値が 1 個なら new_scale に、
    # 2 個なら (name, new_scale) に割り当てられ曖昧にならない (tests/cli 参照)。
    pj_scale = pj_sub.add_parser('scale', help='Scale containers online')
    pj_scale.add_argument('name', nargs='?', default=None, help='Project name')
    pj_scale.add_argument('new_scale', type=int, help='New number of containers')

    _add_build_subparser(pj_sub)


def _add_env_parser(subparsers):
    """Env group parser"""
    env_parser = subparsers.add_parser('env', help='Manage environment variables')
    env_sub = env_parser.add_subparsers(dest='subcommand')

    env_init = env_sub.add_parser('init', help='Initial setup (interactive)')
    env_init.add_argument('--reset', action='store_true', help='Reset existing config')

    env_sub.add_parser('sync', help='Resync credentials from sources')

    env_list = env_sub.add_parser('list', help='List variables')
    env_list.add_argument('--global', '-g', action='store_true', dest='global_only',
                          help='Show global variables only')
    env_list.add_argument('--project', '-p', action='store_true', dest='project_only',
                          help='Show project variables only')
    env_list.add_argument('--reveal', '-r', action='store_true', help='Reveal sensitive values')
    env_list.add_argument('--keys', '-k', action='store_true', dest='keys_only',
                          help='Show keys only')

    env_set = env_sub.add_parser('set', help='Set a variable')
    env_set.add_argument('assignment', help='KEY=VALUE')
    env_set.add_argument('--project', '-p', action='store_true', help='Set in project .env')

    env_get = env_sub.add_parser('get', help='Get a variable')
    env_get.add_argument('key', help='Variable name')

    env_delete = env_sub.add_parser('delete', help='Delete a variable')
    env_delete.add_argument('key', help='Variable name')

    env_sub.add_parser('edit', help='Open .env in editor')
    env_sub.add_parser('project', help='Setup project-specific variables')

    env_export = env_sub.add_parser(
        'export',
        help='Export .env files as an encrypted bundle (age)',
    )
    env_export.add_argument('dest', nargs='?', default=None,
                            help="Output path (default: ./devbase-env-<TS>.dbenv, '-' for stdout)")
    env_export.add_argument('--include-project', action='append', default=None,
                            metavar='NAME', dest='include_projects',
                            help='Limit to specified project (repeatable)')
    env_export.add_argument('--exclude-project', action='append', default=[],
                            metavar='NAME', dest='exclude_projects',
                            help='Exclude project (repeatable)')
    env_export.add_argument('--no-global', action='store_true',
                            help='Exclude $DEVBASE_ROOT/.env')
    env_export.add_argument('--no-metadata', action='store_true',
                            help='Exclude $DEVBASE_ROOT/.env.sources.yml')
    env_export.add_argument('--recipient', action='append', default=[],
                            metavar='KEY', dest='recipients',
                            help=("age / OpenSSH public key (repeatable). "
                                  "Formats: 'age1...', 'ssh-ed25519 AAAA...', 'ssh-rsa AAAA...', "
                                  "'@PATH' for file reference. "
                                  "Default: ~/.ssh/id_ed25519.pub, then ~/.ssh/id_rsa.pub "
                                  "(first existing one)"))
    env_export.add_argument('--passphrase-env', metavar='VAR', default=None,
                            help='Read passphrase from environment variable VAR')
    env_export.add_argument('--passphrase-stdin', action='store_true',
                            help='Read passphrase from the first line of stdin')
    env_export.add_argument('--force-unencrypted', action='store_true',
                            help='Write as plaintext tar.gz (rejected by default; '
                                 'warns when sensitive keys are detected)')
    env_export.add_argument('--unsafe-allow-unencrypted-bucket', action='store_true',
                            help='Allow S3 export to buckets without default encryption '
                                 '(per-object SSE is always applied regardless of this flag). '
                                 'Has no effect for non-s3:// destinations.')

    env_import = env_sub.add_parser(
        'import',
        help='Import .env files from a bundle (age-encrypted or plaintext tar.gz)',
    )
    env_import.add_argument('source',
                            help="Bundle path or '-' for stdin")
    env_import.add_argument('--merge', choices=['keep-existing', 'prefer-incoming'],
                            default='keep-existing',
                            help=("Key-level merge mode. keep-existing (default) keeps "
                                  "existing keys and adds new ones; prefer-incoming "
                                  "overwrites with bundle values"))
    env_import.add_argument('--replace-keys', metavar='KEYS', default='',
                            help=("Comma-separated keys to force-overwrite from bundle "
                                  "(other keys behave like keep-existing). "
                                  "Cannot be combined with --replace"))
    env_import.add_argument('--replace', action='store_true',
                            help='Replace each target .env file wholesale (backup is taken)')
    env_import.add_argument('--dry-run', action='store_true',
                            help='Show planned diff without writing')
    env_import.add_argument('--identity', action='append', default=[],
                            metavar='FILE', dest='identities',
                            help=("age / OpenSSH private key file (repeatable). "
                                  "Default: ~/.ssh/id_ed25519, then ~/.ssh/id_rsa "
                                  "(first existing one)"))
    env_import.add_argument('--passphrase-env', metavar='VAR', default=None,
                            help='Read passphrase from environment variable VAR')
    env_import.add_argument('--passphrase-stdin', action='store_true',
                            help='Read passphrase from the first line of stdin')
    env_import.add_argument('--include-project', action='append', default=None,
                            metavar='NAME', dest='include_projects',
                            help='Limit to specified project (repeatable)')
    env_import.add_argument('--exclude-project', action='append', default=[],
                            metavar='NAME', dest='exclude_projects',
                            help='Exclude project (repeatable)')
    env_import.add_argument('--no-global', action='store_true',
                            help='Do not import $DEVBASE_ROOT/.env')
    env_import.add_argument('--no-metadata', action='store_true',
                            help='Do not import $DEVBASE_ROOT/.env.sources.yml '
                                 '(default behavior is reference-only copy; this fully ignores it)')
    env_import.add_argument('--merge-metadata', action='store_true',
                            help='Merge new source entries into existing .env.sources.yml '
                                 '(machine-specific fields are preserved as-is from bundle; '
                                 'run `devbase env sync` after import to refresh)')
    env_import.add_argument('--backup-dir', metavar='DIR', default=None,
                            help='Override backup directory '
                                 '(default: $DEVBASE_ROOT/backups/env-import/<ts>)')
    env_import.add_argument('--keep-last', type=int, default=10, metavar='N',
                            help='Keep only the last N backup directories (default: 10, 0 to disable)')


def _add_plugin_parser(subparsers):
    """Plugin group parser"""
    pl_parser = subparsers.add_parser('plugin', aliases=['pl'],
                                      help='Manage plugins')
    pl_sub = pl_parser.add_subparsers(dest='subcommand')

    p_list = pl_sub.add_parser('list', help='List plugins')
    p_list.add_argument('--available', action='store_true',
                        help='Show available plugins')

    p_install = pl_sub.add_parser('install', help='Install a plugin')
    p_install.add_argument('source', help='Plugin source')
    p_install.add_argument('--link', action='store_true',
                           help='Install as symlink')
    p_install.add_argument('--all', action='store_true', dest='install_all',
                           help='Install all plugins from repository')

    p_uninstall = pl_sub.add_parser('uninstall', help='Uninstall a plugin')
    p_uninstall.add_argument('name', help='Plugin name')

    p_update = pl_sub.add_parser('update', help='Update plugin(s)')
    p_update.add_argument('name', nargs='?', default=None, help='Plugin name')

    p_info = pl_sub.add_parser('info', help='Show plugin details')
    p_info.add_argument('name', help='Plugin name')

    pl_sub.add_parser('sync', help='Resync project symlinks')

    pl_sub.add_parser('migrate',
                      help='Migrate legacy plugins/ installs to repos/ clones')

    # Plugin repo sub-subcommands
    pl_repo = pl_sub.add_parser('repo', help='Manage plugin repositories')
    pl_repo_sub = pl_repo.add_subparsers(dest='repo_command')

    r_add = pl_repo_sub.add_parser('add', help='Register a repository')
    r_add.add_argument('url', help='Repository URL or GitHub shorthand')
    r_add.add_argument('--name', default=None, help='Custom name')

    r_remove = pl_repo_sub.add_parser('remove', help='Unregister a repository')
    r_remove.add_argument('name', help='Repository name')
    r_remove.add_argument('--force', action='store_true',
                          help='Force removal even if repo has uncommitted/unpushed changes')

    pl_repo_sub.add_parser('list', help='List repositories')

    r_refresh = pl_repo_sub.add_parser('refresh', help='Refresh repository')
    r_refresh.add_argument('name', nargs='?', default=None, help='Repository name')


def _add_snapshot_parser(subparsers):
    """Snapshot group parser"""
    ss_parser = subparsers.add_parser('snapshot', aliases=['ss'],
                                      help='Manage snapshots')
    ss_sub = ss_parser.add_subparsers(dest='subcommand')

    s_create = ss_sub.add_parser('create', help='Create a snapshot')
    s_create.add_argument('--name', default=None, help='Snapshot name')
    s_create.add_argument('--full', action='store_true', help='Force full backup')

    ss_sub.add_parser('list', help='List snapshots')

    s_restore = ss_sub.add_parser('restore', help='Restore from a snapshot')
    s_restore.add_argument('name', help='Snapshot name')
    s_restore.add_argument('--point', type=int, default=None, metavar='N',
                           help='Restore up to incr-N only')

    s_copy = ss_sub.add_parser('copy', help='Copy a snapshot')
    s_copy.add_argument('name', help='Source snapshot name')
    s_copy.add_argument('new_name', help='New snapshot name')

    s_delete = ss_sub.add_parser('delete', help='Delete a snapshot')
    s_delete.add_argument('name', help='Snapshot name')

    s_rotate = ss_sub.add_parser('rotate', help='Rotate old snapshots')
    s_rotate.add_argument('--keep', type=int, default=3, help='Generations to keep')


def _add_shortcuts(subparsers):
    """Top-level shortcut parsers.

    委譲先の `project` サブコマンドと引数体系を揃えるため、`up` / `down` / `ps` /
    `scale` は `project <sub> [name]` と同じく省略可能な `[name]` positional を
    受け付ける (`devbase up carmo` ≡ `devbase project up carmo`)。受理した name は
    _dispatch でショートカット経由でも下流 (cmd_project → _dispatch_lifecycle) へ
    伝播する。name の実解決は PLAN06 Task 2 (PR2) で実装するため、PR1 では up/scale
    も含め name 指定時に未対応 warning を出す (container.py 参照)。

    `login` は project login と同様に単一 positional を `index` として扱い `[name]`
    は受け付けない (曖昧さ回避)。`build` はショートカットに含めない (SHORTCUTS の
    注記参照): bin/devbase が build を shell 実装 (cmd_build) に委譲するため、
    Python 側でトップレベル build を広告すると実経路と乖離する。
    """
    login_sc = subparsers.add_parser('login', help='Login to container')
    login_sc.add_argument('index', nargs='?', default='1', help='Container index')

    ps_sc = subparsers.add_parser('ps', help='Show container status')
    ps_sc.add_argument('name', nargs='?', default=None, help='Project name')
    ps_sc.add_argument('--all', '-a', action='store_true', help='Show all containers')

    up_sc = subparsers.add_parser('up', help='Start containers')
    up_sc.add_argument('name', nargs='?', default=None, help='Project name')

    down_sc = subparsers.add_parser('down', help='Stop and remove containers')
    down_sc.add_argument('name', nargs='?', default=None, help='Project name')

    # `[name]` optional + `new_scale` 必須 int の順 (project scale と同じ規則)。
    scale_sc = subparsers.add_parser('scale', help='Scale containers online')
    scale_sc.add_argument('name', nargs='?', default=None, help='Project name')
    scale_sc.add_argument('new_scale', type=int, help='New number of containers')


def _create_parser():
    """Create command line parser"""
    parser = argparse.ArgumentParser(
        prog='devbase',
        description='Docker-based Development Environment Manager',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Shortcuts:\n"
            "  up            project up\n"
            "  down          project down\n"
            "  login         project login\n"
            "  ps            project ps\n"
            "  scale         project scale\n"
            "\n"
            "Note: `container` is deprecated; use `project` instead.\n"
        )
    )

    parser.add_argument(
        '--version',
        action='version',
        version=f'devbase {__version__}'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        default=False,
        help='Enable verbose (debug) output'
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # --- Top-level commands ---
    subparsers.add_parser('init', help='Initialize devbase environment')
    subparsers.add_parser('status', help='Show overall status')
    subparsers.add_parser(
        'shell-rc',
        help='Print shell RC file path (e.g. source "$(devbase shell-rc)")'
    )

    _add_project_parser(subparsers)
    _add_container_parser(subparsers)
    _add_env_parser(subparsers)
    _add_plugin_parser(subparsers)
    _add_snapshot_parser(subparsers)
    _add_shortcuts(subparsers)

    return parser


def _resolve_prefix(input_cmd, candidates, preferences=None):
    """Resolve an abbreviated command to its full name via unique prefix matching.

    Returns the full command name if exactly one candidate matches.
    If ambiguous, falls back to `preferences[input_cmd]` (if provided) to keep
    backward compatibility with previously-unique abbreviations.
    Otherwise returns the input as-is.
    """
    matches = [c for c in candidates if c.startswith(input_cmd)]
    if len(matches) == 1:
        return matches[0]
    if preferences and input_cmd in preferences:
        preferred = preferences[input_cmd]
        if preferred in matches:
            return preferred
    return input_cmd


def _expand_argv():
    """Expand abbreviated command/subcommand names in sys.argv in-place."""
    # この `commands` リストの並びは _create_parser のグループ登録順と一致させる:
    # トップレベル → グループ (各 group の直後にその alias を隣接配置: container/ct,
    # plugin/pl, snapshot/ss) → ショートカット。`project` (推奨) を `container`
    # (非推奨) より前に置くのは登録順と揃えた意図的な並びで、prefix 解決は
    # _resolve_prefix が一意一致のみ採用するため順序に機能的影響はない。
    # `build` はトップレベルショートカットから除外 (SHORTCUTS の注記参照)。
    # bin/devbase が build を shell 実装に委譲するため Python 側には top-level
    # build parser が無い。project build / container build は引き続き利用可能。
    commands = ['init', 'status', 'shell-rc', 'project', 'container', 'ct', 'env', 'plugin', 'pl',
                'snapshot', 'ss', 'up', 'down', 'login', 'ps', 'scale', 'help']
    repo_subcmds = ['add', 'remove', 'list', 'refresh']

    if len(sys.argv) >= 2 and not sys.argv[1].startswith('-'):
        sys.argv[1] = _resolve_prefix(sys.argv[1], commands)

    if len(sys.argv) >= 3 and not sys.argv[2].startswith('-'):
        cmd = sys.argv[1]
        for aliases, subcmds in SUBCMD_MAP.items():
            if cmd in aliases:
                preferences = SUBCMD_PREFIX_PREFERENCES.get(aliases)
                sys.argv[2] = _resolve_prefix(sys.argv[2], subcmds, preferences)
                break

    # plugin repo sub-subcommand
    if (len(sys.argv) >= 4 and not sys.argv[3].startswith('-')
            and sys.argv[1] in ('plugin', 'pl') and sys.argv[2] == 'repo'):
        sys.argv[3] = _resolve_prefix(sys.argv[3], repo_subcmds)


def main():
    """Main entry point for Python implementation"""
    _expand_argv()
    parser = _create_parser()
    args = parser.parse_args()

    setup(verbose=args.verbose)

    if not args.command:
        parser.print_help()
        return 0

    cmd = args.command

    try:
        return _dispatch(cmd, args)
    except DevbaseError as e:
        logger.error("%s", e)
        return 1


def _dispatch(cmd, args):
    """Dispatch command to handler."""
    # Resolve group aliases
    cmd = GROUP_ALIASES.get(cmd, cmd)

    # --- Shortcuts (top-level -> project subcommand) ---
    # ショートカットは非推奨ではないため、warning を出す cmd_container ではなく
    # 共有の cmd_project へ委譲する。
    if cmd in SHORTCUTS:
        args.subcommand = SHORTCUTS[cmd]
        from devbase.commands.container import cmd_project
        return cmd_project(args)

    # --- Project group (推奨) ---
    if cmd == 'project':
        from devbase.commands.container import cmd_project
        return cmd_project(args)

    # --- Container group (非推奨: project へ委譲 + warning) ---
    if cmd == 'container':
        from devbase.commands.container import cmd_container
        return cmd_container(args)

    # --- Commands not requiring DEVBASE_ROOT ---
    if cmd == 'shell-rc':
        from devbase.commands.shell_rc import cmd_shell_rc
        return cmd_shell_rc()

    # --- Commands requiring DEVBASE_ROOT ---
    devbase_root = _require_devbase_root()

    if cmd == 'init':
        from devbase.commands.init import cmd_init
        return cmd_init(devbase_root)

    if cmd == 'status':
        from devbase.commands.status import cmd_status
        return cmd_status(devbase_root)

    if cmd == 'env':
        from devbase.commands.env import cmd_env
        return cmd_env(devbase_root, args)

    if cmd == 'plugin':
        from devbase.commands.plugin import cmd_plugin
        return cmd_plugin(devbase_root, args)

    if cmd == 'snapshot':
        from devbase.commands.snapshot import cmd_snapshot
        return cmd_snapshot(devbase_root, args)

    logger.error("Unknown command: '%s'", cmd)
    return 1


if __name__ == '__main__':
    sys.exit(main())
