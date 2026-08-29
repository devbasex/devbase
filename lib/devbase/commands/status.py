"""devbase status - 環境ステータスの一覧表示"""

import os
import subprocess
from datetime import datetime
from pathlib import Path

from devbase.log import get_logger
from devbase.plugin.registry import PluginRegistry

try:
    from devbase import __version__
except ImportError:
    __version__ = "3.0.0"

logger = get_logger(__name__)


_COMPOSE_PROJECT_LABEL = "com.docker.compose.project"

# `counts` 引数の「未指定」を docker 不在 (None) と区別するための sentinel。
_UNSET = object()


def _running_counts_by_project() -> dict[str, int] | None:
    """全 running コンテナを単一の ``docker ps`` で取得し、compose project 名
    ごとの running 数を返す。docker が使えない / 取得失敗時は ``None``。

    プロジェクト数ぶん ``docker compose ps`` を起動する代わりに ``docker ps``
    1 回で全コンテナのラベルを集計し、サブプロセス起動コストを N→1 に削減する。
    compose project は ``com.docker.compose.project`` ラベル (= devbase up 時の
    COMPOSE_PROJECT_NAME = プロジェクト名) で識別するため、呼び出し側プロセスが
    継承する COMPOSE_PROJECT_NAME に一切影響されない (一覧が一律同一状態になる
    回帰を構造的に回避する)。``docker ps`` は既定で running のみを列挙する。
    """
    try:
        proc = subprocess.run(
            ["docker", "ps",
             "--filter", f"label={_COMPOSE_PROJECT_LABEL}",
             "--format", f'{{{{.Label "{_COMPOSE_PROJECT_LABEL}"}}}}'],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return None
    except (subprocess.TimeoutExpired, OSError):
        # docker コマンドが利用できない、またはタイムアウト
        return None

    counts: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        name = line.strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _container_status_for(entry: Path, counts=_UNSET) -> dict | None:
    """単一プロジェクトディレクトリのコンテナ状態を取得する。

    `projects/<name>` (実ディレクトリ or plugin への symlink) を受け取り、
    ``{"name", "status", "count"}`` を返す。対象外 (compose.yml が無い) や docker
    コマンドが利用できない場合は ``None`` を返す。

    ``counts`` には ``_running_counts_by_project()`` の戻り値 (compose project 名
    → running 数) を渡す。一覧表示では呼び出し側が 1 回だけ集計して全 entry で
    使い回すことで docker サブプロセスの起動を 1 回に抑える。``counts`` を省略
    した単発呼び出しでは本関数内で都度集計する。``None`` (docker 不在) が明示的に
    渡された場合は再集計せず ``None`` を返す。
    """
    compose_file = entry / "compose.yml"
    if not compose_file.exists():
        return None

    if counts is _UNSET:
        counts = _running_counts_by_project()
    if counts is None:
        # docker が利用できない / 取得失敗
        return None

    # devbase up は COMPOSE_PROJECT_NAME = entry.name でコンテナを起動するため、
    # compose project ラベルが entry.name の running 数がこのプロジェクトの稼働数。
    running = counts.get(entry.name, 0)
    if running > 0:
        status = f"running ({running} containers)"
    else:
        status = "stopped"

    return {"name": entry.name, "status": status, "count": running}


def _get_container_status(projects_dir: Path) -> list[dict]:
    """projects/ 配下の各プロジェクトのコンテナ状態を取得する"""
    results = []
    if not projects_dir.exists():
        return results

    # docker ps は 1 回だけ実行し、全 entry で使い回す。
    counts = _running_counts_by_project()

    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue
        status = _container_status_for(entry, counts)
        if status is not None:
            results.append(status)

    return results


def _get_plugin_info(registry: PluginRegistry) -> list[dict]:
    """インストール済みプラグインとプロジェクト数を取得する"""
    results = []
    plugins = registry.list_installed()

    for plugin in plugins:
        # plugin.path は devbase_root からの相対パス。
        # repos/ ベース (repos/<repo>/<subdir>) と --link ベース
        # (plugins/<name>) の両方を同じロジックで解決する。
        # path が空の場合 (旧/破損エントリ) は devbase_root/projects を
        # 誤参照してしまうため、先にガードして 0 件扱いとする。
        if not plugin.path:
            results.append({"name": plugin.name, "project_count": 0})
            continue
        plugin_projects_dir = registry.devbase_root / plugin.path / "projects"
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


def _get_account_group() -> dict | None:
    """解決されたアカウントグループとボリューム名を返す (PLAN39)。

    ``devbase status`` は devbase ルートで実行されることが多く、その場合
    ``DEVBASE_ACCOUNT_GROUP`` はグローバル ``env`` 由来の値 (無ければ ``default``)
    になる。プロジェクトディレクトリで実行すればそのプロジェクトの解決結果になる。
    どちらの値を見ているのかが分かるよう、判定の出どころも返す。

    グループ名が不正な場合はここで例外にせず ``None`` を返す。``status`` は
    状態を見るためのコマンドで、設定の誤りで一覧全体を出せなくする必要はない。
    """
    from devbase.errors import DevbaseError
    from devbase.volume.manager import get_group_volume, resolve_account_group

    declared = os.environ.get("DEVBASE_ACCOUNT_GROUP")
    try:
        group = resolve_account_group()
        volume = get_group_volume(group)
    except DevbaseError as e:
        return {"group": None, "volume": None, "error": str(e)}
    return {
        "group": group,
        "volume": volume,
        "source": "env" if (declared or "").strip() else "既定",
        "error": None,
    }


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
        group_info = _get_account_group()
        if env_info or group_info:
            print()
            print("[環境]")
        if env_info:
            print(
                f"  {'devbase/.env':<24}"
                f"{env_info['var_count']}変数 "
                f"(最終更新: {env_info['last_modified']})"
            )
        if group_info:
            if group_info["error"]:
                print(f"  {'アカウントグループ':<20}(設定エラー) {group_info['error']}")
            else:
                print(
                    f"  {'アカウントグループ':<20}"
                    f"{group_info['group']} "
                    f"({group_info['volume']} / {group_info['source']})"
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
