#!/usr/bin/env python3
"""devbase CLI entry point"""

import argparse
import os
import sys
from importlib import import_module
from pathlib import Path
from typing import Optional

from devbase.errors import DevbaseError
from devbase.log import get_logger, setup

try:
    from . import __version__
except ImportError:
    __version__ = "3.2.2"

logger = get_logger("devbase.cli")

# Shortcuts: top-level command -> project subcommand
# 委譲先は共有の cmd_project (PLAN06 で container は非推奨化)。
# NOTE: `build` はここに含めない。配布入口 bin/devbase が `build` の引数を見て
# 振り分けており (PLAN49)、Python 側で単一のショートカットとして広告すると実経路と
# 乖離するため:
#   - 既定 / --no-cache / --project-no-cache -> shell の cmd_build
#     (devbase-base 依存検出 + 2 段ビルド)
#   - <image> 指定 / --expires               -> Python の project build
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
    # `rebuild` は Python 実装 (cmd_rebuild = build --expires=7 相当の期限判定ビルド) で
    # 完結するため `build` と異なりトップレベルショートカットに含めてよい
    # (build は shell 実装に委譲するため除外している。上の NOTE 参照)。
    'rebuild': 'rebuild',
}

# Group aliases
GROUP_ALIASES = {
    'ct': 'container',
    'pl': 'plugin',
    'ss': 'snapshot',
}

# Subcommand map for prefix resolution: {(aliases...): [subcmds]}
SUBCMD_MAP = {
    ('project',):        ['up', 'down', 'ps', 'login', 'logs', 'scale', 'build', 'rebuild', 'list'],
    ('container', 'ct'): ['up', 'down', 'ps', 'login', 'logs', 'scale', 'build', 'rebuild'],
    ('env',):            ['init', 'sync', 'list', 'set', 'get', 'delete', 'edit', 'project', 'keygen',
                          'exec', 'encrypt', 'decrypt', 'rekey', 'doctor',
                          'export', 'import'],
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
        # `exec` 追加で `ex` が `exec` / `export` の両方にマッチするため、
        # 既存ショートカット (`devbase env ex` → `export`) を維持する。
        # `exec` は `exe` 以降で一意に決まる。
        'ex': 'export',
        # `decrypt` 追加で `d` / `de` が `delete` とも一致するため、既存
        # ショートカット (`devbase env d` → `delete`) を維持する。
        # `decrypt` は `dec` 以降で一意に決まる。
        'd': 'delete',
        'de': 'delete',
    },
}

# トップレベルコマンドの ambiguous prefix 後方互換 preference。
# `list` (PLAN06 Task 3) 追加で `l` が `login` / `list` の両方にマッチして
# ambiguous になったため、既存ショートカット (`devbase l` → `login`) を維持する。
# bin/devbase の resolve_command 内 preference と同期させること。
TOP_PREFIX_PREFERENCES = {
    'l': 'login',
}


def _require_devbase_root() -> Path:
    """Get DEVBASE_ROOT from environment, exiting if not set."""
    root = os.environ.get('DEVBASE_ROOT')
    if not root:
        logger.error("DEVBASE_ROOT environment variable not set")
        sys.exit(1)
    return Path(root)


def _add_name_arg(parser):
    """省略可能な `[name]` positional (プロジェクト名) を登録する。

    `project <sub> [name]` とトップレベルショートカットで同一定義を共有する。
    """
    parser.add_argument('name', nargs='?', default=None, help='Project name')
    return parser


