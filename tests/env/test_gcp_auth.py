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


def test_unset_with_legacy_key_resolves_to_key():
    """後方互換の GOOGLE_APPLICATION_CREDENTIALS_BASE64 も鍵として数える。"""
    env = {"GOOGLE_APPLICATION_CREDENTIALS_BASE64": "eyJ9"}
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

NAMES = ["ANTHROPIC_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
         "BIGQUERY_KEY_FILE", "GCP_CREDENTIALS_BASE64__default"]


def test_adc_drops_the_key_only_names():
    filtered = gcp_auth.filter_key_env_names(NAMES, gcp_auth.AUTH_MODE_ADC)
    assert filtered == ["ANTHROPIC_API_KEY", "GCP_CREDENTIALS_BASE64__default"]


def test_key_mode_keeps_every_name():
    filtered = gcp_auth.filter_key_env_names(NAMES, gcp_auth.AUTH_MODE_KEY)
    assert filtered == NAMES


def test_none_is_passed_through():
    """由来の内訳が渡されない場合の None をつぶさない。"""
    assert gcp_auth.filter_key_env_names(None, gcp_auth.AUTH_MODE_ADC) is None


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
        {"GCP_CREDENTIALS_BASE64__x": "eyJ9"})[keys.GCP_AUTH_MODE] == "key"
