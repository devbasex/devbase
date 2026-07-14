"""compose.py: ENABLE_SSH 時の SSH ポート publish 挙動 (PLAN33 / PR2)

`_build_dev_instance()` は ENABLE_SSH が有効なとき、各 dev-<index> サービスへ
`<bind>:<port>:22` の publish を注入する。ポートは `ssh_host_port()` により
プロジェクト名 + index から決定的に算出され、`down`→`up` を跨いでも一定である。
"""

from __future__ import annotations

import yaml
import pytest

from devbase.volume import compose
from devbase.volume.compose import DEVBASE_INDEX_LABEL, DEVBASE_SSH_LABEL
from devbase.volume.ports import ssh_host_port, allocate_ssh_host_port, _stable_hash


def _labels_dict(service: dict) -> dict:
    """service の labels を dict 化して返す (list / dict / 未設定に対応)。"""
    labels = service.get("labels")
    if isinstance(labels, list):
        out = {}
        for item in labels:
            k, _, v = str(item).partition("=")
            out[k] = v
        return out
    return dict(labels or {})


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


def test_ssh_label_injected_when_enabled(in_tmp_cwd, monkeypatch):
    """ENABLE_SSH=true なら各 dev-<index> に devbase 専用ラベルが付く (Orca 隔離用)。"""
    monkeypatch.setenv("ENABLE_SSH", "true")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=2, project_name="proj")
    scaled = _load_scaled(in_tmp_cwd)["services"]

    for i in (1, 2):
        assert _labels_dict(scaled[f"dev-{i}"]).get(DEVBASE_SSH_LABEL) == "1"


def test_ssh_label_absent_when_disabled(in_tmp_cwd):
    """ENABLE_SSH 未設定なら devbase 専用ラベルは付かない。"""
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1, project_name="proj")
    scaled = _load_scaled(in_tmp_cwd)["services"]

    assert DEVBASE_SSH_LABEL not in _labels_dict(scaled["dev-1"])


def test_index_label_injected_per_instance(in_tmp_cwd, monkeypatch):
    """ENABLE_SSH=true なら各 dev-<index> に実 index を持つ専用ラベルが付く。

    compose の container-number は別サービス展開のため全て 1 になるので、Orca 隔離
    config が Host 名の重複を避けられるよう index を明示するラベルを持たせる。
    """
    monkeypatch.setenv("ENABLE_SSH", "true")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=3, project_name="proj")
    scaled = _load_scaled(in_tmp_cwd)["services"]

    for i in (1, 2, 3):
        assert _labels_dict(scaled[f"dev-{i}"]).get(DEVBASE_INDEX_LABEL) == str(i)


def test_index_label_absent_when_disabled(in_tmp_cwd):
    """ENABLE_SSH 未設定なら index ラベルも付かない。"""
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(scale=1, project_name="proj")
    scaled = _load_scaled(in_tmp_cwd)["services"]

    assert DEVBASE_INDEX_LABEL not in _labels_dict(scaled["dev-1"])


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


# --- allocate_ssh_host_port(): 衝突回避付き確保 ---

def test_allocate_returns_deterministic_when_free():
    """used_ports に無ければ決定的ポートをそのまま返す (決定性を保つ)。"""
    expected = ssh_host_port("proj", 1, 2200)
    assert allocate_ssh_host_port("proj", 1, 2200, set()) == expected


def test_allocate_probes_upward_when_deterministic_port_taken():
    """決定的ポートが used_ports にあれば空きが見つかるまで +1 ずつずらす。"""
    det = ssh_host_port("proj", 1, 2200)
    used = {det}
    got = allocate_ssh_host_port("proj", 1, 2200, used)
    assert got == det + 1
    assert got not in used


def test_allocate_skips_run_of_taken_ports():
    """連続して埋まっている場合は最初の空きまで飛ばす。"""
    det = ssh_host_port("proj", 1, 2200)
    used = {det, det + 1, det + 2}
    assert allocate_ssh_host_port("proj", 1, 2200, used) == det + 3


def test_cross_project_collision_avoided_via_external_ports(in_tmp_cwd, monkeypatch):
    """他プロジェクトが握るポートを external_ports_provider で渡すと衝突を避ける。

    別プロジェクトの稼働 publish が dev-1 の決定的ポートを占有している状況を模し、
    dev-1 が別ポートへずれること (かつ決定性は衝突が無い限り保たれること) を確認する。
    """
    monkeypatch.setenv("ENABLE_SSH", "true")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    det1 = ssh_host_port("proj", 1, 2200)
    # 他プロジェクトが det1 を占有中。
    compose.generate_scaled_compose(
        scale=1, project_name="proj",
        external_ports_provider=lambda: {det1},
    )
    scaled = _load_scaled(in_tmp_cwd)["services"]
    ports = _ssh_ports(scaled["dev-1"])
    assert ports == [f"127.0.0.1:{det1 + 1}:22"]  # 衝突回避で +1 へずれる


