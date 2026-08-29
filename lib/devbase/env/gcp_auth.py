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
from devbase.log import get_logger

logger = get_logger(__name__)

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


def active_profile(env: Mapping[str, str]) -> str:
    """アクティブなプロファイル名を返す。

    entrypoint の ``${GCP_ACTIVE_PROFILE:-default}`` と同じ解釈 (未設定・空なら
    ``default``)。ホストとコンテナで別のプロファイルを見ないよう、判定はここへ
    集約する。
    """
    return (env.get(keys.GCP_ACTIVE_PROFILE) or "").strip() or "default"


def has_service_account_key(env: Mapping[str, str]) -> bool:
    """**アクティブプロファイル**のサービスアカウント鍵が env にあるか。

    プロファイル別の ``GCP_CREDENTIALS_BASE64__<profile>`` を見て、無ければ
    後方互換の ``GOOGLE_APPLICATION_CREDENTIALS_BASE64`` を見る。値が空の変数は
    「無い」として扱う (``env`` に空で書かれていても鍵にはならない)。

    entrypoint の ``devbase_setup_gcp_credentials`` が見るのと**同じ 1 本だけ**を
    見るのが要点である。全プロファイルを走査すると、別プロファイルの鍵しか無い
    構成でホストは ``key`` と判定するのに、コンテナ側は鍵を書けず ``adc`` へ
    落ちる。その結果、実体の無いパスを指す 2 変数だけが生成 compose に残り、
    ``docker exec`` のシェルから使ったときに ``DefaultCredentialsError`` になる。
    """
    profile = active_profile(env)
    return bool(env.get(keys.gcp_credentials_key(profile))
                or env.get(keys.GOOGLE_APPLICATION_CREDENTIALS_BASE64))


def resolve_auth_mode(env: Mapping[str, str]) -> str:
    """認証モードを解決する (entrypoint と同じ条件・同じフォールバックで)。

    ``GCP_AUTH_MODE`` が ``adc`` なら鍵があっても ADC。それ以外 (``key`` 宣言・
    未設定・空・未知の値) は**アクティブプロファイルの鍵の有無**で決める。

    ``key`` を宣言していても鍵が無ければ ``adc`` へ倒すのは、entrypoint が同じ
    フォールバックを持つため。ホストだけ ``key`` のままだと、鍵の実体が無いのに
    2 変数がコンテナへ渡り ``DefaultCredentialsError`` を招く。

    未知の値を拒否せず auto へ倒すのは、タイプミスで**既存プロジェクトが
    起動できなくなる**のを避けるため。auto は現行 main と同じ挙動になる。
    """
    declared = (env.get(keys.GCP_AUTH_MODE) or "").strip().lower()
    if declared == AUTH_MODE_ADC:
        return AUTH_MODE_ADC
    if has_service_account_key(env):
        return AUTH_MODE_KEY
    if declared == AUTH_MODE_KEY:
        logger.warning(
            "%s=key ですが %s が env にありません。adc として構成します",
            keys.GCP_AUTH_MODE,
            keys.gcp_credentials_key(active_profile(env)))
    return AUTH_MODE_ADC


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
