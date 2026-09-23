"""既定ユーザーによる npm globals 更新の現状固定。

実行前に現行のソースから専用イメージを用意する:
    docker build -t devbase-base:npm-globals-test containers/base
ネットワーク、ホストのマウント、sudo、prefix/PATH の変更は使わない。
"""

import shutil
import subprocess
import uuid

import pytest

IMAGE = "devbase-base:npm-globals-test"

PROBE = r"""
set -eu
printf 'uid=%s\n' "$(id -u)"
test "$(id -u)" -ne 0
# 置換対象が既にインストールされていることを確認する。
npm ls -g @openai/codex --depth=0 >/dev/null
fixture_dir=$(mktemp -d)
cd "$fixture_dir"
cat > package.json <<'JSON'
{"name":"@openai/codex","version":"0.0.0-characterization","bin":{"codex":"codex.js"}}
JSON
cat > codex.js <<'JS'
#!/usr/bin/env node
console.log("devbase-npm-global-characterization");
JS
tarball=$(npm pack --offline --ignore-scripts --no-audit --no-fund --silent)
set +e
npm install -g --offline --ignore-scripts --no-audit --no-fund "$fixture_dir/$tarball"
install_status=$?
output=$(/bin/bash -c codex)
command_status=$?
printf 'install_status=%s\ncommand_status=%s\noutput=%s\n' \
    "$install_status" "$command_status" "$output"
"""


def test_default_user_can_replace_and_run_root_installed_npm_global():
    if shutil.which("docker") is None:
        pytest.skip("docker が PATH に無い")
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=30, check=True)
    except (subprocess.SubprocessError, OSError) as error:
        pytest.skip(f"docker daemon が利用できない: {error}")
    inspected = subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True, text=True, timeout=30,
    )
    if inspected.returncode != 0:
        pytest.skip(f"{IMAGE} が無い。モジュール冒頭の docker build を実行する: {inspected.stderr}")

    name = f"devbase-npm-globals-{uuid.uuid4().hex}"
    try:
        result = subprocess.run(
            ["docker", "run", "--rm", "--name", name, "--network=none",
             "--entrypoint=/bin/bash", IMAGE, "-c", PROBE],
            capture_output=True, text=True, timeout=180,
        )
    finally:
        # --rm に加え、タイムアウトでクライアントが終了してもコンテナを残さない。
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)

    assert result.returncode == 0, result.stdout + result.stderr
    observed = dict(line.split("=", 1) for line in result.stdout.splitlines()
                    if line.startswith(("uid=", "install_status=", "command_status=", "output=")))
    assert int(observed.pop("uid")) != 0
    # 現行 Dockerfile からのイメージで実測: UID=1000、両終了コード=0、下記識別値。
    assert observed == {
        "install_status": "0",
        "command_status": "0",
        "output": "devbase-npm-global-characterization",
    }, result.stdout + result.stderr