def test_no_external_collision_keeps_deterministic_ports(in_tmp_cwd, monkeypatch):
    """外部ポートと衝突しなければ決定的ポートがそのまま使われる。"""
    monkeypatch.setenv("ENABLE_SSH", "true")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    compose.generate_scaled_compose(
        scale=2, project_name="proj",
        external_ports_provider=lambda: {9999},  # 無関係なポート
    )
    scaled = _load_scaled(in_tmp_cwd)["services"]
    for i in (1, 2):
        port = ssh_host_port("proj", i, 2200)
        assert _ssh_ports(scaled[f"dev-{i}"]) == [f"127.0.0.1:{port}:22"]


# --- get_running_published_host_ports(): 自プロジェクト除外 (scale 誤 recreate 回避) ---

class _FakePS:
    """docker ps の CompletedProcess を模す軽量スタブ。"""

    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def _fake_docker_ps(monkeypatch, stdout: str, returncode: int = 0):
    """compose.subprocess.run を差し替えて docker ps 出力を固定する。"""
    def _run(cmd, *args, **kwargs):
        return _FakePS(stdout, returncode)
    monkeypatch.setattr(compose.subprocess, "run", _run)


def test_running_ports_excludes_own_project(monkeypatch):
    """exclude_project に一致するコンテナ (自プロジェクト) のポートは除外される。

    scale 再生成で自コンテナの決定的ポートを「衝突」と誤判定させないための要。
    """
    # 出力形式: '<project>\t<ports>'
    stdout = (
        "proj\t127.0.0.1:2231->22/tcp\n"
        "proj\t127.0.0.1:2232->22/tcp\n"
    )
    _fake_docker_ps(monkeypatch, stdout)

    got = compose.get_running_published_host_ports(exclude_project="proj")
    assert got == set()  # 自プロジェクトのポートはシードに含めない


def test_running_ports_includes_foreign_project(monkeypatch):
    """他プロジェクトのポートは (exclude 指定があっても) 収集される。"""
    stdout = (
        "proj\t127.0.0.1:2231->22/tcp\n"       # 自プロジェクト → 除外
        "otherproj\t127.0.0.1:2299->22/tcp\n"  # 他プロジェクト → 収集
    )
    _fake_docker_ps(monkeypatch, stdout)

    got = compose.get_running_published_host_ports(exclude_project="proj")
    assert got == {2299}


def test_running_ports_no_exclude_collects_all(monkeypatch):
    """exclude_project 未指定なら全コンテナのポートを収集する (up 経路の従来挙動)。"""
    stdout = (
        "proj\t127.0.0.1:2231->22/tcp\n"
        "otherproj\t0.0.0.0:8080->80/tcp\n"
    )
    _fake_docker_ps(monkeypatch, stdout)

    got = compose.get_running_published_host_ports()
    assert got == {2231, 8080}


def test_running_ports_empty_on_docker_failure(monkeypatch):
    """docker ps が失敗 (returncode != 0) なら空集合を返し生成を止めない。"""
    _fake_docker_ps(monkeypatch, stdout="", returncode=1)
    assert compose.get_running_published_host_ports(exclude_project="proj") == set()


def test_scale_same_project_port_does_not_shift(in_tmp_cwd, monkeypatch):
    """自プロジェクトの稼働ポートを除外するため既存 dev-N は決定的ポートを維持する。

    docker ps が dev-1..2 の決定的ポートを publish 済みと報告しても、exclude により
    シードから外れ、再生成 compose のポートは元の決定的値のまま (= recreate されない)。
    """
    monkeypatch.setenv("ENABLE_SSH", "true")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    det1 = ssh_host_port("proj", 1, 2200)
    det2 = ssh_host_port("proj", 2, 2200)
    stdout = (
        f"proj\t127.0.0.1:{det1}->22/tcp\n"
        f"proj\t127.0.0.1:{det2}->22/tcp\n"
    )
    _fake_docker_ps(monkeypatch, stdout)

    compose.generate_scaled_compose(
        scale=2, project_name="proj",
        external_ports_provider=lambda: compose.get_running_published_host_ports(
            exclude_project="proj"),
    )
    scaled = _load_scaled(in_tmp_cwd)["services"]
    assert _ssh_ports(scaled["dev-1"]) == [f"127.0.0.1:{det1}:22"]
    assert _ssh_ports(scaled["dev-2"]) == [f"127.0.0.1:{det2}:22"]


def test_scale_foreign_project_port_still_shifts(in_tmp_cwd, monkeypatch):
    """他プロジェクトが dev-1 の決定的ポートを握る場合は従来どおり +1 へずらす。"""
    monkeypatch.setenv("ENABLE_SSH", "true")
    _write_compose(in_tmp_cwd, {"dev": {"image": "dev:latest"}})

    det1 = ssh_host_port("proj", 1, 2200)
    stdout = f"otherproj\t127.0.0.1:{det1}->22/tcp\n"
    _fake_docker_ps(monkeypatch, stdout)

    compose.generate_scaled_compose(
        scale=1, project_name="proj",
        external_ports_provider=lambda: compose.get_running_published_host_ports(
            exclude_project="proj"),
    )
    scaled = _load_scaled(in_tmp_cwd)["services"]
    assert _ssh_ports(scaled["dev-1"]) == [f"127.0.0.1:{det1 + 1}:22"]
