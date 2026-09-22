"""建てた base イメージの中の解決先 (PLAN63 / #161, #160)

Dockerfile の文字列検査 (``test_base_dockerfile_fonts.py``) では足りない。固定したいのは
「``COPY`` の行があること」ではなく「``fc-match sans-serif`` が日本語を返すこと」で、後者は
文字列からは分からない。設計の決定 2 のような壊れ方 (``lang`` の条件が広すぎて欧文の指定から
書体を奪う) は、Dockerfile を読んでも見えない。

**Docker が無い / イメージが無い / イメージが古いときは skip する。** この変更より前に建てた
``devbase-base:latest`` を持っている人が ``pytest tests/`` で全員赤くなるのを避けるためで、
古いイメージを「失敗」として知らせると、赤の意味が「壊れている」と「イメージが古い」で混ざる。

**``docker run`` はセッションで 1 回に抑える。** ``fc-match`` を 28 回別々に走らせると、
コンテナの起動だけで数十秒かかる。
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

IMAGE = "devbase-base:latest"
BUILD_HINT = f"`devbase build base --no-cache` で {IMAGE} を建て直すと、この検査が効く"

# イメージの中に /etc/fonts/local.conf が無い (= この変更より前のイメージ) ときの終了コード
STALE_IMAGE_EXIT = 90

# 設計「解決先の表」の「変更後」の列。26 行のうち Meiryo / Yu Gothic / MS PGothic の行を
# 3 つへ開いてある
EXPECTED_MATCHES = {
    # 総称ファミリ (受け入れ条件 1・2・3)
    "sans-serif": "Noto Sans CJK JP",
    "sans-serif:lang=ja": "Noto Sans CJK JP",
    "sans": "Noto Sans CJK JP",
    "serif": "Noto Serif CJK JP",
    "monospace": "Noto Sans Mono CJK JP",
    # 日本語環境でよく指定される書体名と、イメージに無い書体名 (受け入れ条件 4)
    "Noto Sans JP": "Noto Sans CJK JP",
    "Meiryo": "Noto Sans CJK JP",
    "Yu Gothic": "Noto Sans CJK JP",
    "MS PGothic": "Noto Sans CJK JP",
    "Zen Kaku Gothic New": "Noto Sans CJK JP",
    # 欧文は壊れない (受け入れ条件 5)
    "Arial": "Liberation Sans",
    "Times New Roman": "Liberation Serif",
    "Courier New": "Liberation Mono",
    # 欧文の metric 互換が直る (受け入れ条件 11。変更前はどちらも WenQuanYi Zen Hei)
    "Calibri": "Carlito",
    "Cambria": "Caladea",
    # 他言語は壊れない。様式 (sans / serif / 等幅) も保つ (受け入れ条件 6)
    "sans-serif:lang=zh-cn": "Noto Sans CJK SC",
    "sans:lang=zh-cn": "Noto Sans CJK SC",
    "serif:lang=zh-cn": "Noto Serif CJK SC",
    "monospace:lang=zh-cn": "Noto Sans Mono CJK SC",
    "sans-serif:lang=ko": "Noto Sans CJK KR",
    "sans:lang=ko": "Noto Sans CJK KR",
    "serif:lang=ko": "Noto Serif CJK KR",
    "monospace:lang=ko": "Noto Sans Mono CJK KR",
    # 欧文の指定は言語で変わらない。決定 2 の壊れ方を捕まえるのはこの 3 行である
    "Arial:lang=zh-cn": "Liberation Sans",
    "Times New Roman:lang=zh-cn": "Liberation Serif",
    "Arial:lang=ko": "Liberation Sans",
    # 実在する書体を名指しした指定は奪わない
    "WenQuanYi Zen Hei": "WenQuanYi Zen Hei",
    "IPAPGothic": "IPAPGothic",
}

# 受け入れ条件 9・12
EXPECTED_COMMANDS = {
    "pdftoppm": True,
    "pdfinfo": True,
    "pdffonts": True,
    "pdftocairo": True,
    "uv": True,
    "soffice": False,
    "libreoffice": False,
    "pip": False,
    "pip3": False,
}

_PROBE = r"""
set -u
# この変更より前に建てたイメージなら、測らずに抜ける
test -f /etc/fonts/local.conf || exit {stale}

