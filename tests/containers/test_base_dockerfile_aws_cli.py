"""AWS CLI v2 の現行ダウンロード・展開・導入を Dockerfile の文言で固定する。"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "containers" / "base" / "Dockerfile"


def _aws_gcloud_run() -> str:
    blocks = []
    block = []
    for line in DOCKERFILE.read_text().splitlines():
        if line.lstrip().startswith("#"):
            continue
        if not block and not line.startswith("RUN "):
            continue
        block.append(line)
        if not line.rstrip().endswith("\\"):
            blocks.append("\n".join(block))
            block = []
    found = [b for b in blocks if "ssm_arch=" in b and "gcloud_arch=" in b]
    assert len(found) == 1
    return found[0]


def test_aws_cli_uses_uname_directly_then_unpacks_and_installs():
    """case を挟まず uname -m を URL に埋め、その ZIP を展開して導入する現状。"""
    flat = _aws_gcloud_run().replace("\\\n", " ")
    assert re.search(
        r'curl\s+-fsSL\s+"https://awscli\.amazonaws\.com/'
        r'awscli-exe-linux-\$\(uname -m\)\.zip"\s+-o\s+/tmp/awscliv2\.zip;\s*'
        r'unzip\s+-q\s+/tmp/awscliv2\.zip\s+-d\s+/tmp;\s*'
        r'/tmp/aws/install;',
        flat,
    )
