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

同じ理由で、**鍵の実体を運ぶ base64 変数**も列挙から外す必要がある (issue #134)。
``adc`` が止めるのは「鍵をファイルへ書き出すこと」だけで、環境変数としての配布は
止まらない。名前が列挙に残る限り Compose が値を解決して渡すため、アカウント
グループを分けても他社の鍵がコンテナ内から ``env`` で読めてしまう。外す対象は
:func:`dev_excluded_env_names` にまとめてある。
"""

from typing import Iterable, Mapping, Sequence

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


def key_only_env_names(mode: str) -> Sequence[str]:
    """``mode`` で **dev の列挙から外す**変数名を返す (``key`` なら空)。

    生成 compose の ``environment:`` に名前が載らなければ、Compose はその変数を
    コンテナへ渡さない。値を空文字にするのではなく**渡さない**ことで、
    ``docker exec`` のシェルから見ても未設定になる。

    外すのは devbase が管理する dev サービスの列挙だけである。``GCP_AUTH_MODE``
    は dev の認証方式の宣言であって、元々この 2 変数を ``env_file`` から受け取って
    いた非 dev サービス (独自に鍵を持つ batch 等) の設定ではない。
    """
    return () if mode == AUTH_MODE_KEY else KEY_ONLY_ENV_KEYS


def inactive_profile_key_names(env: Mapping[str, str],
                               names: Iterable[str] = ()) -> Sequence[str]:
    """**アクティブプロファイル以外**の ``GCP_CREDENTIALS_BASE64__*`` を返す。

    entrypoint の ``devbase_setup_gcp_credentials`` が読むのは
    ``GCP_CREDENTIALS_BASE64__${GCP_ACTIVE_PROFILE}`` の **1 本だけ**である。
    他プロファイルの鍵はどのモードでもコンテナ内で使われないので、dev の列挙
    から外して**値ごと渡さない**。

    ``adc`` が止めるのは「鍵をファイルへ書き出すこと」だけで、環境変数としての
    配布は止まらない。名前が列挙に残る限り Compose が値を解決して渡すため、
    アカウントグループを分けても他社の鍵がコンテナ内から ``env`` で読める。

    ``env`` だけでなく ``names`` (生成 compose へ列挙する機密の名前) も走査する。
    ``runtime.inject`` を経ていれば両者は一致するが、その前提に寄りかかると
    「列挙されているのに ``os.environ`` には無い」名前を外し損ねる。判定を呼び
    出し順に依存させないため、両方を候補にする。
    """
    active = keys.gcp_credentials_key(active_profile(env))
    candidates = dict.fromkeys([*env, *names])
    return tuple(
        name for name in candidates
        if name.startswith(keys.GCP_CREDENTIALS_BASE64_PREFIX) and name != active
    )


def _legacy_key_is_the_source(env: Mapping[str, str], mode: str) -> bool:
    """後方互換キーが**実際に鍵の供給源になる**か。

    entrypoint は ``GCP_CREDENTIALS_BASE64__<active>`` が無いときだけ
    ``GOOGLE_APPLICATION_CREDENTIALS_BASE64`` へフォールバックする
    (``${!var:-${GOOGLE_APPLICATION_CREDENTIALS_BASE64:-}}``)。したがって残す
    必要があるのは「鍵モード」かつ「アクティブプロファイルの鍵が無い」ときだけ。

    ここを落とすと、プロファイル別キーへ未移行のプロジェクトが鍵を受け取れ
    なくなって壊れる。空文字を「無い」として扱うのは
    :func:`has_service_account_key` と同じ判定である。
    """
    if mode != AUTH_MODE_KEY:
        return False
    return not env.get(keys.gcp_credentials_key(active_profile(env)))


def dev_excluded_env_names(env: Mapping[str, str], mode: str,
                           names: Iterable[str] = ()) -> Sequence[str]:
    """dev の列挙から外す変数名を返す (鍵のパス + 使われない鍵の実体)。

    :func:`key_only_env_names` が外すのは「鍵ファイルのパスを指す 2 変数」で、
    ``DefaultCredentialsError`` を避けるためのもの。こちらはそれに加えて、
    **鍵の中身を運ぶ base64 変数のうちコンテナ内で使われないもの**を外す。

    ``adc`` では**アクティブプロファイルの鍵も外す**。entrypoint の
    ``devbase_setup_gcp_credentials`` は ``adc`` だと ``creds_b64`` を使わずに
    return するので、鍵の実体は 1 本も要らない。アクティブ分だけ残すと、
    「鍵を使わない」と宣言したコンテナの ``env`` から秘密鍵が読めてしまう。

    許可リストとして働くのが要点である。プロジェクト側の ``env`` で不要な鍵を
    1 本ずつ空文字に潰す拒否リスト方式だと、グローバルへプロファイルが増える
    たびに全プロジェクトへ追記が要り、漏れてもエラーにならない。
    """
    excluded = list(key_only_env_names(mode))
    excluded.extend(inactive_profile_key_names(env, names))
    if mode != AUTH_MODE_KEY:
        excluded.append(keys.gcp_credentials_key(active_profile(env)))
    if not _legacy_key_is_the_source(env, mode):
        excluded.append(keys.GOOGLE_APPLICATION_CREDENTIALS_BASE64)
    # 呼び出し側が渡した順序を保ちつつ重複を除く
    return tuple(dict.fromkeys(excluded))
