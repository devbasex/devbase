"""base イメージの日本語の描画と、文書を扱う軽量の道具の「形」 (PLAN63 / #161, #160)

Docker を起動せず、``containers/base/Dockerfile`` と ``containers/base/fonts-local.conf`` の
文字列と構造だけを固定する。実際の解決先 (``fc-match`` が何を返すか) は
``tests/containers/test_base_image_font_matching.py`` が建てたイメージの中で固定する。

ここで固定するのは次の 5 つである。

- 6 パッケージが **1 つ目の RUN の 1 回目の** ``apt-get install`` の一覧にある (設計の決定 4)
- ``COPY`` の宛先が ``/etc/fonts/local.conf`` であり ``conf.d/`` ではない (決定 1)
- ``fc-cache -f`` が 1 度だけ、``COPY`` より後、かつ Playwright の ``RUN`` より後にある (決定 5)
- ``fonts-local.conf`` が 4 つの ``<alias>`` と 9 つの ``<match>`` を持ち、言語の規則が
  総称ファミリの ``<test>`` を必ず伴う (決定 2。この ``<test>`` を省くと欧文の指定を奪う)
- 入れないもの (LibreOffice / pip) と、消さないもの (``fonts-wqy-zenhei``) が守られている
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2] / "containers" / "base"
DOCKERFILE = BASE_DIR / "Dockerfile"
FONTS_CONF = BASE_DIR / "fonts-local.conf"

# 設計「解決の経路」で決めた、総称ファミリの向き先
GENERIC_TO_JP = {
    "sans-serif": "Noto Sans CJK JP",
    "sans": "Noto Sans CJK JP",
    "serif": "Noto Serif CJK JP",
    "monospace": "Noto Sans Mono CJK JP",
}
# 言語を明示したときの向き先。**様式 (sans / serif / 等幅) を保つ**
LANG_RULES = {
    ("sans-serif", "zh-cn"): "Noto Sans CJK SC",
    ("sans", "zh-cn"): "Noto Sans CJK SC",
    ("serif", "zh-cn"): "Noto Serif CJK SC",
    ("monospace", "zh-cn"): "Noto Sans Mono CJK SC",
    ("sans-serif", "ko"): "Noto Sans CJK KR",
    ("sans", "ko"): "Noto Sans CJK KR",
    ("serif", "ko"): "Noto Serif CJK KR",
    ("monospace", "ko"): "Noto Sans Mono CJK KR",
}
FALLBACK_FAMILY = "Noto Sans CJK JP"
NEW_PACKAGES = (
    "poppler-utils",
    "python3-pil",
    "python3-defusedxml",
    "python3-lxml",
    "fonts-crosextra-carlito",
    "fonts-crosextra-caladea",
)


def _statements() -> str:
    """コメント行を除いた Dockerfile の本文 (説明の注記に assertion が反応しないように)"""
    return "\n".join(
        line for line in DOCKERFILE.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def _first_run_block() -> str:
    """1 つ目の RUN の 1 命令分 (行継続を含む) を取り出す"""
    lines = _statements().splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("RUN ")), None)
    assert start is not None, "RUN が 1 つも見つからない"
    block = []
    for line in lines[start:]:
        block.append(line)
        if not line.rstrip().endswith("\\"):
            break
    return "\n".join(block)


def _first_apt_install(block: str) -> str:
    """1 つ目の RUN の**1 回目**の apt-get install の一覧だけを取り出す

    1 つ目の RUN は apt-get install を 2 回呼ぶ。1 回目は Ubuntu の標準のアーカイブから、
    2 回目は後から足したリポジトリ (docker-ce / terraform / gh / nodejs) からである。
    """
    calls = [m.start() for m in re.finditer(r"apt-get install", block)]
    assert len(calls) >= 2, "1 つ目の RUN に apt-get install が 2 回無い"
    return block[calls[0]:calls[1]]


# ---------------------------------------------------------------------------
# Dockerfile: 6 パッケージ (#160)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("package", NEW_PACKAGES)
def test_the_six_packages_are_in_the_first_apt_install(package):
    """6 つとも標準のアーカイブにあるので、1 回目の一覧へ置く (決定 4)"""
    assert re.search(rf"(?<![\w-]){re.escape(package)}(?![\w-])", _first_apt_install(_first_run_block()))


def test_no_extra_run_is_added_for_the_six_packages():
    """新しい RUN を立てない (決定 4)。層を増やさず、apt-get update をもう 1 回走らせない"""
    text = _statements()
    runs = [line for line in text.splitlines() if line.startswith("RUN ")]
    # 6 パッケージを入れるためだけの RUN が増えていないこと
    for run in runs[1:]:
        assert "poppler-utils" not in run
        assert "fonts-crosextra" not in run
    assert text.count("apt-get update") == 2, "apt-get update の回数が変わっている"


def test_libreoffice_and_pip_are_not_installed():
    """前提 3・8。base には LibreOffice も pip も入れない"""
    text = _statements().lower()
    assert "libreoffice" not in text
    assert "soffice" not in text
    assert not re.search(r"\bpython3?-pip\b", text)
    assert "get-pip" not in text


def test_wqy_zenhei_is_not_removed():
    """前提 2。削除しても日本語にはならず、中国語のページを豆腐にするだけになる"""
    text = _statements()
    assert not re.search(
        r"(apt-get\s+(remove|purge)|apt\s+(remove|purge)|dpkg\s+(-r|--purge))"
        r"[^;\n]*fonts-wqy",
        text,
    )


def test_lang_is_not_set_as_an_env():
    """前提 7。ENV LANG を置くと、影響がフォントの外 (出力・ソート順・日付) へ出る"""
    assert not re.search(r"^ENV LANG(=|\s)", _statements(), flags=re.MULTILINE)


# ---------------------------------------------------------------------------
# Dockerfile: 置き場所と fc-cache (#161)
# ---------------------------------------------------------------------------

def test_the_conf_is_copied_to_etc_fonts_local_conf():
    """決定 1。conf.d/ へ置くと <alias><prefer> が効かない"""
    assert re.search(
        r"^COPY --chmod=0644 fonts-local\.conf /etc/fonts/local\.conf$",
        _statements(),
        flags=re.MULTILINE,
    )


def test_nothing_is_copied_into_fonts_conf_d():
    assert not re.search(r"^COPY\b.*\s/etc/fonts/conf\.d/", _statements(), flags=re.MULTILINE)


def test_fc_cache_runs_once_and_after_the_copy():
    text = _statements()
    assert len(re.findall(r"fc-cache -f", text)) == 1
    assert text.index("COPY --chmod=0644 fonts-local.conf") < text.index("fc-cache -f")


def test_fc_cache_runs_after_playwright_installs_its_fonts():
    """決定 5。--with-deps が後から入れる書体を知らないキャッシュを残さない"""
    text = _statements()
    assert text.index("npx playwright install") < text.index("fc-cache -f")


# ---------------------------------------------------------------------------
# fonts-local.conf の構造
# ---------------------------------------------------------------------------

def _root() -> ET.Element:
    root = ET.fromstring(FONTS_CONF.read_text())
    assert root.tag == "fontconfig"
    return root


def test_the_conf_is_well_formed_xml():
    _root()


def test_the_four_generic_families_are_aliased_to_the_jp_faces():
    aliases = _root().findall("alias")
    got = {}
    for alias in aliases:
        family = alias.findtext("family")
        preferred = [f.text for f in alias.findall("prefer/family")]
        assert preferred, f"{family} の <alias> に <prefer><family> が無い"
        got[family] = preferred[0]
    assert got == GENERIC_TO_JP


def test_there_are_nine_matches_one_fallback_and_eight_language_rules():
    matches = _root().findall("match")
    assert len(matches) == 9
    fallbacks = [m for m in matches if not m.findall("test")]
    assert len(fallbacks) == 1


def test_the_fallback_is_a_weakly_bound_append():
    """決定 3。弱い結合なので、実在する指定 (Arial など) を妨げない"""
    fallback = next(m for m in _root().findall("match") if not m.findall("test"))
    assert fallback.get("target") == "pattern"
    edits = fallback.findall("edit")
    assert len(edits) == 1
    edit = edits[0]
    assert edit.get("name") == "family"
    assert edit.get("mode") == "append"
    assert edit.get("binding") == "weak"
    assert edit.findtext("string") == FALLBACK_FAMILY


def test_every_language_rule_also_tests_the_generic_family():
    """決定 2。<test name="family"> を省くと Arial:lang=zh-cn から Liberation Sans を奪う"""
    got = {}
    for match in _root().findall("match"):
        tests = match.findall("test")
        if not tests:
            continue
        assert match.get("target") == "pattern"
        names = [t.get("name") for t in tests]
        assert "family" in names, "言語の規則が総称ファミリの <test> を持たない"
        assert "lang" in names
        family = next(t.findtext("string") for t in tests if t.get("name") == "family")
        lang_test = next(t for t in tests if t.get("name") == "lang")
        assert lang_test.get("compare") == "contains"
        edits = match.findall("edit")
        assert len(edits) == 1
        assert edits[0].get("name") == "family"
        assert edits[0].get("mode") == "prepend"
        assert edits[0].get("binding") == "strong"
        got[(family, lang_test.findtext("string"))] = edits[0].findtext("string")
    assert got == LANG_RULES


def test_the_leading_comment_explains_why_the_location_cannot_move():
    """受け入れ条件 8。置き場所を動かせない理由と、その実測を先頭のコメントに残す"""
    text = FONTS_CONF.read_text()
    head = text[:text.index("<fontconfig>")]
    for token in ("51-local.conf", "conf.d", "99", "/etc/fonts/local.conf", "#161"):
        assert token in head, f"先頭のコメントに {token} が無い"
