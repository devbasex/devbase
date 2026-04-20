"""devbase env コマンド実装"""

import os
import subprocess
from pathlib import Path

import yaml

from devbase.log import get_logger
from devbase.env import keys
from devbase.env.store import EnvFile, safe_input
from devbase.env.sources import SourcesManager, file_hash, dir_hash
from devbase.env.collector import CollectorRegistry

logger = get_logger(__name__)


def cmd_env(devbase_root: Path, args) -> int:
    """envサブコマンドの振り分け"""
    subcmd = getattr(args, 'subcommand', None)

    handlers = {
        'init':    lambda: cmd_env_init(devbase_root, reset=getattr(args, 'reset', False)),
        'sync':    lambda: cmd_env_sync(devbase_root),
        'list':    lambda: cmd_env_list(devbase_root,
                                        global_only=getattr(args, 'global_only', False),
                                        project_only=getattr(args, 'project_only', False),
                                        reveal=getattr(args, 'reveal', False),
                                        keys_only=getattr(args, 'keys_only', False)),
        'set':     lambda: cmd_env_set(devbase_root, getattr(args, 'assignment', ''),
                                       project=getattr(args, 'project', False)),
        'get':     lambda: cmd_env_get(devbase_root, getattr(args, 'key', '')),
        'delete':  lambda: cmd_env_delete(devbase_root, getattr(args, 'key', '')),
        'edit':    lambda: cmd_env_edit(devbase_root),
        'project': lambda: cmd_env_project(devbase_root),
    }

    handler = handlers.get(subcmd)
    if handler:
        return handler()

    logger.error("サブコマンドを指定してください: %s", ', '.join(handlers))
    return 1


def cmd_env_init(devbase_root: Path, reset: bool = False) -> int:
    """全体環境の初期セットアップ（対話式）"""
    env_path = devbase_root / '.env'
    env_file = EnvFile(env_path)
    env_file.load()

    if env_file.count() > 0 and not reset:
        print(f"環境は既にセットアップ済みです ({env_file.count()}変数)")
        print("  更新: devbase env sync")
        print("  やり直し: devbase env init --reset")
        return 0

    if reset and env_path.exists():
        env_file.backup()
        logger.info("既存の設定をバックアップしました")
        env_file = EnvFile(env_path)
        env_file.load()
        for key in list(env_file.get_all().keys()):
            env_file.delete(key)

    print("\n" + "=" * 42)
    print("devbase 環境セットアップ")
    print("=" * 42)

    registry = CollectorRegistry()
    registry.discover()

    for i, collector in enumerate(registry.collectors, 1):
        print(f"\n[{i}/{len(registry.collectors)}] {collector.display_name}")
        collector.collect_fn(env_file)

    env_file.save()

    _update_source_metadata(devbase_root, env_file)

    logger.info("セットアップ完了: %s (%d変数)", env_path, env_file.count())
    return 0


def cmd_env_sync(devbase_root: Path) -> int:
    """ソースファイルから認証情報を再同期する"""
    env_path = devbase_root / '.env'
    env_file = EnvFile(env_path)
    env_file.load()

    sources = SourcesManager(devbase_root)
    sources.load()

    updated = 0

    # AWS
    def _encode_aws():
        from devbase.env.collectors.aws import _encode_aws_config_files
        return _encode_aws_config_files()

    updated += _sync_source(sources, env_file, 'aws', 'AWS認証', _encode_aws)

    # Git
    def _encode_git():
        import base64
        cred_path = Path.home() / '.git-credentials'
        if cred_path.exists():
            content = cred_path.read_text(encoding='utf-8')
            return base64.b64encode(content.encode('utf-8')).decode('ascii')
        return None

    updated += _sync_source(sources, env_file, 'git_credentials', 'Git認証', _encode_git)

    # GCP（プロファイル管理があるため個別処理）
    updated += _sync_gcp(sources, env_file)

    if updated > 0:
        env_file.save()
        _update_source_metadata(devbase_root, env_file)
        logger.info("同期完了 (%d件更新)", updated)
    else:
        if not any(sources.get_source(n) for n in ('aws', 'git_credentials', 'gcp')):
            logger.info("ソース情報がありません。先に devbase env init を実行してください")
        else:
            logger.info("同期完了 (変更なし)")

    return 0


def _sync_source(sources, env_file, name, label, encode_fn):
    """AWS/Gitなどの単一ソース同期の共通処理。更新件数(0 or 1)を返す。"""
    source = sources.get_source(name)
    if not source:
        return 0

    changed = sources.check_changed(name)
    if changed:
        encoded = encode_fn()
        if encoded:
            env_file.set(source['env_key'], encoded)
            logger.info("%s: 更新しました", label)
            return 1
        else:
            logger.warning("%s: エンコードに失敗", label)
    elif changed is False:
        logger.info("%s: 変更なし", label)
    else:
        logger.info("%s: ソース未登録", label)
    return 0


