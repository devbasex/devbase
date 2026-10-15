"""手元の Docker とイメージが使えるかを調べる共通の部品。

建てたイメージの中身を見るテストが、使えないときに飛ばす理由を作るのに使う。Docker を起動するため、
Docker を起動せずに Dockerfile の形を読む ``dockerfile_parse.py`` とは分けて置く。
``docker_images.py`` は ``test_`` で始まらないため pytest は収集しない。
"""

from __future__ import annotations

import shutil
import subprocess

from tests.conftest import host_docker_env


def image_unavailable(image: str, missing_suffix: str = "") -> str | None:
    """Docker が使えない / ``image`` が無い理由。使えるなら None

    docker の有無 → daemon → イメージの有無の順に調べる。イメージが無いときの文は
    ``f"{image} が無い{missing_suffix}"`` で、建て方の案内は呼び出し側が ``missing_suffix`` で渡す。
    """
    if shutil.which("docker") is None:
        return "docker が PATH に無い"
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=30, check=True,
                       env=host_docker_env())
    except (subprocess.SubprocessError, OSError):
        return "docker daemon へ繋がらない"
    try:
        out = subprocess.run(["docker", "image", "inspect", image],
                             capture_output=True, timeout=30, env=host_docker_env())
    except (subprocess.SubprocessError, OSError):
        return f"{image} を調べられない"
    if out.returncode != 0:
        return f"{image} が無い{missing_suffix}"
    return None
