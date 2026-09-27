"""建てた lfm のイメージに base の設定が届いているか (#275)

到達の検査 (``test_lfm_base_settings.py``) は Dockerfile の文字列から「届く形か」を見る。
ここでは建てた ``devbase-lfm:latest`` と ``devbase-base:latest`` を ``docker run`` で比べ、
起動定義・読み込み器・tmux・フォント・Claude Code のフックが実際に base と同じに効くかを見る。

**Docker が無い / どちらかのイメージが無い / lfm がこの変更より前のもの
(``/etc/devbase/shellrc-dir.sh`` が無い) ときは skip する。** 古いイメージを持つ人の
``pytest tests/`` が全員赤くなるのを避ける (``test_base_image_font_matching.py`` と同じ)。
``docker run`` はイメージごとに 1 回へまとめ、全ケースを同じワーカーへ寄せる。

GPU を使う ``nvidia-smi`` の確認は ``--gpus all`` が通る端末に限られるため、ここに入れない。
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.conftest import host_docker_env

LFM = "devbase-lfm:latest"
BASE = "devbase-base:latest"
BUILD_HINT = "`devbase build base --no-cache` の後に lfm を建て直すと、この検査が効く"
STALE_IMAGE_EXIT = 90

pytestmark = pytest.mark.xdist_group("lfm_image_probe")

ALIASES = ("claude", "claudb", "gemini", "codex", "kiro", "agy")
SAME_FILES = ("/etc/devbase/ai-cli-aliases.sh", "/etc/devbase/shellrc-dir.sh", "/etc/tmux.conf",
              "/etc/fonts/local.conf", "/home/ubuntu/.claude/settings.json")
FONT_PATTERNS = ("sans-serif", "sans-serif:lang=ja", "sans")
TOOLS = {
    "nvcc": "nvcc --version",
    "cargo": "cargo --version",
    "gfortran": "gfortran --version",
    "mecab": "mecab -v",
    "docker": "docker --version",
    "terraform": "terraform version",
    "gh": "gh --version",
    "node": "node --version",
    "aws": "aws --version",
    "gcloud": "gcloud --version",
}
# 変更前の lfm (#275 の前の Dockerfile で建てたもの) で測った sha256。lfm の固有の設定は変えない
UNCHANGED_SHA256 = {
    "/etc/nvidia-container-runtime/config.toml":
        "4505186b8a5a46f5295fbc7c0592a03550d89dae8832705130abf9ed7e7ae670",
    "/etc/docker/daemon.json":
        "e45c7bbdbf4a9778c5b7b89ab77d9659c6c59f144c1075656c14254d703bd7c1",
}

_COMMON = r"""
set -u
emit() { printf '%s\t%s\t%s\n' "$1" "$2" "$3"; }
for f in {same_files}; do
  emit sha "$f" "$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1)"
done
for a in {aliases}; do
  emit alias "$a" "$(bash -ic "alias $a" 2>/dev/null)"
done
"""

_LFM_ONLY = r"""
emit bashrc-alias-lines "" "$(grep -cE '^[[:space:]]*alias[[:space:]]+(claude|claudb|gemini|codex|kiro|agy)=' ~/.bashrc)"
emit env-noninteractive "" "$(bash -c 'echo "$DEVBASE_SHELLRC_DIR"')"
mkdir -p "$DEVBASE_SHELLRC_DIR"
cat > "$DEVBASE_SHELLRC_DIR/a.sh" <<'SH'
alias zz-275='echo ok'
alias claude='echo overridden'
SH
emit shellrc-alias "" "$(bash -ic 'zz-275' 2>/dev/null)"
emit shellrc-override "" "$(bash -ic 'alias claude' 2>/dev/null)"
rm -rf "$DEVBASE_SHELLRC_DIR"
emit tmux "" "$(command -v tmux)"
export TMUX_TMPDIR="$(mktemp -d)"
unset TMUX TMUX_PANE
tmux new-session -d -s zz275 >/dev/null 2>&1
tmux-session peek -n 0 zz275 >/dev/null 2>&1; emit tmux-session-peek "" "$?"
tmux kill-server >/dev/null 2>&1
tmux -L zz275 -f /etc/tmux.conf new-session -d >/dev/null 2>&1
emit prefix-s "" "$(tmux -L zz275 list-keys -T prefix S 2>/dev/null)"
tmux -L zz275 kill-server >/dev/null 2>&1
first_family() {{ sed -n 's/^[^:]*: "\([^"]*\)".*/\1/p' | cut -d, -f1; }}
for p in {fonts}; do emit fc-match "$p" "$(fc-match "$p" | first_family)"; done
emit fc-match-s-first sans-serif:lang=ja "$(fc-match -s sans-serif:lang=ja | head -1 | first_family)"
emit owner ~/.claude "$(stat -c %U ~/.claude)"
emit owner ~/.claude/settings.json "$(stat -c %U ~/.claude/settings.json)"
emit owner ~/.bashrc "$(stat -c %U ~/.bashrc)"
{tools}
for f in {unchanged}; do
  emit unchanged "$f" "$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1)"
