"""entrypoint 側の GCP 認証モードと設定ディレクトリ (PLAN39 Task 5)

``containers/base/entrypoint.sh`` を ``DEVBASE_ENTRYPOINT_LIB_ONLY=1`` で source し、
``GCP_AUTH_MODE`` × 鍵 env の組み合わせで「鍵を書くか」「2 変数が残るか」を固定する。

コンテナへ渡る環境変数そのものを決めるのはホスト側 (``lib/devbase/env/gcp_auth.py``)
だが、entrypoint 側にも同じ判定を持たせている。古いホストから起動された場合と、
プロジェクトの ``env`` に 2 変数が直書きされている場合の保険である。
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "containers" / "base" / "entrypoint.sh"

KEY_JSON = json.dumps({"type": "service_account", "project_id": "example"})
KEY_B64 = base64.b64encode(KEY_JSON.encode()).decode()


def run(script: str, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("DEVBASE_", "GIT_", "GCP_", "GOOGLE_", "BIGQUERY_"))}
    full = f'set -e\nDEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"\n{script}\n'
    return subprocess.run(
        ["bash", "-c", full], cwd=cwd, env={**base, **env},
        capture_output=True, text=True,
    )


def setup_credentials(home: Path, env: dict) -> dict:
    """``devbase_setup_gcp_credentials`` を実行し、実行後の 2 変数を返す。"""
    script = (
        f'devbase_setup_gcp_credentials "{home}"\n'
        'echo "GAC=${GOOGLE_APPLICATION_CREDENTIALS-<unset>}"\n'
        'echo "BQ=${BIGQUERY_KEY_FILE-<unset>}"\n'
    )
    result = run(script, env, home)
    assert result.returncode == 0, result.stderr or result.stdout
    values = {}
    for line in result.stdout.splitlines():
        if line.startswith(("GAC=", "BQ=")):
            name, _, value = line.partition("=")
            values[name] = value
    values["stdout"] = result.stdout
    return values


@pytest.fixture
def home(tmp_path: Path) -> Path:
    d = tmp_path / "home"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# 鍵モード (AC11 / AC12 (2))
# ---------------------------------------------------------------------------

def test_key_is_written_when_a_key_env_is_present(home):
    values = setup_credentials(home, {"GCP_CREDENTIALS_BASE64__default": KEY_B64})

    written = home / ".config" / "gcloud" / "credentials.json"
    assert written.read_text() == KEY_JSON
    assert values["GAC"] == str(written)
    assert values["BQ"] == str(written)


def test_explicit_key_mode_writes_the_key(home):
    values = setup_credentials(home, {
        "GCP_AUTH_MODE": "key",
        "GCP_CREDENTIALS_BASE64__default": KEY_B64,
    })

    assert (home / ".config" / "gcloud" / "credentials.json").read_text() == KEY_JSON
    assert values["GAC"].endswith("/credentials.json")


def test_active_profile_selects_the_key(home):
    values = setup_credentials(home, {
        "GCP_ACTIVE_PROFILE": "kkg",
        "GCP_CREDENTIALS_BASE64__kkg": KEY_B64,
        "GCP_CREDENTIALS_BASE64__default": base64.b64encode(b"wrong").decode(),
    })

    assert (home / ".config" / "gcloud" / "credentials.json").read_text() == KEY_JSON
    assert "profile: kkg" in values["stdout"]


def test_custom_paths_are_honoured(home, tmp_path):
    """プロジェクト env でパスを上書きしている構成を壊さない (前提 11)。"""
    gac = tmp_path / "custom" / "gac.json"
    bq = tmp_path / "custom" / "bq.json"

    values = setup_credentials(home, {
        "GCP_CREDENTIALS_BASE64__default": KEY_B64,
        "GOOGLE_APPLICATION_CREDENTIALS": str(gac),
        "BIGQUERY_KEY_FILE": str(bq),
    })

    assert gac.read_text() == KEY_JSON
    assert bq.read_text() == KEY_JSON
    assert values["GAC"] == str(gac)
    assert values["BQ"] == str(bq)


def test_key_file_permissions_are_restricted(home):
    setup_credentials(home, {"GCP_CREDENTIALS_BASE64__default": KEY_B64})

    written = home / ".config" / "gcloud" / "credentials.json"
    assert oct(written.stat().st_mode & 0o777) == "0o600"


# ---------------------------------------------------------------------------
# ADC モード (AC12 (1)(3) / AC13)
# ---------------------------------------------------------------------------

def test_adc_is_the_default_without_a_key(home):
    values = setup_credentials(home, {})

    assert values["GAC"] == "<unset>"
    assert values["BQ"] == "<unset>"
    assert not (home / ".config" / "gcloud" / "credentials.json").exists()


def test_adc_unsets_leftover_variables(home):
    """AC12 (3): key → adc へ戻したとき、値だけ残らないようにする。

    値だけ残って実体が無いと ADC はユーザー認証へフォールバックせず
    DefaultCredentialsError で落ちる (前提 10)。
    """
    values = setup_credentials(home, {
        "GCP_AUTH_MODE": "adc",
        "GOOGLE_APPLICATION_CREDENTIALS": "/home/ubuntu/.config/gcloud/credentials.json",
        "BIGQUERY_KEY_FILE": "/home/ubuntu/.config/gcloud/credentials.json",
    })

    assert values["GAC"] == "<unset>"
    assert values["BQ"] == "<unset>"


def test_explicit_adc_does_not_write_a_key_even_when_one_exists(home):
    """AC13: 鍵の env があっても adc なら鍵を書かない。"""
    values = setup_credentials(home, {
        "GCP_AUTH_MODE": "adc",
        "GCP_CREDENTIALS_BASE64__default": KEY_B64,
    })

    assert not (home / ".config" / "gcloud" / "credentials.json").exists()
    assert values["GAC"] == "<unset>"


def test_key_mode_without_a_key_falls_back_to_adc(home):
    """鍵が無いのに key を宣言しても、実体の無いパスを残さない。"""
    values = setup_credentials(home, {"GCP_AUTH_MODE": "key"})

    assert values["GAC"] == "<unset>"
    assert values["BQ"] == "<unset>"
    assert "ADC へ切り替えます" in values["stdout"]


def test_unknown_mode_falls_back_to_auto(home):
    values = setup_credentials(home, {
        "GCP_AUTH_MODE": "yes",
        "GCP_CREDENTIALS_BASE64__default": KEY_B64,
    })

    assert (home / ".config" / "gcloud" / "credentials.json").exists()


# ---------------------------------------------------------------------------
# 設定ディレクトリ (AC1 / AC2 / 前提 18)
# ---------------------------------------------------------------------------

def test_config_dirs_are_created(home, tmp_path):
    gcloud = tmp_path / "group" / "gcloud"
    gws = tmp_path / "group" / "gws"

    result = run('devbase_setup_cloud_config_dirs "$(id -un)"', {
        "CLOUDSDK_CONFIG": str(gcloud),
        "GOOGLE_WORKSPACE_CLI_CONFIG_DIR": str(gws),
    }, home)

    assert result.returncode == 0, result.stderr
    assert gcloud.is_dir()
    assert gws.is_dir()


def test_existing_config_dirs_are_left_alone(home, tmp_path):
    gcloud = tmp_path / "group" / "gcloud"
    gcloud.mkdir(parents=True)
    (gcloud / "credentials.db").write_text("kept")

    result = run('devbase_setup_cloud_config_dirs "$(id -un)"',
                 {"CLOUDSDK_CONFIG": str(gcloud)}, home)

    assert result.returncode == 0, result.stderr
    assert (gcloud / "credentials.db").read_text() == "kept"


def test_unset_config_dirs_are_skipped(home):
    """古いホストから起動された場合でも落ちない。"""
    result = run('devbase_setup_cloud_config_dirs "$(id -un)"', {}, home)

    assert result.returncode == 0, result.stderr
