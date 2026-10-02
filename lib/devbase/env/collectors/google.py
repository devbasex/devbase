"""Google Cloud認証情報コレクター（複数プロファイル対応）"""

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from devbase.log import get_logger
from devbase.env import keys
from devbase.env.store import EnvFile, safe_input, collect_key
from devbase.env.collector import Collector
from devbase.env.host_import import HostImport

logger = get_logger(__name__)

STEP = "GCP認証"
#: アクティブプロファイルの質問で「設定しない」を選ぶ語
NONE_ANSWER = "none"

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
    """プロファイル名を環境変数として安全な文字種に正規化する (警告は出さない。#334 I6)"""
    return re.sub(r'[^A-Za-z0-9_]', '_', name)


@dataclass
class CredentialFiles:
    """鍵ファイルの発見の結果 (#334)

    ``files`` はプロファイル名 → 鍵ファイル (ファイル名の昇順)。``renamed`` は正規化で変わった
    名前 → 元の名前、``collisions`` は名前 → 同じ名前に正規化され、先のファイルに負けて外した
    鍵ファイルの並び。
    """
    files: Dict[str, Path] = field(default_factory=dict)
    renamed: Dict[str, str] = field(default_factory=dict)
    collisions: Dict[str, List[Path]] = field(default_factory=dict)

    def lookup(self, name: str) -> Optional[Path]:
        """プロファイル名から鍵ファイルを引く。``default`` で無ければ ``~/google_credential.json``"""
        path = self.files.get(name)
        if path and path.exists():
            return path
        if name == 'default' and LEGACY_CREDENTIALS_FILE.exists():
            return LEGACY_CREDENTIALS_FILE
        return None


def find_credential_files() -> CredentialFiles:
    """``~/gcp-credentials/`` の鍵ファイルを見つける。JSON を読まず、何も出力しない (I6・I9)

    ファイル名の昇順に読み、正規化した名前が衝突したら先のファイルを採る。ディレクトリに
    鍵が無ければ ``~/google_credential.json`` を ``default`` として返す。
    """
    found = CredentialFiles()
    if GCP_CREDENTIALS_DIR.is_dir():
        for f in sorted(GCP_CREDENTIALS_DIR.iterdir()):
            if f.suffix != '.json' or not f.is_file():
                continue
            name = _safe_profile_name(f.stem)
            if name in found.files:
                found.collisions.setdefault(name, []).append(f)
                continue
            found.files[name] = f
            if name != f.stem:
                found.renamed[name] = f.stem
        if found.files:
            return found
        found = CredentialFiles()

    if LEGACY_CREDENTIALS_FILE.exists():
        found.files['default'] = LEGACY_CREDENTIALS_FILE
    return found


def _warn_for_chosen(chosen: List[str], found: CredentialFiles) -> None:
    """取り込むと決めた名前について、正規化と衝突の警告を 1 回ずつ出す (I7・I8)"""
    for name in chosen:
        if name in found.renamed:
            logger.warning("プロファイル名 '%s' を '%s' に正規化しました", found.renamed[name], name)
        for dropped in found.collisions.get(name, []):
            logger.warning(
                "プロファイル名 '%s' が衝突しています: '%s' と '%s' (後者をスキップ)",
                name, str(found.files[name]), str(dropped),
            )


def collect_google_credentials(env_file: EnvFile, *, host: HostImport) -> None:
    """Google Cloud認証情報を対話的に収集する（複数プロファイル対応）

    ``host`` は取り込みの方針 (#314)。``~/gcp-credentials/`` の鍵は、方針が「取り込む」なら
    全部を、「尋ねる」なら選んだものだけを登録する。候補が無いときの手入力は方針によらない (前提 6)。
    """
    print("\n=== Google Cloud認証情報 ===")

    found = find_credential_files()

    if not found.files:
        existing = env_file.get(keys.gcp_credentials_key("default"))
        has_key = bool(existing)
        if existing:
            logger.info("%s: 設定済み", keys.gcp_credentials_key("default"))
        else:
            creds_path_str = safe_input("credentialファイルのパス (空でスキップ): ")
            if creds_path_str:
                creds_path = Path(creds_path_str).expanduser()
                if creds_path.exists():
                    _register_profile(env_file, 'default', creds_path)
                    has_key = True
                else:
                    logger.error("ファイルが見つかりません: %s", creds_path)
        _collect_common_settings(env_file, has_key=has_key)
        return

    names = list(found.files)
    project_ids = {name: _extract_project_id(path) for name, path in found.files.items()}
    if host.importing:
        print(f"\n検出されたcredential ({len(names)}件):")
        print('\n'.join(f"  - {name} (project: {project_ids[name]})" for name in names))
        chosen = names
    else:
        labels = [f"{name} (project: {project_ids[name] or 'N/A'})" for name in names]
        selection = host.choose(STEP, f"ホストで見つけた GCP の鍵 ({len(names)}件):", labels,
                                "例: 1,2 / all で全部 / 空で取り込まない")
        chosen = [names[i] for i in selection.indexes]
    if not chosen:
        # 前提 5: 鍵を 1 つも取り込まなければ、共通設定も書かない
        host.declined(STEP)
        host.record('gcp', [])
        return
    _warn_for_chosen(chosen, found)

    default_active = 'default' if 'default' in chosen else chosen[0]
    active = _ask_active_profile(chosen, default_active)
    if active is None:
        logger.info("GCP認証: アクティブプロファイルを設定しないため、GCP の鍵と設定を書きません")
        host.record('gcp', [])
        return

    for name in chosen:
        _register_profile(env_file, name, found.files[name])

    env_file.set(keys.GCP_ACTIVE_PROFILE, active)
    logger.info("%s: %s", keys.GCP_ACTIVE_PROFILE, active)

    project_id = project_ids.get(active)
    if project_id:
        env_file.set(keys.GOOGLE_CLOUD_PROJECT, project_id)
        env_file.set(keys.BIGQUERY_PROJECT, project_id)
        logger.info("%s: %s", keys.GOOGLE_CLOUD_PROJECT, project_id)

    _collect_common_settings(env_file, has_key=True)
    host.record('gcp', chosen)


