"""GCP の認証モード解決 (PLAN39)

Google はサービスアカウント鍵を非推奨とし、ローカル開発には
``gcloud auth application-default login`` を推奨している。PLAN39 でユーザー認証を
アカウントグループ単位に永続化するため、ADC を既定の経路にできるようになった。
権限の都合で鍵が要る場面は残るので ``GCP_AUTH_MODE`` で切り替えられる。

**`adc` では 2 変数を「値を空にする」のではなく「渡さない」のが要点**である。
``GOOGLE_APPLICATION_CREDENTIALS`` が実在しないファイルを指していると、ADC は
ユーザー認証へフォールバックせず ``DefaultCredentialsError`` で落ちる。

コンテナへ渡る環境変数を決めるのは**ホスト側の生成 compose** であり、entrypoint の
``export`` / ``unset`` は PID 1 の子プロセスにしか効かない (``docker exec`` の
シェルはコンテナの env 設定を継承する)。したがって 2 変数の除外はここで行う。
"""

from typing import Mapping, Optional, Sequence

from devbase.env import keys

# 認証モード
AUTH_MODE_ADC = "adc"
AUTH_MODE_KEY = "key"
AUTH_MODES = (AUTH_MODE_ADC, AUTH_MODE_KEY)

# 鍵モードでのみコンテナへ渡す変数。adc では渡さない (値を空にするのではない)
KEY_ONLY_ENV_KEYS = (
    keys.GOOGLE_APPLICATION_CREDENTIALS,
    keys.BIGQUERY_KEY_FILE,
)

# gcloud / gws の設定ディレクトリ。グループボリューム配下へ向けることで、
# credentials.db / access_tokens.db / application_default_credentials.json と
# gws の credentials.enc / .encryption_key がグループ単位に分かれる。
CLOUDSDK_CONFIG_DIR = "/persistent/group/gcloud"
GWS_CONFIG_DIR = "/persistent/group/gws"

CLOUDSDK_CONFIG = "CLOUDSDK_CONFIG"
GOOGLE_WORKSPACE_CLI_CONFIG_DIR = "GOOGLE_WORKSPACE_CLI_CONFIG_DIR"


def has_service_account_key(env: Mapping[str, str]) -> bool:
    """サービスアカウント鍵の env が 1 つでもあるか。

    プロファイル別の ``GCP_CREDENTIALS_BASE64__<profile>`` と、後方互換の
    ``GOOGLE_APPLICATION_CREDENTIALS_BASE64`` の両方を見る。値が空の変数は
    「無い」として扱う (``env`` に空で書かれていても鍵にはならない)。
    """
    if env.get("GOOGLE_APPLICATION_CREDENTIALS_BASE64"):
        return True
    return any(
        name.startswith(keys.GCP_CREDENTIALS_BASE64_PREFIX) and value
        for name, value in env.items()
    )


def resolve_auth_mode(env: Mapping[str, str]) -> str:
    """認証モードを解決する。

    ``GCP_AUTH_MODE`` が ``adc`` / ``key`` ならその値。未設定・空・未知の値なら
    auto 判定として「鍵の env があれば ``key``、無ければ ``adc``」にする。

    未知の値を拒否せず auto へ倒すのは、タイプミスで**既存プロジェクトが
    起動できなくなる**のを避けるため。auto は現行 main と同じ挙動になる。
    """
    declared = (env.get(keys.GCP_AUTH_MODE) or "").strip().lower()
    if declared in AUTH_MODES:
        return declared
    return AUTH_MODE_KEY if has_service_account_key(env) else AUTH_MODE_ADC


def container_env(env: Mapping[str, str]) -> dict:
    """dev サービスへ載せる GCP 関連の環境変数を組み立てる。

    設定ディレクトリはグループボリューム配下の固定パス。解決した認証モードも
    渡し、entrypoint 側で再解決させない (ホストとコンテナで判定がずれないよう
    にする)。
    """
    return {
        CLOUDSDK_CONFIG: CLOUDSDK_CONFIG_DIR,
        GOOGLE_WORKSPACE_CLI_CONFIG_DIR: GWS_CONFIG_DIR,
        keys.GCP_AUTH_MODE: resolve_auth_mode(env),
    }


def filter_key_env_names(
    names: Optional[Sequence[str]], mode: str,
) -> Optional[Sequence[str]]:
    """``adc`` モードでは鍵モード専用の変数名を列挙から外す。

    生成 compose の ``environment:`` に名前が載らなければ、Compose はその変数を
    コンテナへ渡さない。値を空文字にするのではなく**渡さない**ことで、
    ``docker exec`` のシェルから見ても未設定になる。
    """
    if names is None or mode == AUTH_MODE_KEY:
        return names
    return [name for name in names if name not in KEY_ONLY_ENV_KEYS]
