"""同時起動でもリンクの段が失敗しない (#357)

``containers/base/entrypoint.sh`` を ``DEVBASE_ENTRYPOINT_LIB_ONLY=1`` で source し、別々のホームと
同じ 2 つのボリューム (一時ディレクトリ) を渡したリンクの段を、同時に始める。Docker には依存しない。

**1 回の同時実行では、修正前の手順でも通ることがある。** そのため 1 件の中で同時実行を繰り返し、
1 回でも条件が破れたら落とす。繰り返す回数は、修正前の ``entrypoint.sh`` に当てて 3 回続けて落ちる
ことを確かめて決めた。

``flock`` が無い環境 (macOS) では、リンクの段はロックなしで進む。ロックそのものを確かめるケースだけ
skip し、同時実行のケースは走らせる (設計の決定 7)。
"""

from __future__ import annotations

import os
import resource
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "containers" / "base" / "entrypoint.sh"

SHARED_CLAUDE = ["plugins", "skills", "commands", "CLAUDE.md", "settings.json"]

# 同時実行を繰り返す回数。修正前の entrypoint.sh に当てると、受け入れ条件 1・2 のケースは負荷の
# 無いときに macOS・Linux とも 1〜2 回目で落ちた (各 6〜10 回測った)。その倍を取る
ROUNDS_PAIR = 4
ROUNDS = 4

# ほかのプロセスとの重なりで 1 回だけ遅れても落ちないよう、要求の 30 秒をそのまま使う
TIME_LIMIT = 30.0

NEEDS_FLOCK = pytest.mark.skipif(
    shutil.which("flock") is None,
    reason="flock が無い (macOS)。ロックそのものは Linux の CI で確かめる",
)
NOT_ROOT = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root では書き込めない状態を作れない",
)

# 修正前の手順。#357 より前の entrypoint.sh の 4 つの関数をそのまま写した。
# 受け入れ条件 7 の相手と、移行性 (修正前の手順へ戻す) と、所要の比べる相手に使う
OLD_FUNCS = r'''
devbase_ensure_entry() {
    local path="$1"

    mkdir -p "$(dirname "$path")"
    if [ -e "$path" ]; then
        return 0
    fi
    if devbase_is_file_entry "$path"; then
        if devbase_is_json_file_entry "$path"; then
            printf '{}' > "$path"
        else
            : > "$path"
        fi
    else
        mkdir -p "$path"
    fi
}

devbase_link_setting() {
    local link_path="$1" target_path="$2" owner="${3:-${USERNAME:-ubuntu}}"

    devbase_ensure_entry "$target_path"

    if [ -L "$link_path" ] && [ "$(readlink "$link_path")" = "$target_path" ]; then
        echo "  ✓ ${link_path} (symlink exists)"
        return 0
    fi

    mkdir -p "$(dirname "$link_path")"
    if [ -e "$link_path" ] || [ -L "$link_path" ]; then
        echo "  Removing existing ${link_path}..."
        rm -rf "$link_path"
    fi

    echo "  Creating symlink: ${link_path} -> ${target_path}"
    ln -s "$target_path" "$link_path"
    chown -h "${owner}:${owner}" "$link_path" 2>/dev/null || true
}

devbase_seed_entry() {
    local src="$1" dest="$2"
    shift 2

    if [ -e "$dest" ]; then
        return 0
    fi
    if [ ! -e "$src" ]; then
        echo "  skip (シード元なし): $src"
        return 0
    fi

    mkdir -p "$(dirname "$dest")"
    if [ ! -d "$src" ]; then
        cp -a "$src" "$dest"
        echo "  seeded: $dest"
        return 0
    fi

    mkdir -p "$dest"
    local child name excluded skip
    for child in "$src"/* "$src"/.[!.]* "$src"/..?*; do
        [ -e "$child" ] || [ -L "$child" ] || continue
        name="${child##*/}"
        skip=0
        for excluded in "$@"; do
            if [ "$name" = "$excluded" ]; then
                skip=1
                break
            fi
        done
        [ "$skip" = "1" ] && continue
        cp -a "$child" "$dest/$name"
    done
    echo "  seeded: $dest"
}

devbase_setup_ai_settings() {
    local home_root="$1" ai_root="$2" group_root="$3"
    local owner="${5:-${USERNAME:-ubuntu}}"
    local entry

    devbase_ensure_persistent_root "$ai_root" "$owner"
    devbase_ensure_persistent_root "$group_root" "$owner"

    devbase_seed_image_claude_settings "$home_root" "$ai_root"
    for entry in "${DEVBASE_GROUP_HOME_SEED_SETTINGS[@]}"; do
        [ -L "$home_root/$entry" ] && continue
        devbase_seed_entry "$home_root/$entry" "$group_root/$entry"
    done

    for entry in "${DEVBASE_SHARED_SETTINGS[@]}"; do
        devbase_link_setting "$home_root/$entry" "$ai_root/$entry" "$owner"
    done
    for entry in "${DEVBASE_GROUP_SETTINGS[@]}"; do
        devbase_link_setting "$home_root/$entry" "$group_root/$entry" "$owner"
    done
    for entry in "${DEVBASE_SHARED_CLAUDE_SETTINGS[@]}"; do
        devbase_link_setting "$group_root/.claude/$entry" \
            "$ai_root/.claude/$entry" "$owner"
    done
}
'''


