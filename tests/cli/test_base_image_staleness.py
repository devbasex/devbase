"""devbase up の自動再ビルドにおける base イメージ日付判定のテスト。

project イメージが閾値超過で再ビルド対象になっても、base イメージが閾値内なら
base を no-cache build しない。base が古い/判定不能な場合だけ、base も含めて
no-cache build する。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from devbase.commands import container


def _inspect_json(days_old: int) -> str:
    created = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return json.dumps([{"Created": created}])


def test_get_base_image_ref_parses_from(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM devbase-base:latest\nRUN echo hi\n")
    dev_service = {"build": {"context": str(tmp_path), "dockerfile": "Dockerfile"}}
    assert container._get_base_image_ref(dev_service) == "devbase-base:latest"


def test_get_base_image_ref_appends_latest_tag(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM devbase-general\n")
    dev_service = {"build": {"context": str(tmp_path), "dockerfile": "Dockerfile"}}
    assert container._get_base_image_ref(dev_service) == "devbase-general:latest"


def test_get_base_image_ref_none_when_not_devbase(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM ubuntu:noble\n")
    dev_service = {"build": {"context": str(tmp_path), "dockerfile": "Dockerfile"}}
    assert container._get_base_image_ref(dev_service) is None


def test_get_base_image_ref_string_build(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM devbase-base\n")
    dev_service = {"build": str(tmp_path)}
    assert container._get_base_image_ref(dev_service) == "devbase-base:latest"


def test_base_image_is_fresh_true(monkeypatch):
    monkeypatch.setattr(container, "_get_base_image_ref", lambda _s: "devbase-base:latest")

    class _R:
        returncode = 0
        stdout = _inspect_json(2)

    monkeypatch.setattr(container.subprocess, "run", lambda *a, **k: _R())
    assert container._base_image_is_fresh({}, 7) is True


def test_base_image_is_fresh_false_when_stale(monkeypatch):
    monkeypatch.setattr(container, "_get_base_image_ref", lambda _s: "devbase-base:latest")

    class _R:
        returncode = 0
        stdout = _inspect_json(10)

    monkeypatch.setattr(container.subprocess, "run", lambda *a, **k: _R())
    assert container._base_image_is_fresh({}, 7) is False


def test_build_with_expires_skips_when_project_fresh(monkeypatch):
    """project が期限内なら再ビルドせず (ビルドを一切呼ばず) True を返す。"""
    called = []
    monkeypatch.setattr(container, "_run_build", lambda **k: called.append(k) or True)
    assert container._build_with_expires(7, "dev:latest", _inspect_json(1), {}) is True
    assert called == []


def test_build_with_expires_project_only_when_base_fresh(monkeypatch):
    captured = {}
    monkeypatch.setattr(container, "_base_image_is_fresh", lambda _s, _m: True)
    monkeypatch.setattr(container, "_run_build", lambda **k: captured.update(k) or True)
    assert container._build_with_expires(7, "dev:latest", _inspect_json(10), {}) is True
    assert captured == {"project_no_cache": True}


def test_build_with_expires_full_no_cache_when_base_stale(monkeypatch):
    captured = {}
    monkeypatch.setattr(container, "_base_image_is_fresh", lambda _s, _m: False)
    monkeypatch.setattr(container, "_run_build", lambda **k: captured.update(k) or True)
    assert container._build_with_expires(7, "dev:latest", _inspect_json(10), {}) is True
    assert captured == {"no_cache": True}


def _setup_devbase_root(tmp_path, monkeypatch):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "devbase").write_text("#!/bin/bash\n")
    monkeypatch.setenv("DEVBASE_ROOT", str(tmp_path))


def test_run_build_project_no_cache_flag(tmp_path, monkeypatch):
    _setup_devbase_root(tmp_path, monkeypatch)
    captured = {}

    class _R:
        returncode = 0

    monkeypatch.setattr(
        container.subprocess,
        "run",
        lambda cmd, **k: captured.update(cmd=cmd) or _R(),
    )
    assert container._run_build(project_no_cache=True) is True
    assert captured["cmd"][-2:] == ["build", "--project-no-cache"]


def test_run_build_no_cache_flag(tmp_path, monkeypatch):
    _setup_devbase_root(tmp_path, monkeypatch)
    captured = {}

    class _R:
        returncode = 0

    monkeypatch.setattr(
        container.subprocess,
        "run",
        lambda cmd, **k: captured.update(cmd=cmd) or _R(),
    )
    assert container._run_build(no_cache=True) is True
    assert captured["cmd"][-2:] == ["build", "--no-cache"]


def test_wrapper_has_project_no_cache_mode():
    wrapper = (Path(__file__).resolve().parents[2] / "bin" / "devbase").read_text()
    assert "--project-no-cache) project_no_cache=1" in wrapper
    assert 'docker compose build "${DEV_SERVICE_NAME:-dev}" --no-cache "$@"' in wrapper


# ---------------------------------------------------------------------------
# _build_resolved: build --expires / rebuild の共通エントリ
# ---------------------------------------------------------------------------

def _make_compose(tmp_path, monkeypatch):
    (tmp_path / "compose.yml").write_text("services: {}\n")
    monkeypatch.chdir(tmp_path)


def test_build_resolved_missing_compose_returns_1(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert container._build_resolved(expires=7, no_cache=False) == 1


def test_build_resolved_no_cache_skips_inspection(tmp_path, monkeypatch):
    _make_compose(tmp_path, monkeypatch)
    captured = {}
    monkeypatch.setattr(container, "_run_build", lambda **k: captured.update(k) or True)

    def _boom():
        raise AssertionError("no_cache path must not inspect compose")

    monkeypatch.setattr(container, "_resolve_dev_service", _boom)
    assert container._build_resolved(expires=None, no_cache=True) == 0
    assert captured == {"no_cache": True}


def test_build_resolved_plain_when_no_expires(tmp_path, monkeypatch):
    _make_compose(tmp_path, monkeypatch)
    captured = {}
    monkeypatch.setattr(container, "_run_build", lambda **k: captured.update(k) or True)
    assert container._build_resolved(expires=None, no_cache=False) == 0
    assert captured == {}


def test_build_resolved_expires_missing_image_builds_cached(tmp_path, monkeypatch):
    _make_compose(tmp_path, monkeypatch)
    monkeypatch.setattr(container, "_resolve_dev_service", lambda: {"image": "dev:latest"})

    class _R:
        returncode = 1  # docker image inspect → 未存在
        stdout = ""

    monkeypatch.setattr(container.subprocess, "run", lambda *a, **k: _R())
    captured = {}
    monkeypatch.setattr(container, "_run_build", lambda **k: captured.update(k) or True)
    assert container._build_resolved(expires=7, no_cache=False) == 0
    assert captured == {}


def test_build_resolved_expires_present_delegates(tmp_path, monkeypatch):
    _make_compose(tmp_path, monkeypatch)
    monkeypatch.setattr(container, "_resolve_dev_service", lambda: {"image": "dev:latest"})

    class _R:
        returncode = 0
        stdout = _inspect_json(10)

    monkeypatch.setattr(container.subprocess, "run", lambda *a, **k: _R())
    seen = {}
    monkeypatch.setattr(
        container, "_build_with_expires",
        lambda expires, image, inspect_json, dev: seen.update(expires=expires, image=image) or True,
    )
    assert container._build_resolved(expires=7, no_cache=False) == 0
    assert seen == {"expires": 7, "image": "dev:latest"}
