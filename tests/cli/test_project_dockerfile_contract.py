"""通常のビルドと `--expires` の判定が同じプロジェクトの Dockerfile を読む (#415 I9・受け入れ条件 4)。

1 つの入力の表 (compose の構成と置くファイル) を、2 つの経路の両方に通す。

- 通常のビルド: `bin/devbase build` を実プロセスで起動し (場所を出す入口は本物)、建てた段から
  読んだ Dockerfile を知る。表の各 Dockerfile は別々の `devbase-<名前>` を直の親に持つ
- `--expires` の判定: 同じ構成の開発サービスを `container._get_base_image_ref` に渡す

どちらかの経路だけが別の規則で場所を決めると、期待する Dockerfile と食い違って落ちる。
あわせて、場所の決め方 (`project_dockerfile_path`) の表を直接確かめる。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbase.commands import container
from devbase.utils.dockerfile import project_dockerfile_path

# Dockerfile の置き場 (プロジェクトからの相対) と、その直の親の名前
_FILES = {
    "Dockerfile": "decoy",
    "dev/Dockerfile": "str",
    "ctx/Dockerfile": "ctx",
    "Dockerfile.only": "only",
    "ctx/Dockerfile.both": "both",
    "abs/Dockerfile.abs": "abs",
    "env/Dockerfile": "env",
    "other/Dockerfile": "decoy",
    "devsvc/Dockerfile": "order",
}


def _case_services(project: Path) -> dict:
    """行の名前 -> (開発サービスより前のサービスも含む構成, 読むべき直の親の名前か None)。

    `docker compose config` は `context` を展開済みの絶対パスで出す。`context` に環境変数を含む行は、
    compose が展開した後の値 (`${CTX_DIR}` -> 絶対パス) を両方の経路が受ける。
    """
    p = str(project)
    return {
        "string": ({"dev": {"build": f"{p}/dev"}}, "str"),
        "context_only": ({"dev": {"build": {"context": f"{p}/ctx"}}}, "ctx"),
        "dockerfile_only": ({"dev": {"build": {"dockerfile": "Dockerfile.only"}}}, "only"),
        "both": ({"dev": {"build": {"context": f"{p}/ctx", "dockerfile": "Dockerfile.both"}}}, "both"),
        "absolute_dockerfile": (
            {"dev": {"build": {"context": f"{p}/ctx", "dockerfile": f"{p}/abs/Dockerfile.abs"}}}, "abs"),
        "context_with_env": ({"dev": {"build": {"context": f"{p}/env", "dockerfile": "Dockerfile"}}}, "env"),
        "other_service_first": ({
            "other": {"build": {"context": f"{p}/other", "dockerfile": "Dockerfile"}},
            "dev": {"build": {"context": f"{p}/devsvc", "dockerfile": "Dockerfile"}},
        }, "order"),
        "no_build": ({"dev": {"image": "example/dev:latest"}}, None),
    }


_CASES = ["string", "context_only", "dockerfile_only", "both", "absolute_dockerfile",
          "context_with_env", "other_service_first", "no_build"]


def _project(root: Path) -> Path:
    project = root / "myproj"
    project.mkdir(parents=True)
    (project / "compose.yml").write_text("services: {}\n")
    for rel, name in _FILES.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"FROM devbase-{name}\n")
    return project


def _build_path_parent(compose_wrapper, project: Path, services: dict):
    """通常のビルドが読んだ Dockerfile の直の親の名前。I8 の分岐へ進んだら None。"""
    compose_wrapper.compose_config(services)
    result = compose_wrapper.run(["build"], cwd=project)
    assert result.returncode == 0, result.stdout + result.stderr
    built = [line.split("buildx build --load -t ", 1)[1].split(":", 1)[0]
             for line in result.stdout.splitlines()
             if line.startswith("UV:") and "buildx build --load -t " in line]
    if "[1/2] devbase-base already exists (use --no-cache to rebuild)" in result.stdout:
        assert built == [], result.stdout
        return None
    assert len(built) == 1, result.stdout
    return built[0][len("devbase-"):]


def _expires_path_parent(project: Path, services: dict, monkeypatch):
    monkeypatch.chdir(project)
    ref = container._get_base_image_ref(services["dev"])
    if ref is None:
        return None
    return ref.split(":", 1)[0][len("devbase-"):]


@pytest.mark.parametrize("case", _CASES)
def test_both_paths_read_the_same_project_dockerfile(compose_wrapper, monkeypatch, case):
    for name in sorted(set(_FILES.values())):
        compose_wrapper.container(name)
    project = _project(compose_wrapper.work)
    services, expected = _case_services(project)[case]

    by_build = _build_path_parent(compose_wrapper, project, services)
    by_expires = _expires_path_parent(project, services, monkeypatch)

    assert by_build == expected
    assert by_expires == expected


# ---- 場所の決め方の表 ----


@pytest.mark.parametrize("dev_service, expected", [
    ({}, None),
    ({"image": "x"}, None),
    ({"build": None}, None),
    ({"build": ""}, None),
    ({"build": {}}, None),
    ({"build": "./dev"}, Path("dev/Dockerfile")),
    ({"build": "/abs/dev"}, Path("/abs/dev/Dockerfile")),
    ({"build": {"context": "/p/ctx"}}, Path("/p/ctx/Dockerfile")),
    ({"build": {"dockerfile": "Dockerfile.dev"}}, Path("Dockerfile.dev")),
    ({"build": {"context": "/p/ctx", "dockerfile": "sub/D"}}, Path("/p/ctx/sub/D")),
    ({"build": {"context": "/p/ctx", "dockerfile": "/q/D"}}, Path("/q/D")),
    ({"build": {"context": "/p/ctx", "dockerfile_inline": "FROM x"}}, Path("/p/ctx/Dockerfile")),
])
def test_project_dockerfile_path_table(dev_service, expected):
    assert project_dockerfile_path(dev_service) == expected
