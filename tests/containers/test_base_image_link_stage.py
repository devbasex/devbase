"""base イメージのコンテナでの同時起動 (#357 受け入れ条件 13)

``devbase-base:latest`` のコンテナに作業ツリーの ``entrypoint.sh`` を読み取り専用でマウントし、
使い捨ての名前のボリュームで 8 個を同時に起動する。イメージは建て直さず、利用者のボリュームにも
``devbase up`` にも触れない (設計の決定 9)。使ったボリュームとコンテナは最後に消す。

base イメージの ``ln`` と ``mkdir`` は uutils coreutils で、CI の runner (GNU coreutils) とは振る舞いが
違う。排他の作成 (I9) をイメージの道具で確かめるのもここで行う。

**Docker が無い / イメージが無いときは skip する。** CI では skip になり、完了判定のときに開発者の
端末で走る。
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from tests.conftest import host_docker_env

IMAGE = "devbase-base:latest"
ENTRYPOINT = Path(__file__).resolve().parents[2] / "containers" / "base" / "entrypoint.sh"
# 1 回ぶんに docker run が 10 回要る。並列で流すとほかのケースと重なり 1 回 15 秒を超えるため絞る
ROUNDS = 2
CONTAINERS = 8

# 全ケースを同じワーカーへ寄せ、8 個のコンテナの起動をほかのケースと重ねない
pytestmark = pytest.mark.xdist_group("base_image_link_stage")


def _docker(*args: str, timeout: float = 120, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout,
                          check=check, env=host_docker_env())


def _docker_unavailable() -> str | None:
    if shutil.which("docker") is None:
        return "docker が PATH に無い"
    try:
        _docker("info", timeout=30)
    except (subprocess.SubprocessError, OSError):
        return "docker daemon へ繋がらない"
    try:
        out = _docker("image", "inspect", IMAGE, timeout=30, check=False)
    except (subprocess.SubprocessError, OSError):
        return f"{IMAGE} を調べられない"
    if out.returncode != 0:
        return f"{IMAGE} が無い"
    return None


@pytest.fixture
def docker_ready():
    reason = _docker_unavailable()
    if reason:
        pytest.skip(reason)


@pytest.fixture
def volumes(docker_ready):
    """使い捨ての名前の共通のボリューム・グループのボリューム・合図のボリューム"""
    tag = uuid.uuid4().hex[:8]
    names = {k: f"devbase-test357-{k}-{tag}" for k in ("ai", "grp", "go")}
    for name in names.values():
        _docker("volume", "create", name)
    containers: list[str] = []
    try:
        yield names, containers
    finally:
        if containers:
            _docker("rm", "-f", *containers, check=False)
        _docker("volume", "rm", "-f", *names.values(), check=False)


def _mounts(names: dict[str, str]) -> list[str]:
    return ["-v", f"{names['ai']}:/persistent/ai", "-v", f"{names['grp']}:/persistent/group",
            "-v", f"{names['go']}:/go", "-v", f"{ENTRYPOINT}:/entrypoint.sh:ro"]


def _run(names: dict[str, str], script: str, *, user: str | None = None):
    args = ["run", "--rm", *_mounts(names), "--entrypoint", "bash"]
    if user:
        args += ["--user", user]
    return _docker(*args, IMAGE, "-c", script, timeout=300, check=False)


def test_eight_containers_relink_a_settings_file(volumes):
    """受け入れ条件 13: 8 個とも entrypoint が 0 で終わる"""
    names, containers = volumes
    # 1 個だけ起動してリンクを張った状態を作る
    r = _run(names, "DEVBASE_ACCOUNT_GROUP=t357 exec bash /entrypoint.sh true")
    assert r.returncode == 0, r.stdout + r.stderr
    for rnd in range(ROUNDS):
        # 受け入れ条件 1 の状態 (settings.json だけ通常のファイル) にし、合図を消す
        r = _run(names, "rm -f /go/go /persistent/group/.claude/settings.json && "
                        "echo '{}' > /persistent/group/.claude/settings.json && "
                        # 開発ユーザーの持ち物にする。root の持ち物は控えのハードリンクを
                        # 作れず、張らずに残す (#372)
                        "chown --reference=/persistent/group/.claude "
                        "/persistent/group/.claude/settings.json && "
                        "chmod 777 /go", user="root")
        assert r.returncode == 0, r.stderr
        ids = []
        for i in range(CONTAINERS):
            r = _docker("run", "-d", *_mounts(names), "-e", "DEVBASE_ACCOUNT_GROUP=t357",
                        "--entrypoint", "bash", IMAGE, "-c",
                        "until [ -e /go/go ]; do sleep 0.01; done; "
                        "exec bash /entrypoint.sh true")
            ids.append(r.stdout.strip())
        containers.extend(ids)
        r = _run(names, "touch /go/go", user="root")
        assert r.returncode == 0, r.stderr
        codes = [int(_docker("wait", cid, timeout=300).stdout.strip()) for cid in ids]
        for cid, code in zip(ids, codes):
            if code != 0:
                logs = _docker("logs", cid, check=False)
                pytest.fail(f"round {rnd}: {cid[:12]} が {code} で終わった\n"
                            f"{logs.stdout}{logs.stderr}")
        r = _run(names, "readlink /persistent/group/.claude/settings.json")
        assert r.stdout.strip() == "/persistent/ai/.claude/settings.json"


_CREATE_PROBE = r"""
set -u
DEVBASE_ENTRYPOINT_LIB_ONLY=1 . /entrypoint.sh
set +e
for kind in file dir; do
  for r in 1 2 3; do
    d=$(mktemp -d)
    for i in 1 2 3 4 5 6 7 8; do
      ( while [ ! -e "$d/go" ]; do :; done
        devbase_create_entry "$kind" "$d/entry"; echo "$?" > "$d/rc$i" ) &
    done
    sleep 0.2; : > "$d/go"; wait
    printf '%s\t%s\n' "$kind" "$(cat "$d"/rc* | sort | tr -d '\n')"
  done
