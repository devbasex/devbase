"""ブラウザの派生イメージ ``containers/browser`` の形 (#402)

Docker を起動せず、``containers/browser/Dockerfile`` と ``compose.yml`` の形だけを固定する。
建てたイメージの振る舞いは ``test_browser_image.py`` が固定する。

- 最初の ``FROM`` が ``devbase-base:latest`` (``devbase build`` が base を先に建てる経路に乗る)
- 利用者の ``RUN`` で ``npx playwright install --with-deps chromium`` を 1 回打ち、その片付けは
  ブラウザの置き場とその親の ``/opt`` を消さず、``/var/lib/apt/lists`` を消す
- ``fonts-noto-cjk-extra`` を apt で入れ、``fc-cache -f`` は 1 度だけ、apt と Playwright の ``RUN`` より後
- 置き場と ``ENV PLAYWRIGHT_BROWSERS_PATH`` は base から継ぎ、宣言し直さない
"""

from __future__ import annotations

import re
from pathlib import Path

from .dockerfile_parse import Instruction, parse

BROWSER_DIR = Path(__file__).resolve().parents[2] / "containers" / "browser"
DOCKERFILE = BROWSER_DIR / "Dockerfile"
COMPOSE = BROWSER_DIR / "compose.yml"
BROWSERS_PATH = "/opt/ms-playwright"
INSTALL = "npx playwright install --with-deps chromium"


def _instructions() -> list[Instruction]:
    return parse(DOCKERFILE.read_text())


def _run_indexes(instructions: list[Instruction], needle: str) -> list[int]:
    return [i for i, ins in enumerate(instructions) if ins.keyword == "RUN" and needle in ins.args]


def _rm_targets(run: str) -> list[str]:
    targets: list[str] = []
    for command in re.split(r";|&&", run):
        words = command.split()
        if "rm" not in words:
            continue
        targets += [w for w in words[words.index("rm") + 1:] if not w.startswith("-")]
    return targets


def _user_at(instructions: list[Instruction], index: int) -> str:
    """``index`` 番目の命令を実行する利用者 (直前の ``USER``。無ければ base の最後の ubuntu)"""
    user = "ubuntu"
    for ins in instructions[:index]:
        if ins.keyword == "USER":
            user = ins.args
    return user


# --- I9 ---


def test_the_first_from_is_devbase_base():
    froms = [ins.args for ins in _instructions() if ins.keyword == "FROM"]
    assert froms and froms[0] == "devbase-base:latest"


def test_compose_names_the_image_devbase_browser():
    text = COMPOSE.read_text()
    assert re.search(r"^\s+image:\s*devbase-browser:latest\s*$", text, flags=re.MULTILINE)
    assert re.search(r"^\s+context:\s*\.\s*$", text, flags=re.MULTILINE)


# --- I10 ---


def test_the_playwright_install_runs_once_as_the_user():
    instructions = _instructions()
    installs = _run_indexes(instructions, "playwright install")
    assert len(installs) == 1, "Playwright の取得の RUN が 1 つでない"
    assert INSTALL in instructions[installs[0]].args
    assert _user_at(instructions, installs[0]) == "ubuntu", "Playwright の取得を利用者 ubuntu で打っていない"


def test_the_cleanup_keeps_the_browsers_path_and_removes_apt_lists():
    instructions = _instructions()
    run = instructions[_run_indexes(instructions, "playwright install")[0]].args
    targets = _rm_targets(run)
    assert targets, "片付けの rm -rf が見つからない"
    for target in targets:
        cleaned = target.rstrip("/*").rstrip("/")
        assert cleaned not in {"/opt", BROWSERS_PATH, "$PLAYWRIGHT_BROWSERS_PATH",
                               "${PLAYWRIGHT_BROWSERS_PATH}"}, target
        assert not target.startswith(BROWSERS_PATH), target
    assert "/var/lib/apt/lists/*" in targets, "片付けが apt の一覧を消さない"


def test_the_browsers_path_is_inherited_not_redeclared():
    for ins in _instructions():
        assert not (ins.keyword == "ENV" and "PLAYWRIGHT_BROWSERS_PATH" in ins.args), \
            "置き場は base から継ぐ。ENV を宣言し直している"


# --- I11 ---


def test_the_extra_weights_are_installed_with_apt():
    applied = [ins.args for ins in _instructions()
               if ins.keyword == "RUN" and re.search(r"apt-get install[^;&]*\bfonts-noto-cjk-extra\b", ins.args)]
    assert len(applied) == 1, "fonts-noto-cjk-extra を apt で入れる RUN が 1 つでない"
    assert "/var/lib/apt/lists/*" in _rm_targets(applied[0]), "apt の RUN が apt の一覧を消さない"


def test_fc_cache_runs_once_after_apt_and_playwright():
    instructions = _instructions()
    caches = _run_indexes(instructions, "fc-cache -f")
    assert len(caches) == 1, "fc-cache -f が 1 度でない"
    apt = _run_indexes(instructions, "fonts-noto-cjk-extra")
    playwright = _run_indexes(instructions, "playwright install")
    assert apt and playwright
    assert max(apt + playwright) < caches[0], "fc-cache が apt か Playwright の RUN より前にある"
