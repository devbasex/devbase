"""base の Playwright の Chromium の置き場 (#220)

Docker を起動せず、``containers/base/Dockerfile`` の形だけを固定する。

- ブラウザの置き場は ``/opt/ms-playwright``。``ENV PLAYWRIGHT_BROWSERS_PATH`` で示し、
  ``npx playwright install`` より前に宣言する
- base の片付けはブラウザの置き場もその親の ``/opt`` も消さない
- base は ``chromium-browser`` (snap スタブ) を入れない
- システムの Chrome は両アーキとも入れない (#401)
"""

from __future__ import annotations

import re
from pathlib import Path

from .dockerfile_parse import Instruction, parse

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE = REPO_ROOT / "containers" / "base" / "Dockerfile"
BROWSERS_PATH = "/opt/ms-playwright"
ENV_LINE = f"PLAYWRIGHT_BROWSERS_PATH={BROWSERS_PATH}"


def _instructions(path: Path) -> list[Instruction]:
    return parse(path.read_text())


def _env_indexes(instructions: list[Instruction]) -> list[int]:
    return [i for i, ins in enumerate(instructions)
            if ins.keyword == "ENV" and ins.args.startswith("PLAYWRIGHT_BROWSERS_PATH")]


def _install_indexes(instructions: list[Instruction]) -> list[int]:
    return [i for i, ins in enumerate(instructions)
            if ins.keyword == "RUN" and "npx playwright install" in ins.args]


def test_browsers_path_is_declared_once_before_the_install():
    instructions = _instructions(BASE)
    envs = _env_indexes(instructions)
    installs = _install_indexes(instructions)
    assert len(envs) == 1 and installs
    assert instructions[envs[0]].args == ENV_LINE
    assert envs[0] < min(installs)


def _base_install_run() -> str:
    instructions = _instructions(BASE)
    installs = _install_indexes(instructions)
    assert len(installs) == 1
    return instructions[installs[0]].args


def _rm_targets(run: str) -> list[str]:
    targets: list[str] = []
    for command in re.split(r";|&&", run):
        words = command.split()
        if "rm" not in words:
            continue
        targets += [w for w in words[words.index("rm") + 1:] if not w.startswith("-")]
    return targets


def test_base_install_keeps_with_deps():
    assert "npx playwright install --with-deps chromium" in _base_install_run()


def test_the_cleanup_does_not_remove_the_browsers_path():
    targets = _rm_targets(_base_install_run())
    assert targets, "片付けの rm -rf が見つからない"
    for target in targets:
        cleaned = target.rstrip("/*").rstrip("/")
        assert cleaned not in {"/opt", BROWSERS_PATH, "$PLAYWRIGHT_BROWSERS_PATH",
                               "${PLAYWRIGHT_BROWSERS_PATH}"}, target
        assert not target.startswith(f"{BROWSERS_PATH}"), target


def test_the_browsers_path_is_made_like_the_npm_prefix_before_the_install():
    """決定 1: 置き場は npm のグローバル領域と同じ root の RUN で、$USERNAME:npm・2775 で作る"""
    instructions = _instructions(BASE)
    made = re.compile(r'install -d -m 2775 -o "\$USERNAME" -g npm "\$PLAYWRIGHT_BROWSERS_PATH"')
    runs = [i for i, ins in enumerate(instructions) if ins.keyword == "RUN" and made.search(ins.args)]
    assert len(runs) == 1, "ブラウザの置き場を $USERNAME:npm・2775 で作る RUN が 1 つでない"
    assert "NPM_CONFIG_PREFIX" in instructions[runs[0]].args, "npm のグローバル領域と同じ RUN で作っていない"
    assert _env_indexes(instructions)[0] < runs[0] < _install_indexes(instructions)[0]


def test_base_does_not_install_chromium_browser():
    installs = [ins.args for ins in _instructions(BASE)
                if ins.keyword == "RUN" and "apt-get install" in ins.args]
    assert installs
    for run in installs:
        assert not re.search(r"(?<![\w-])chromium-browser(?![\w-])", run)


def test_system_chrome_stays_amd64_only():
    """#401。システムの Chrome は両アーキとも入れない (Google の apt の取得元も足さない)"""
    text = BASE.read_text()
    assert not re.search(r"dl\.google\.com/linux", text), "Google の apt の取得元を足している"
    installs = [ins.args for ins in _instructions(BASE)
                if ins.keyword == "RUN" and "apt-get install" in ins.args]
    assert installs
    for run in installs:
        assert not re.search(r"google-chrome", run), "google-chrome を入れている"
