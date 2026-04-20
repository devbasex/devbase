"""AWS認証情報コレクター"""

import base64
import configparser
import io
import tarfile
from pathlib import Path
from typing import Optional, List, Tuple

from devbase.log import get_logger
from devbase.env import keys
from devbase.env.store import EnvFile, safe_input
from devbase.env.collector import Collector

logger = get_logger(__name__)


class AWSConfigParser:
    """AWS config/credentials ファイルのパーサー"""

    def __init__(self):
        self.home = Path.home()
        self.config_path = self.home / '.aws' / 'config'
        self.credentials_path = self.home / '.aws' / 'credentials'

    def get_profiles(self) -> List[str]:
        if not self.config_path.exists():
            return []

        config = configparser.ConfigParser()
        try:
            config.read(self.config_path)
            profiles = ['default'] if 'default' in config else []
            profiles += [section[8:] if section.startswith('profile ') else section
                         for section in config.sections() if section != 'default']
            return profiles
        except Exception as e:
            logger.warning("AWS configのパースに失敗: %s", e)
            return []

    def get_profile_region(self, profile: str) -> Optional[str]:
        if not self.config_path.exists():
            return None
        config = configparser.ConfigParser()
        try:
            config.read(self.config_path)
            if profile == 'default':
                section = 'default'
            else:
                section = f'profile {profile}'
                if section not in config:
                    section = profile
            if section in config and 'region' in config[section]:
                return config[section]['region']
        except Exception as e:
            logger.warning("AWS configのパースに失敗: %s", e)
        return None

    def get_default_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        if not self.credentials_path.exists():
            return None, None
        config = configparser.ConfigParser()
        try:
            config.read(self.credentials_path)
            if 'default' in config:
                return (config['default'].get('aws_access_key_id'),
                        config['default'].get('aws_secret_access_key'))
        except Exception as e:
            logger.warning("AWS credentialsのパースに失敗: %s", e)
        return None, None


def _encode_aws_config_files() -> Optional[str]:
    """~/.aws/config と credentials を tar.gz → base64 にエンコード"""
    aws_dir = Path.home() / '.aws'
    existing = {name: aws_dir / name
                for name in ('config', 'credentials')
                if (aws_dir / name).exists()}

    if not existing:
        return None

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for name, path in existing.items():
            tar.add(path, arcname=name)

    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode('ascii')


def _get_aws_config_info() -> List[str]:
    aws_dir = Path.home() / '.aws'
    return [f"  {name} ({(aws_dir / name).stat().st_size} bytes)"
            for name in ('config', 'credentials')
            if (aws_dir / name).exists()]


def collect_aws_credentials(env_file: EnvFile) -> None:
    """AWS認証情報を対話的に収集する"""
    print("\n=== AWS認証情報 ===")

    existing_config_base64 = env_file.get(keys.AWS_CONFIG_BASE64)
    existing_profile = env_file.get(keys.AWS_PROFILE)
    existing_access_key = env_file.get(keys.AWS_ACCESS_KEY_ID)
    existing_region = env_file.get(keys.AWS_DEFAULT_REGION)

    current_method = None
    if existing_config_base64:
        current_method = "config_base64"
        print("現在の認証方法: AWS Config Files (AWS_CONFIG_BASE64)")
        if existing_profile:
            print(f"AWS_PROFILE: 設定済み ({existing_profile})")
    elif existing_profile:
        current_method = "profile"
        print(f"現在の認証方法: SSO Profile (AWS_PROFILE={existing_profile})")
    elif existing_access_key:
        current_method = "access_key"
        print(f"現在の認証方法: Access Key (AWS_ACCESS_KEY_ID={existing_access_key[:10]}...)")

    if current_method:
        if existing_region:
            print(f"AWS_DEFAULT_REGION: 設定済み ({existing_region})")
        change = safe_input("\n認証方法を変更しますか? [y/N]: ", "n")
        if change.lower() != 'y':
            return

        for key in keys.AWS_ALL_KEYS:
            env_file.delete(key)
        logger.info("既存のAWS認証情報をクリアしました")

    print("\nAWS認証方法を選択してください:")
    print("  1) AWS Config Files (~/.aws全体をbase64化、全profile対応、推奨)")
    print("  2) AWS SSO Profile (AWS_PROFILE + オプションでAWS_SSO_URL)")
    print("  3) Access Key (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)")
    print("  4) スキップ")

    auth_method = safe_input("選択 [1/2/3/4] (デフォルト: 1): ", "1")

    auth_handlers = {
        "1": lambda: _collect_config_base64(env_file),
        "2": lambda: _collect_sso_profile(env_file),
        "3": lambda: _collect_access_keys(env_file),
        "4": lambda: logger.info("AWS認証設定をスキップしました"),
    }

    handler = auth_handlers.get(auth_method, auth_handlers["1"])
    handler()