def _ask_active_profile(names: list, default_active: str) -> Optional[str]:
    """アクティブプロファイルを尋ねる。``none`` は「設定しない」(``None``)。

    選んだ鍵に無い名前は尋ね直す (決定 5)。EOF は既定の値で終わる。
    """
    prompt = f"\nアクティブプロファイル (名前 / none で設定しない、デフォルト: {default_active}): "
    while True:
        active = safe_input(prompt, default_active)
        if active == NONE_ANSWER:
            return None
        if active in names:
            return active
        print(f"'{active}' は選んだ鍵にありません")


def _register_profile(env_file: EnvFile, name: str, file_path: Path) -> None:
    """プロファイルをエンコードしてenv_fileに登録"""
    try:
        encoded = _encode_credentials_file(file_path)
        env_key = keys.gcp_credentials_key(name)
        env_file.set(env_key, encoded)
        logger.info("%s: エンコード完了 (%d 文字)", env_key, len(encoded))
    except Exception as e:
        logger.error("credentialファイルの処理に失敗: %s", e)


def _collect_common_settings(env_file: EnvFile, has_key: bool = False) -> None:
    """GCP共通設定を収集

    Args:
        env_file: 書き込み先
        has_key: サービスアカウント鍵を登録したか。鍵モード専用の変数
            (``GOOGLE_APPLICATION_CREDENTIALS`` / ``BIGQUERY_KEY_FILE``) は
            鍵があるときだけ書く。実体の無いパスが env に残っていると ADC が
            ユーザー認証へフォールバックせず ``DefaultCredentialsError`` で
            落ちるため (PLAN39 / 前提 10)。
    """
    collect_key(env_file, keys.GOOGLE_CLOUD_LOCATION, auto_value="global", mask_after=0,
                prompt=f"{keys.GOOGLE_CLOUD_LOCATION} (デフォルト: global): ")

    collect_key(env_file, keys.BIGQUERY_DATASETS, mask_after=0,
                prompt=f"{keys.BIGQUERY_DATASETS} (カンマ区切り、空でスキップ): ")

    collect_key(env_file, keys.BIGQUERY_LOCATION, auto_value="asia-northeast1", mask_after=0,
                prompt=f"{keys.BIGQUERY_LOCATION} (デフォルト: asia-northeast1): ")

    if not has_key:
        logger.info(
            "サービスアカウント鍵が未登録のため %s / %s は設定しません "
            "(ADC を使う場合は不要。詳細: docs/user/google-auth.md)",
            keys.GOOGLE_APPLICATION_CREDENTIALS, keys.BIGQUERY_KEY_FILE)
        return

    # コンテナ内パス（devbaseコンテナイメージの仕様に依存）。
    # CLOUDSDK_CONFIG を向け直したあとの ~/.config/gcloud は gcloud の設定
    # ディレクトリではなく、単なる鍵の置き場になる (PLAN39)。
    env_file.set(keys.BIGQUERY_KEY_FILE, "/home/ubuntu/.config/gcloud/credentials.json")
    env_file.set(keys.GOOGLE_APPLICATION_CREDENTIALS, "/home/ubuntu/.config/gcloud/credentials.json")


COLLECTOR = Collector(
    name="google",
    display_name="GCP認証",
    collect_fn=collect_google_credentials,
    source_files=["~/gcp-credentials/"],
    source_type="named_profiles",
    host_import=True,
)