def _add_open_args(parser):
    """`up` に エディタ自動オープン関連フラグを登録する (PLAN31_3)。

    三状態フラグ: ``--open`` (True) / ``--no-open`` (False) / 未指定 (None →
    env ``DEVBASE_OPEN_EDITOR`` に委ねる)。``project up`` / ``container up`` /
    トップレベル ``up`` で共有する。
    """
    parser.add_argument('--open', dest='open_editor', action='store_true',
                        default=None,
                        help='Open editor attached to the dev container after start '
                             '(overrides DEVBASE_OPEN_EDITOR)')
    parser.add_argument('--no-open', dest='open_editor', action='store_false',
                        help='Do not open editor (overrides DEVBASE_OPEN_EDITOR)')
    parser.add_argument('--open-index', dest='open_index', type=int, default=None,
                        metavar='N',
                        help='Container index to open (default: 1)')
    return parser


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
    # `--no-cache` と `--expires` は仕様上併用しない (無条件 no-cache か期限判定の
    # いずれか)。併用すると no-cache が優先され --expires が黙殺されるため、
    # add_mutually_exclusive_group で CLI レベルの排他制御を行い usage error で落とす。
    build_mode = p.add_mutually_exclusive_group()
    build_mode.add_argument('--no-cache', action='store_true',
                            help='Rebuild base and project images without cache')
    # `--expires` 単独 (値なし) は const=-1 を渡し、cmd_build 側で既定日数
    # (_image_max_age_days, 環境変数 DEVBASE_IMAGE_MAX_AGE_DAYS 既定 7) に解決する。
    build_mode.add_argument('--expires', nargs='?', type=int, const=-1, default=None,
                            metavar='DAYS',
                            help='Rebuild without cache only if the image is older than '
                                 'DAYS days (default 7). Base image is judged independently.')


def _add_container_parser(subparsers):
    """Container group parser"""
    ct_parser = subparsers.add_parser('container', aliases=['ct'],
                                      help='Manage containers')
    ct_sub = ct_parser.add_subparsers(dest='subcommand')

    _add_open_args(ct_sub.add_parser('up', help='Start containers'))
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

    ct_sub.add_parser('rebuild', help='Rebuild stale images (= build --expires=7)')


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

    _add_open_args(_add_name_arg(pj_sub.add_parser('up', help='Start containers')))
    _add_name_arg(pj_sub.add_parser('down', help='Stop and remove containers'))

    _add_login_subparser(pj_sub)

    pj_ps = pj_sub.add_parser('ps', help='Show container status')
    _add_name_arg(pj_ps)
    pj_ps.add_argument('--all', '-a', action='store_true', help='Show all containers')

    pj_logs = pj_sub.add_parser('logs', help='Show container logs')
    _add_name_arg(pj_logs)
    pj_logs.add_argument('--follow', '-f', action='store_true', help='Follow log output')
    pj_logs.add_argument('--tail', type=int, default=None, help='Number of lines')

    # NOTE: `[name]` optional + `new_scale` 必須 int の順。値が 1 個なら new_scale に、
    # 2 個なら (name, new_scale) に割り当てられ曖昧にならない (tests/cli 参照)。
    pj_scale = pj_sub.add_parser('scale', help='Scale containers online')
    _add_name_arg(pj_scale)
    pj_scale.add_argument('new_scale', type=int, help='New number of containers')

    _add_build_subparser(pj_sub)

    # `rebuild` は Python 実装 (`build --expires=7` 相当の期限判定ビルド)。up/down 同様に
    # 省略可能な `[name]` を取り、name 指定時は _dispatch_lifecycle が chdir してから
    # 実行する。wrapper の _PROJECT_NAME_SUBCOMMANDS / _NAME_RESOLVABLE_SHORTCUTS にも
    # 追加すること。
    _add_name_arg(pj_sub.add_parser(
        'rebuild', help='Rebuild stale images (= build --expires=7)'))

    # `list` は lifecycle ではなく一覧表示 (commands/project.py)。name positional は
    # 取らない (wrapper の _PROJECT_NAME_SUBCOMMANDS にも含めない)。
    _add_list_subparser(pj_sub)

    # `migrate-config` は旧 env 形式から project.yml への変換 (PLAN32)。lifecycle
    # ではないため wrapper の _PROJECT_NAME_SUBCOMMANDS には含めない。
    pj_migrate = pj_sub.add_parser(
        'migrate-config',
        help='Convert legacy env (GIT_USER/GIT_REPO/...) into project.yml')
    pj_migrate.add_argument('names', nargs='*', metavar='NAME',
                            help='Limit to the given projects (default: all)')
    pj_migrate.add_argument('--dry-run', action='store_true',
                            help='Show what would change without writing')
    # plugin リポジトリには devbase へリンクしていない projects/ もある
    # (`repos/<repo>/<plugin>/projects`)。一括移行のため直接指定できるようにする。
    pj_migrate.add_argument('--projects-dir', metavar='DIR', default=None,
                            help='Directory holding the projects '
                                 '(default: $DEVBASE_ROOT/projects)')


