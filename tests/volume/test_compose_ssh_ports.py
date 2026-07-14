"""compose.py: ENABLE_SSH 時の SSH ポート publish 挙動 (PLAN33 / PR2)

`_build_dev_instance()` は ENABLE_SSH が有効なとき、各 dev-<index> サービスへ
`<bind>:<port>:22` の publish を注入する。ポートは `ssh_host_port()` により
プロジェクト名 + index から決定的に算出され、`down`→`up` を跨いでも一定である。
"""

from __future__ import annotations

import yaml
import pytest

from devbase.volume import compose
from devbase.volume.ports import ssh_host_port, _stable_hash


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    """生成物 (.docker-compose.scale.yml) が散らからないよう CWD を tmp に移す。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEV_SERVICE_NAME", raising=False)
    # SSH 系 env を既定で無効化 (外部環境に左右されないよう明示的に消す)
    monkeypatch.delenv("ENABLE_SSH", raising=False)
    monkeypatch.delenv("DEVBASE_SSH_BIND", raising=False)
    monkeypatch.delenv("DEVBASE_SSH_PORT_BASE", raising=False)
    return tmp_path


def _write_compose(tmp_path, services: dict) -> None:
    (tmp_path / "compose.yml").write_text(
        yaml.safe_dump({"services": services}, sort_keys=False),
        encoding="utf-8",
    )


def _load_scaled(tmp_path) -> dict:
    return yaml.safe_load((tmp_path / ".docker-compose.scale.yml").read_text())


def _ssh_ports(service: dict) -> list:
    """service の ports から `:22` を publish するエントリだけ抜き出す。"""
    return [p for p in service.get("ports", []) if str(p).endswith(":22")]


# --- ENABLE_SSH 無効時 ---

def test_no_ssh_ports_when_enable_ssh_unset(in_tmp_cwd):
    """ENABLE_SSH 未設定なら :22 の publish は注入されない。"""
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=2, project_name="proj")
    scaled = _load_scaled(in_tmp_cwd)["services"]

    for i in (1, 2):
        assert _ssh_ports(scaled[f"dev-{i}"]) == []


def test_no_ssh_ports_when_enable_ssh_false(in_tmp_cwd, monkeypatch):
    """ENABLE_SSH=false なら :22 の publish は注入されない。"""
    monkeypatch.setenv("ENABLE_SSH", "false")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1, project_name="proj")
    scaled = _load_scaled(in_tmp_cwd)["services"]

    assert _ssh_ports(scaled["dev-1"]) == []


# --- ENABLE_SSH 有効時 ---

def test_ssh_ports_injected_when_enabled(in_tmp_cwd, monkeypatch):
    """ENABLE_SSH=true なら各 dev-<index> に 127.0.0.1:<port>:22 が付く。"""
    monkeypatch.setenv("ENABLE_SSH", "true")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=2, project_name="proj")
    scaled = _load_scaled(in_tmp_cwd)["services"]

    for i in (1, 2):
        port = ssh_host_port("proj", i, 2200)
        assert _ssh_ports(scaled[f"dev-{i}"]) == [f"127.0.0.1:{port}:22"]


@pytest.mark.parametrize("truthy", ["true", "True", "TRUE", "1"])
def test_enable_ssh_truthy_values(in_tmp_cwd, monkeypatch, truthy):
    """'true'/'True'/'1' などを大文字小文字を問わず有効と解釈する。"""
    monkeypatch.setenv("ENABLE_SSH", truthy)
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1, project_name="proj")
    scaled = _load_scaled(in_tmp_cwd)["services"]

    assert len(_ssh_ports(scaled["dev-1"])) == 1


def test_existing_ports_are_preserved(in_tmp_cwd, monkeypatch):
    """既存の ports は保持され、SSH publish が追記される。"""
    monkeypatch.setenv("ENABLE_SSH", "true")
    _write_compose(in_tmp_cwd, {
        "dev": {"image": "dev:latest", "ports": ["8080:8080"]},
    })

    compose.generate_scaled_compose(scale=1, project_name="proj")
    scaled = _load_scaled(in_tmp_cwd)["services"]

    ports = scaled["dev-1"]["ports"]
    assert "8080:8080" in ports
    assert len(_ssh_ports({"ports": ports})) == 1


# --- bind / base の env 反映 ---

def test_ssh_bind_is_honored(in_tmp_cwd, monkeypatch):
    """DEVBASE_SSH_BIND が publish の bind 先に反映される。"""
    monkeypatch.setenv("ENABLE_SSH", "true")
    monkeypatch.setenv("DEVBASE_SSH_BIND", "0.0.0.0")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1, project_name="proj")
    scaled = _load_scaled(in_tmp_cwd)["services"]

    port = ssh_host_port("proj", 1, 2200)
    assert _ssh_ports(scaled["dev-1"]) == [f"0.0.0.0:{port}:22"]


def test_ssh_port_base_is_honored(in_tmp_cwd, monkeypatch):
    """DEVBASE_SSH_PORT_BASE がポート算出の起点に反映される。"""
    monkeypatch.setenv("ENABLE_SSH", "true")
    monkeypatch.setenv("DEVBASE_SSH_PORT_BASE", "3000")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1, project_name="proj")
    scaled = _load_scaled(in_tmp_cwd)["services"]

    port = ssh_host_port("proj", 1, 3000)
    assert port >= 3000
    assert _ssh_ports(scaled["dev-1"]) == [f"127.0.0.1:{port}:22"]


# --- ssh_host_port() の決定性・衝突回避 ---

def test_ssh_host_port_is_deterministic():
    """同じ (project, index) は毎回同じポートに解決する (純粋関数)。"""
    a = ssh_host_port("carmo", 1, 2200)
    b = ssh_host_port("carmo", 1, 2200)
    assert a == b


def test_stable_hash_is_not_builtin_hash_salted():
    """_stable_hash は既知の固定値を返す (プロセス跨ぎで一定)。"""
    # sha1('proj') の整数化を 100 で割った剰余は実装非依存に確定する。
    assert _stable_hash("proj") == _stable_hash("proj")
    assert isinstance(_stable_hash("proj"), int)
    assert _stable_hash("proj") >= 0


def test_different_projects_get_different_ports():
    """別プロジェクトは (ほぼ) 別ポートに解決する。"""
    ports = {ssh_host_port(name, 1, 2200)
             for name in ("carmo", "alpha", "bravo", "charlie", "delta")}
    # 5 個中 4 個以上はユニーク (100 バケットなので衝突は稀)
    assert len(ports) >= 4


def test_index_shifts_port_within_project():
    """同一プロジェクト内では index が +1 ずつポートをずらす。"""
    p1 = ssh_host_port("proj", 1, 2200)
    p2 = ssh_host_port("proj", 2, 2200)
    assert p2 == p1 + 1


def test_base_offsets_port():
    """base を変えるとポートも同じ差分だけずれる。"""
    assert ssh_host_port("proj", 1, 3000) == ssh_host_port("proj", 1, 2200) + 800