def _sync_gcp(sources, env_file):
    """GCPプロファイルの同期処理"""
    gcp_source = sources.get_source('gcp')
    if not gcp_source:
        return 0

    updated = 0
    gcp_changes = sources.check_gcp_changed()
    for profile_name, changed in gcp_changes.items():
        if changed:
            profile_info = gcp_source.get('profiles', {}).get(profile_name, {})
            file_str = profile_info.get('file', '')
            if not file_str:
                continue
            file_path = Path(file_str).expanduser()
            if file_path.exists():
                import base64
                encoded = base64.b64encode(file_path.read_bytes()).decode('ascii')
                env_file.set(keys.gcp_credentials_key(profile_name), encoded)
                updated += 1
                logger.info("GCP認証 (%s): 更新しました", profile_name)
        else:
            logger.info("GCP認証 (%s): 変更なし", profile_name)

    return updated


def _print_env_vars(vars_dict, keys_only, reveal):
    """変数一覧の表示（重複排除用共通関数）"""
    if not vars_dict:
        print("  (変数なし)")
        return
    fmt = (lambda k: f"  {k}") if keys_only else (lambda k: f"  {k:<35} {_format_value(k, vars_dict[k], reveal)}")
    print('\n'.join(fmt(k) for k in sorted(vars_dict)))


def cmd_env_list(devbase_root: Path, global_only: bool = False,
                 project_only: bool = False, reveal: bool = False,
                 keys_only: bool = False) -> int:
    """設定済み変数の一覧表示"""
    if not project_only:
        env_path = devbase_root / '.env'
        env_file = EnvFile(env_path)
        env_file.load()
        all_vars = env_file.get_all()

        print(f"\n=== グローバル ({env_path}) ===")
        _print_env_vars(all_vars, keys_only, reveal)
        print(f"\nグローバル: {len(all_vars)}変数")

    if not global_only:
        current_dir = Path(os.environ.get('PWD', os.getcwd()))
        projects_dir = devbase_root / 'projects'

        try:
            current_dir.relative_to(projects_dir)
        except ValueError:
            pass
        else:
            project_env_path = current_dir / '.env'
            if project_env_path.exists():
                proj_env = EnvFile(project_env_path)
                proj_env.load()
                proj_vars = proj_env.get_all()

                print(f"\n=== プロジェクト: {current_dir.name} ({project_env_path}) ===")
                _print_env_vars(proj_vars, keys_only, reveal)
                print(f"\nプロジェクト: {len(proj_vars)}変数")

    return 0


def _format_value(key: str, value: str, reveal: bool) -> str:
    """表示用に値をフォーマットする"""
    sensitive_patterns = ('KEY', 'SECRET', 'TOKEN', 'PASSWORD', 'CREDENTIALS', 'BASE64')
    is_sensitive = any(p in key.upper() for p in sensitive_patterns)

    if is_sensitive and not reveal:
        return f"██████ ({len(value)}文字)" if len(value) > 100 else "██████"
    return f"{value[:57]}..." if len(value) > 60 else value


def cmd_env_set(devbase_root: Path, assignment: str, project: bool = False) -> int:
    """変数を設定する"""
    if '=' not in assignment:
        logger.error("形式: devbase env set KEY=VALUE")
        return 1

    key, _, value = assignment.partition('=')
    key = key.strip()
    value = value.strip()

    if not key:
        logger.error("キー名が空です")
        return 1

    if project:
        env_path = Path(os.environ.get('PWD', os.getcwd())) / '.env'
    else:
        env_path = devbase_root / '.env'

    env_file = EnvFile(env_path)
    env_file.load()
    env_file.set(key, value)
    env_file.save()

    logger.info("%s を設定しました", key)
    return 0


def cmd_env_get(devbase_root: Path, key: str) -> int:
    """変数の値を取得する"""
    env_path = devbase_root / '.env'
    env_file = EnvFile(env_path)
    env_file.load()

    value = env_file.get(key)
    if value is not None:
        print(value)
        return 0

    current_dir = Path(os.environ.get('PWD', os.getcwd()))
    project_env_path = current_dir / '.env'
    if project_env_path.exists() and project_env_path != env_path:
        proj_env = EnvFile(project_env_path)
        proj_env.load()
        value = proj_env.get(key)
        if value is not None:
            print(value)
            return 0

    logger.error("変数 '%s' は設定されていません", key)
    return 1


def cmd_env_delete(devbase_root: Path, key: str) -> int:
    """変数を削除する"""
    env_path = devbase_root / '.env'
    env_file = EnvFile(env_path)
    env_file.load()

    if env_file.delete(key):
        env_file.save()
        logger.info("%s を削除しました", key)
        return 0

    logger.error("変数 '%s' は存在しません", key)
    return 1