def _add_list_subparser(sub):
    """`list` サブコマンドを登録する (project list / top-level list 共通)。

    NAME / PLUGIN / STATUS の一覧表示。デフォルトで対話選択 → `project up` 起動。
    `--no-interactive` (`--plain`) で一覧表示のみ。非 TTY では自動的に一覧のみ。
    """
    p = sub.add_parser('list', help='List projects (NAME / PLUGIN / STATUS)')
    # 対話選択をデフォルト ON にする。`-i` / `--interactive` は後方互換のため
    # 引き続き受け付ける (既に default=True なので実質 no-op)。
    p.add_argument('--interactive', '-i', dest='interactive',
                   action='store_true', default=True,
                   help='Select a project interactively and start it (default)')
    p.add_argument('--no-interactive', '--plain', '-P', dest='interactive',
                   action='store_false',
                   help='Just print the table without interactive selection')


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

    # delete / edit の --project は set と対。設定が暗号化されると利用者がエディタで
    # 直接開いて消せなくなるため、プロジェクト設定を CLI から掃除する経路を残す。
    env_delete = env_sub.add_parser('delete', help='Delete a variable')
    env_delete.add_argument('key', help='Variable name')
    env_delete.add_argument('--project', '-p', action='store_true',
                            help='Delete from project .env')

    env_edit = env_sub.add_parser('edit', help='Open .env in editor')
    env_edit.add_argument('--project', '-p', action='store_true',
                          help='Edit project .env')

    env_sub.add_parser('project', help='Setup project-specific variables')

    # 生成先を選ぶオプションは置かない。復号側は $DEVBASE_AGE_KEY_FILE か既定パスしか
    # 探索しないため、任意のパスへ生成できると「その鍵で保存した機密を復号できない」
    # 状態を作れてしまう。場所を変えたい場合は環境変数を設定してから実行してもらう。
    # 説明中の環境変数名は devbase.env.agekeys.KEY_FILE_ENV と対。agekeys は pyrage を
    # 引き込むため、parser 構築時に import せず文字列で持つ (暗号機能を使わない
    # コマンドまで pyrage のロード失敗に巻き込まないため)。
    env_exec = env_sub.add_parser(
        'exec',
        help='Run a command with the decrypted secrets in its environment')
    env_exec.add_argument('argv', nargs=argparse.REMAINDER,
                          metavar='-- CMD [ARGS...]',
                          help='Command to run (prefix with -- to pass flags)')

    for name, action in (('encrypt', 'Move plaintext settings into the encrypted store'),
                         ('decrypt', 'Move encrypted settings back to plaintext')):
        sub = env_sub.add_parser(name, help=action)
        sub.add_argument('--project', action='append', default=[],
                         metavar='NAME', dest='projects',
                         help='Limit to the specified project (repeatable)')
        sub.add_argument('--dry-run', action='store_true',
                         help='Show what would change without writing')
        sub.add_argument('--yes', '-y', action='store_true', dest='assume_yes',
                         help='Skip the confirmation prompt')

    env_rekey = env_sub.add_parser(
        'rekey', help='Change who can decrypt the secrets and re-encrypt them')
    env_rekey.add_argument('--add-recipient', action='append', default=[],
                           metavar='KEY', dest='add_recipients',
                           help=("Public key to add (repeatable). Formats: "
                                 "'age1...', 'ssh-ed25519 ...', '@PATH'"))
    env_rekey.add_argument('--remove-recipient', action='append', default=[],
                           metavar='KEY', dest='remove_recipients',
                           help='Public key to remove (repeatable)')
    env_rekey.add_argument('--dry-run', action='store_true',
                           help='Show what would change without writing')
    env_rekey.add_argument('--yes', '-y', action='store_true', dest='assume_yes',
                           help='Skip the confirmation prompt')

    env_sub.add_parser(
        'doctor',
        help='Check for leftover plaintext secrets and ignore-rule gaps')

    env_keygen = env_sub.add_parser(
        'keygen',
        help='Generate the devbase age key used by the secret store '
             '(written to $DEVBASE_AGE_KEY_FILE or ~/.config/devbase/age/keys.txt; '
             'set $DEVBASE_AGE_KEY_FILE before running to use another location)')
    env_keygen.add_argument('--force', action='store_true',
                            help='Overwrite an existing key (previously encrypted '
                                 'secrets may become unrecoverable)')
    env_keygen.add_argument('--yes', '-y', action='store_true', dest='assume_yes',
                            help='Skip the confirmation prompt for --force')

    _add_env_export_parser(env_sub)
    _add_env_import_parser(env_sub)


