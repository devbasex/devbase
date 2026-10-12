"""派生イメージに言語サーバを専用の置き場へ入れる形

Docker を起動せず、``general``・``php``・``php85`` の Dockerfile の文字列だけを固定する。
言語サーバは ``npm install --prefix /opt/language-servers`` で root が入れ、PATH へ出すのは
``bin`` に張る 3 つのコマンドだけにする。base の npm のグローバル領域（``tsc``）には触れず、
版は厳密に固定して 3 つのイメージで揃える。Serena の uv のキャッシュは温めない。
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from .dockerfile_parse import Instruction, parse

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGES = ("general", "php", "php85")
PLACE = "/opt/language-servers"
PACKAGES = {"pyright", "typescript-language-server", "typescript", "intelephense"}
COMMANDS = {"pyright-langserver", "typescript-language-server", "intelephense"}
EXACT = re.compile(r"\d+\.\d+\.\d+")


def _instructions(image: str) -> list[Instruction]:
    return parse((REPO_ROOT / "containers" / image / "Dockerfile").read_text())


def _install_index(instructions: list[Instruction]) -> int:
    """言語サーバを置き場へ入れる RUN の位置"""
    found = [
        i for i, ins in enumerate(instructions)
        if ins.keyword == "RUN" and f"--prefix {PLACE}" in ins.args
    ]
    assert len(found) == 1, f"言語サーバを入れる RUN が 1 つではない: {len(found)}"
    return found[0]


def _install_args(run: str) -> list[str]:
    """``npm install --prefix`` の 1 文の語"""
    statement = next(s for s in run.split(";") if f"--prefix {PLACE}" in s)
    words = shlex.split(statement)
    assert words[:2] == ["npm", "install"], statement
    return words[2:]


def _package_set(image: str) -> dict[str, str]:
    """``npm install --prefix`` が入れるパッケージの名前と版"""
    instructions = _instructions(image)
    run = instructions[_install_index(instructions)].args
    specs = [w for w in _install_args(run) if not w.startswith("-") and w != PLACE]
    packages: dict[str, str] = {}
    for spec in specs:
        name, at, version = spec.rpartition("@")
        assert at, f"{image}: 版の無い指定 {spec}"
        packages[name] = version
    return packages


@pytest.mark.parametrize("image", IMAGES)
def test_installs_the_four_packages_into_the_place(image):
    assert set(_package_set(image)) == PACKAGES


@pytest.mark.parametrize("image", IMAGES)
def test_versions_are_exact_and_typescript_is_5(image):
    packages = _package_set(image)
    loose = {n: v for n, v in packages.items() if not EXACT.fullmatch(v)}
    assert not loose, f"{image}: 厳密でない版 {loose}"
    assert packages["typescript"].startswith("5."), f"{image}: typescript {packages['typescript']}"


def test_package_sets_match_across_images():
    sets = {image: _package_set(image) for image in IMAGES}
    first = sets[IMAGES[0]]
    differ = {image: s for image, s in sets.items() if s != first}
    assert not differ, f"{IMAGES[0]} の組 {first} と違う: {differ}"


@pytest.mark.parametrize("image", IMAGES)
def test_does_not_touch_the_global_npm_and_links_only_three_commands(image):
    instructions = _instructions(image)
    run = instructions[_install_index(instructions)].args
    assert not re.search(r"npm\s+(i|install)\b[^;]*\s(-g|--global)\b", run)
    links = re.findall(r"ln\s+-s\S*\s+([^\s;]+)\s+([^\s;]+)", run)
    assert {Path(dest).name for _, dest in links} == COMMANDS
    for src, dest in links:
        assert Path(dest).parent == Path(PLACE) / "bin", dest
        assert src == f"{PLACE}/node_modules/.bin/{Path(dest).name}", src


@pytest.mark.parametrize("image", IMAGES)
def test_path_has_only_the_bin_of_the_place(image):
    paths = [
        ins.args for ins in _instructions(image)
        if ins.keyword == "ENV" and ins.args.startswith("PATH=")
    ]
    assert any(f"{PLACE}/bin:" in p for p in paths), paths
    assert not any("node_modules" in p for p in paths), paths


@pytest.mark.parametrize("image", IMAGES)
def test_does_not_warm_the_uv_cache_for_serena(image):
    text = (REPO_ROOT / "containers" / image / "Dockerfile").read_text()
    body = "\n".join(ins.args for ins in parse(text))
    assert "UV_CACHE_DIR" not in body
    assert "serena-agent" not in body


@pytest.mark.parametrize("image", IMAGES)
def test_installs_as_root_drops_npm_cache_and_returns_to_ubuntu(image):
    instructions = _instructions(image)
    index = _install_index(instructions)
    users = [(i, ins.args) for i, ins in enumerate(instructions) if ins.keyword == "USER"]
    before = [u for i, u in users if i < index]
    after = [u for i, u in users if i > index]
    assert before and before[-1] == "root", before
    assert after and after[-1] == "ubuntu", after
    assert "rm -rf /root/.npm" in instructions[index].args