# ~/.bashrc が入れている PATH の行と同じ状態にする。uv / claude / agy は
# $HOME/.local/bin にあり、bash -c は対話でも login でもないため .bashrc を読まない
export PATH="$HOME/.local/bin:$PATH"

first_family() {{
  # fc-match の既定の出力は `<file>: "<family>" "<style>"`。
  # 別名を持つ書体は family がコンマで連なるため、先頭だけを採る
  sed -n 's/^[^:]*: "\([^"]*\)".*/\1/p' | cut -d, -f1
}}

while IFS= read -r pattern; do
  [ -n "$pattern" ] || continue
  printf 'fc-match\t%s\t%s\n' "$pattern" "$(fc-match "$pattern" | first_family)"
done <<'PATTERNS'
{patterns}
PATTERNS

# 受け入れ条件 2 は fc-match の 1 件だけでなく、-s の 1 件目も見る
printf 'fc-match-s-first\t%s\t%s\n' 'sans-serif:lang=ja' \
  "$(fc-match -s 'sans-serif:lang=ja' | head -1 | first_family)"

for c in {commands}; do
  printf 'command\t%s\t%s\n' "$c" "$(command -v "$c" 2>/dev/null || true)"
done

if python3 -c 'import PIL, defusedxml, lxml' >/dev/null 2>&1; then
  printf 'python-imports\t\tok\n'
else
  printf 'python-imports\t\tng\n'
fi
"""


def _docker_unavailable() -> str | None:
    """Docker が使えない / イメージが無い理由。使えるなら None"""
    if shutil.which("docker") is None:
        return "docker が PATH に無い"
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=30, check=True)
    except (subprocess.SubprocessError, OSError):
        return "docker daemon へ繋がらない"
    try:
        out = subprocess.run(["docker", "image", "inspect", IMAGE],
                             capture_output=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return f"{IMAGE} を調べられない"
    if out.returncode != 0:
        return f"{IMAGE} が無い。{BUILD_HINT}"
    return None


@pytest.fixture(scope="session")
def probe() -> dict[str, dict[str, str]]:
    """1 回の docker run で fc-match・command -v・python3 の import をまとめて採る"""
    reason = _docker_unavailable()
    if reason:
        pytest.skip(reason)
    script = _PROBE.format(
        stale=STALE_IMAGE_EXIT,
        patterns="\n".join(EXPECTED_MATCHES),
        commands=" ".join(EXPECTED_COMMANDS),
    )
    # ここは包まない。skip してよいのは Docker が使えない・イメージが無い (直前の判定) と
    # イメージが古い (STALE_IMAGE_EXIT) の 3 つだけで、Docker もイメージもある状態で probe が
    # タイムアウトした・起動に失敗したのは「壊れている」ため、例外のまま失敗として知らせる
    out = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "/bin/bash", IMAGE, "-c", script],
        capture_output=True, text=True, timeout=300)
    if out.returncode == STALE_IMAGE_EXIT:
        pytest.skip(f"{IMAGE} に /etc/fonts/local.conf が無い (この変更より前のイメージ)。{BUILD_HINT}")
    assert out.returncode == 0, f"probe が失敗した (exit={out.returncode}):\n{out.stderr}"

    collected: dict[str, dict[str, str]] = {}
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        kind, key, value = line.split("\t", 2)
        collected.setdefault(kind, {})[key] = value
    return collected


@pytest.mark.parametrize("pattern,expected", sorted(EXPECTED_MATCHES.items()))
def test_fc_match_resolves_as_designed(probe, pattern, expected):
    assert probe["fc-match"][pattern] == expected


def test_the_first_of_the_sorted_japanese_list_is_the_jp_face(probe):
    """受け入れ条件 2。変更前は WenQuanYi Zen Hei -> IPAPGothic -> Loma の順だった"""
    assert probe["fc-match-s-first"]["sans-serif:lang=ja"] == "Noto Sans CJK JP"


@pytest.mark.parametrize("command,should_exist", sorted(EXPECTED_COMMANDS.items()))
def test_the_expected_commands_are_present_or_absent(probe, command, should_exist):
    """受け入れ条件 9・12。poppler は入り、LibreOffice と pip は入らない"""
    found = bool(probe["command"][command])
    assert found is should_exist


def test_the_document_python_modules_import(probe):
    """受け入れ条件 10"""
    assert probe["python-imports"][""] == "ok"
