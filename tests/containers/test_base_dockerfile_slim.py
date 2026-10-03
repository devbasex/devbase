"""base イメージから使う者の無い中身を外した形 (#400)

Docker を起動せず、``containers/base/Dockerfile`` と ``containers/base/dpkg-excludes`` を読んで固定する。
外した物 (dockerd・containerd・グローバルの aws-cdk-lib・root の uv・apt の一覧・文書・``dind``) を
戻すと、その名前を挙げて落ちる。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from .dockerfile_parse import Instruction, parse

BASE_DIR = Path(__file__).resolve().parents[2] / "containers" / "base"
DOCKERFILE = BASE_DIR / "Dockerfile"
DPKG_EXCLUDES = BASE_DIR / "dpkg-excludes"
UBUNTU_EXCLUDES = "/etc/dpkg/dpkg.cfg.d/excludes"
UV_INSTALLER = "astral.sh/uv/install.sh"


@pytest.fixture(scope="module")
def instructions() -> list[Instruction]:
    return parse(DOCKERFILE.read_text())


def _runs(instructions: list[Instruction]) -> list[tuple[int, Instruction]]:
    return [(i, ins) for i, ins in enumerate(instructions) if ins.keyword == "RUN"]


def _first_run(instructions: list[Instruction]) -> tuple[int, Instruction]:
    return _runs(instructions)[0]


def _user_index(instructions: list[Instruction]) -> int:
    """root でない利用者へ切り替える最初の USER の位置"""
    for i, ins in enumerate(instructions):
        if ins.keyword == "USER" and ins.args not in ("root", "0"):
            return i
    raise AssertionError("USER で利用者へ切り替えていない")


def _user_layer_run(instructions: list[Instruction]) -> Instruction:
    """USER の直後の、利用者の道具を入れる RUN"""
    user = _user_index(instructions)
    return next(ins for i, ins in _runs(instructions) if i > user)


def _words(args: str) -> set[str]:
    return set(re.split(r"[\s;\\]+", args))


def _commands(args: str) -> list[str]:
    return [c.strip() for c in re.split(r";|&&", args)]


# --- I1: dockerd と containerd を入れず、CLI・buildx・compose は入れる ---


@pytest.mark.parametrize("package", ["docker-ce", "containerd.io"])
def test_daemon_packages_are_not_installed(instructions, package):
    for _, run in _runs(instructions):
        assert package not in _words(run.args), f"{package} を apt で入れている"


@pytest.mark.parametrize("package", ["docker-ce-cli", "docker-buildx-plugin",
                                     "docker-compose-plugin"])
def test_docker_client_packages_are_installed(instructions, package):
    _, first = _first_run(instructions)
    assert package in _words(first.args), f"{package} を apt で入れていない"


# --- I2: グローバルの aws-cdk-lib を入れず、aws-cdk は入れる ---


def _npm_global_installs(instructions: list[Instruction]) -> list[set[str]]:
    found = []
    for _, run in _runs(instructions):
        for command in _commands(run.args):
            if re.match(r"npm (i|install) -g\b", command):
                found.append(_words(command))
    assert found, "npm のグローバルの install が無い"
    return found


def test_aws_cdk_lib_is_not_installed_globally(instructions):
    for words in _npm_global_installs(instructions):
        assert "aws-cdk-lib" not in words, "aws-cdk-lib を npm のグローバルへ入れている"


def test_aws_cdk_cli_is_installed_globally(instructions):
    assert any("aws-cdk" in words for words in _npm_global_installs(instructions)), \
        "aws-cdk を npm のグローバルへ入れていない"


# --- I3: uv は ubuntu の利用者にだけ入れ、PATH に /root/.local/bin を置かない ---


def test_uv_is_installed_only_for_user(instructions):
    user = _user_index(instructions)
    positions = [i for i, run in _runs(instructions) if UV_INSTALLER in run.args]
    assert positions, "uv のインストーラを呼ぶ RUN が無い"
    root_runs = [i for i in positions if i < user]
    assert not root_runs, "root の RUN で uv を入れている"


def test_path_has_no_root_local_bin(instructions):
    for ins in instructions:
        if ins.keyword == "ENV":
            assert "/root/.local/bin" not in ins.args, "ENV PATH に /root/.local/bin がある"


# --- I4: apt の一覧を取得する RUN は、同じ RUN で一覧を消す ---


def test_runs_fetching_apt_lists_remove_them(instructions):
    for i, run in _runs(instructions):
        fetches = "apt-get update" in run.args or "--with-deps" in run.args
        cached = re.search(r"--mount=type=cache,target=/var/lib/apt\b", run.args)
        if fetches and not cached:
            assert "/var/lib/apt/lists/*" in run.args, \
                f"{i} 番目の命令 (RUN) が apt の一覧を残す: {run.args[:80]}"


# --- I5: 文書の除外の設定と、残る文書の削除 ---


def _excludes_copy(instructions: list[Instruction]) -> tuple[int, str]:
    for i, ins in enumerate(instructions):
        if ins.keyword == "COPY" and ins.args.split()[-2:-1] == ["dpkg-excludes"]:
            return i, ins.args.split()[-1]
    raise AssertionError("dpkg-excludes を COPY していない")


def test_excludes_are_copied_before_first_run(instructions):
    copy_at, _ = _excludes_copy(instructions)
    first_at, _ = _first_run(instructions)
    assert copy_at < first_at, "文書の除外の設定が最初の RUN より後にある"


def test_excludes_are_read_after_ubuntu_excludes(instructions):
    _, dest = _excludes_copy(instructions)
    directory, _, name = dest.rpartition("/")
    assert directory == "/etc/dpkg/dpkg.cfg.d"
    assert name > UBUNTU_EXCLUDES.rpartition("/")[2], \
        f"{name} は Ubuntu の excludes より先に読まれる"


def _rules() -> list[str]:
    return [line.strip() for line in DPKG_EXCLUDES.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def test_doc_is_excluded_except_copyright():
    rules = _rules()
    exclude = rules.index("path-exclude=/usr/share/doc/*")
    include = rules.index("path-include=/usr/share/doc/*/copyright")
    assert exclude < include, "copyright の取り込みが doc の除外より前にある"
    assert not [r for r in rules if r.startswith("path-include=") and "copyright" not in r], \
        "copyright のほかを取り込んでいる"


@pytest.mark.parametrize("path", ["/usr/share/man/*", "/usr/share/info/*", "/usr/include/node/*"])
def test_paths_are_excluded(path):
    assert f"path-exclude={path}" in _rules(), f"{path} を外していない"


def _doc_cleanup(run: Instruction) -> str:
    found = [c for c in _commands(run.args) if re.search(r"\bfind /usr/share/doc\b", c)]
    assert found, f"/usr/share/doc の copyright でない物を消していない: {run.args[:80]}"
    return found[0]


def _assert_doc_cleanup(command: str) -> None:
    for part in ["! -type d", "! -xtype d", "! -name copyright", "-delete"]:
        assert part in command, f"/usr/share/doc の削除に {part} が無い: {command}"
    assert "-type f" not in command, f"/usr/share/doc の削除が symlink を残す: {command}"


def test_first_run_removes_non_copyright_docs(instructions):
    _, first = _first_run(instructions)
    _assert_doc_cleanup(_doc_cleanup(first))
    assert "/usr/share/info/*" in first.args, "最初の RUN が /usr/share/info を消していない"


def test_user_layer_removes_non_copyright_docs_with_sudo(instructions):
    command = _doc_cleanup(_user_layer_run(instructions))
    _assert_doc_cleanup(command)
    assert command.startswith("sudo find "), f"利用者の層の削除が sudo でない: {command}"


# --- I6: dind を置かない ---


def test_dind_script_is_gone():
    assert not (BASE_DIR / "dind").exists(), "containers/base/dind が残っている"


def test_dockerfile_does_not_install_dind(instructions):
    for ins in instructions:
        assert "/usr/local/bin/dind" not in ins.args, f"{ins.keyword} が dind を置いている"
        if ins.keyword == "COPY":
            assert "dind" not in ins.args.split(), "dind を COPY している"