# ---------------------------------------------------------------------------
# 走らせる道具
# ---------------------------------------------------------------------------

def _base_env(path: str | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DEVBASE_", "GIT_"))}
    if path is not None:
        env["PATH"] = path
    return env


def _bash() -> str:
    return shutil.which("bash") or "bash"


def run_script(script: str, cwd: Path, *, path: str | None = None, old: bool = False,
               pre: str = "", timeout: float = 120):
    """関数を読み込み ``script`` を ``set -e`` で走らせる。``old`` なら修正前の手順に差し替える"""
    full = (f'set -e\nDEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"\n'
            f'{OLD_FUNCS if old else ""}\n{pre}\n{script}\n')
    return subprocess.run([_bash(), "-c", full], cwd=cwd, env=_base_env(path),
                          capture_output=True, text=True, timeout=timeout, check=False)


def setup_cmd(home: Path, ai: Path, grp: Path) -> str:
    return f'devbase_setup_ai_settings "{home}" "{ai}" "{grp}" acme'


class Job:
    def __init__(self, rc: int, out: str, err: str):
        self.rc, self.out, self.err = rc, out, err

    @property
    def text(self) -> str:
        return self.out + self.err


def run_concurrent(work: Path, jobs: list[str], *, path: str | None = None,
                   pre: str = "") -> tuple[list[Job], float]:
    """``jobs`` (bash の断片) を同時に始め、終了コードと出力と全体の所要を返す。

    断片はそれぞれ ``set -e`` のサブシェルで走る。``old_funcs`` を読めば修正前の手順になる。
    全員が名前付きパイプを読む側で開いて待ち、書く側が開いた瞬間に一斉に走り出す。
    待つ間に CPU を使わないため、並列で流すほかのケースの負荷を増やさない。書く側は全員が
    終わるまで開いたままにし、遅れて着いた断片も止まらずに走る。
    """
    work.mkdir(parents=True, exist_ok=True)
    old = work / "old_funcs.sh"
    old.write_text(OLD_FUNCS)
    go = work / "go"
    os.mkfifo(go)
    # entrypoint.sh は読み込んだシェルに set -e を掛ける。失敗した断片の終了コードを残すため外す
    lines = [f'DEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"', "set +e", pre,
             f'old_funcs() {{ . "{old}"; }}']
    for i, body in enumerate(jobs):
        lines.append(f"job_{i}() {{\n{body}\n}}")
    for i in range(len(jobs)):
        lines.append(
            f'{{ : <"{go}"; '
            f'( set -e; job_{i} ) >"{work}/{i}.out" 2>"{work}/{i}.err"; '
            f'echo $? >"{work}/{i}.rc"; }} &'
        )
    lines.append("sleep 0.3")
    lines.append(f'exec 9>"{go}"')
    lines.append("wait")
    lines.append("exec 9>&-")
    script = "\n".join(lines) + "\n"
    started = time.monotonic()
    subprocess.run([_bash(), "-c", script], cwd=work, env=_base_env(path),
                   capture_output=True, text=True, timeout=120, check=False)
    elapsed = time.monotonic() - started - 0.3
    results = []
    for i in range(len(jobs)):
        results.append(Job(int((work / f"{i}.rc").read_text().strip()),
                           (work / f"{i}.out").read_text(),
                           (work / f"{i}.err").read_text()))
    return results, elapsed


def path_without(tmp: Path, *names: str) -> str:
    """PATH の実行ファイルを ``names`` を除いて 1 つのディレクトリへ集めた PATH を返す"""
    d = tmp / ("bin-without-" + "-".join(names))
    if d.exists():
        return str(d)
    d.mkdir(parents=True)
    seen: set[str] = set()
    for dirp in os.environ.get("PATH", "").split(os.pathsep):
        try:
            entries = os.listdir(dirp)
        except OSError:
            continue
        for e in entries:
            if e in seen or e in names:
                continue
            full = os.path.join(dirp, e)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                seen.add(e)
                os.symlink(full, d / e)
    return str(d)


def snapshot(root: Path, subs: dict[str, str]) -> dict[str, str]:
    """ボリュームの並び・リンクの先・ファイルの中身。リンクの先のルートは ``subs`` で置き換える"""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            if os.path.islink(p):
                target = os.readlink(p)
                for k, v in subs.items():
                    target = target.replace(k, v)
                out[rel] = "L " + target
            elif os.path.isdir(p):
                out[rel] = "D"
            else:
                out[rel] = "F " + Path(p).read_text(errors="replace")
    return out


class Volumes:
    """1 回ぶんの共通のボリューム・グループのボリューム・ホーム"""

    def __init__(self, base: Path):
        self.base = base
        self.ai = base / "persistent" / "ai"
        self.grp = base / "persistent" / "group"

    def home(self, i: int | str) -> Path:
        h = self.base / f"home-{i}"
        h.mkdir(parents=True, exist_ok=True)
        return h

    def link_once(self) -> None:
        """使い捨てのホームで 1 回走らせ、すべてのリンクを正しい状態にする"""
        r = run_script(setup_cmd(self.home("prep"), self.ai, self.grp), self.base)
        assert r.returncode == 0, r.stderr

    def shared(self, name: str) -> Path:
        return self.grp / ".claude" / name

    def stage_jobs(self, n: int, prefix: str = "") -> list[str]:
        return [prefix + setup_cmd(self.home(i), self.ai, self.grp) for i in range(n)]

    def snap(self) -> tuple[dict, dict]:
        subs = {str(self.ai): "<AI>", str(self.grp): "<GROUP>"}
        return snapshot(self.ai, subs), snapshot(self.grp, subs)


def assert_all_ok(jobs: list[Job]) -> None:
    for i, j in enumerate(jobs):
        assert j.rc == 0, f"job {i} rc={j.rc}\n{j.text}"


def assert_no_ln_lines(jobs: list[Job]) -> None:
    """受け入れ条件 10・I4: 0 で終わったリンクの段に `ln:` で始まる行が無い"""
    for i, j in enumerate(jobs):
        if j.rc != 0:
            continue
        bad = [line for line in j.text.splitlines() if line.startswith("ln:")]
        assert not bad, f"job {i}: {bad}"


def assert_no_leftovers(v: Volumes) -> None:
    """受け入れ条件 11・I8: 一時的な名前のエントリもロックのためのエントリも無い
    (置き換わった共有のリンクの控えは #372 で残すものなので除く)"""
    names = [n for n in os.listdir(v.grp / ".claude") if ".replaced-" not in n]
    assert sorted(names) == sorted(SHARED_CLAUDE)
    assert set(os.listdir(v.ai)) <= {".codex", ".serena", ".ssh", ".kiro", "share", ".claude"}
    assert set(os.listdir(v.grp)) <= {".claude.json", ".claude", ".gemini", ".local",
                                      ".shellrc.d"}


def assert_shared_links(v: Volumes) -> None:
    for name in SHARED_CLAUDE:
        p = v.shared(name)
        assert p.is_symlink(), f"{name} が symlink でない"
        assert os.readlink(p) == str(v.ai / ".claude" / name)


def assert_no_nested_links(v: Volumes) -> None:
    """I2: 共通のボリュームのディレクトリの中に、自分自身の名前のリンクが無い"""
    for name in ("plugins", "skills", "commands"):
        assert not (v.ai / ".claude" / name / name).is_symlink(), f"{name}/{name} がある"


def check_round(v: Volumes, jobs: list[Job], elapsed: float) -> None:
    assert_all_ok(jobs)
    assert_no_ln_lines(jobs)
    assert_shared_links(v)
    assert_no_nested_links(v)
    assert_no_leftovers(v)
    assert elapsed < TIME_LIMIT, f"{elapsed:.1f} 秒かかった"


def make_home_claude(home: Path, *, plugins: int = 0) -> None:
    """どのホームにも同じ中身の ~/.claude を置く"""
    c = home / ".claude"
    c.mkdir(parents=True, exist_ok=True)
    (c / "settings.json").write_text('{"hooks": {"image": true}}')
    if plugins:
        for k in range(plugins):
            d = c / "plugins" / f"p{k:02d}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "plugin.json").write_text(f'{{"n": {k}}}')
        (c / "skills" / "s1").mkdir(parents=True, exist_ok=True)
        (c / "skills" / "s1" / "SKILL.md").write_text("skill")
        (c / "commands").mkdir(exist_ok=True)
        (c / "commands" / "c1.md").write_text("cmd")
        (c / "CLAUDE.md").write_text("# memo")


# ---------------------------------------------------------------------------
# 受け入れ条件 1・2: settings.json だけが通常のファイル
# ---------------------------------------------------------------------------

def _settings_as_file(v: Volumes) -> None:
    v.link_once()
    (v.ai / ".claude" / "settings.json").write_text('{"keep": true}')
    p = v.shared("settings.json")
    p.unlink()
    p.write_text('{"replaced": true}')


def test_two_stages_relink_a_settings_file(tmp_path):
    """受け入れ条件 1"""
    for r in range(ROUNDS_PAIR):
        v = Volumes(tmp_path / f"r{r}")
        _settings_as_file(v)
        jobs, elapsed = run_concurrent(v.base / "run", v.stage_jobs(2))
        check_round(v, jobs, elapsed)


def test_eight_stages_relink_a_settings_file_and_keep_the_shared_content(tmp_path):
    """受け入れ条件 2"""
    for r in range(ROUNDS):
        v = Volumes(tmp_path / f"r{r}")
        _settings_as_file(v)
        jobs, elapsed = run_concurrent(v.base / "run", v.stage_jobs(8))
        check_round(v, jobs, elapsed)
        assert (v.ai / ".claude" / "settings.json").read_text() == '{"keep": true}'


# ---------------------------------------------------------------------------
# 受け入れ条件 3: 空のボリュームから 8 個。1 個だけ走らせた結果と同じ
# ---------------------------------------------------------------------------

def _single_run_snapshot(tmp: Path, *, plugins: int = 0, path: str | None = None):
    v = Volumes(tmp)
    make_home_claude(v.home(0), plugins=plugins)
    r = run_script(setup_cmd(v.home(0), v.ai, v.grp), v.base, path=path)
    assert r.returncode == 0, r.stderr
    return v.snap()


def test_eight_stages_on_empty_volumes_match_a_single_run(tmp_path):
    """受け入れ条件 3"""
    expected = _single_run_snapshot(tmp_path / "single")
    for r in range(ROUNDS):
        v = Volumes(tmp_path / f"r{r}")
        for i in range(8):
            make_home_claude(v.home(i))
        jobs, elapsed = run_concurrent(v.base / "run", v.stage_jobs(8))
        check_round(v, jobs, elapsed)
        assert v.snap() == expected


# ---------------------------------------------------------------------------
# 受け入れ条件 4〜6
# ---------------------------------------------------------------------------

def test_eight_stages_on_a_new_group_volume(tmp_path):
    """受け入れ条件 4: 共通のボリュームに中身があり、グループのボリュームが空"""
    for r in range(ROUNDS):
        v = Volumes(tmp_path / f"r{r}")
        v.link_once()
        shutil.rmtree(v.grp)
        jobs, elapsed = run_concurrent(v.base / "run", v.stage_jobs(8))
        check_round(v, jobs, elapsed)


def test_eight_stages_replace_a_real_plugins_directory(tmp_path):
    """受け入れ条件 5"""
    for r in range(ROUNDS):
        v = Volumes(tmp_path / f"r{r}")
        v.link_once()
        p = v.shared("plugins")
        p.unlink()
        (p / "local").mkdir(parents=True)
        (p / "local" / "x.json").write_text("{}")
        jobs, elapsed = run_concurrent(v.base / "run", v.stage_jobs(8))
        check_round(v, jobs, elapsed)


@pytest.mark.parametrize("kind", ["other-target", "broken"])
@pytest.mark.parametrize("name", ["plugins", "settings.json"])
def test_eight_stages_fix_a_wrong_link(tmp_path, kind, name):
    """受け入れ条件 6"""
    for r in range(ROUNDS):
        v = Volumes(tmp_path / f"r{r}")
        v.link_once()
        p = v.shared(name)
        p.unlink()
        if kind == "other-target":
            other = v.base / "other" / name
            other.parent.mkdir(parents=True)
            other.mkdir() if name == "plugins" else other.write_text("{}")
            p.symlink_to(other)
        else:
            p.symlink_to(v.base / "nowhere" / name)
        jobs, elapsed = run_concurrent(v.base / "run", v.stage_jobs(8))
        check_round(v, jobs, elapsed)


# ---------------------------------------------------------------------------
# 受け入れ条件 7: 修正前の手順で張る相手と並ぶ
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["settings.json", "plugins"])
def test_a_stage_survives_an_old_partner(tmp_path, name):
    """受け入れ条件 7。相手が非 0 で終わることは許す"""
    for r in range(ROUNDS_PAIR):
        v = Volumes(tmp_path / f"r{r}")
        v.link_once()
        p = v.shared(name)
        p.unlink()
        if name == "plugins":
            p.mkdir()
        else:
            p.write_text("{}")
        # 相手は修正前のイメージのコンテナと同じく、リンクの段の全体を修正前の手順で走らせる
        partner = "old_funcs; " + setup_cmd(v.home(1), v.ai, v.grp)
        jobs, elapsed = run_concurrent(
            v.base / "run", [setup_cmd(v.home(0), v.ai, v.grp), partner])
        new = jobs[0]
        assert new.rc == 0, new.text
        assert_no_ln_lines([new])
        assert p.is_symlink()
        assert os.readlink(p) == str(v.ai / ".claude" / name)
        assert elapsed < TIME_LIMIT