def _add_env_export_parser(env_sub):
    """`env export` サブコマンドを登録する。"""
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


def _add_env_import_parser(env_sub):
    """`env import` サブコマンドを登録する。"""
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
    注記参照): bin/devbase が build の引数を見て shell (cmd_build) と Python
    (project build) へ振り分けるため、Python 側でトップレベル build を単一の
    ショートカットとして広告すると実経路と乖離する。
    """
    _add_login_subparser(subparsers)

    ps_sc = subparsers.add_parser('ps', help='Show container status')
    _add_name_arg(ps_sc)
    ps_sc.add_argument('--all', '-a', action='store_true', help='Show all containers')

    _add_open_args(_add_name_arg(subparsers.add_parser('up', help='Start containers')))
    _add_name_arg(subparsers.add_parser('down', help='Stop and remove containers'))

    # `[name]` optional + `new_scale` 必須 int の順 (project scale と同じ規則)。
    scale_sc = subparsers.add_parser('scale', help='Scale containers online')
    _add_name_arg(scale_sc)
    scale_sc.add_argument('new_scale', type=int, help='New number of containers')

    # `rebuild` は project rebuild のトップレベルシノニム (Python 実装のため build と
    # 異なりショートカット可)。up/down と同じく `[name]` を受け付ける。
    _add_name_arg(subparsers.add_parser(
        'rebuild', help='Rebuild stale images (= build --expires=7)'))

    # `list` は `project list` のトップレベルシノニム。lifecycle ではなく一覧表示
    # のため SHORTCUTS (project lifecycle へ写像) ではなく _dispatch で個別に
    # cmd_project_list へ振り分ける。
    _add_list_subparser(subparsers)


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
            "  rebuild       project rebuild (= build --expires=7)\n"
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
    commands = ['init', 'status', 'project', 'container', 'ct', 'env', 'plugin', 'pl',
                'snapshot', 'ss', 'up', 'down', 'login', 'ps', 'scale', 'rebuild', 'list', 'help']
    repo_subcmds = ['add', 'remove', 'list', 'refresh']

    if len(sys.argv) >= 2 and not sys.argv[1].startswith('-'):
        sys.argv[1] = _resolve_prefix(sys.argv[1], commands, TOP_PREFIX_PREFERENCES)

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

    _load_secret_env(cmd, getattr(args, 'subcommand', None))

    try:
        return _dispatch(cmd, args)
    except DevbaseError as e:
        logger.error("%s", e)
        return 1


# 機密の注入を行わないコマンド。鍵の生成や暗号化・復号は「まだ鍵が無い」
# 「復号できない」状態でこそ実行されるため、注入を試みると本来の操作の前に
# 落ちてしまう。
#
# `env` のように注入が要るサブコマンド (`env list` など) と要らないサブコマンド
# が同居するグループがあるため、``(コマンド, サブコマンド)`` の組で持つ。
# サブコマンドが ``None`` の項目は「そのコマンド全体をスキップする」意味。
_NO_SECRET_INJECTION = frozenset({
    ('init', None),
    ('env', 'keygen'),
    ('env', 'encrypt'),
    ('env', 'decrypt'),
})


def _skip_secret_injection(cmd: str, subcommand: Optional[str]) -> bool:
    return ((cmd, None) in _NO_SECRET_INJECTION
            or (cmd, subcommand) in _NO_SECRET_INJECTION)


def _load_secret_env(cmd: str, subcommand: Optional[str] = None) -> None:
    """機密を復号して自プロセスの環境変数へ載せる。

    起動ラッパーは共通の機密ファイルを読み込まなくなった (plan35 §4.4)。
    従来はラッパーが全コマンドに対して値を環境変数として渡していたため、
    同じ範囲を Python 側で肩代わりする。ここで載せておけば、エディタ起動や
    Docker Compose の変数展開など、値を必要とする処理が従来どおり動く。

    復号に失敗しても停止しない。鍵が未整備でも `env keygen` や `--help` は
    使えるべきで、値が本当に要る操作 (コンテナ起動など) は各コマンド側で
    改めて必須として読み込む。
    """
    if _skip_secret_injection(cmd, subcommand):
        return
    root = os.environ.get('DEVBASE_ROOT')
    if not root:
        return
    try:
        from devbase.env import runtime as _runtime

        _runtime.inject(Path(root), _runtime.current_project_name(Path(root)))
    except DevbaseError as e:
        logger.debug("機密を読み込めませんでした: %s", e)
    except Exception as e:  # noqa: BLE001 - 通常コマンドを暗号化都合で倒さない
        logger.debug("機密の読み込みで想定外のエラー: %s", e)


# DEVBASE_ROOT 必須コマンドの定義: cmd -> (module, function, args を渡すか)。
# 起動コストを抑えるため import は dispatch 時に遅延させる (従来の関数内 import と同等)。
_ROOT_COMMANDS = {
    'init':     ('devbase.commands.init',     'cmd_init',     False),
    'status':   ('devbase.commands.status',   'cmd_status',   False),
    'env':      ('devbase.commands.env',      'cmd_env',      True),
    'plugin':   ('devbase.commands.plugin',   'cmd_plugin',   True),
    'snapshot': ('devbase.commands.snapshot', 'cmd_snapshot', True),
}


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
        # `project list` は lifecycle ではなく一覧表示 (DEVBASE_ROOT 必須)。
        if getattr(args, 'subcommand', None) == 'list':
            devbase_root = _require_devbase_root()
            from devbase.commands.project import cmd_project_list
            return cmd_project_list(devbase_root, args)
        # `project migrate-config` も lifecycle ではなく projects/ 全体の変換。
        if getattr(args, 'subcommand', None) == 'migrate-config':
            devbase_root = _require_devbase_root()
            from devbase.commands.project import cmd_project_migrate_config
            return cmd_project_migrate_config(devbase_root, args)
        from devbase.commands.container import cmd_project
        return cmd_project(args)

    # --- Top-level `list` synonym for `project list` ---
    if cmd == 'list':
        devbase_root = _require_devbase_root()
        from devbase.commands.project import cmd_project_list
        return cmd_project_list(devbase_root, args)

    # --- Container group (非推奨: project へ委譲 + warning) ---
    if cmd == 'container':
        from devbase.commands.container import cmd_container
        return cmd_container(args)

    # --- Commands requiring DEVBASE_ROOT ---
    spec = _ROOT_COMMANDS.get(cmd)
    if spec is None:
        logger.error("Unknown command: '%s'", cmd)
        return 1
    module_name, func_name, takes_args = spec
    func = getattr(import_module(module_name), func_name)
    devbase_root = _require_devbase_root()
    return func(devbase_root, args) if takes_args else func(devbase_root)


if __name__ == '__main__':
    sys.exit(main())