done
"""

_SEED_PROBE = r"""
set -u
DEVBASE_ENTRYPOINT_LIB_ONLY=1 . /entrypoint.sh
set +e
# flock を除いた PATH (ロックなしで進むリンクの段)
nf=$(mktemp -d)
IFS=: read -r -a dirs <<< "$PATH"
for d in "${dirs[@]}"; do
  for f in "$d"/*; do
    n=${f##*/}
    [ "$n" = flock ] && continue
    [ -e "$nf/$n" ] || ln -s "$f" "$nf/$n"
  done
done
PATH=$nf

make_home() {
  mkdir -p "$1/.claude/skills/s1" "$1/.claude/commands"
  for k in 00 01 02 03 04 05 06 07 08 09 10 11; do
    mkdir -p "$1/.claude/plugins/p$k"; echo "{\"n\": \"$k\"}" > "$1/.claude/plugins/p$k/plugin.json"
  done
  echo skill > "$1/.claude/skills/s1/SKILL.md"; echo cmd > "$1/.claude/commands/c1.md"
  echo memo > "$1/.claude/CLAUDE.md"; echo '{"hooks": 1}' > "$1/.claude/settings.json"
}
snap() {
  ( cd "$1" && find . | LC_ALL=C sort | while IFS= read -r p; do
      if [ -L "$p" ]; then echo "L $p $(readlink "$p" | sed "s#^$2#ROOT#")"
      elif [ -d "$p" ]; then echo "D $p"
      else echo "F $p $(md5sum < "$p")"; fi
    done )
}
one=$(mktemp -d); make_home "$one/h0"
( set -e; devbase_setup_ai_settings "$one/h0" "$one/ai" "$one/grp" t357 ) >/dev/null 2>&1 || echo "single failed"
for r in 1 2 3; do
  b=$(mktemp -d)
  for i in 1 2 3 4 5 6 7 8; do
    make_home "$b/h$i"
    ( while [ ! -e "$b/go" ]; do :; done
      ( set -e; devbase_setup_ai_settings "$b/h$i" "$b/ai" "$b/grp" t357 ) >/dev/null 2>&1
      echo "$?" > "$b/rc$i" ) &
  done
  sleep 0.2; : > "$b/go"; wait
  rcs=$(cat "$b"/rc* | tr -d '\n')
  same=same
  [ "$(snap "$b/ai" "$b/ai")" = "$(snap "$one/ai" "$one/ai")" ] || same=diff-ai
  [ "$(snap "$b/grp" "$b/ai")" = "$(snap "$one/grp" "$one/ai")" ] || same=diff-grp
  printf 'seed\t%s\t%s\n' "$rcs" "$same"
done
"""


def test_only_one_process_creates_an_entry_with_the_image_tools(docker_ready):
    """I9 (base イメージの道具): 8 個同時でも 0 を返すのは 1 個だけ"""
    r = _docker("run", "--rm", "-v", f"{ENTRYPOINT}:/entrypoint.sh:ro", "--entrypoint", "bash",
                IMAGE, "-c", _CREATE_PROBE, timeout=300, check=False)
    assert r.returncode == 0, r.stderr
    lines = [line.split("\t") for line in r.stdout.splitlines() if "\t" in line]
    assert len(lines) == 6, r.stdout + r.stderr
    for kind, rcs in lines:
        assert rcs == "01111111", f"{kind}: {rcs}"


def test_eight_stages_without_flock_match_a_single_run_with_the_image_tools(docker_ready):
    """I9 (base イメージの道具): ロックなしで 8 個同時でも、1 個だけ走らせた結果と同じ"""
    r = _docker("run", "--rm", "-v", f"{ENTRYPOINT}:/entrypoint.sh:ro", "--entrypoint", "bash",
                IMAGE, "-c", _SEED_PROBE, timeout=300, check=False)
    assert r.returncode == 0, r.stderr
    assert "single failed" not in r.stdout
    lines = [line.split("\t") for line in r.stdout.splitlines() if line.startswith("seed\t")]
    assert len(lines) == 3, r.stdout + r.stderr
    for _, rcs, same in lines:
        assert rcs == "00000000" and same == "same", (rcs, same)