def _collect_config_base64(env_file: EnvFile) -> None:
    aws_dir = Path.home() / '.aws'
    if not (aws_dir / 'config').exists() and not (aws_dir / 'credentials').exists():
        print("\nエラー: ~/.aws/config または ~/.aws/credentials が存在しません")
        print("AWS CLIで `aws configure` または `aws configure sso` を実行してください")
        return

    info = _get_aws_config_info()
    if info:
        print("\n検出されたファイル:")
        print('\n'.join(info))

    parser = AWSConfigParser()
    profiles = parser.get_profiles()
    if profiles:
        print("\n利用可能なprofile:")
        print('\n'.join(f"  - {p}" for p in profiles))

    encoded = _encode_aws_config_files()
    if encoded:
        env_file.set(keys.AWS_CONFIG_BASE64, encoded)
        logger.info("%s: エンコード完了 (%d 文字)", keys.AWS_CONFIG_BASE64, len(encoded))

        if profiles:
            default_profile = "default" if "default" in profiles else profiles[0]
            profile_name = safe_input(f"\n使用するAWS_PROFILE (デフォルト: {default_profile}): ", default_profile)
            env_file.set(keys.AWS_PROFILE, profile_name)
            logger.info("%s: %s に設定", keys.AWS_PROFILE, profile_name)

            region = parser.get_profile_region(profile_name)
            if region:
                env_file.set(keys.AWS_DEFAULT_REGION, region)
                logger.info("%s: 自動取得完了 (%s)", keys.AWS_DEFAULT_REGION, region)
            else:
                region = safe_input(f"{keys.AWS_DEFAULT_REGION} (デフォルト: ap-northeast-1): ", "ap-northeast-1")
                env_file.set(keys.AWS_DEFAULT_REGION, region)
    else:
        logger.error("AWS設定ファイルのエンコードに失敗しました")


def _collect_sso_profile(env_file: EnvFile) -> None:
    parser = AWSConfigParser()
    profiles = parser.get_profiles()
    if profiles:
        print("\n利用可能なprofile:")
        print('\n'.join(f"  - {p}" for p in profiles))

    profile = safe_input(f"{keys.AWS_PROFILE} (空でスキップ): ")
    if not profile:
        logger.info("AWS SSO設定をスキップしました")
        return

    env_file.set(keys.AWS_PROFILE, profile)
    region = parser.get_profile_region(profile)
    if region:
        env_file.set(keys.AWS_DEFAULT_REGION, region)
        logger.info("%s: 自動取得完了 (%s)", keys.AWS_DEFAULT_REGION, region)
    else:
        region = safe_input(f"{keys.AWS_DEFAULT_REGION} (デフォルト: ap-northeast-1): ", "ap-northeast-1")
        env_file.set(keys.AWS_DEFAULT_REGION, region)

    sso_url = safe_input(f"{keys.AWS_SSO_URL} (空でスキップ): ")
    if sso_url:
        env_file.set(keys.AWS_SSO_URL, sso_url)


def _collect_access_keys(env_file: EnvFile) -> None:
    parser = AWSConfigParser()
    access_key, secret_key = parser.get_default_credentials()

    for key_name, auto_value in {keys.AWS_ACCESS_KEY_ID: access_key,
                                  keys.AWS_SECRET_ACCESS_KEY: secret_key}.items():
        if auto_value:
            env_file.set(key_name, auto_value)
            logger.info("%s: 自動取得完了", key_name)
        else:
            value = safe_input(f"{key_name} (空でスキップ): ")
            if value:
                env_file.set(key_name, value)

    existing_region = env_file.get(keys.AWS_DEFAULT_REGION)
    if not existing_region:
        region = parser.get_profile_region('default')
        if region:
            env_file.set(keys.AWS_DEFAULT_REGION, region)
            logger.info("%s: 自動取得完了 (%s)", keys.AWS_DEFAULT_REGION, region)
        else:
            region = safe_input(f"{keys.AWS_DEFAULT_REGION} (デフォルト: ap-northeast-1): ", "ap-northeast-1")
            env_file.set(keys.AWS_DEFAULT_REGION, region)
    else:
        logger.info("%s: 設定済み (%s)", keys.AWS_DEFAULT_REGION, existing_region)


COLLECTOR = Collector(
    name="aws",
    display_name="AWS認証",
    collect_fn=collect_aws_credentials,
    source_files=["~/.aws/config", "~/.aws/credentials"],
    source_type="tar_base64",
)