# ---------------------------------------------------------------------------
# 受け入れ条件 9・I3: 張れない状態は成功として扱わない
# ---------------------------------------------------------------------------

@NOT_ROOT
def test_unwritable_group_claude_fails_with_the_link_path(tmp_path):
    v = Volumes(tmp_path)
    claude = v.grp / ".claude"
    claude.mkdir(parents=True)
    claude.chmod(0o555)
    try:
        r = run_script(setup_cmd(v.home(0), v.ai, v.grp), tmp_path)
    finally:
        claude.chmod(0o755)
    assert r.returncode != 0
    assert any(str(claude / "plugins") in line and "ERROR" in line
               for line in r.stderr.splitlines()), r.stderr


@NOT_ROOT
def test_unwritable_seed_destination_fails_with_the_entry_path(tmp_path):
    """I9 (作れない): コピー先の親に書き込めないと非 0 で、エントリのパスを出す"""
    v = Volumes(tmp_path)
    make_home_claude(v.home(0))
    dest_parent = v.ai / ".claude"
    dest_parent.mkdir(parents=True)
    dest_parent.chmod(0o555)
    try:
        r = run_script(setup_cmd(v.home(0), v.ai, v.grp), tmp_path)
    finally:
        dest_parent.chmod(0o755)
    assert r.returncode != 0
    assert any(str(dest_parent / "settings.json") in line and "ERROR" in line
               for line in r.stderr.splitlines()), r.stderr


