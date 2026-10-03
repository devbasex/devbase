"""base はブラウザを持たず、空のブラウザの置き場だけを作る (#220, #402)

Docker を起動せず、``containers/base/Dockerfile`` の形だけを固定する。

- base は Playwright の Chromium を取得しない (``npx playwright install`` が無い)。
  ``fonts-noto-cjk-extra``・``terraform``・HashiCorp の apt の取得元も無い (#402)
- ``npm i -g`` の一覧に ``@playwright/test`` は残る (#402)
- ブラウザの置き場は ``/opt/ms-playwright``。``ENV PLAYWRIGHT_BROWSERS_PATH`` で 1 度だけ示し、
  置き場を ``$USERNAME:npm``・2775 で作る root の ``RUN`` より前に宣言する (#220, #402)
- base は ``chromium-browser`` (snap スタブ) を入れない
- システムの Chrome は両アーキとも入れない (#401)
- ``containers/bi-tools/Dockerfile`` の先頭のコメントは、base に含まれるものとして
  ``terraform`` を挙げない (#402)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from .dockerfile_parse import Instruction, parse

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE = REPO_ROOT / "containers" / "base" / "Dockerfile"
BI_TOOLS = REPO_ROOT / "containers" / "bi-tools" / "Dockerfile"
BROWSERS_PATH = "/opt/ms-playwright"
ENV_LINE = f"PLAYWRIGHT_BROWSERS_PATH={BROWSERS_PATH}"


def _instructions(path: Path) -> list[Instruction]:
    return parse(path.read_text())


def _runs(path: Path) -> list[str]:
    return [ins.args for ins in _instructions(path) if ins.keyword == "RUN"]


def _apt_installs(path: Path) -> list[str]:
    """``apt-get install`` の一覧 (次の ``;`` か ``&&`` まで) を全部取り出す"""
    found = []
    for run in _runs(path):
        for command in re.split(r";|&&", run):
            if "apt-get install" in command:
                found.append(command)
    assert found, "apt-get install が見つからない"
    return found


def _word(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])")


def _env_indexes(instructions: list[Instruction]) -> list[int]:
    return [i for i, ins in enumerate(instructions)
            if ins.keyword == "ENV" and ins.args.startswith("PLAYWRIGHT_BROWSERS_PATH")]


# --- I1: base はブラウザ・追加の太さのフォント・terraform を持たない ---


def test_base_does_not_install_the_playwright_chromium():
    for run in _runs(BASE):
        assert "playwright install" not in run, "base の RUN が Playwright の Chromium を取得している"


@pytest.mark.parametrize("package", ["fonts-noto-cjk-extra", "terraform"])
def test_base_does_not_apt_install(package):
    for command in _apt_installs(BASE):
        assert not _word(package).search(command), f"base が {package} を apt で入れている"


def test_base_does_not_add_the_hashicorp_apt_source():
    for run in _runs(BASE):
        assert "apt.releases.hashicorp.com" not in run, "base が HashiCorp の apt の取得元を足している"


# --- I3: @playwright/test は残る ---


def test_playwright_test_stays_in_the_npm_globals():
    globals_ = [command for run in _runs(BASE) for command in re.split(r";|&&", run)
                if re.match(r"\s*npm (i|install) -g\b", command)]
    assert globals_, "npm のグローバルの install が無い"
    assert any("@playwright/test" in command.split() for command in globals_)


# --- I4: 空のブラウザの置き場 ---


def test_browsers_path_is_declared_once_before_it_is_made():
    """置き場は npm のグローバル領域と同じ root の RUN で、$USERNAME:npm・2775 で作る"""
    instructions = _instructions(BASE)
    envs = _env_indexes(instructions)
    assert len(envs) == 1, "ENV PLAYWRIGHT_BROWSERS_PATH が 1 つでない"
    assert instructions[envs[0]].args == ENV_LINE
    made = re.compile(r'install -d -m 2775 -o "\$USERNAME" -g npm "\$PLAYWRIGHT_BROWSERS_PATH"')
    runs = [i for i, ins in enumerate(instructions) if ins.keyword == "RUN" and made.search(ins.args)]
    assert len(runs) == 1, "ブラウザの置き場を $USERNAME:npm・2775 で作る RUN が 1 つでない"
    assert "NPM_CONFIG_PREFIX" in instructions[runs[0]].args, "npm のグローバル領域と同じ RUN で作っていない"
    assert envs[0] < runs[0]


# --- ブラウザの扱い (#220, #401) ---


def test_base_does_not_install_chromium_browser():
    for command in _apt_installs(BASE):
        assert not _word("chromium-browser").search(command)


def test_base_does_not_install_the_system_chrome():
    """#401。システムの Chrome は両アーキとも入れない (Google の apt の取得元も足さない)"""
    text = BASE.read_text()
    assert not re.search(r"dl\.google\.com/linux", text), "Google の apt の取得元を足している"
    for command in _apt_installs(BASE):
        assert not re.search(r"google-chrome", command), "google-chrome を入れている"


# --- I16: bi-tools の先頭のコメント ---


def test_bi_tools_comment_does_not_list_terraform_in_base():
    head = BI_TOOLS.read_text().split("\nFROM ", 1)[0]
    assert "terraform" not in head.lower(), "bi-tools の先頭のコメントが terraform を挙げている"
