"""GCP の認証モード解決と鍵モード専用変数の除外 (PLAN39 Task 5)

要点は「``adc`` では 2 変数を**渡さない**」こと。値を空にするだけでは
``docker exec`` のシェルから見て未設定にならず、実体の無いパスが残ると ADC は
ユーザー認証へフォールバックせず ``DefaultCredentialsError`` で落ちる。
"""

from __future__ import annotations

import pytest

from devbase.env import gcp_auth, keys


# ---------------------------------------------------------------------------
# モード解決
# ---------------------------------------------------------------------------

def test_unset_without_key_resolves_to_adc():
    """鍵の env が無ければ ADC。新規プロジェクトの既定。"""
    assert gcp_auth.resolve_auth_mode({}) == gcp_auth.AUTH_MODE_ADC


def test_unset_with_profile_key_resolves_to_key():
    """既存プロジェクトは現行どおり鍵モードで動く (auto 判定)。"""
    env = {"GCP_CREDENTIALS_BASE64__default": "eyJ0eXBlIjogInNlcnZpY2VfYWNjb3VudCJ9"}
    assert gcp_auth.resolve_auth_mode(env) == gcp_auth.AUTH_MODE_KEY


def test_active_profile_selects_the_key():
    """entrypoint と同じく GCP_ACTIVE_PROFILE の鍵だけを見る。"""
    env = {keys.GCP_ACTIVE_PROFILE: "prod",
           "GCP_CREDENTIALS_BASE64__prod": "eyJ9"}
    assert gcp_auth.resolve_auth_mode(env) == gcp_auth.AUTH_MODE_KEY


def test_other_profiles_key_does_not_make_it_key_mode():
    """別プロファイルの鍵しか無い構成でホストだけ key と判定しない。

    entrypoint はアクティブプロファイルの鍵が無ければ adc へ落ちる。ホストが
    key のままだと実体の無いパスを指す 2 変数だけがコンテナへ渡り、
    docker exec のシェルで DefaultCredentialsError になる。
    """
    env = {keys.GCP_ACTIVE_PROFILE: "dev",
           "GCP_CREDENTIALS_BASE64__prod": "eyJ9"}
    assert gcp_auth.resolve_auth_mode(env) == gcp_auth.AUTH_MODE_ADC


def test_declared_key_without_a_key_falls_back_to_adc():
    """key 宣言でも鍵が無ければ adc (entrypoint と同じフォールバック)。"""
    env = {keys.GCP_AUTH_MODE: "key"}
    assert gcp_auth.resolve_auth_mode(env) == gcp_auth.AUTH_MODE_ADC


def test_declared_key_with_another_profiles_key_falls_back_to_adc():
    env = {keys.GCP_AUTH_MODE: "key",
           keys.GCP_ACTIVE_PROFILE: "dev",
           "GCP_CREDENTIALS_BASE64__prod": "eyJ9"}
    assert gcp_auth.resolve_auth_mode(env) == gcp_auth.AUTH_MODE_ADC


@pytest.mark.parametrize("profile", ["", "   "])
def test_blank_active_profile_means_default(profile):
    """entrypoint の ``${GCP_ACTIVE_PROFILE:-default}`` と揃える。"""
    env = {keys.GCP_ACTIVE_PROFILE: profile,
           "GCP_CREDENTIALS_BASE64__default": "eyJ9"}
    assert gcp_auth.resolve_auth_mode(env) == gcp_auth.AUTH_MODE_KEY


def test_unset_with_legacy_key_resolves_to_key():
    """後方互換の GOOGLE_APPLICATION_CREDENTIALS_BASE64 も鍵として数える。"""
    env = {"GOOGLE_APPLICATION_CREDENTIALS_BASE64": "eyJ9"}
    assert gcp_auth.resolve_auth_mode(env) == gcp_auth.AUTH_MODE_KEY


def test_legacy_key_covers_a_named_active_profile():
    """プロファイル別の鍵が無ければ後方互換の変数を見る (entrypoint と同じ)。"""
    env = {keys.GCP_ACTIVE_PROFILE: "prod",
           "GOOGLE_APPLICATION_CREDENTIALS_BASE64": "eyJ9"}
    assert gcp_auth.resolve_auth_mode(env) == gcp_auth.AUTH_MODE_KEY


