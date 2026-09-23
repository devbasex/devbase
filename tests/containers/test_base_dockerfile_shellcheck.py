"""base イメージの shellcheck の導入の「形」 (PLAN67 / #249)

Docker を起動せず、``containers/base/Dockerfile`` の文字列だけを固定する。イメージの中に
入っていることは、版の確認の ``RUN`` がビルドの時点で守る (設計の決定 2)。ここで固定するのは
次の 3 つである (設計の決定 4)。

- ``shellcheck`` が **1 つ目の RUN の 1 回目の** ``apt-get install`` の一覧にある (受け入れ条件 5)
- ``shellcheck`` を入れる ``RUN`` が他に無く、``apt-get update`` が 2 回のまま (受け入れ条件 5)
- 版の確認の ``RUN`` に ``shellcheck --version`` がある (受け入れ条件 4)

補助の関数は ``test_base_dockerfile_fonts.py`` から import しない。テストのファイルどうしを
依存させない (``test_base_dockerfile_bao.py`` も自前の ``_statements`` を持つ)。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

DOCKERFILE = Path(__file__).resolve().parents[2] / "containers" / "base" / "Dockerfile"
SHELLCHECK = re.compile(r"(?<![\w-])shellcheck(?![\w-])")


def _statements() -> str:
    """コメント行を除いた Dockerfile の本文 (説明の注記に assertion が反応しないように)"""
    return "\n".join(
        line for line in DOCKERFILE.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def _run_blocks() -> list[str]:
    """Dockerfile を RUN ブロック単位 (行継続を含む 1 命令分) に分ける

    ``RUN`` で始まる**行**だけを見ると、行継続の先にあるパッケージ名を 1 つも拾えない。
    """
    blocks: list[str] = []
    block: list[str] | None = None
    for line in _statements().splitlines():
        if block is None:
            if not line.startswith("RUN "):
                continue
            block = []
        block.append(line)
        if not line.rstrip().endswith("\\"):
            blocks.append("\n".join(block))
            block = None
    if block is not None:  # 最終行が \ で終わっていても取りこぼさない
        blocks.append("\n".join(block))
    assert blocks, "RUN が 1 つも見つからない"
    return blocks


def _first_apt_install(block: str) -> str:
    """1 つ目の RUN の**1 回目**の apt-get install の一覧だけを取り出す

    1 つ目の RUN は apt-get install を 2 回呼ぶ。RUN の本文全体で探すと、2 回目の一覧
    (後から足したリポジトリの docker-ce / gh / nodejs など) にあっても通ってしまう。
    """
    calls = [m.start() for m in re.finditer(r"apt-get install", block)]
    assert len(calls) >= 2, "1 つ目の RUN に apt-get install が 2 回無い"
    return block[calls[0]:calls[1]]


def _version_check_run() -> str:
    """gh / node / aws などの版を確かめる RUN の 1 命令分"""
    found = [b for b in _run_blocks() if "gh --version" in b and "session-manager-plugin --version" in b]
    assert len(found) == 1, "版の確認の RUN がちょうど 1 つではない"
    return found[0]


def test_shellcheck_is_in_the_first_apt_install():
    """決定 1。標準のアーカイブのパッケージなので、1 回目の一覧へ置く"""
    assert SHELLCHECK.search(_first_apt_install(_run_blocks()[0]))


def test_shellcheck_is_not_in_the_second_apt_install():
    """2 回目は後から足したリポジトリのパッケージを入れる場所で、混ぜない"""
    first = _run_blocks()[0]
    second = first[[m.start() for m in re.finditer(r"apt-get install", first)][1]:]
    assert not SHELLCHECK.search(second)


def test_no_extra_run_installs_shellcheck():
    """決定 1。新しい RUN を立てず、apt-get update をもう 1 回走らせない"""
    for block in _run_blocks()[1:]:
        assert not re.search(r"apt-get\s+install[^;&]*(?<![\w-])shellcheck(?![\w-])", block)
    assert _statements().count("apt-get update") == 2, "apt-get update の回数が変わっている"


def test_version_check_run_calls_shellcheck():
    """決定 2。入れ損ないを、版の確認の RUN で止める (無ければ終了コード 127)"""
    commands = [c.strip() for c in _version_check_run().removeprefix("RUN ").split("&&")]
    assert "shellcheck --version" in commands


def test_first_run_apt_package_sets_match_current_dockerfile():
    """現行 RUN から抽出して観測した 2 回分の集合。順序・オプションは固定しない。"""
    calls = re.findall(r"apt-get\s+install\s+([^;]+);", _run_blocks()[0])
    packages = [
        {word for word in call.replace("\\\n", " ").split()
         if word != "\\" and not word.startswith("-")}
        for call in calls
    ]
    assert packages == [
        {
            "ca-certificates", "curl", "fonts-crosextra-caladea",
            "fonts-crosextra-carlito", "fonts-noto-cjk", "fonts-noto-cjk-extra",
            "git", "gnupg", "jq", "libnss3", "libxrandr2", "libxss1", "locales",
            "lsb-release", "make", "nano", "openssh-client", "poppler-utils",
            "python3-defusedxml", "python3-lxml", "python3-pil", "shellcheck",
            "sudo", "tmux", "unzip", "vim", "wget",
        },
        {
            "$BROWSER_PKG", "chromium-browser", "containerd.io",
            "docker-buildx-plugin", "docker-ce", "docker-ce-cli",
            "docker-compose-plugin", "gh", "nodejs", "terraform",
        },
    ]


# ---------------------------------------------------------------------------
# AWS CLI + gcloud SDK + uv + npm globals の RUN のアーキテクチャ分岐 (R1-004)
#
# session-manager-plugin と gcloud SDK は、ダウンロードするアーキテクチャを ``case`` で
# 選ぶ。この RUN を分けたりまとめたりしたときに、片方の対応や未対応時の停止が欠けても
# 検出できるよう、現状の分岐をそのまま固定する。正しさは主張せず現状を記録する。
# ---------------------------------------------------------------------------


def _aws_gcloud_run() -> str:
    """AWS CLI / session-manager-plugin / gcloud SDK / uv / npm を入れる RUN の 1 命令分"""
    found = [
        b for b in _run_blocks()
        if "ssm_arch=" in b and "gcloud_arch=" in b
    ]
    assert len(found) == 1, "ssm_arch と gcloud_arch を持つ RUN がちょうど 1 つではない"
    return found[0]


def _case_branches(block: str, var: str) -> dict[str, str]:
    """``<pat>) <var>="値" ;;`` 形式の枝を {パターン: 値} に、``*) ... exit N`` の枝を
    {"*": "exit N"} に写し取る。行継続の ``\\`` と空白は畳んで扱う。
    """
    flat = block.replace("\\\n", " ")
    branches: dict[str, str] = {}
    for pat, value in re.findall(
        rf'([\w*]+)\)\s+{re.escape(var)}="([^"]+)"\s*;;', flat
    ):
        branches[pat] = value
    fallback = re.search(r"\*\)[^;]*?(exit\s+\d+)", flat)
    if fallback:
        branches["*"] = fallback.group(1)
    return branches


def test_ssm_arch_case_maps_current_architectures():
    """session-manager-plugin の case。amd64 / arm64 が現状の値へ、それ以外は exit 1"""
    branches = _case_branches(_aws_gcloud_run(), "ssm_arch")
    assert branches["amd64"] == "ubuntu_64bit"
    assert branches["arm64"] == "ubuntu_arm64"
    assert branches["*"] == "exit 1"


def test_gcloud_arch_case_maps_current_architectures():
    """gcloud SDK の case。x86_64 / aarch64 が現状の値へ、それ以外は exit 1"""
    branches = _case_branches(_aws_gcloud_run(), "gcloud_arch")
    assert branches["x86_64"] == "x86_64"
    assert branches["aarch64"] == "arm"
    assert branches["*"] == "exit 1"


def test_ssm_and_gcloud_arch_values_are_used_in_download_urls():
    """case で選んだ値がダウンロード URL の ${ssm_arch} / ${gcloud_arch} で参照される"""
    block = _aws_gcloud_run()
    assert "session-manager-downloads" in block and "${ssm_arch}" in block
    assert "google-cloud-cli-linux-${gcloud_arch}" in block


def test_version_check_run_commands_match_current_dockerfile():
    """版の確認の RUN の現状のコマンド一式 (R1-005)。順序は仕様ではないので集合で比べる

    shellcheck 以外の 6 つも固定し、RUN を組み替えたときに確認が黙って欠けないようにする。
    """
    commands = {c.strip() for c in _version_check_run().removeprefix("RUN ").split("&&")}
    assert commands == {
        "gh --version", "node --version", "npm --version", "aws --version",
        "gcloud --version", "session-manager-plugin --version", "shellcheck --version",
    }


@pytest.mark.parametrize(
    ("failure", "exit_code"),
    [
        ("download", 22), ("installer", 42),
        # macOS の /bin/sh は実体のない絶対パスの実行で 1、Linux は 127。
        ("missing_binary", 1 if sys.platform == "darwin" else 127),
    ],
)
def test_agy_failure_stops_user_tools_run(tmp_path, failure, exit_code):
    """現状固定: agy の各失敗を伝播し、後続ツールの成果を作らない。"""
    blocks = [b for b in _run_blocks() if "https://antigravity.google/cli/install.sh" in b]
    assert len(blocks) == 1
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    for name in ("uv", "claude"):
        (fixtures / name).write_text(f'printf installed > "$ARTIFACTS/{name}"\n')
    (fixtures / "agy").write_text(
        'printf started > "$ARTIFACTS/agy-installer"\n'
        + ("exit 42\n" if failure == "installer" else "exit 0\n")
    )
    # PATH にホストのコマンドを含めず、シェルだけ本物で fixture を実行する。
    for name in ("sh", "bash"):
        (bin_dir / name).symlink_to(f"/bin/{name}")
    stub = tmp_path / "command-stub"
    stub.write_text(
        f"#!{sys.executable}\n"
        + '''import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
artifacts = Path(os.environ["ARTIFACTS"])
if name == "curl":
    url = next(arg for arg in args if arg.startswith("https://"))
    sources = {
        "https://astral.sh/uv/install.sh": "uv",
        "https://claude.ai/install.sh": "claude",
        "https://antigravity.google/cli/install.sh": "agy",
    }
    if url in sources:
        source = sources[url]
        if source == "agy":
            (artifacts / "agy-download").write_text("attempted")
            if os.environ["FAILURE"] == "download":
                sys.exit(22)
        data = (Path(os.environ["FIXTURES"]) / source).read_text()
    elif url.startswith("https://desktop-release.q.us-east-1.amazonaws.com/"):
        (artifacts / "kiro-download").write_text("downloaded")
        data = "kiro fixture"
    else:
        raise AssertionError(url)
    if "-o" in args:
        Path(args[args.index("-o") + 1]).write_text(data)
    else:
        sys.stdout.write(data)
elif name == "uname":
    print("x86_64")
elif name == "unzip":
    Path("kirocli").mkdir(exist_ok=True)
    installer = Path("kirocli/install.sh")
    installer.write_text("#!/bin/sh\\nexit 0\\n")
    installer.chmod(0o755)
elif name == "npx":
    (artifacts / "playwright-install").write_text("installed")
elif name in ("rm", "sudo"):
    pass  # ホストの削除・権限昇格は実行しない。
else:
    raise AssertionError(name)
'''
    )
    stub.chmod(0o755)
    for name in ("curl", "uname", "unzip", "npx", "rm", "sudo"):
        (bin_dir / name).symlink_to(stub)
    result = subprocess.run(
        ["/bin/sh", "-c", blocks[0].removeprefix("RUN ")],
        cwd=tmp_path,
        env={
            "HOME": str(home), "PATH": str(bin_dir),
            "ARTIFACTS": str(artifacts), "FIXTURES": str(fixtures),
            "FAILURE": failure,
        },
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == exit_code, result.stderr
    assert (artifacts / "uv").read_text() == "installed"
    assert (artifacts / "claude").read_text() == "installed"
    assert (artifacts / "agy-download").exists()
    assert (artifacts / "agy-installer").exists() == (failure != "download")
    assert not (artifacts / "kiro-download").exists()
    assert not (artifacts / "playwright-install").exists()
