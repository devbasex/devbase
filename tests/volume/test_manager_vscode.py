"""VS Code Server ボリュームの名前解決と作成 (PLAN36 Task 1)

``~/.vscode-server`` はコンテナの書き込みレイヤ上にあり、``devbase up`` の
``down`` → ``up`` で消える。再作成をまたいで保つため、**コンテナ 1 つにつき 1 本**の
named volume ``devbase_vscode_<project>_<index>`` を割り当てる。

VS Code Server は 1 マシン 1 セットの状態 (接続トークン・marker・ログ) を持つため、
名前にプロジェクト名とインスタンス番号の両方を含めて、scale > 1 の同時 attach
(AC3) と別プロジェクトとの同時起動 (AC4) で状態を共有させない。
"""

from __future__ import annotations

import pytest

from devbase.volume import manager


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """外部環境の COMPOSE_PROJECT_NAME に左右されないよう既定で未設定にする。"""
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    monkeypatch.delenv("DEVBASE_ACCOUNT_GROUP", raising=False)


# ---------------------------------------------------------------------------
# 名前の組み立て (AC3 / AC4)
# ---------------------------------------------------------------------------

def test_volume_name_contains_project_and_index():
    """名前は devbase_vscode_<project>_<index>。"""
    assert manager.get_vscode_volume_for("carmo-ai", 1) == \
        "devbase_vscode_carmo-ai_1"


def test_each_instance_gets_its_own_volume():
    """scale > 1 でインスタンスごとに別のボリュームになる (AC3)。"""
    names = {manager.get_vscode_volume_for("carmo-ai", i) for i in (1, 2, 3)}
    assert len(names) == 3


def test_each_project_gets_its_own_volume():
    """プロジェクトが違えば別のボリュームになる (AC4)。"""
    assert manager.get_vscode_volume_for("carmo-ai", 1) != \
        manager.get_vscode_volume_for("bi-tools", 1)


def test_project_name_falls_back_to_environment(monkeypatch):
    """省略時は COMPOSE_PROJECT_NAME を読む。

    ボリュームを作る ``ensure_volumes`` と、マウントを書く生成 compose が
    別々にプロジェクト名を決めると、作った名前とマウントする名前がずれる。
    ``devbase up`` と同じ ``get_project_name`` へ委ねて経路を 1 つにする。
    """
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "carmo-ai")
    assert manager.get_vscode_volume_for(None, 1) == "devbase_vscode_carmo-ai_1"


def test_project_name_falls_back_to_current_directory(monkeypatch, tmp_path):
    """COMPOSE_PROJECT_NAME が無ければカレントディレクトリ名 (``devbase up`` と同じ)。"""
    project = tmp_path / "carmo-ai"
    project.mkdir()
    monkeypatch.chdir(project)

    assert manager.get_vscode_volume_for(None, 1) == "devbase_vscode_carmo-ai_1"


# ---------------------------------------------------------------------------
# 正規化 (リスク: Docker のボリューム名に使えない文字)
# ---------------------------------------------------------------------------

def test_unusable_characters_are_replaced():
    """``/`` のような使えない文字は ``_`` へ置き換える。"""
    assert manager.get_vscode_volume_for("group/project", 1) == \
        "devbase_vscode_group_project_1"


def test_usable_characters_are_kept():
    """英数字・ドット・ハイフン・アンダースコアはそのまま残す。"""
    assert manager.get_vscode_volume_for("a.b-c_D9", 2) == \
        "devbase_vscode_a.b-c_D9_2"


def test_empty_project_name_does_not_break_the_volume_name():
    """名前として使えるものが残らない場合も壊れた名前を作らない。"""
    assert manager.normalize_volume_component("") == "default"
    assert manager.normalize_volume_component("//") == "__"


# ---------------------------------------------------------------------------
# 作成 (AC5: 空ボリュームでも attach できるよう、先に作っておく)
# ---------------------------------------------------------------------------

def _record_docker(monkeypatch) -> list[str]:
    created: list[str] = []
    monkeypatch.setattr(
        manager.VolumeManager, "_volume_exists", lambda self, name: False)
    monkeypatch.setattr(
        manager.VolumeManager, "_create_volume",
        lambda self, name: created.append(name) or True)
    return created


def test_ensure_volumes_creates_one_per_instance(monkeypatch):
    """scale の数だけ VS Code Server ボリュームを作る。"""
    created = _record_docker(monkeypatch)

    manager.ensure_volumes(2, project_name="carmo-ai")

    assert "devbase_vscode_carmo-ai_1" in created
    assert "devbase_vscode_carmo-ai_2" in created


def test_ensure_volumes_keeps_existing_volumes(monkeypatch):
    """既にあるボリュームは作り直さない (中身を失わない)。"""
    created: list[str] = []
    monkeypatch.setattr(
        manager.VolumeManager, "_volume_exists",
        lambda self, name: name == "devbase_vscode_carmo-ai_1")
    monkeypatch.setattr(
        manager.VolumeManager, "_create_volume",
        lambda self, name: created.append(name) or True)

    manager.ensure_volumes(1, project_name="carmo-ai")

    assert "devbase_vscode_carmo-ai_1" not in created


def test_ensure_volumes_does_not_create_volumes_beyond_scale(monkeypatch):
    """scale を超えるインスタンスのボリュームは作らない。"""
    created = _record_docker(monkeypatch)

    manager.ensure_volumes(1, project_name="carmo-ai")

    assert "devbase_vscode_carmo-ai_2" not in created
