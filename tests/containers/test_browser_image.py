"""建てたブラウザの派生イメージ (#220 の受け入れ条件 5〜9、#402 の受け入れ条件 8・9)

base はブラウザを持たない (#402)。Playwright の Chromium・ブラウザの依存パッケージ・追加の太さの
フォントはブラウザの派生イメージ ``devbase-browser`` が持つ。

Dockerfile の形の検査 (``test_browser_dockerfile.py`` など) では足りない。固定したいのは
「``ENV`` の行があること」ではなく「建てたイメージで Playwright の Chromium がネットワーク無しで
起動し、日本語のページを Noto Sans CJK JP で PDF にできること」で、後者は文字列からは分からない。

**検査するイメージは ``DEVBASE_TEST_BROWSER_IMAGE`` で差し替える (#220 の決定 3)。** 既定は
``devbase-browser:latest``。マージ前の確認は ``devbase-browser:latest`` を上書きしない別のタグを渡し、
リリース後テストでは既定のまま同じ検査を走らせる。

**skip するのは次の 3 つだけ。**

- Docker が無い・daemon へ繋がらない
- 検査するイメージが無い
- 既定の ``devbase-browser:latest`` を見ていて、ブラウザの置き場 (``/opt/ms-playwright``) が無い

``DEVBASE_TEST_BROWSER_IMAGE`` を明示したときは、置き場が無くても skip せず落とす。置き場を
片付けで消して建てたイメージは、置き場の無い古いイメージと見分けられないため、明示したタグまで
skip すると、テスト設計の「片付けに置き場を足して建てる」壊し方が skip で通ってしまう。

**``docker run`` はセッションで 1 回に抑える。** Chromium の起動は数秒かかり、ケースごとに
コンテナを起こすと遅い。並列 (pytest-xdist) では session scope がワーカーごとになるため、
このモジュールのケースは ``xdist_group`` で 1 つのワーカーへ寄せる。
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from tests.conftest import host_docker_env

DEFAULT_IMAGE = "devbase-browser:latest"
# 隔離の fixture より前 (読み込み時) に読む。隔離の一覧に入っていないが、入っても効くように
_IMAGE_FROM_ENV = os.environ.get("DEVBASE_TEST_BROWSER_IMAGE", "").strip()
IMAGE = _IMAGE_FROM_ENV or DEFAULT_IMAGE
IMAGE_IS_EXPLICIT = bool(_IMAGE_FROM_ENV)
BUILD_HINT = f"`devbase build base` の後に `devbase build browser` で {DEFAULT_IMAGE} を建てると、この検査が効く"

BROWSERS_PATH = "/opt/ms-playwright"
USERNAME = "ubuntu"

# 受け入れ条件 8。ブラウザの依存パッケージは減らない
DEPENDENCY_PACKAGES = ("fonts-liberation", "fonts-ipafont-gothic", "fonts-wqy-zenhei", "libnss3")
# #402 の受け入れ条件 9。追加の太さのフォント
EXTRA_WEIGHTS_PACKAGE = "fonts-noto-cjk-extra"
EXTRA_WEIGHTS = ("Thin", "Black")

# 並列でも probe (docker run) を 1 回で済ませるため、全ケースを同じワーカーへ割り当てる
pytestmark = pytest.mark.xdist_group("browser_image_probe")

# 1 行 1 項目の `<kind>\t<key>\t<value>` を出す。失敗しても止めずに全項目を採る
# (どの受け入れ条件が落ちたかをケースごとに分けて見せるため)。
# Python の str.format は使わない (JS と bash の波括弧が多いため)。__PATH__ などを置き換える
_PROBE = r"""
set -u
emit() { printf '%s\t%s\t%s\n' "$1" "$2" "$3"; }

if [ -d '__PATH__' ]; then emit browsers-dir '' present; else emit browsers-dir '' absent; fi

# 受け入れ条件 9 (前半)。ENV の値を env の出力から採る
emit env PLAYWRIGHT_BROWSERS_PATH "$(env | sed -n 's/^PLAYWRIGHT_BROWSERS_PATH=//p')"

# 受け入れ条件 5・7。ネットワーク無しで Playwright の Chromium を起動し、日本語のページを PDF にする。
# イメージにあるのは npm のグローバルの @playwright/test だけなので、Node から require する
work="$(mktemp -d)"
cat > "$work/pdf.js" <<'JS'
const path = require('path');
const root = require('child_process').execSync('npm root -g').toString().trim();
const { chromium } = require(path.join(root, '@playwright/test'));
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.setContent('<!doctype html><html lang="ja"><head><meta charset="utf-8"></head>'
    + '<body><h1>ブラウザの置き場</h1><p>日本語の本文を PDF にする。</p></body></html>');
  await page.pdf({ path: process.argv[2] });
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
JS
node "$work/pdf.js" "$work/ja.pdf" >"$work/node.log" 2>&1
emit pdf exit "$?"
emit pdf size "$(stat -c %s "$work/ja.pdf" 2>/dev/null || echo 0)"
emit pdf log "$(tail -c 2000 "$work/node.log" | tr '\t\n' '  ')"
emit pdf fonts "$(pdffonts "$work/ja.pdf" 2>/dev/null | tail -n +3 | awk '{print $1}' | sort -u | paste -sd, -)"