def cmd_env_edit(devbase_root: Path) -> int:
    """エディタで.envを開く"""
    env_path = devbase_root / '.env'
    editor = os.environ.get('EDITOR', 'vi')
    return subprocess.call([editor, str(env_path)])


def cmd_env_project(devbase_root: Path) -> int:
    """プロジェクト固有変数の設定（対話式）"""
    current_dir = Path(os.environ.get('PWD', os.getcwd()))
    projects_dir = devbase_root / 'projects'

    try:
        current_dir.relative_to(projects_dir)
    except ValueError:
        logger.error("projects/ 配下で実行してください")
        return 1

    project_name = current_dir.name

    env_yml_path = current_dir / 'env.yml'
    env_path = current_dir / '.env'
    env_file = EnvFile(env_path)
    env_file.load()

    print(f"\n=== {project_name} プロジェクト環境変数 ===")

    if env_yml_path.exists():
        with open(env_yml_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}

        variables = config.get('variables', [])
        for var in variables:
            name = var.get('name', '')
            prompt = var.get('prompt', name)
            default = var.get('default', '')
            required = var.get('required', False)
            generate = var.get('generate', '')

            existing = env_file.get(name)
            if existing:
                print(f"{name}: 設定済み")
                continue

            if generate:
                import secrets
                length = 64
                if ':' in generate:
                    _, length_str = generate.split(':', 1)
                    length = int(length_str)
                value = secrets.token_hex(length // 2)
                env_file.set(name, value)
                print(f"{name}: (自動生成)")
            else:
                suffix = f" (デフォルト: {default})" if default else ""
                suffix += " (必須)" if required else " (空でスキップ)"
                value = safe_input(f"{prompt}{suffix}: ", default)
                if value:
                    env_file.set(name, value)
                elif required:
                    logger.error("必須変数 '%s' が設定されていません", name)
                    return 1
    else:
        print("env.yml が見つかりません。手動で変数を追加してください。")
        print("(Ctrl+Dで終了)")
        try:
            while True:
                line = safe_input("\nKEY=VALUE (空で終了): ")
                if not line:
                    break
                if '=' in line:
                    key, _, value = line.partition('=')
                    env_file.set(key.strip(), value.strip())
                else:
                    print("形式: KEY=VALUE")
        except EOFError:
            pass

    env_file.save()
    logger.info("保存完了: %s (%d変数)", env_path, env_file.count())
    return 0


def _update_source_metadata(devbase_root: Path, env_file: EnvFile) -> None:
    """ソースメタデータを更新する"""
    sources = SourcesManager(devbase_root)
    sources.load()

    # AWS
    if env_file.get(keys.AWS_CONFIG_BASE64):
        aws_dir = Path.home() / '.aws'
        files = ["~/.aws/config", "~/.aws/credentials"]
        filenames = ['config', 'credentials']
        h = dir_hash(aws_dir, filenames)
        if h:
            sources.set_source('aws', 'tar_base64', files,
                              keys.AWS_CONFIG_BASE64, h)

    # Git
    if env_file.get(keys.GIT_CREDENTIALS_BASE64):
        cred_path = Path.home() / '.git-credentials'
        h = file_hash(cred_path)
        if h:
            sources.set_source('git_credentials', 'file_base64',
                              ["~/.git-credentials"],
                              keys.GIT_CREDENTIALS_BASE64, h)

    # GCP (プロファイルごと)
    from devbase.env.collectors.google import GCP_CREDENTIALS_DIR, LEGACY_CREDENTIALS_FILE
    all_vars = env_file.get_all()
    prefix = keys.GCP_CREDENTIALS_BASE64_PREFIX

    # 正規化名→実ファイルの逆引きマップを構築
    _gcp_file_map = {}
    if GCP_CREDENTIALS_DIR.is_dir():
        from devbase.env.collectors.google import _safe_profile_name
        _gcp_file_map = {
            _safe_profile_name(f.stem): f
            for f in GCP_CREDENTIALS_DIR.iterdir()
            if f.suffix == '.json' and f.is_file()
        }

    def _resolve_gcp_path(profile_name: str):
        mapped = _gcp_file_map.get(profile_name)
        if mapped and mapped.exists():
            return mapped
        if profile_name == 'default' and LEGACY_CREDENTIALS_FILE.exists():
            return LEGACY_CREDENTIALS_FILE
        return None

    gcp_profiles = {
        name: {'file': str(path), 'hash': file_hash(path)}
        for key in all_vars if key.startswith(prefix)
        for name in [key[len(prefix):]]
        for path in [_resolve_gcp_path(name)]
        if path and path.exists()
    }

    if gcp_profiles:
        active = env_file.get(keys.GCP_ACTIVE_PROFILE, "default")
        sources.set_gcp_source(gcp_profiles, active)

    sources.save()
