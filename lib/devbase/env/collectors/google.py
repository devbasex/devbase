"""Google Cloud認証情報コレクター（複数プロファイル対応）"""

import base64
import json
import re
from pathlib import Path
from typing import Optional

from devbase.log import get_logger
from devbase.env import keys
from devbase.env.store import EnvFile, safe_input, collect_key
from devbase.env.collector import Collector

logger = get_logger(__name__)

# GCPクレデンシャルのデフォルトディレクトリ
GCP_CREDENTIALS_DIR = Path.home() / 'gcp-credentials'
# 従来の単一ファイル（後方互換）
LEGACY_CREDENTIALS_FILE = Path.home() / 'google_credential.json'


def _encode_credentials_file(file_path: Path) -> str:
    return base64.b64encode(file_path.read_bytes()).decode('ascii')


def _extract_project_id(file_path: Path) -> Optional[str]:
    try:
        data = json.loads(file_path.read_text(encoding='utf-8'))
        return data.get('project_id')
    except Exception as e:
        logger.warning("Google credentials JSONのパースに失敗: %s", e)
        return None


def _safe_profile_name(name: str) -> str:
    """プロファイル名を環境変数として安全な文字種に正規化"""
    safe_name = re.sub(r'[^A-Za-z0-9_]', '_', name)
    if safe_name != name:
        logger.warning("プロファイル名 '%s' を '%s' に正規化しました", name, safe_name)
    return safe_name


def _discover_credential_files() -> dict:
    """利用可能なcredentialファイルを検出する"""
    if GCP_CREDENTIALS_DIR.is_dir():
        profiles = {}
        for f in sorted(GCP_CREDENTIALS_DIR.iterdir()):
            if f.suffix != '.json' or not f.is_file():
                continue
            safe_name = _safe_profile_name(f.stem)
            if safe_name in profiles:
                logger.warning(
                    "プロファイル名 '%s' が衝突しています: '%s' と '%s' (後者をスキップ)",
                    safe_name, profiles[safe_name]['file'], str(f),
                )
                continue
            profiles[safe_name] = {'file': str(f), 'project_id': _extract_project_id(f)}
        if profiles:
            return profiles

    if LEGACY_CREDENTIALS_FILE.exists():
        return {'default': {
            'file': str(LEGACY_CREDENTIALS_FILE),
            'project_id': _extract_project_id(LEGACY_CREDENTIALS_FILE),
        }}

    return {}


def collect_google_credentials(env_file: EnvFile) -> None:
    """Google Cloud認証情報を対話的に収集する（複数プロファイル対応）"""
    print("\n=== Google Cloud認証情報 ===")

    profiles = _discover_credential_files()

    if not profiles:
        existing = env_file.get(keys.gcp_credentials_key("default"))
        if existing:
            logger.info("%s: 設定済み", keys.gcp_credentials_key("default"))
        else:
            creds_path_str = safe_input("credentialファイルのパス (空でスキップ): ")
            if creds_path_str:
                creds_path = Path(creds_path_str).expanduser()
                if creds_path.exists():
                    _register_profile(env_file, 'default', creds_path)
                else:
                    logger.error("ファイルが見つかりません: %s", creds_path)
        _collect_common_settings(env_file)
        return

    print(f"\n検出されたcredential ({len(profiles)}件):")
    print('\n'.join(f"  - {name} (project: {info.get('project_id', 'N/A')})"
                    for name, info in profiles.items()))

    for name, info in profiles.items():
        _register_profile(env_file, name, Path(info['file']))

    profile_names = list(profiles.keys())
    default_active = 'default' if 'default' in profile_names else profile_names[0]
    active = safe_input(f"\nアクティブプロファイル (デフォルト: {default_active}): ", default_active)
    if active not in profile_names:
        logger.warning("'%s' は存在しません。'%s' を使用します", active, default_active)
        active = default_active
    env_file.set(keys.GCP_ACTIVE_PROFILE, active)
    logger.info("%s: %s", keys.GCP_ACTIVE_PROFILE, active)

    active_info = profiles.get(active, {})
    project_id = active_info.get('project_id')
    if project_id:
        env_file.set(keys.GOOGLE_CLOUD_PROJECT, project_id)
        env_file.set(keys.BIGQUERY_PROJECT, project_id)
        logger.info("%s: %s", keys.GOOGLE_CLOUD_PROJECT, project_id)

    _collect_common_settings(env_file)


def _register_profile(env_file: EnvFile, name: str, file_path: Path) -> None:
    """プロファイルをエンコードしてenv_fileに登録"""
    try:
        encoded = _encode_credentials_file(file_path)
        env_key = keys.gcp_credentials_key(name)
        env_file.set(env_key, encoded)
        logger.info("%s: エンコード完了 (%d 文字)", env_key, len(encoded))
    except Exception as e:
        logger.error("credentialファイルの処理に失敗: %s", e)


def _collect_common_settings(env_file: EnvFile) -> None:
    """GCP共通設定を収集"""
    collect_key(env_file, keys.GOOGLE_CLOUD_LOCATION, auto_value="global", mask_after=0,
                prompt=f"{keys.GOOGLE_CLOUD_LOCATION} (デフォルト: global): ")

    collect_key(env_file, keys.BIGQUERY_DATASETS, mask_after=0,
                prompt=f"{keys.BIGQUERY_DATASETS} (カンマ区切り、空でスキップ): ")

    collect_key(env_file, keys.BIGQUERY_LOCATION, auto_value="asia-northeast1", mask_after=0,
                prompt=f"{keys.BIGQUERY_LOCATION} (デフォルト: asia-northeast1): ")

    # コンテナ内パス（devbaseコンテナイメージの仕様に依存）
    env_file.set(keys.BIGQUERY_KEY_FILE, "/home/ubuntu/.config/gcloud/credentials.json")
    env_file.set(keys.GOOGLE_APPLICATION_CREDENTIALS, "/home/ubuntu/.config/gcloud/credentials.json")


COLLECTOR = Collector(
    name="google",
    display_name="GCP認証",
    collect_fn=collect_google_credentials,
    source_files=["~/gcp-credentials/"],
    source_type="named_profiles",
)