# ---------------------------------------------------------------------------
# I1・I2: 相手が先に張り終えていた
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["settings.json", "plugins"])
def test_link_already_made_by_another_process_is_success(tmp_path, name):
    """張る操作が失敗しても、読み直して正しければ 0。ディレクトリの中へ入れ子を作らない"""
    v = Volumes(tmp_path)
    v.link_once()
    link = v.shared(name)
    target = v.ai / ".claude" / name
    # 1 回目の「消す」の直後に、ほかのプロセスが張り終えた状態を作る
    pre = (f'rm() {{ command rm "$@"; command ln -s "{target}" "{link}"; '
           f'unset -f rm; }}')
    link.unlink()
    link.write_text("{}") if name == "settings.json" else link.mkdir()
    r = run_script(f'devbase_link_setting "{link}" "{target}"', tmp_path, pre=pre)
    assert r.returncode == 0, r.stderr
    assert "ln:" not in r.stdout + r.stderr
    assert os.readlink(link) == str(target)
    assert_no_nested_links(v)


# ---------------------------------------------------------------------------
# I6・I9: ロックなしで進む
# ---------------------------------------------------------------------------

def test_eight_stages_without_flock_match_a_single_run(tmp_path):
    """I6・I9 (ロックなしの退避): 退避が重なっても 1 個だけの結果と同じ"""
    path = path_without(tmp_path, "flock")
    expected = _single_run_snapshot(tmp_path / "single", plugins=12, path=path)
    for r in range(ROUNDS):
        v = Volumes(tmp_path / f"r{r}")
        for i in range(8):
            make_home_claude(v.home(i), plugins=12)
        jobs, elapsed = run_concurrent(v.base / "run", v.stage_jobs(8), path=path)
        check_round(v, jobs, elapsed)
        assert v.snap() == expected


