"""base の設定が lfm へ届くかの到達の検査 (#275)

lfm は base を ``FROM`` で継がず、base のイメージから ``COPY --from=devbase-base:latest`` で
取り込む。base に設定を足した開発者が lfm へ届けることを覚えていなくてよいように、
Docker を起動せず 2 つの Dockerfile の文字列から次を判定する。

- base が build context から置くファイルは、lfm の取り込みに覆われる (I1)
- base の ``RUN`` が書く利用者のファイル (``~/.bashrc`` など) は、lfm の取り込みに覆われる (I2)
- base の ``ENV`` (``PATH`` を除く) は、lfm に同じ名前・同じ値で宣言されている (I3)
- base の ``PATH`` の各要素は、lfm の ``PATH`` に含まれる (I4)
- base の設定を動かす apt のパッケージ (``tmux``) は、lfm の apt の一覧にある (I5)

届かなくてよい項目は ``EXCLUDED`` に理由とともに置き、使われない除外は落とす (I10)。
lfm の Dockerfile の形 (I6〜I9) は下の別のテストで縛る。
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

CONTAINERS = Path(__file__).resolve().parents[2] / "containers"
BASE_DOCKERFILE = CONTAINERS / "base" / "Dockerfile"
LFM_DOCKERFILE = CONTAINERS / "lfm" / "Dockerfile"

BASE_IMAGE = "devbase-base:latest"
HOME = "/home/ubuntu"

# Dockerfile の命令から導けない、base の設定を動かす本体 (決定 5)
REQUIRED_APT = {
    "tmux": "/usr/local/bin/tmux-* と /etc/tmux.conf が使う",
}

# lfm へ届かなくてよい base の項目と理由。キーは missing_settings が返す名前と同じ形
EXCLUDED = {
    "PATH /root/.local/bin": "base で root の uv が入る場所。lfm は /root を取り込まず、"
    "ubuntu の ~/.local/bin を PATH に持つ",
}

# lfm がまるごと取り込んではならない元のパス (I9)。CUDA のイメージの /etc が base のものになる
FORBIDDEN_IMPORT_SOURCES = {"/", "/etc", "/home", HOME}


@dataclass(frozen=True)
class Instruction:
    keyword: str
    args: str


def parse(text: str) -> list[Instruction]:
    """行の継続をつなぎ、コメントの行を除いて命令に分ける。RUN の heredoc の本文も命令に含める"""
    instructions: list[Instruction] = []
    lines = text.splitlines()
    buf: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            buf.append(stripped[:-1])
            continue
        buf.append(stripped)
        joined = " ".join(buf)
        buf = []
        marker = re.search(r"<<-?\s*'?\"?(\w+)'?\"?", joined)
        if marker and joined.split(None, 1)[0].upper() == "RUN":
            body: list[str] = []
            while i < len(lines) and lines[i].strip() != marker.group(1):
                body.append(lines[i])
                i += 1
            i += 1
            joined += "\n" + "\n".join(body)
        keyword, _, args = joined.partition(" ")
        instructions.append(Instruction(keyword.upper(), args.strip()))
    if buf:
        joined = " ".join(buf)
        keyword, _, args = joined.partition(" ")
        instructions.append(Instruction(keyword.upper(), args.strip()))
    return instructions


def _split_flags(args: str) -> tuple[dict[str, str], list[str]]:
    flags: dict[str, str] = {}
    rest: list[str] = []
    for token in args.split():
        if token.startswith("--") and not rest:
            name, _, value = token[2:].partition("=")
            flags[name] = value
        else:
            rest.append(token)
    return flags, rest


def _env_pairs(args: str) -> list[tuple[str, str]]:
    """``ENV A=b C="d e"`` を名前と値の組にする。古い ``ENV A b`` の形も読む"""
    pairs = re.findall(r'(\w+)=("(?:[^"\\]|\\.)*"|\S*)', args)
    if pairs:
        return [(name, value.strip('"')) for name, value in pairs]
    name, _, value = args.partition(" ")
    return [(name, value.strip())]


def _expand(value: str, variables: dict[str, str]) -> str:
    return re.sub(r"\$\{(\w+)\}|\$(\w+)",
                  lambda m: variables.get(m.group(1) or m.group(2), m.group(0)), value)


@dataclass
class Summary:
    placed_files: list[str]  # base: build context から置くファイルの宛先
    written_files: list[str]  # base: RUN が書く利用者のファイル
    env: dict[str, str]  # PATH 以外の ENV (展開済み)
    path: list[str]  # ENV PATH の要素 (${PATH} を除く)
    imports: list[str]  # lfm: 同じパスへの取り込みの元のパス
    apt: set[str]


def summarize(text: str) -> Summary:
    variables: dict[str, str] = {}
    summary = Summary([], [], {}, [], [], set())
    for ins in parse(text):
        if ins.keyword == "ARG":
            name, _, default = ins.args.partition("=")
            variables.setdefault(name.strip(), default.strip().strip('"'))
        elif ins.keyword == "ENV":
            for name, value in _env_pairs(ins.args):
                value = _expand(value, variables) if name != "PATH" else value
                if name == "PATH":
                    for element in value.split(":"):
                        if element not in ("${PATH}", "$PATH") and element not in summary.path:
                            summary.path.append(element)
                else:
                    summary.env[name] = value
                    variables[name] = value
        elif ins.keyword == "COPY":
            flags, rest = _split_flags(ins.args)
            if len(rest) < 2:
                continue
            sources, dest = rest[:-1], _expand(rest[-1], variables)
            if "from" not in flags:
                summary.placed_files.append(dest)
                continue
            if flags["from"] != BASE_IMAGE:
                continue
            for src in sources:
                placed = posixpath.join(dest, posixpath.basename(src)) \
                    if len(sources) > 1 or dest.endswith("/") else dest
                if placed.rstrip("/") == src.rstrip("/"):
                    summary.imports.append(src.rstrip("/") or "/")
        elif ins.keyword == "RUN":
            for target in re.findall(r">>?\s*((?:~|\$HOME)/[^\s;&|'\"]+)", ins.args):
                path = HOME + "/" + target.split("/", 1)[1]
                if path not in summary.written_files:
                    summary.written_files.append(path)
            for chunk in re.findall(r"apt-get install\s+(.*?)(?:;|&&|$)", ins.args, re.S):
                summary.apt.update(t for t in chunk.split() if not t.startswith("-") and "$" not in t)
    return summary


def _covered(path: str, imports: list[str]) -> bool:
    return any(path == src or path.startswith(src.rstrip("/") + "/") for src in imports)


def missing_settings(base_text: str, lfm_text: str) -> list[str]:
    """base の設定のうち lfm へ届かない項目の名前。除外表の項目は含めない"""
    return [name for name in collect_missing(base_text, lfm_text) if name not in EXCLUDED]


def collect_missing(base_text: str, lfm_text: str) -> list[str]:
    base, lfm = summarize(base_text), summarize(lfm_text)
    missing: list[str] = []
    for path in base.placed_files + base.written_files:
        if not _covered(path, lfm.imports):
            missing.append(path)
    for name, value in base.env.items():
        if lfm.env.get(name) != value:
            missing.append(f"ENV {name}")
    for element in base.path:
        if element not in lfm.path:
            missing.append(f"PATH {element}")
    for package in REQUIRED_APT:
        if package not in lfm.apt:
            missing.append(f"apt {package}")
    return missing


def base_items(base_text: str) -> set[str]:
    base = summarize(base_text)
    return (set(base.placed_files) | set(base.written_files)
            | {f"ENV {n}" for n in base.env} | {f"PATH {e}" for e in base.path})


@pytest.fixture(scope="module")
def base_text() -> str:
    return BASE_DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def lfm_text() -> str:
    return LFM_DOCKERFILE.read_text(encoding="utf-8")


def _message(missing: list[str]) -> str:
    return "lfm へ届かない base の設定:\n" + "\n".join(f"  {m}" for m in missing)


# ---- 到達 (AC11・AC12) ----


def test_every_base_setting_reaches_lfm(base_text, lfm_text):
    """AC12。実物の 2 つの Dockerfile で、届かない項目が無い"""
    missing = missing_settings(base_text, lfm_text)
    assert missing == [], _message(missing)


def test_the_collected_base_settings_include_the_known_ones(base_text):
    """集め方が壊れて何も集めなくなると、上のテストが黙って通るため、既知の項目で縛る"""
    items = base_items(base_text)
    for expected in ("/etc/devbase/ai-cli-aliases.sh", "/etc/devbase/shellrc-dir.sh",
                     "/etc/tmux.conf", "/etc/fonts/local.conf", f"{HOME}/.bashrc",
                     f"{HOME}/.claude/settings.json", "ENV DEVBASE_SHELLRC_DIR",
                     "/usr/local/bin/tmux-session", "/entrypoint.sh"):
        assert expected in items, expected


def test_a_file_added_under_etc_devbase_reaches_lfm_unchanged(base_text, lfm_text):
    """AC11。/etc/devbase の下へ置く設定は lfm を変えずに届く"""
    added = base_text + "\nCOPY --chmod=0644 zz-275.sh /etc/devbase/zz-275.sh\n"
    assert "/etc/devbase/zz-275.sh" in base_items(added)
    missing = missing_settings(added, lfm_text)
    assert missing == [], _message(missing)


def test_an_added_env_is_named_when_lfm_does_not_declare_it(base_text, lfm_text):
    """AC11。ENV は取り込めないため、lfm に宣言が無ければ名前が出る"""
    added = base_text + "\nENV DEVBASE_ZZ_275=/home/${USERNAME}/zz\n"
    assert missing_settings(added, lfm_text) == ["ENV DEVBASE_ZZ_275"]


def test_a_file_added_outside_the_imports_is_named(base_text, lfm_text):
    """AC11。/etc/devbase の外へ置く設定は、取り込みを足すまで名前が出る"""
    added = base_text + "\nCOPY --chmod=0644 zz-275.conf /etc/zz-275.conf\n"
    assert missing_settings(added, lfm_text) == ["/etc/zz-275.conf"]


def test_a_user_file_written_by_run_is_named(base_text, lfm_text):
    """AC11。base の RUN が書く利用者のファイルも項目になる"""
    added = base_text + "\nRUN echo x > ~/.zz-275rc\n"
    assert missing_settings(added, lfm_text) == [f"{HOME}/.zz-275rc"]


@pytest.mark.parametrize("removed,expected", [
    ("COPY --from=devbase-base:latest /etc/devbase /etc/devbase\n", "/etc/devbase/shellrc-dir.sh"),
    ("COPY --from=devbase-base:latest /etc/tmux.conf /etc/tmux.conf\n", "/etc/tmux.conf"),
    ("COPY --from=devbase-base:latest /etc/fonts/local.conf /etc/fonts/local.conf\n",
     "/etc/fonts/local.conf"),
    ("COPY --from=devbase-base:latest --chown=ubuntu:ubuntu /home/ubuntu/.bashrc "
     "/home/ubuntu/.bashrc\n", f"{HOME}/.bashrc"),
    ("COPY --from=devbase-base:latest --chown=ubuntu:ubuntu /home/ubuntu/.claude/settings.json "
     "/home/ubuntu/.claude/settings.json\n", f"{HOME}/.claude/settings.json"),
    ("ENV DEVBASE_SHELLRC_DIR=/home/ubuntu/.shellrc.d\n", "ENV DEVBASE_SHELLRC_DIR"),
    (" tmux \\\n", "apt tmux"),
])
def test_removing_an_import_from_lfm_names_the_setting(base_text, lfm_text, removed, expected):
    """lfm から取り込み・宣言・apt のどれか 1 つを消すと、その項目が出る"""
    assert removed in lfm_text, removed
    broken = lfm_text.replace(removed, " \\\n" if removed.startswith(" tmux") else "")
    assert expected in missing_settings(base_text, broken)


def test_a_per_file_import_of_etc_devbase_misses_a_new_file(base_text, lfm_text):
    """/etc/devbase をファイルごとの取り込みにすると、base が足したファイルが届かない"""
    per_file = lfm_text.replace(
        "COPY --from=devbase-base:latest /etc/devbase /etc/devbase\n",
        "COPY --from=devbase-base:latest /etc/devbase/ai-cli-aliases.sh "
        "/etc/devbase/shellrc-dir.sh /etc/devbase/\n")
    assert missing_settings(base_text, per_file) == []
    added = base_text + "\nCOPY zz-275.sh /etc/devbase/zz-275.sh\n"
    assert missing_settings(added, per_file) == ["/etc/devbase/zz-275.sh"]


def test_env_values_are_compared_after_arg_expansion():
    """I3。base の ${USERNAME} は ARG の既定値で展開されて比べられる"""
    base = 'ARG USERNAME="ubuntu"\nENV X=/home/${USERNAME}/.x\n'
    lfm = "RUN apt-get install -y tmux\n"
    assert missing_settings(base, lfm + "ENV X=/home/ubuntu/.x\n") == []
    assert missing_settings(base, lfm + "ENV X=/home/other/.x\n") == ["ENV X"]


def test_path_elements_are_compared_one_by_one(base_text, lfm_text):
    """I4。/root/.local/bin だけが除外され、他の要素は lfm の PATH にある"""
    assert "PATH /root/.local/bin" in collect_missing(base_text, lfm_text)
    broken = lfm_text.replace("/opt/google-cloud-sdk/bin:", "")
    assert "PATH /opt/google-cloud-sdk/bin" in missing_settings(base_text, broken)


def test_every_exclusion_points_at_a_real_base_item(base_text):
    """I10。除外表が古いまま残らない"""
    unused = sorted(set(EXCLUDED) - base_items(base_text))
    assert unused == [], f"base に無い項目を除外している: {unused}"


# ---- lfm の Dockerfile の形 (I6〜I9) ----


def _index(instructions: list[Instruction], predicate) -> list[int]:
    return [i for i, ins in enumerate(instructions) if predicate(ins)]


def test_lfm_does_not_write_ai_cli_aliases_itself(lfm_text):
    """I6・AC3。起動定義は base の /etc/devbase/ai-cli-aliases.sh から届く"""
    writers = _index(parse(lfm_text), lambda ins: ins.keyword == "RUN"
                     and re.search(r"alias\s+\w+=", ins.args) and ".bashrc" in ins.args)
    assert writers == []


def test_the_bashrc_import_comes_before_lfm_appends_to_it(lfm_text):
    """I7。後に置くと rustup と lfm の ~/.bashrc への追記が消える"""
    instructions = parse(lfm_text)
    imports = _index(instructions, lambda ins: ins.keyword == "COPY"
                     and f"{HOME}/.bashrc" in ins.args)
    appends = _index(instructions, lambda ins: ins.keyword == "RUN"
                     and re.search(r">>?\s*~/\.bashrc", ins.args))
    rustup = _index(instructions, lambda ins: ins.keyword == "RUN" and "rustup" in ins.args)
    assert len(imports) == 1 and appends and rustup
    assert imports[0] < min(appends + rustup)


def test_fc_cache_runs_once_after_the_fonts_conf_and_playwright(lfm_text):
    """I8。書体を入れる最後の RUN の後でなければ、後から入った書体を知らないキャッシュが残る"""
    instructions = parse(lfm_text)
    caches = _index(instructions, lambda ins: ins.keyword == "RUN" and "fc-cache -f" in ins.args)
    conf = _index(instructions, lambda ins: ins.keyword == "COPY"
                  and "/etc/fonts/local.conf" in ins.args)
    playwright = _index(instructions, lambda ins: ins.keyword == "RUN"
                        and "playwright install --with-deps" in ins.args)
    assert len(caches) == 1 and conf and playwright
    assert caches[0] > max(conf + playwright)


def test_lfm_does_not_import_etc_or_home_whole(lfm_text):
    """I9。取り込むのは base の設定の置き場所だけ"""
    whole = sorted(set(summarize(lfm_text).imports) & FORBIDDEN_IMPORT_SOURCES)
    assert whole == []
    broken = lfm_text + "\nCOPY --from=devbase-base:latest /etc /etc\n"
    assert "/etc" in summarize(broken).imports


def test_the_claude_dir_is_made_for_ubuntu_before_settings_json_is_imported(lfm_text):
    """COPY --from は足りない親を root で作り、entrypoint の ~/.claude の差し替えが止まる"""
    instructions = parse(lfm_text)
    made = _index(instructions, lambda ins: ins.keyword == "RUN"
                  and re.search(r"install -d -o \"?\$USERNAME\"? .*\.claude", ins.args))
    imported = _index(instructions, lambda ins: ins.keyword == "COPY"
                      and "/.claude/settings.json" in ins.args)
    assert made and imported and made[0] < imported[0]
    assert "--chown=ubuntu:ubuntu" in instructions[imported[0]].args