def test_empty_key_value_is_not_a_key():
    """env に空で書かれているだけの変数は鍵として数えない。"""
    env = {"GCP_CREDENTIALS_BASE64__default": ""}
    assert gcp_auth.resolve_auth_mode(env) == gcp_auth.AUTH_MODE_ADC


@pytest.mark.parametrize("declared,expected", [
    ("adc", gcp_auth.AUTH_MODE_ADC),
    ("key", gcp_auth.AUTH_MODE_KEY),
    ("ADC", gcp_auth.AUTH_MODE_ADC),
    ("  key  ", gcp_auth.AUTH_MODE_KEY),
])
def test_explicit_mode_wins(declared, expected):
    env = {keys.GCP_AUTH_MODE: declared,
           "GCP_CREDENTIALS_BASE64__default": "eyJ9"}
    assert gcp_auth.resolve_auth_mode(env) == expected


def test_explicit_adc_wins_over_present_key():
    """鍵があっても adc を宣言すれば鍵を使わない (AC12 (3) の戻り方向)。"""
    env = {keys.GCP_AUTH_MODE: "adc",
           "GCP_CREDENTIALS_BASE64__default": "eyJ9"}
    assert gcp_auth.resolve_auth_mode(env) == gcp_auth.AUTH_MODE_ADC


@pytest.mark.parametrize("declared", ["", "  ", "yes", "adc2", "keys"])
def test_unknown_mode_falls_back_to_auto(declared):
    """タイプミスで既存プロジェクトが起動できなくなるのを避ける。"""
    with_key = {keys.GCP_AUTH_MODE: declared,
                "GCP_CREDENTIALS_BASE64__default": "eyJ9"}
    without_key = {keys.GCP_AUTH_MODE: declared}

    assert gcp_auth.resolve_auth_mode(with_key) == gcp_auth.AUTH_MODE_KEY
    assert gcp_auth.resolve_auth_mode(without_key) == gcp_auth.AUTH_MODE_ADC


# ---------------------------------------------------------------------------
# 鍵モード専用変数の除外 (AC12 / AC13)
# ---------------------------------------------------------------------------

def test_adc_excludes_the_key_only_names():
    assert gcp_auth.key_only_env_names(gcp_auth.AUTH_MODE_ADC) == (
        "GOOGLE_APPLICATION_CREDENTIALS", "BIGQUERY_KEY_FILE")


def test_key_mode_excludes_nothing():
    assert gcp_auth.key_only_env_names(gcp_auth.AUTH_MODE_KEY) == ()


# ---------------------------------------------------------------------------
# コンテナへ渡す環境変数
# ---------------------------------------------------------------------------

