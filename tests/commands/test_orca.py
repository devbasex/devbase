"""commands/orca.py: Orca 用隔離 SSH config の生成/隔離/剪定/列挙 (PLAN33 / PR3)

`_render_config` は純関数として、与えた devbase ホストだけを (project, index) 順で
出力する。`_parse_inspect` は docker inspect JSON から 22/tcp を publish しかつ
compose project ラベルを持つコンテナだけを SSH target として抽出する。
本テストは実 docker を一切呼ばず、fake targets / サンプル JSON を注入して検証する。
"""

from __future__ import annotations

import types

import pytest

from devbase.commands import orca
from devbase.commands.orca import SSHTarget


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def home_in_tmp(tmp_path, monkeypatch):
    """HOME を tmp に移し、生成ファイルが実ホームを汚さないようにする。

    HostName / User を左右する env も既定で消し、外部環境に依存させない。
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("DEVBASE_ORCA_HOSTNAME", raising=False)
    monkeypatch.delenv("DEVBASE_SSH_BIND", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    return tmp_path


def _args(subcommand):
    return types.SimpleNamespace(subcommand=subcommand)


# ---------------------------------------------------------------------------
# _render_config: ヘッダ + 指定ホストのみ + 並び順
# ---------------------------------------------------------------------------

def test_render_config_basic_fields():
    targets = [SSHTarget(project="carmo", index=1, port=2231)]
    out = orca._render_config(targets, hostname="127.0.0.1", user="ubuntu")

    assert out.startswith("# Managed by devbase")
    assert "Host devbase-carmo-1" in out
    assert "  HostName 127.0.0.1" in out
    assert "  Port 2231" in out
    assert "  User ubuntu" in out
    assert "  IdentityFile ~/.ssh/id_ed25519" in out
    assert "  StrictHostKeyChecking accept-new" in out


def test_render_config_sorts_by_project_then_index():
    targets = [
        SSHTarget(project="bravo", index=1, port=2300),
        SSHTarget(project="alpha", index=2, port=2211),
        SSHTarget(project="alpha", index=1, port=2210),
    ]
    out = orca._render_config(targets, hostname="127.0.0.1", user="ubuntu")

    order = [
        out.index("Host devbase-alpha-1"),
        out.index("Host devbase-alpha-2"),
        out.index("Host devbase-bravo-1"),
    ]
    assert order == sorted(order)


def test_render_config_isolation_only_devbase_hosts():
    """2 プロジェクトぶんの target を与えても devbase-* 以外の Host は現れない。"""
    targets = [
        SSHTarget(project="carmo", index=1, port=2231),
        SSHTarget(project="orca-web", index=1, port=2251),
    ]
    out = orca._render_config(targets, hostname="127.0.0.1", user="ubuntu")

    host_lines = [ln for ln in out.splitlines() if ln.startswith("Host ")]
    assert host_lines == ["Host devbase-carmo-1", "Host devbase-orca-web-1"]
    assert all(ln.startswith("Host devbase-") for ln in host_lines)


def test_render_config_empty_targets_is_header_only():
    out = orca._render_config([], hostname="127.0.0.1", user="ubuntu")
    assert out.strip() == orca._HEADER
    assert "Host " not in out


# ---------------------------------------------------------------------------
# HostName / User の env 上書き (書き込み経由で確認)
# ---------------------------------------------------------------------------

def test_orca_hostname_env_overrides_hostname(home_in_tmp, monkeypatch):
    monkeypatch.setenv("DEVBASE_ORCA_HOSTNAME", "mac.tailnet.ts.net")
    targets = [SSHTarget(project="carmo", index=1, port=2231)]

    path = orca._write_config(targets)
    content = path.read_text(encoding="utf-8")
    assert "  HostName mac.tailnet.ts.net" in content


def test_hostname_defaults_to_loopback_when_unset(home_in_tmp):
    path = orca._write_config([SSHTarget(project="carmo", index=1, port=2231)])
    assert "  HostName 127.0.0.1" in path.read_text(encoding="utf-8")


def test_username_env_overrides_user(home_in_tmp, monkeypatch):
    monkeypatch.setenv("USERNAME", "devuser")
    path = orca._write_config([SSHTarget(project="carmo", index=1, port=2231)])
    assert "  User devuser" in path.read_text(encoding="utf-8")


def test_user_defaults_to_ubuntu_when_unset(home_in_tmp):
    path = orca._write_config([SSHTarget(project="carmo", index=1, port=2231)])
    assert "  User ubuntu" in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# regenerate / prune: 稼働 0 のときヘッダのみ / 停止済みは消える
# ---------------------------------------------------------------------------

def test_regenerate_zero_targets_writes_header_only(home_in_tmp):
    targets, path = orca.regenerate_config(targets_provider=lambda: [])
    assert targets == []
    assert path == orca._config_path()
    assert path.read_text(encoding="utf-8").strip() == orca._HEADER


def test_prune_drops_stale_entries(home_in_tmp):
    """一度 2 件書いた後、稼働 1 件で再生成すると停止分が消える (全上書き)。"""
    orca.regenerate_config(targets_provider=lambda: [
        SSHTarget(project="carmo", index=1, port=2231),
        SSHTarget(project="carmo", index=2, port=2232),
    ])

    # prune ≡ 稼働中コンテナから再生成。carmo-2 が停止した想定。
    rc = orca.cmd_orca(home_in_tmp, _args("prune"),
                       targets_provider=lambda: [
                           SSHTarget(project="carmo", index=1, port=2231)])
    assert rc == 0

    content = orca._config_path().read_text(encoding="utf-8")
    assert "Host devbase-carmo-1" in content
    assert "Host devbase-carmo-2" not in content


def test_cmd_orca_sync_uses_injected_targets(home_in_tmp):
    rc = orca.cmd_orca(home_in_tmp, _args("sync"),
                       targets_provider=lambda: [
                           SSHTarget(project="carmo", index=1, port=2231)])
    assert rc == 0
    assert "Host devbase-carmo-1" in orca._config_path().read_text(encoding="utf-8")


def test_cmd_orca_status_reports_path(home_in_tmp, capsys):
    orca.regenerate_config(targets_provider=lambda: [
        SSHTarget(project="carmo", index=1, port=2231)])

    rc = orca.cmd_orca(home_in_tmp, _args("status"))
    assert rc == 0
    out = capsys.readouterr().out
    assert str(orca._config_path()) in out
    assert "Host devbase-carmo-1" in out


def test_cmd_orca_unknown_subcommand_returns_1(home_in_tmp):
    assert orca.cmd_orca(home_in_tmp, _args(None)) == 1


# ---------------------------------------------------------------------------
# _parse_inspect: 22/tcp publish + compose ラベルで隔離
# ---------------------------------------------------------------------------

def _container(project=None, number="1", ssh_port="2231", extra_ports=None):
    labels = {}
    if project is not None:
        labels["com.docker.compose.project"] = project
        labels["com.docker.compose.container-number"] = number
    ports = dict(extra_ports or {})
    if ssh_port is not None:
        ports["22/tcp"] = [{"HostIp": "127.0.0.1", "HostPort": ssh_port}]
    return {
        "Config": {"Labels": labels},
        "NetworkSettings": {"Ports": ports},
    }


def test_parse_inspect_includes_ssh_publishing_compose_container():
    containers = [_container(project="carmo", number="1", ssh_port="2231")]
    targets = orca._parse_inspect(containers)
    assert targets == [SSHTarget(project="carmo", index=1, port=2231)]


def test_parse_inspect_excludes_container_without_ssh_port():
    """22/tcp を publish しないコンテナ (= Orca SSH target ではない) は除外。"""
    containers = [
        _container(project="carmo", number="1", ssh_port=None,
                   extra_ports={"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]}),
    ]
    assert orca._parse_inspect(containers) == []


def test_parse_inspect_excludes_container_without_compose_project():
    """compose project ラベルが無いコンテナは除外 (隔離)。"""
    containers = [_container(project=None, ssh_port="2231")]
    assert orca._parse_inspect(containers) == []


def test_parse_inspect_project_name_with_dashes_preserved():
    """project 名の dash を壊さない (名前 split ではなくラベル直読み)。"""
    containers = [_container(project="orca-web-app", number="2", ssh_port="2242")]
    targets = orca._parse_inspect(containers)
    assert targets == [SSHTarget(project="orca-web-app", index=2, port=2242)]


def test_parse_inspect_prefers_bind_matching_host_ip():
    containers = [{
        "Config": {"Labels": {
            "com.docker.compose.project": "carmo",
            "com.docker.compose.container-number": "1",
        }},
        "NetworkSettings": {"Ports": {"22/tcp": [
            {"HostIp": "0.0.0.0", "HostPort": "9999"},
            {"HostIp": "127.0.0.1", "HostPort": "2231"},
        ]}},
    }]
    targets = orca._parse_inspect(containers, bind="127.0.0.1")
    assert targets == [SSHTarget(project="carmo", index=1, port=2231)]
