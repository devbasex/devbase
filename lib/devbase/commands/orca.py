"""devbase orca ... — Orca 用の隔離 SSH config を生成/剪定/表示する (PLAN33)。

Orca (https://www.onorca.dev/) から devbase コンテナへ SSH 接続するため、稼働中の
SSH publish 済みコンテナを列挙して専用ファイル ``~/.config/devbase/orca/ssh_config``
を全生成する。ホストの ``~/.ssh/config`` は一切触らず、Orca にはこのファイルだけを
import させることで他ホストとの隔離を実現する。

サブコマンド:
  - ``sync``   : 稼働中コンテナを集約して config を再生成 (毎回上書き)。
  - ``prune``  : 停止済みコンテナのエントリを除去する。稼働中コンテナから再生成
                 するだけで停止済みは自然に落ちるため ``sync`` と同義。
  - ``status`` : 現在の config パス・内容・Orca への import 手順を表示する。

詳細: docs/user/orca.md
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from devbase.env import keys
from devbase.log import get_logger

logger = get_logger(__name__)

DEFAULT_HOSTNAME = "127.0.0.1"
DEFAULT_USER = "ubuntu"

# 生成ファイル先頭に置く管理ブロックのヘッダ (docs/user/orca.md と一致させる)。
_HEADER = (
    "# Managed by devbase — do not edit. "
    "Import this file into Orca (Settings → SSH)."
)


@dataclass(frozen=True)
class SSHTarget:
    """1 コンテナぶんの Orca SSH target。"""
    project: str
    index: int
    port: int


def _config_path() -> Path:
    """Orca 用隔離 SSH config の絶対パス (``~/.config/devbase/orca/ssh_config``)。"""
    return Path.home() / ".config" / "devbase" / "orca" / "ssh_config"


# ---------------------------------------------------------------------------
# コンテナ列挙 (docker inspect ベース。名前の dash split はしない)
# ---------------------------------------------------------------------------

def _parse_index(raw) -> int:
    """compose の container-number ラベルを 1 始まり index に変換する。"""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def _pick_host_port(port_bindings: Sequence[dict], bind: Optional[str]) -> Optional[int]:
    """``22/tcp`` の publish 一覧から採用するホストポートを 1 つ選ぶ。

    ``bind`` (DEVBASE_SSH_BIND) に一致する HostIp のエントリを優先し、無ければ
    最初に見つかった HostPort を採用する。整数化できなければ None。
    """
    chosen = None
    for entry in port_bindings or []:
        host_port = entry.get("HostPort")
        if not host_port:
            continue
        if bind and entry.get("HostIp") == bind:
            chosen = host_port
            break
        if chosen is None:
            chosen = host_port
    if chosen is None:
        return None
    try:
        return int(chosen)
    except (TypeError, ValueError):
        return None


def _parse_inspect(containers, bind: Optional[str] = None) -> List[SSHTarget]:
    """``docker inspect`` の JSON (コンテナ配列) から SSH target を抽出する純関数。

    compose project ラベルを持ち、かつ ``22/tcp`` を publish しているコンテナだけを
    対象にする。この 2 条件によるフィルタが隔離を担保する (devbase の SSH 有効
    コンテナだけが Orca config に現れる)。project ラベルが無い / ``22/tcp`` を
    publish しないコンテナ (= Orca SSH target ではない) は除外する。

    コンテナ名を dash で split して project/index を得る方式は取らない
    (project 名自体が dash を含みうるため)。ラベルから直接読む。
    """
    targets: List[SSHTarget] = []
    for container in containers or []:
        config = container.get("Config") or {}
        labels = config.get("Labels") or {}
        project = labels.get("com.docker.compose.project")
        if not project:
            continue
        net = container.get("NetworkSettings") or {}
        port_bindings = (net.get("Ports") or {}).get("22/tcp")
        if not port_bindings:
            continue
        host_port = _pick_host_port(port_bindings, bind)
        if host_port is None:
            continue
        index = _parse_index(labels.get("com.docker.compose.container-number"))
        targets.append(SSHTarget(project=project, index=index, port=host_port))
    return targets


def _docker_json(args: Sequence[str]) -> Optional[str]:
    """``docker <args>`` を実行し stdout を返す。失敗時は warning を出して None。

    docker が無い / 異常終了しても呼び出し側 (up/down フック) を倒さないため
    例外は握り、None を返す。
    """
    try:
        result = subprocess.run(
            ["docker", *args], capture_output=True, text=True, check=False
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("docker %s に失敗しました (Orca 同期をスキップ): %s", args[0], e)
        return None
    if result.returncode != 0:
        logger.warning(
            "docker %s に失敗しました (Orca 同期をスキップ): %s",
            args[0], (result.stderr or "").strip(),
        )
        return None
    return result.stdout


def _running_ssh_targets() -> List[SSHTarget]:
    """稼働中の devbase SSH コンテナを docker から列挙する (best-effort)。

    ``docker ps -q`` で稼働中コンテナ id を集め、``docker inspect`` の JSON を
    :func:`_parse_inspect` に渡す。docker が無い / 失敗した場合は空リストを返す。
    """
    ps_out = _docker_json(["ps", "-q"])
    if ps_out is None:
        return []
    ids = ps_out.split()
    if not ids:
        return []
    inspect_out = _docker_json(["inspect", *ids])
    if inspect_out is None:
        return []
    try:
        containers = json.loads(inspect_out)
    except json.JSONDecodeError as e:
        logger.warning("docker inspect の出力を解析できませんでした (Orca 同期をスキップ): %s", e)
        return []
    bind = os.environ.get(keys.DEVBASE_SSH_BIND) or None
    return _parse_inspect(containers, bind=bind)


# ---------------------------------------------------------------------------
# config レンダリング / 書き込み
# ---------------------------------------------------------------------------

def _render_config(targets: Sequence[SSHTarget], hostname: str, user: str) -> str:
    """SSH target 群から config テキストを生成する純関数。

    エントリは (project, index) 昇順で安定ソートする。target が空でもヘッダのみの
    安全な空ファイルを返す。
    """
    lines = [_HEADER, ""]
    for t in sorted(targets, key=lambda x: (x.project, x.index)):
        lines.append(f"Host devbase-{t.project}-{t.index}")
        lines.append(f"  HostName {hostname}")
        lines.append(f"  Port {t.port}")
        lines.append(f"  User {user}")
        lines.append("  IdentityFile ~/.ssh/id_ed25519")
        lines.append("  StrictHostKeyChecking accept-new")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _write_config(targets: Sequence[SSHTarget]) -> Path:
    """config を全再生成して書き込み、パスを返す。親ディレクトリは作成する。"""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    hostname = os.environ.get(keys.DEVBASE_ORCA_HOSTNAME) or DEFAULT_HOSTNAME
    user = os.environ.get("USERNAME") or DEFAULT_USER
    path.write_text(_render_config(targets, hostname, user), encoding="utf-8")
    return path


def regenerate_config(
    targets_provider: Optional[Callable[[], List[SSHTarget]]] = None,
) -> Tuple[List[SSHTarget], Path]:
    """稼働中コンテナを列挙して config を全再生成する。``(targets, path)`` を返す。

    up/down フックからも呼べる共通エントリ。``targets_provider`` はテスト注入用。
    """
    provider = targets_provider or _running_ssh_targets
    targets = list(provider())
    path = _write_config(targets)
    return targets, path


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------

def _cmd_regenerate(targets_provider: Optional[Callable[[], List[SSHTarget]]]) -> int:
    """sync / prune 共通の再生成処理。停止済みは列挙から外れるため両者は同義。"""
    targets, path = regenerate_config(targets_provider)
    if targets:
        logger.info("Orca SSH config を生成しました (%d 件): %s", len(targets), path)
    else:
        logger.info("稼働中の SSH 対象コンテナがありません。ヘッダのみの config を書き出しました: %s", path)
        logger.info("ENABLE_SSH=true で `devbase up` するとコンテナが対象になります。")
    return 0


def _cmd_status() -> int:
    """現在の config パス・内容・import 手順を表示する。"""
    path = _config_path()
    print(f"Orca SSH config: {path}")
    print("")
    if path.exists():
        print("--- 現在の内容 ---")
        print(path.read_text(encoding="utf-8"), end="")
    else:
        print("(まだ生成されていません。`devbase orca sync` を実行してください)")
    print("")
    print("Orca への登録: Orca の Settings → SSH でこのファイルを import してください。")
    return 0


def cmd_orca(
    devbase_root: Path, args,
    targets_provider: Optional[Callable[[], List[SSHTarget]]] = None,
) -> int:
    """``devbase orca <sub>`` ディスパッチャ。

    ``targets_provider`` はテスト用のコンテナ列挙注入口 (通常は None で
    :func:`_running_ssh_targets` を使う)。
    """
    subcmd = getattr(args, "subcommand", None)

    handlers = {
        "sync":   lambda: _cmd_regenerate(targets_provider),
        "prune":  lambda: _cmd_regenerate(targets_provider),
        "status": _cmd_status,
    }

    handler = handlers.get(subcmd)
    if not handler:
        logger.error("サブコマンドを指定してください: %s", ", ".join(handlers))
        return 1
    return handler()