done
emit npm-gid "" "$(getent group npm | cut -d: -f3)"
npm i -g --no-fund --no-audit is-number >/dev/null 2>&1; emit npm-install "" "$?"
"""


def _unavailable() -> str | None:
    if shutil.which("docker") is None:
        return "docker が PATH に無い"
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=30, check=True,
                       env=host_docker_env())
    except (subprocess.SubprocessError, OSError):
        return "docker daemon へ繋がらない"
    for image in (BASE, LFM):
        out = subprocess.run(["docker", "image", "inspect", image], capture_output=True,
                             timeout=30, env=host_docker_env())
        if out.returncode != 0:
            return f"{image} が無い。{BUILD_HINT}"
    return None


def _run(image: str, script: str) -> dict[str, dict[str, str]]:
    # skip してよいのは Docker・イメージが無いときと、lfm が古いときだけ。起動の失敗は失敗として知らせる
    out = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "/bin/bash", image, "-c", script],
        capture_output=True, text=True, timeout=600, env=host_docker_env())
    if out.returncode == STALE_IMAGE_EXIT:
        pytest.skip(f"{image} に /etc/devbase/shellrc-dir.sh が無い (この変更より前のイメージ)。"
                    f"{BUILD_HINT}")
    assert out.returncode == 0, f"{image} の probe が失敗した (exit={out.returncode}):\n{out.stderr}"
    collected: dict[str, dict[str, str]] = {}
    for line in out.stdout.splitlines():
        if line.count("\t") < 2:
            continue
        kind, key, value = line.split("\t", 2)
        collected.setdefault(kind, {})[key] = value
    return collected


@pytest.fixture(scope="session")
def probes() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    reason = _unavailable()
    if reason:
        pytest.skip(reason)
    common = _COMMON.format(same_files=" ".join(SAME_FILES), aliases=" ".join(ALIASES))
    tools = "\n".join(f'{cmd} >/dev/null 2>&1; emit tool {name} "$?"' for name, cmd in TOOLS.items())
    lfm_script = ("test -f /etc/devbase/shellrc-dir.sh || exit %d\n" % STALE_IMAGE_EXIT) + common \
        + _LFM_ONLY.format(fonts=" ".join(FONT_PATTERNS), tools=tools,
                           unchanged=" ".join(UNCHANGED_SHA256))
    lfm = _run(LFM, lfm_script)
    base = _run(BASE, common + 'emit npm-gid "" "$(getent group npm | cut -d: -f3)"\n')
    return lfm, base


@pytest.fixture(scope="session")
def lfm(probes):
    return probes[0]


@pytest.fixture(scope="session")
def base(probes):
    return probes[1]


@pytest.mark.parametrize("path", SAME_FILES)
def test_base_settings_files_are_byte_identical(lfm, base, path):
    """AC1・AC7・AC10。起動定義・読み込み器・tmux.conf・フォント・Claude Code のフックが base と同じ"""
    assert base["sha"][path], f"base に {path} が無い"
    assert lfm["sha"][path] == base["sha"][path]


@pytest.mark.parametrize("name", ALIASES)
def test_ai_cli_aliases_match_base(lfm, base, name):
    """AC2"""
    assert base["alias"][name], f"base に alias {name} が無い"
    assert lfm["alias"][name] == base["alias"][name]


def test_claudb_does_not_pin_the_model(lfm):
    """AC2。#261 の古い定義は ANTHROPIC_MODEL を固定していた"""
    assert "ANTHROPIC_MODEL" not in lfm["alias"]["claudb"]


def test_bashrc_has_no_hardcoded_ai_cli_aliases(lfm):
    """AC3"""
    assert lfm["bashrc-alias-lines"][""] == "0"


def test_shellrc_dir_is_visible_to_non_interactive_shells(lfm):
    """AC4"""
    assert lfm["env-noninteractive"][""] == "/home/ubuntu/.shellrc.d"


def test_the_shellrc_dir_is_read_by_interactive_shells(lfm):
    """AC5"""
    assert lfm["shellrc-alias"][""] == "ok"


def test_the_shellrc_dir_is_read_after_the_ai_cli_aliases(lfm):
    """AC6"""
    assert lfm["shellrc-override"][""] == "alias claude='echo overridden'"


def test_tmux_is_installed(lfm):
    """AC7"""
    assert lfm["tmux"][""]


def test_tmux_session_works_and_prefix_s_is_bound(lfm):
    """AC8"""
    assert lfm["tmux-session-peek"][""] == "0"
    assert "tmux-menu" in lfm["prefix-s"][""]


@pytest.mark.parametrize("pattern", FONT_PATTERNS)
def test_japanese_resolves_to_the_jp_face(lfm, pattern):
    """AC9。#243 では WenQuanYi Zen Hei に解決されていた"""
    assert lfm["fc-match"][pattern] == "Noto Sans CJK JP"


def test_the_first_of_the_sorted_japanese_list_is_the_jp_face(lfm):
    """AC9"""
    assert lfm["fc-match-s-first"]["sans-serif:lang=ja"] == "Noto Sans CJK JP"


@pytest.mark.parametrize("path", ["~/.claude", "~/.claude/settings.json", "~/.bashrc"])
def test_imported_user_files_belong_to_ubuntu(lfm, path):
    """AC10。~/.claude が root の持ち物だと entrypoint のリンクの差し替えが止まる"""
    owners = {k.replace("/home/ubuntu", "~"): v for k, v in lfm["owner"].items()}
    assert owners[path] == "ubuntu"


@pytest.mark.parametrize("name", TOOLS)
def test_lfm_specific_tools_still_run(lfm, name):
    """AC13"""
    assert lfm["tool"][name] == "0"


@pytest.mark.parametrize("path,expected", sorted(UNCHANGED_SHA256.items()))
def test_nvidia_and_docker_settings_are_unchanged(lfm, path, expected):
    """AC14"""
    assert lfm["unchanged"][path] == expected


def test_ubuntu_can_npm_install_globally_without_sudo(lfm, base):
    """AC15。npm グループの GID が base と同じ"""
    assert lfm["npm-gid"][""] == base["npm-gid"][""]
    assert lfm["npm-install"][""] == "0"
