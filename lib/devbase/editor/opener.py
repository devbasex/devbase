"""`devbase up` 後に dev コンテナへ接続した VS Code を自動で開く。

設計の核心 (issues/PLAN31_3_up-open-editor.md §2):

- 一貫機構は **PATH 上の ``code`` への委譲**。VS Code 統合ターミナルでは
  ``VSCODE_IPC_HOOK_CLI`` 経由でクライアント側 VS Code に「このフォルダを開け」を
  IPC 委譲する。WSL では ``code`` ラッパが Windows 側 VS Code を起動する。
  Remote-SSH 統合ターミナルでは ``code`` シムがクライアント (例: Windows) に窓を
  開く。よって ``code --folder-uri <attach-uri>`` を叩くだけで実行コンテキストに
  応じた正しいクライアントへ開ける。
- コンテナ attach URI は ``{"containerName":"/<実コンテナ名>"}`` を hex 化した
  authority を持つ (:func:`build_attach_uri`)。
- ``ssh-remote+host`` と ``attached-container+...`` を 1 本に合成する記法は
  公式未サポート (microsoft/vscode#242489)。よって VS Code 外の plain SSH では
  クライアントへ push できず、手元で叩くコマンドを提示する degrade に留める。

本モジュールの関数は実 docker / VS Code を必要とせず、``environ`` 等を引数で
差し替えてテストできるよう副作用を :func:`open_editor` に集約している。
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from devbase.log import get_logger

logger = get_logger(__name__)

# DEVBASE_OPEN_EDITOR を真と解釈する値 (大小無視)
_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EditorContext:
    """エディタ起動先の判定に使う実行コンテキスト。"""

    is_tty: bool
    in_vscode: bool   # VSCODE_IPC_HOOK_CLI が設定されている
    is_wsl: bool
    is_ssh: bool
    is_darwin: bool


@dataclass(frozen=True)
class OpenPlan:
    """:func:`decide_action` の判定結果。"""

    action: str   # 'launch' | 'print_command' | 'skip'
    reason: str


def _stdout_isatty() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _detect_wsl(environ) -> bool:
    if environ.get("WSL_DISTRO_NAME") or environ.get("WSL_INTEROP"):
        return True
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def detect_context(environ=None, isatty: Optional[bool] = None,
                   system: Optional[str] = None) -> EditorContext:
    """env / OS からエディタ起動先判定に必要なコンテキストを抽出する。

    引数はテスト用の差し替え口。未指定なら ``os.environ`` / ``sys.stdout`` /
    ``platform.system()`` を用いる。
    """
    env = os.environ if environ is None else environ
    if isatty is None:
        isatty = _stdout_isatty()
    if system is None:
        system = platform.system()
    return EditorContext(
        is_tty=bool(isatty),
        in_vscode=bool(env.get("VSCODE_IPC_HOOK_CLI")),
        is_wsl=_detect_wsl(env),
        is_ssh=any(env.get(k) for k in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")),
        is_darwin=(system == "Darwin"),
    )


def is_open_enabled(environ=None) -> bool:
    """``DEVBASE_OPEN_EDITOR`` env が真かどうか (未設定は False)。"""
    env = os.environ if environ is None else environ
    value = env.get("DEVBASE_OPEN_EDITOR")
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY


def resolve_editor_cmd(environ=None) -> Optional[list]:
    """起動に使うエディタコマンド (argv list) を解決する。

    ``DEVBASE_EDITOR`` があればそれを (シェル風に分割して) 優先。なければ既定の
    ``code``。attach URI は VS Code 系 CLI でのみ解釈できるため、``$EDITOR``
    (vi 等) へのフォールバックは意図的に行わない。実在しなければ None。
    """
    env = os.environ if environ is None else environ
    explicit = env.get("DEVBASE_EDITOR")
    if explicit:
        parts = shlex.split(explicit)
        if parts and shutil.which(parts[0]):
            return parts
        return None
    if shutil.which("code"):
        return ["code"]
    return None


def build_attach_uri(container_name: str, workdir: str) -> str:
    """``vscode-remote://attached-container+<hex>/<workdir>`` を組む。

    ``<hex>`` は ``{"containerName":"/<container_name>"}`` を UTF-8 hex 化したもの
    (Docker 内部のコンテナ名は先頭 ``/`` 付き)。
    """
    payload = json.dumps({"containerName": f"/{container_name}"}, separators=(",", ":"))
    hexname = payload.encode("utf-8").hex()
    path = workdir if workdir.startswith("/") else f"/{workdir}"
    return f"vscode-remote://attached-container+{hexname}{path}"


def resolve_container_name(dev_service_name: str, project_name: str, index: int = 1) -> str:
    """dev コンテナの実コンテナ名を返す。

    scale 生成 compose が ``container_name = ${COMPOSE_PROJECT_NAME}-{dev}-{index}``
    を全インスタンスへ設定する (volume/compose.py)。COMPOSE_PROJECT_NAME は
    project_name と一致するため決定的に組み立てられる。
    """
    return f"{project_name}-{dev_service_name}-{index}"


def resolve_workdir(environ=None, project_name: Optional[str] = None) -> str:
    """コンテナ内で開くワークスペースパス (``/work/$GIT_REPO``) を返す。"""
    env = os.environ if environ is None else environ
    workdir = env.get("WORK_DIR")
    if workdir:
        return workdir
    repo = env.get("GIT_REPO") or project_name
    return f"/work/{repo}" if repo else "/work"


def decide_action(ctx: EditorContext, editor_cmd: Optional[list]) -> OpenPlan:
    """コンテキストとエディタ可用性から起動方針を決める (§2.4 マトリクス)。"""
    if not editor_cmd:
        return OpenPlan(
            "skip",
            "エディタ (code) が見つかりません。VS Code の `code` コマンドを PATH に "
            "通すか DEVBASE_EDITOR を設定してください",
        )
    if not ctx.is_tty:
        return OpenPlan("skip", "非対話 (非TTY/CI) 環境のため")
    if ctx.in_vscode:
        # VS Code 統合ターミナル (ローカル / WSL / Remote-SSH シム)。code が
        # クライアント側へ委譲するため直接起動でよい。
        return OpenPlan("launch", "VS Code 統合ターミナル経由")
    if ctx.is_ssh:
        # plain SSH (VS Code 外)。クライアントへ push する公式手段が無いため
        # コマンドを提示する degrade。
        return OpenPlan("print_command", "SSH セッション (VS Code 外) のため")
    return OpenPlan("launch", "ローカル/WSL 端末")


def _launch(cmd: list, env: dict) -> None:
    """エディタを非ブロッキングで起動する (up プロセスを待たせない)。"""
    subprocess.Popen(  # noqa: S603 - argv はコード生成で外部入力を渡さない
        cmd, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def open_editor(*, project_name: str, dev_service_name: str, workdir: str,
                index: int = 1, environ=None,
                isatty: Optional[bool] = None, system: Optional[str] = None,
                launcher: Optional[Callable[[list, dict], None]] = None) -> str:
    """dev コンテナへ接続した VS Code を開く / コマンド提示 / スキップする。

    戻り値は実行された action ('launch' | 'print_command' | 'skip')。例外は
    握り潰して warning にし、``up`` 本体を絶対に失敗させない。``isatty`` /
    ``system`` は :func:`detect_context` への差し替え口 (テスト用)。
    """
    env = os.environ if environ is None else environ
    ctx = detect_context(env, isatty=isatty, system=system)
    editor = resolve_editor_cmd(env)
    plan = decide_action(ctx, editor)

    container = resolve_container_name(dev_service_name, project_name, index)
    uri = build_attach_uri(container, workdir)

    if plan.action == "skip":
        logger.info("エディタの自動オープンをスキップ: %s", plan.reason)
        return "skip"

    quoted = " ".join(shlex.quote(c) for c in editor)
    if plan.action == "print_command":
        logger.info("SSH セッションを検出しました (%s)。", plan.reason)
        logger.info(
            "手元の VS Code で次を実行するか、VS Code の Remote-SSH 統合ターミナルから "
            "`devbase up` を実行すると自動で開きます:"
        )
        logger.info("  %s --folder-uri '%s'", quoted, uri)
        return "print_command"

    logger.info("[editor] %s を起動します (%s)", quoted, plan.reason)
    cmd = [*editor, "--folder-uri", uri]
    try:
        (launcher or _launch)(cmd, dict(env))
    except Exception as e:  # noqa: BLE001 - 起動失敗で up を倒さない
        logger.warning("エディタの起動に失敗しましたが処理は続行します: %s", e)
    return "launch"