@pytest.mark.parametrize("kind", ["file", "dir"])
def test_only_one_process_creates_an_entry(tmp_path, kind):
    """I9 (作れるのは 1 つ): 0 を返すのは 1 個だけ。既にあれば 1 で中身を変えない"""
    for r in range(ROUNDS):
        target = tmp_path / f"r{r}" / "entry"
        target.parent.mkdir(parents=True)
        body = (f'rc=0; devbase_create_entry {kind} "{target}" || rc=$?; '
                f'echo "rc=$rc"')
        jobs, _ = run_concurrent(tmp_path / f"r{r}" / "run", [body] * 8)
        rcs = [j.out.strip() for j in jobs]
        assert rcs.count("rc=0") == 1, rcs
        assert rcs.count("rc=1") == 7, rcs
    existing = tmp_path / "existing"
    if kind == "file":
        existing.write_text("keep")
    else:
        existing.mkdir()
        (existing / "child").write_text("keep")
    r = run_script(f'rc=0; devbase_create_entry {kind} "{existing}" || rc=$?; echo "rc=$rc"',
                   tmp_path)
    assert r.stdout.strip() == "rc=1"
    if kind == "file":
        assert existing.read_text() == "keep"
    else:
        assert (existing / "child").read_text() == "keep"


def test_without_perl_a_single_run_matches_the_run_with_perl(tmp_path):
    """決定 10: perl が無くても 0 で終わり、結果が同じ"""
    with_perl = _single_run_snapshot(tmp_path / "with", plugins=3)
    without = _single_run_snapshot(tmp_path / "without", plugins=3,
                                   path=path_without(tmp_path, "perl"))
    assert without == with_perl


