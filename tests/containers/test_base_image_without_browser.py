"""建てた base はブラウザ・追加の太さのフォント・terraform を持たない (#402 の受け入れ条件 5・6)

Dockerfile の形の検査 (``test_base_dockerfile_playwright.py``) では足りない。固定したいのは
「建てた base で Playwright の Chromium を起動すると、次にすること (``playwright install``) を示して
止まること」で、Playwright の出力の文言は建てたイメージでしか分からない。

**検査するイメージは ``DEVBASE_TEST_BASE_IMAGE`` で差し替える。** 既定は ``devbase-base:latest``。
マージ前の確認は ``devbase-base:latest`` を上書きしない別のタグを渡す。

**skip するのは次の 3 つだけ。**

- Docker が無い・daemon へ繋がらない
- 検査するイメージが無い
- 既定の ``devbase-base:latest`` を見ていて、置き場に Chromium がある (この変更より前に建てたイメージ)

``DEVBASE_TEST_BASE_IMAGE`` を明示したときは、Chromium があれば skip せず落とす。

**``docker run`` はセッションで 1 回に抑える。** 並列 (pytest-xdist) では session scope が
ワーカーごとになるため、このモジュールのケースは ``xdist_group`` で 1 つのワーカーへ寄せる。
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from tests.conftest import host_docker_env

DEFAULT_IMAGE = "devbase-base:latest"
_IMAGE_FROM_ENV = os.environ.get("DEVBASE_TEST_BASE_IMAGE", "").strip()
IMAGE = _IMAGE_FROM_ENV or DEFAULT_IMAGE
IMAGE_IS_EXPLICIT = bool(_IMAGE_FROM_ENV)
BUILD_HINT = f"`devbase build base --no-cache` で {DEFAULT_IMAGE} を建て直すと、この検査が効く"

BROWSERS_PATH = "/opt/ms-playwright"
USERNAME = "ubuntu"
# 起動に失敗したときの出力に、次にすることのどちらかが含まれる
NEXT_STEPS = ("playwright install", "devbase-browser")

pytestmark = pytest.mark.xdist_group("base_image_without_browser_probe")

# 1 行 1 項目の `<kind>\t<key>\t<value>` を出す。失敗しても止めずに全項目を採る
_PROBE = r"""
set -u
emit() { printf '%s\t%s\t%s\n' "$1" "$2" "$3"; }

found=""
for d in '__PATH__'/chromium-* '__PATH__'/chromium_headless_shell-*; do
  [ -e "$d" ] && found="$found ${d##*/}"
done
emit browsers chromium "${found# }"

dpkg -s fonts-noto-cjk-extra >/dev/null 2>&1
emit dpkg fonts-noto-cjk-extra "$?"
command -v terraform >/dev/null 2>&1
emit command terraform "$?"

work="$(mktemp -d)"
cat > "$work/launch.js" <<'JS'
const path = require('path');
const root = require('child_process').execSync('npm root -g').toString().trim();
const { chromium } = require(path.join(root, '@playwright/test'));
(async () => {
  const browser = await chromium.launch({ headless: true });
  await browser.close();
})().catch((e) => { console.error(String(e && e.message || e)); process.exit(1); });
JS
node "$work/launch.js" >"$work/node.log" 2>&1
emit launch exit "$?"
emit launch log "$(tail -c 4000 "$work/node.log" | tr '\t\n' '  ')"
exit 0
"""


def _docker_unavailable() -> str | None:
    if shutil.which("docker") is None:
        return "docker が PATH に無い"
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=30, check=True,
                       env=host_docker_env())
    except (subprocess.SubprocessError, OSError):
        return "docker daemon へ繋がらない"
    try:
        out = subprocess.run(["docker", "image", "inspect", IMAGE],
                             capture_output=True, timeout=30, env=host_docker_env())
    except (subprocess.SubprocessError, OSError):
        return f"{IMAGE} を調べられない"
    if out.returncode != 0:
        hint = "" if IMAGE_IS_EXPLICIT else f"。{BUILD_HINT}"
        return f"{IMAGE} が無い{hint}"
    return None


@pytest.fixture(scope="session")
def probe() -> dict[str, dict[str, str]]:
    """1 回の docker run (--network none、利用者 ubuntu の非対話の bash -c) で値をまとめて採る"""
    reason = _docker_unavailable()
    if reason:
        pytest.skip(reason)
    out = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--user", USERNAME,
         "--entrypoint", "/bin/bash", IMAGE, "-c", _PROBE.replace("__PATH__", BROWSERS_PATH)],
        capture_output=True, text=True, timeout=300, env=host_docker_env())
    assert out.returncode == 0, f"probe が失敗した (exit={out.returncode}):\n{out.stderr}"

    collected: dict[str, dict[str, str]] = {}
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        kind, key, value = line.split("\t", 2)
        collected.setdefault(kind, {})[key] = value

    if collected["browsers"]["chromium"] and not IMAGE_IS_EXPLICIT:
        pytest.skip(f"{IMAGE} に Chromium がある (#402 より前のイメージ)。{BUILD_HINT}")
    return collected


def test_the_browsers_path_has_no_chromium(probe):
    """受け入れ条件 6。置き場に Chromium の版のディレクトリが無い"""
    assert probe["browsers"]["chromium"] == "", f"置き場にある: {probe['browsers']['chromium']}"


def test_the_extra_weights_are_not_installed(probe):
    """受け入れ条件 6"""
    assert probe["dpkg"]["fonts-noto-cjk-extra"] != "0"


def test_terraform_is_not_installed(probe):
    """受け入れ条件 6"""
    assert probe["command"]["terraform"] != "0"


def test_launching_chromium_fails_and_names_the_next_step(probe):
    """受け入れ条件 5。非 0 で終わり、出力に取得のコマンドかブラウザの派生イメージの名前がある"""
    launch = probe["launch"]
    assert launch["exit"] != "0", "base で Chromium が起動した"
    assert any(step in launch["log"] for step in NEXT_STEPS), f"出力: {launch['log']}"
