"""devbase status - 環境ステータスの一覧表示"""

import json
import subprocess
from datetime import datetime
from pathlib import Path

from devbase.log import get_logger
from devbase.plugin.registry import PluginRegistry

try:
    from devbase import __version__
except ImportError:
    __version__ = "2.2.0"

logger = get_logger(__name__)


def _get_container_status(projects_dir: Path) -> list[dict]:
    """projects/ 配下の各プロジェクトのコンテナ状態を取得する"""
    results = []
    if not projects_dir.exists():
        return results

    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue
        compose_file = entry / "compose.yml"
        if not compose_file.exists():
            continue

        try:
            proc = subprocess.run(
                ["docker", "compose", "ps", "--format", "json"],
                cwd=str(entry),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                continue

            output = proc.stdout.strip()
            if not output:
                results.append({
                    "name": entry.name,
                    "status": "stopped",
                    "count": 0,
                })
                continue

            # docker compose ps --format json は1行1JSONまたはJSON配列
            containers = []
            for line in output.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, list):
                        containers.extend(parsed)
                    else:
                        containers.append(parsed)
                except json.JSONDecodeError:
                    continue

            if not containers:
                results.append({
                    "name": entry.name,
                    "status": "stopped",
                    "count": 0,
                })
                continue

            running = sum(
                1 for c in containers
                if c.get("State", "").lower() == "running"
            )
            total = len(containers)

            if running > 0:
                status = f"running ({total} containers)"
            else:
                status = "stopped"

            results.append({
                "name": entry.name,
                "status": status,
                "count": total,
            })

        except (subprocess.TimeoutExpired, OSError):
            # dockerコマンドが利用できない、またはタイムアウト
            continue

    return results


def _get_plugin_info(registry: PluginRegistry) -> list[dict]:
    """インストール済みプラグインとプロジェクト数を取得する"""
    results = []
    plugins = registry.list_installed()
    plugins_dir = registry.get_plugins_dir()

    for plugin in plugins:
        plugin_projects_dir = plugins_dir / plugin.name / "projects"
        if plugin_projects_dir.is_dir():
            project_count = sum(
                1 for p in plugin_projects_dir.iterdir() if p.is_dir()
            )
        else:
            project_count = 0

        results.append({
            "name": plugin.name,
            "project_count": project_count,
        })

    return results


def _get_env_info(devbase_root: Path) -> dict | None:
    """devbase/.env の情報を取得する"""
    env_file = devbase_root / ".env"
    if not env_file.exists():
        return None

    try:
        content = env_file.read_text(encoding="utf-8")
        var_count = sum(
            1 for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        mtime = env_file.stat().st_mtime
        last_modified = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        return {
            "var_count": var_count,
            "last_modified": last_modified,
        }
    except OSError:
        return None


def _get_snapshot_info(devbase_root: Path) -> dict | None:
    """スナップショットの概要情報を取得する"""
    try:
        from devbase.snapshot.manager import SnapshotManager
    except ImportError:
        return None

    try:
        mgr = SnapshotManager(devbase_root)
        snapshots = mgr.list()
        if not snapshots:
            return {"latest": None, "count": 0}
        latest = snapshots[-1]
        return {
            "latest": latest.get("name", "unknown"),
            "count": len(snapshots),
        }
    except Exception:
        return None


def cmd_status(devbase_root: Path) -> int:
    """devbase 環境のステータスを一覧表示する"""

    print(f"devbase v{__version__}")

    # --- コンテナセクション ---
    try:
        projects_dir = devbase_root / "projects"
        containers = _get_container_status(projects_dir)
        if containers:
            print()
            print("[コンテナ]")
            for c in containers:
                print(f"  {c['name']:<24}{c['status']}")
    except Exception:
        logger.debug("コンテナ情報の取得に失敗しました", exc_info=True)

    # --- プラグインセクション ---
    try:
        registry = PluginRegistry(devbase_root)
        plugins = _get_plugin_info(registry)
        if plugins:
            print()
            print("[プラグイン]")
            for p in plugins:
                print(f"  {p['name']:<24}{p['project_count']} projects")
    except Exception:
        logger.debug("プラグイン情報の取得に失敗しました", exc_info=True)

    # --- 環境セクション ---
    try:
        env_info = _get_env_info(devbase_root)
        if env_info:
            print()
            print("[環境]")
            print(
                f"  {'devbase/.env':<24}"
                f"{env_info['var_count']}変数 "
                f"(最終更新: {env_info['last_modified']})"
            )
    except Exception:
        logger.debug("環境情報の取得に失敗しました", exc_info=True)

    # --- スナップショットセクション ---
    try:
        snap_info = _get_snapshot_info(devbase_root)
        if snap_info is not None:
            print()
            print("[スナップショット]")
            if snap_info["latest"]:
                print(
                    f"  最新: {snap_info['latest']} "
                    f"({snap_info['count']}世代)"
                )
            else:
                print("  なし")
    except Exception:
        logger.debug("スナップショット情報の取得に失敗しました", exc_info=True)

    return 0