# ---------------------------------------------------------------------------
# I5・I6・I7・可用性: ロックそのもの (flock が要る)
# ---------------------------------------------------------------------------

def _hold_lock(ai: Path, seconds: float) -> subprocess.Popen:
    """共通のボリュームのルートにロックを掛けて ``seconds`` 秒持つプロセス"""
    ai.mkdir(parents=True, exist_ok=True)
    ready = ai.parent / "held"
    p = subprocess.Popen([_bash(), "-c",
                          f'exec 9<"{ai}"; flock 9; : > "{ready}"; exec sleep {seconds}'])
    deadline = time.monotonic() + 10
    while not ready.exists():
        assert time.monotonic() < deadline, "ロックを持つプロセスが用意できない"
        time.sleep(0.02)
    ready.unlink()
    return p


@NEEDS_FLOCK
def test_stage_waits_for_the_lock_holder(tmp_path):
    """I5: ロックを持つプロセスがある間は退避もリンクも始めず、手放された後に進む"""
    v = Volumes(tmp_path)
    make_home_claude(v.home(0))
    holder = _hold_lock(v.ai, 2)
    try:
        stage = subprocess.Popen(
            [_bash(), "-c", f'set -e\nDEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"\n'
                            + setup_cmd(v.home(0), v.ai, v.grp)],
            env=_base_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1)
        assert not (v.ai / ".claude").exists()
        assert not (v.grp / ".claude").exists()
        _, err = stage.communicate(timeout=TIME_LIMIT)
    finally:
        holder.kill()
        holder.wait()
    assert stage.returncode == 0, err
    assert "WARNING" not in err
    assert_shared_links(v)


@NEEDS_FLOCK
def test_stage_goes_on_without_the_lock_after_the_wait_limit(tmp_path):
    """I6: 手放さない持ち主がいても、待ち時間の上限の後に警告を出して 0 で終わる"""
    v = Volumes(tmp_path)
    holder = _hold_lock(v.ai, 60)
    try:
        started = time.monotonic()
        r = run_script(setup_cmd(v.home(0), v.ai, v.grp), tmp_path,
                       pre="DEVBASE_LINK_LOCK_WAIT=1")
        elapsed = time.monotonic() - started
    finally:
        holder.kill()
        holder.wait()
    assert r.returncode == 0, r.stderr
    warnings = [line for line in r.stderr.splitlines() if line.startswith("WARNING")]
    assert len(warnings) == 1 and str(v.ai) in warnings[0]
    assert elapsed < TIME_LIMIT
    assert_shared_links(v)


