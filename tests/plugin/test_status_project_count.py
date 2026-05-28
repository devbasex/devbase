"""Regression tests for devbase status の project_count 算出。

PLAN04 で plugin の実体配置が plugins/<name> から repos/<repo>/<subdir>
へ移行したため、status の project_count も InstalledPlugin.path
(devbase_root からの相対パス) を基準に数える必要がある。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbase.commands.status import _get_plugin_info
from devbase.plugin.models import InstalledPlugin
from devbase.plugin.registry import PluginRegistry


@pytest.fixture
def devbase_root(tmp_path):
    (tmp_path / "projects").mkdir()
    return tmp_path


@pytest.fixture
def registry(devbase_root):
    return PluginRegistry(devbase_root)


def _make_projects(base: Path, names: list[str]) -> None:
    proj = base / "projects"
    proj.mkdir(parents=True, exist_ok=True)
    for n in names:
        (proj / n).mkdir()


def test_project_count_for_repos_based_plugin(registry, devbase_root):
    """repos/ ベースのプラグインで project_count が正しく数えられる。"""
    plugin_dir = devbase_root / "repos" / "github.com-owner-repo" / "myplugin"
    _make_projects(plugin_dir, ["proj-a", "proj-b"])

    registry.add(InstalledPlugin(
        name="myplugin",
        version="1.0.0",
        source="https://github.com/owner/repo",
        installed_at=registry.now_iso(),
        path="repos/github.com-owner-repo/myplugin",
        linked=False,
    ))

    info = _get_plugin_info(registry)
    assert info == [{"name": "myplugin", "project_count": 2}]


def test_project_count_for_linked_plugin(registry, devbase_root):
    """--link インストール (plugins/<name>) でも従来どおり数えられる。"""
    plugin_dir = devbase_root / "plugins" / "linkedplugin"
    _make_projects(plugin_dir, ["only-one"])

    registry.add(InstalledPlugin(
        name="linkedplugin",
        version="0.1.0",
        source="/local/path",
        installed_at=registry.now_iso(),
        path="plugins/linkedplugin",
        linked=True,
    ))

    info = _get_plugin_info(registry)
    assert info == [{"name": "linkedplugin", "project_count": 1}]


def test_project_count_zero_when_no_projects_dir(registry, devbase_root):
    """projects/ ディレクトリが無い場合は 0。"""
    (devbase_root / "repos" / "github.com-owner-repo" / "noproj").mkdir(parents=True)

    registry.add(InstalledPlugin(
        name="noproj",
        version="1.0.0",
        source="https://github.com/owner/repo",
        installed_at=registry.now_iso(),
        path="repos/github.com-owner-repo/noproj",
        linked=False,
    ))

    info = _get_plugin_info(registry)
    assert info == [{"name": "noproj", "project_count": 0}]


def test_project_count_zero_when_path_empty(registry, devbase_root):
    """path が空 (旧/破損エントリ) のとき環境ルートの projects を誤参照しない。"""
    # 環境ルートに projects/ が存在し、中身があっても 0 と数えること。
    _make_projects(devbase_root, ["root-proj-a", "root-proj-b"])

    registry.add(InstalledPlugin(
        name="brokenpath",
        version="0.1.0",
        source="",
        installed_at=registry.now_iso(),
        path="",
        linked=False,
    ))

    info = _get_plugin_info(registry)
    assert info == [{"name": "brokenpath", "project_count": 0}]