# 受け入れ条件 6。利用者が置き場の直下にファイルを作れる
touch '__PATH__/.devbase-touch-probe' 2>/dev/null
emit touch exit "$?"

# 受け入れ条件 8
dpkg -s chromium-browser >/dev/null 2>&1
emit dpkg chromium-browser "$?"
for pkg in __PACKAGES__; do
  dpkg -s "$pkg" >/dev/null 2>&1
  emit dpkg "$pkg" "$?"
done

# #402 の受け入れ条件 9。追加の太さのフォントのパッケージと、Noto Sans CJK JP の太さ
dpkg -s __EXTRA_PACKAGE__ >/dev/null 2>&1
emit extra package "$?"
emit extra styles "$(fc-list ':family=Noto Sans CJK JP' style | tr '\n' ',')"

# 受け入れ条件 9 (後半)。Chromium を起動した後でも ~/.cache の置き場が無い
if [ -e "$HOME/.cache/ms-playwright" ]; then emit home-cache '' present; else emit home-cache '' absent; fi
exit 0
"""


def _docker_unavailable() -> str | None:
    """Docker が使えない / イメージが無い理由。使えるなら None"""
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
    """1 回の docker run (--network none) で受け入れ条件 5〜9 の値をまとめて採る"""
    reason = _docker_unavailable()
    if reason:
        pytest.skip(reason)
    script = (_PROBE.replace("__PATH__", BROWSERS_PATH)
              .replace("__PACKAGES__", " ".join(DEPENDENCY_PACKAGES))
              .replace("__EXTRA_PACKAGE__", EXTRA_WEIGHTS_PACKAGE))
    # ここは包まない。Docker もイメージもある状態で probe がタイムアウトした・起動に失敗したのは
    # 「壊れている」ため、例外のまま失敗として知らせる
    out = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--user", USERNAME,
         "--entrypoint", "/bin/bash", IMAGE, "-c", script],
        capture_output=True, text=True, timeout=300, env=host_docker_env())
    assert out.returncode == 0, f"probe が失敗した (exit={out.returncode}):\n{out.stderr}"

    collected: dict[str, dict[str, str]] = {}
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        kind, key, value = line.split("\t", 2)
        collected.setdefault(kind, {})[key] = value

    if collected["browsers-dir"][""] == "absent" and not IMAGE_IS_EXPLICIT:
        pytest.skip(f"{IMAGE} に {BROWSERS_PATH} が無い。{BUILD_HINT}")
    return collected


def test_the_browsers_path_exists_in_the_image(probe):
    """置き場がある。明示したタグで置き場を消して建てたときに、原因をここで名指す"""
    assert probe["browsers-dir"][""] == "present", f"{IMAGE} に {BROWSERS_PATH} が無い"


def test_chromium_renders_a_japanese_page_to_pdf_without_network(probe):
    """受け入れ条件 5。--network none で起動するため、取得は起きない"""
    pdf = probe["pdf"]
    assert pdf["exit"] == "0", f"Chromium の起動か PDF の出力が失敗した: {pdf['log']}"
    assert int(pdf["size"]) > 0


def test_the_user_can_write_into_the_browsers_path(probe):
    """受け入れ条件 6。実行時の道具が別の版の Chromium を同じ置き場へ取得できる"""
    assert probe["touch"]["exit"] == "0"


def test_the_pdf_embeds_noto_sans_cjk_jp_and_not_wenquanyi(probe):
    """受け入れ条件 7。#161 の描画の既定が Chromium の PDF でも効く"""
    fonts = probe["pdf"]["fonts"]
    assert "NotoSansCJKjp" in fonts, f"埋め込まれた書体: {fonts!r}"
    assert "WenQuanYi" not in fonts, f"埋め込まれた書体: {fonts!r}"


def test_the_snap_stub_is_not_installed(probe):
    """受け入れ条件 8 (前半)"""
    assert probe["dpkg"]["chromium-browser"] != "0"


@pytest.mark.parametrize("package", DEPENDENCY_PACKAGES)
def test_the_browser_dependency_packages_remain(probe, package):
    """受け入れ条件 8 (後半)。--with-deps が入れるパッケージは減らない"""
    assert probe["dpkg"][package] == "0"


def test_the_env_points_at_the_browsers_path(probe):
    """受け入れ条件 9 (前半)"""
    assert probe["env"]["PLAYWRIGHT_BROWSERS_PATH"] == BROWSERS_PATH


def test_the_home_cache_has_no_browsers(probe):
    """受け入れ条件 9 (後半)。置き場が ~/.cache に戻っていない"""
    assert probe["home-cache"][""] == "absent"


def test_the_extra_weights_package_is_installed(probe):
    """#402 の受け入れ条件 9 (前半)"""
    assert probe["extra"]["package"] == "0", f"{EXTRA_WEIGHTS_PACKAGE} が入っていない"


@pytest.mark.parametrize("weight", EXTRA_WEIGHTS)
def test_noto_sans_cjk_jp_has_the_extra_weight(probe, weight):
    """#402 の受け入れ条件 9 (後半)。fc-list の style は別名をコンマで連ねるため、語で見る"""
    styles = {s.strip() for s in probe["extra"]["styles"].replace(":style=", ",").split(",")}
    assert weight in styles, f"Noto Sans CJK JP の style: {probe['extra']['styles']!r}"