@NEEDS_FLOCK
def test_killed_lock_holder_does_not_block_the_next_stage(tmp_path):
    """可用性: 持ち主が kill されると、後から始めたリンクの段は待ち切らずに 0 で終わる"""
    v = Volumes(tmp_path)
    holder = _hold_lock(v.ai, 60)
    stage = subprocess.Popen(
        [_bash(), "-c", f'set -e\nDEVBASE_ENTRYPOINT_LIB_ONLY=1 . "{ENTRYPOINT}"\n'
                        + setup_cmd(v.home(0), v.ai, v.grp)],
        env=_base_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    started = time.monotonic()
    time.sleep(1)
    holder.kill()
    holder.wait()
    _, err = stage.communicate(timeout=TIME_LIMIT)
    assert stage.returncode == 0, err
    assert "WARNING" not in err
    assert time.monotonic() - started < TIME_LIMIT
    assert_shared_links(v)


@NEEDS_FLOCK
def test_lock_is_released_when_the_stage_returns(tmp_path):
    """I7: 戻った後、呼んだシェルが生きている間に、別のプロセスが待たずにロックを取れる"""
    v = Volumes(tmp_path)
    r = run_script(setup_cmd(v.home(0), v.ai, v.grp)
                   + f'\n( exec 8<"{v.ai}"; flock -n 8 ) && echo free || echo held',
                   tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().endswith("free")


def test_stage_without_a_link_left_by_a_killed_stage(tmp_path):
    """可用性: 張りかけで終わった (共有のリンクの位置が無い) 状態から 0 で終わる"""
    v = Volumes(tmp_path)
    v.link_once()
    v.shared("settings.json").unlink()
    r = run_script(setup_cmd(v.home(1), v.ai, v.grp), tmp_path)
    assert r.returncode == 0, r.stderr
    assert_shared_links(v)


# ---------------------------------------------------------------------------
# 移行性・性能
# ---------------------------------------------------------------------------

def test_old_procedure_runs_after_the_new_stage(tmp_path):
    """移行性: 修正後のあとに修正前の手順を走らせても 0。並びも修正前の手順と同じ"""
    new = Volumes(tmp_path / "new")
    make_home_claude(new.home(0), plugins=2)
    assert run_script(setup_cmd(new.home(0), new.ai, new.grp), new.base).returncode == 0
    old = Volumes(tmp_path / "old")
    make_home_claude(old.home(0), plugins=2)
    r = run_script(setup_cmd(old.home(0), old.ai, old.grp), old.base, old=True)
    assert r.returncode == 0, r.stderr
    assert new.snap() == old.snap()
    r = run_script(setup_cmd(new.home(1), new.ai, new.grp), new.base, old=True)
    assert r.returncode == 0, r.stderr
    assert_shared_links(new)


def test_single_stage_is_not_much_slower_than_the_old_procedure(tmp_path):
    """性能: 1 個だけの所要が、修正前の手順の所要に 1 秒を足した値を超えない

    所要は、走らせたシェルとその子が使った CPU 時間 (ユーザーとシステムの和) で測る。経過時間は
    並列で流すほかのケースの負荷で倍近くまで揺れる (修正後 2.97 秒 / 修正前 1.62 秒で落ちた)。
    相手のいない 1 個のリンクの段は、ロックを待たず (``flock -w`` はすぐ取れる) 眠らないため、
    所要のほとんどが CPU 時間であり、比べる意味は変わらない。
    """
    def measure(old: bool, i: int) -> float:
        v = Volumes(tmp_path / f"{'old' if old else 'new'}-{i}")
        make_home_claude(v.home(0), plugins=3)
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        r = run_script(setup_cmd(v.home(0), v.ai, v.grp), v.base, old=old)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        assert r.returncode == 0, r.stderr
        return (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)

    # 交互に測り、最小を比べる
    old_ts, new_ts = [], []
    for i in range(5):
        old_ts.append(measure(True, i))
        new_ts.append(measure(False, i))
    old_t, new_t = min(old_ts), min(new_ts)
    assert new_t <= old_t + 1.0, (old_t, new_t)