def test_container_env_points_config_dirs_at_the_group_volume():
    env = gcp_auth.container_env({})

    assert env["CLOUDSDK_CONFIG"] == "/persistent/group/gcloud"
    assert env["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] == "/persistent/group/gws"


def test_container_env_carries_the_resolved_mode():
    """コンテナ側で解決し直させない (ホストと判定がずれないようにする)。"""
    assert gcp_auth.container_env({})[keys.GCP_AUTH_MODE] == "adc"
    assert gcp_auth.container_env(
        {"GCP_CREDENTIALS_BASE64__default": "eyJ9"})[keys.GCP_AUTH_MODE] == "key"


# ---------------------------------------------------------------------------
# 鍵の実体 (base64) の除外 (issue #134)
# ---------------------------------------------------------------------------

def test_inactive_profile_keys_are_excluded():
    """アクティブプロファイル以外の鍵は entrypoint が読まないので外す。"""
    env = {keys.GCP_ACTIVE_PROFILE: "with",
           "GCP_CREDENTIALS_BASE64__default": "eyJ9",
           "GCP_CREDENTIALS_BASE64__kkg": "eyJ9",
           "GCP_CREDENTIALS_BASE64__with": "eyJ9"}

    excluded = set(gcp_auth.inactive_profile_key_names(env))

    assert excluded == {"GCP_CREDENTIALS_BASE64__default",
                        "GCP_CREDENTIALS_BASE64__kkg"}


def test_active_profile_key_is_kept():
    """自分のプロファイルの鍵は entrypoint が使うので残す。"""
    env = {keys.GCP_ACTIVE_PROFILE: "with",
           "GCP_CREDENTIALS_BASE64__with": "eyJ9"}

    assert gcp_auth.inactive_profile_key_names(env) == ()


def test_unset_profile_defaults_to_the_default_key():
    """GCP_ACTIVE_PROFILE 未設定なら default の鍵が残る。"""
    env = {"GCP_CREDENTIALS_BASE64__default": "eyJ9",
           "GCP_CREDENTIALS_BASE64__prod": "eyJ9"}

    assert gcp_auth.inactive_profile_key_names(env) == (
        "GCP_CREDENTIALS_BASE64__prod",)


def test_adc_excludes_the_legacy_key():
    """adc では entrypoint が鍵を一切読まないので後方互換キーも外す。"""
    env = {keys.GCP_AUTH_MODE: "adc",
           "GOOGLE_APPLICATION_CREDENTIALS_BASE64": "eyJ9"}

    assert keys.GOOGLE_APPLICATION_CREDENTIALS_BASE64 in \
        gcp_auth.dev_excluded_env_names(env, gcp_auth.AUTH_MODE_ADC)


def test_key_mode_with_profile_key_excludes_the_legacy_key():
    """プロファイル別キーが使われるならフォールバックは発生しない。"""
    env = {keys.GCP_ACTIVE_PROFILE: "with",
           "GCP_CREDENTIALS_BASE64__with": "eyJ9",
           "GOOGLE_APPLICATION_CREDENTIALS_BASE64": "eyJ9"}

    assert keys.GOOGLE_APPLICATION_CREDENTIALS_BASE64 in \
        gcp_auth.dev_excluded_env_names(env, gcp_auth.AUTH_MODE_KEY)


def test_key_mode_without_profile_key_keeps_the_legacy_key():
    """後方互換キーが鍵の供給源のときだけ残す。

    ここを外すと、プロファイル別キーへ未移行のプロジェクトが鍵を受け取れなく
    なって起動時に壊れる。
    """
    env = {"GOOGLE_APPLICATION_CREDENTIALS_BASE64": "eyJ9"}

    assert keys.GOOGLE_APPLICATION_CREDENTIALS_BASE64 not in \
        gcp_auth.dev_excluded_env_names(env, gcp_auth.AUTH_MODE_KEY)


def test_empty_profile_key_counts_as_absent():
    """空文字は has_service_account_key と同じく「無い」扱い。"""
    env = {keys.GCP_ACTIVE_PROFILE: "with",
           "GCP_CREDENTIALS_BASE64__with": "",
           "GOOGLE_APPLICATION_CREDENTIALS_BASE64": "eyJ9"}

    assert keys.GOOGLE_APPLICATION_CREDENTIALS_BASE64 not in \
        gcp_auth.dev_excluded_env_names(env, gcp_auth.AUTH_MODE_KEY)


def test_dev_excluded_keeps_the_key_only_names():
    """既存の 2 変数の除外は据え置き (adc のときだけ外す)。"""
    adc = gcp_auth.dev_excluded_env_names({}, gcp_auth.AUTH_MODE_ADC)
    key = gcp_auth.dev_excluded_env_names(
        {"GCP_CREDENTIALS_BASE64__default": "eyJ9"}, gcp_auth.AUTH_MODE_KEY)

    assert "GOOGLE_APPLICATION_CREDENTIALS" in adc
    assert "BIGQUERY_KEY_FILE" in adc
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in key
    assert "BIGQUERY_KEY_FILE" not in key


def test_dev_excluded_has_no_duplicates():
    env = {keys.GCP_AUTH_MODE: "adc",
           "GOOGLE_APPLICATION_CREDENTIALS_BASE64": "eyJ9",
           "GCP_CREDENTIALS_BASE64__prod": "eyJ9"}

    names = gcp_auth.dev_excluded_env_names(env, gcp_auth.AUTH_MODE_ADC)

    assert len(names) == len(set(names))
