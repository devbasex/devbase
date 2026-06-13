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


def is_open_terminal_enabled(environ=None) -> bool:
    """``DEVBASE_OPEN_TERMINAL`` env が真か (**未設定は True = 既定 ON**)。

    ``DEVBASE_OPEN_EDITOR`` (既定 OFF) と既定が逆である点に注意。up 時の tasks.json 配置は
    暴発リスクが低く、ユーザ要望で既定 ON とする (PLAN31_3)。
    """
    env = os.environ if environ is None else environ
    value = env.get("DEVBASE_OPEN_TERMINAL")
    if value is None:
        return True
    return value.strip().lower() in _TRUTHY


def build_folder_open_tasks_json() -> str:
    """フォルダを開いた時に統合ターミナルを表示する folderOpen タスク (.vscode/tasks.json)。

    VS Code 公式には「起動時にターミナルを開く」単独設定が無く (``hideOnStartup`` は復元
    された永続セッションを隠すか否かに過ぎず新規生成はしない)、``runOn: folderOpen`` の
    タスクが新規ターミナルを出せる唯一の方法 (docs/terminal/*, docs/debugtest/tasks)。
    ``reveal: always`` でパネルを前面に出し、対話シェル (``$SHELL``) を起動する。

    .. note:: 自動実行には2つの user 設定ゲートがあり devbase からは制御できない:
       Workspace Trust (信頼済みフォルダのみ自動実行) と ``task.allowAutomaticTasks``
       (既定 off = フォルダ毎に1回許可確認)。いずれも application/user スコープ専用。
    """
    tasks = {
        "version": "2.0.0",
        "tasks": [
            {
                "label": "devbase: open terminal",
                "type": "shell",
                "command": "${env:SHELL}",
                "isBackground": True,
                "problemMatcher": [],
                "presentation": {
                    "reveal": "always",
                    "panel": "dedicated",
                    "focus": True,
                },
                "runOptions": {"runOn": "folderOpen"},
            }
        ],
    }
    return json.dumps(tasks, indent=2, ensure_ascii=False) + "\n"


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


def resolve_editor_display(environ=None) -> list:
    """コマンド提示 (print_command) 用のエディタ argv を解決する。

    :func:`resolve_editor_cmd` と異なり ``shutil.which`` による実在チェックは
    行わない。plain SSH では提示コマンドを実行するのは「ユーザの手元 (ローカル)」
    であり、コマンドを実行している側 (リモート) に ``code`` が存在する必要は無い
    ため、リモートの実在に依存せず必ず非 None を返す。

    ``DEVBASE_EDITOR`` があればそれを (シェル風に分割して) 用い、無ければ既定の
    ``["code"]`` を返す。
    """
    env = os.environ if environ is None else environ
    explicit = env.get("DEVBASE_EDITOR")
    if explicit:
        parts = shlex.split(explicit)
        if parts:
            return parts
    return ["code"]


def build_attach_uri(container_name: str, workdir: str,
                     ssh_host: Optional[str] = None,
                     docker_context: Optional[str] = None) -> str:
    """``vscode-remote://attached-container+<hex>[@ssh-remote+<host>]/<workdir>`` を組む。

    ``<hex>`` は ``{"containerName":"/<container_name>"[,"settings":{"context":<ctx>}]}``
    を UTF-8 hex 化したもの (Docker 内部のコンテナ名は先頭 ``/`` 付き)。

    ``ssh_host`` を渡すと authority に ``@ssh-remote+<host>`` を付ける。**Windows VS Code
    → Remote-SSH(<host>) → Mac 上のコンテナ** のような跨ホスト構成では、フラットな
    ``attached-container+...`` だけだと委譲先クライアント (Windows) のローカル Docker を
    見に行きコンテナが見つからない。``@ssh-remote+<host>`` を付けると docker ルックアップが
    ssh 先 (コンテナのある Mac) で行われ解決できる (実機検証済み。PLAN31_3 §2.3/§2.4 を
    更新)。``docker_context`` を渡すと payload に ``settings.context`` を埋め、ssh 先で
    使う docker context を明示する。

    いずれも省略すると従来のフラット URI (ローカル / WSL / Remote-SSH 同一ホスト) を返す。
    """
    payload: dict = {"containerName": f"/{container_name}"}
    if docker_context:
        payload["settings"] = {"context": docker_context}
    hexname = json.dumps(payload, separators=(",", ":")).encode("utf-8").hex()
    authority = f"attached-container+{hexname}"
    if ssh_host:
        authority += f"@ssh-remote+{ssh_host}"
    path = workdir if workdir.startswith("/") else f"/{workdir}"
    return f"vscode-remote://{authority}{path}"


def _parse_compose_ps_name(stdout: str) -> Optional[str]:
    """``docker compose ps --format json`` の出力から ``.Name`` を 1 件取り出す。

    docker compose のバージョン差で出力形式が異なる:

    - 新しめ: 1 行 1 JSON オブジェクト (改行区切り NDJSON)
    - 古め:   JSON 配列 ``[{...}, ...]``

    どちらでも先頭インスタンスの ``Name`` を返す。解釈不能・空なら None。
    """
    text = (stdout or "").strip()
    if not text:
        return None
    # まず JSON 配列としてのパースを試す。
    try:
        obj = json.loads(text)
    except ValueError:
        obj = None
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and item.get("Name"):
                return item["Name"]
        return None
    if isinstance(obj, dict) and obj.get("Name"):
        return obj["Name"]
    # NDJSON (1 行 1 JSON) として行ごとにパース。
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("Name"):
            return item["Name"]
    return None


def _query_container_name(dev_service_name: str, index: int,
                          compose_file=None,
                          runner: Optional[Callable] = None) -> Optional[str]:
    """実 docker へ問い合わせて dev インスタンスの実コンテナ名を取得する (保険)。

    scale 生成 compose ではサービス名が ``{dev}-{index}`` (例 ``dev-1``) になるため
    その service token を指定して ``docker compose ps --format json`` を実行する。
    ``{dev}-{index}`` サービスは override compose (``.docker-compose.scale.yml``)
    側にしか存在しないため、``compose_file`` が与えられた場合は起動時と同じ
    ``-f <compose_file>`` を付与しないと base ``compose.yml`` には無いサービスを
    見に行きほぼ常にフォールバックになる。
    取得できなければ None。docker 不在・非0・例外・空はすべて None に握り潰し、
    呼び出し側が決定的名へフォールバックできるようにする。
    """
    run = runner or subprocess.run
    service_token = f"{dev_service_name}-{index}"
    cmd = ["docker", "compose"]
    if compose_file is not None:
        cmd += ["-f", str(compose_file)]
    cmd += ["ps", "--format", "json", service_token]
    try:
        proc = run(
            cmd,
            capture_output=True, text=True, timeout=10,
        )
    except Exception:  # noqa: BLE001 - docker 不在等は保険なので握り潰す
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    try:
        return _parse_compose_ps_name(proc.stdout)
    except Exception:  # noqa: BLE001 - パース失敗も決定的名へフォールバック
        return None


def resolve_container_name(dev_service_name: str, project_name: str, index: int = 1,
                           compose_file=None,
                           runner: Optional[Callable] = None) -> str:
    """dev コンテナの実コンテナ名を返す。

    PLAN31_3 §3: compose バージョン差異への保険として、まず実 docker へ
    ``docker compose ps --format json`` で問い合わせて実 ``Name`` を取得する。
    取得できなければ決定的名 ``{project_name}-{dev_service_name}-{index}`` へ
    フォールバックする。

    scale 生成 compose は ``container_name = ${COMPOSE_PROJECT_NAME}-{dev}-{index}``
    を全インスタンスへ設定する (volume/compose.py)。COMPOSE_PROJECT_NAME は
    project_name と一致するため、docker 問い合わせに失敗しても決定的に組み立てられる。
    """
    queried = _query_container_name(dev_service_name, index,
                                    compose_file=compose_file, runner=runner)
    if queried:
        return queried
    return f"{project_name}-{dev_service_name}-{index}"


def resolve_workdir(environ=None, project_name: Optional[str] = None) -> str:
    """コンテナ内で開くワークスペースパス (``/work/$GIT_REPO``) を返す。"""
    env = os.environ if environ is None else environ
    workdir = env.get("WORK_DIR")
    if workdir:
        return workdir
    repo = env.get("GIT_REPO") or project_name
    return f"/work/{repo}" if repo else "/work"


def resolve_editor_ssh_host(environ=None) -> Optional[str]:
    """Remote-SSH ネスト URI 用の ssh ホスト名 (``DEVBASE_EDITOR_SSH_HOST``)。

    値はクライアント (手元 VS Code) の ``~/.ssh/config`` の Host 別名 (例 ``mac2``)。
    VS Code はこの別名を Remote-SSH 先 (Mac) の端末 env に渡さない (SSH_CONNECTION は
    IP のみ) ため自動取得できず、明示設定が要る (PLAN31_3 §2.4 / 実機調査)。未設定なら
    None を返し、:func:`build_attach_uri` はフラット URI にフォールバックする。
    """
    env = os.environ if environ is None else environ
    value = env.get("DEVBASE_EDITOR_SSH_HOST")
    value = value.strip() if value else ""
    return value or None


def resolve_docker_context(environ=None, runner: Optional[Callable] = None) -> Optional[str]:
    """ssh 先で使う docker context を解決する。

    ``DEVBASE_EDITOR_DOCKER_CONTEXT`` 明示があればそれ。無ければ devbase up を実行して
    いるホスト (= コンテナのある Mac) の現在の docker context を ``docker context show``
    で取得する。docker 不在・非0・例外・空はすべて None (settings.context を付けない)。
    """
    env = os.environ if environ is None else environ
    explicit = env.get("DEVBASE_EDITOR_DOCKER_CONTEXT")
    if explicit and explicit.strip():
        return explicit.strip()
    run = runner or subprocess.run
    try:
        proc = run(["docker", "context", "show"],
                   capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001 - docker 不在等は best-effort
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    out = (proc.stdout or "").strip()
    return out or None


_NO_EDITOR_REASON = (
    "エディタ (code) が見つかりません。VS Code の `code` コマンドを PATH に "
    "通すか DEVBASE_EDITOR を設定してください"
)


def decide_action(ctx: EditorContext, editor_available: bool) -> OpenPlan:
    """コンテキストとエディタ可用性から起動方針を決める (§2.4 マトリクス)。

    ``editor_available`` はローカルに launch 可能な ``code`` 系コマンドが実在するか
    (``resolve_editor_cmd`` が非 None か) を表す。plain SSH の print_command 経路は
    「ユーザの手元 (ローカル) でコマンドを実行する」前提のため、コマンドを実行して
    いる側 (リモート) の editor 実在には依存させない (``editor_available`` を見ない)。
    """
    if not ctx.is_tty:
        return OpenPlan("skip", "非対話 (非TTY/CI) 環境のため")
    if ctx.in_vscode:
        # VS Code 統合ターミナル (ローカル / WSL / Remote-SSH シム)。code が
        # クライアント側へ委譲するため直接起動でよい。code シムが無いと委譲
        # できないため editor が無ければ skip。
        if not editor_available:
            return OpenPlan("skip", _NO_EDITOR_REASON)
        return OpenPlan("launch", "VS Code 統合ターミナル経由")
    if ctx.is_ssh:
        # plain SSH (VS Code 外)。クライアントへ push する公式手段が無いため
        # 手元で叩くコマンドを提示する degrade。提示先はローカルなのでリモートの
        # editor 実在には依存しない。
        return OpenPlan("print_command", "SSH セッション (VS Code 外) のため")
    # ローカル/WSL 端末。直接 launch するため editor が無ければ skip。
    if not editor_available:
        return OpenPlan("skip", _NO_EDITOR_REASON)
    return OpenPlan("launch", "ローカル/WSL 端末")


def _launch(cmd: list, env: dict) -> None:
    """エディタを非ブロッキングで起動する (up プロセスを待たせない)。"""
    subprocess.Popen(  # noqa: S603 - argv はコード生成で外部入力を渡さない
        cmd, env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def open_editor(*, project_name: str, dev_service_name: str, workdir: str,
                index: int = 1, compose_file=None, environ=None,
                isatty: Optional[bool] = None, system: Optional[str] = None,
                launcher: Optional[Callable[[list, dict], None]] = None) -> str:
    """dev コンテナへ接続した VS Code を開く / コマンド提示 / スキップする。

    戻り値は実行された action ('launch' | 'print_command' | 'skip')。例外は
    握り潰して warning にし、``up`` 本体を絶対に失敗させない。``isatty`` /
    ``system`` は :func:`detect_context` への差し替え口 (テスト用)。``compose_file``
    は実コンテナ名問い合わせ時に起動と同じ override compose を ``-f`` で渡すため。
    """
    env = os.environ if environ is None else environ
    ctx = detect_context(env, isatty=isatty, system=system)
    editor = resolve_editor_cmd(env)        # launch 用 (which 込み・None あり得る)
    display = resolve_editor_display(env)   # print 用 (必ず非 None)
    plan = decide_action(ctx, editor_available=bool(editor))

    container = resolve_container_name(dev_service_name, project_name, index,
                                       compose_file=compose_file)
    # SSH コンテキストでのみネスト authority (@ssh-remote+host) を組む。ssh_host が
    # 設定されていれば跨ホスト構成と見なし docker context も解決して埋める。非 SSH では
    # 従来のフラット URI (ローカル/WSL/同一ホスト Remote-SSH) を維持する。
    ssh_host = resolve_editor_ssh_host(env) if ctx.is_ssh else None
    docker_context = resolve_docker_context(env) if ssh_host else None
    uri = build_attach_uri(container, workdir,
                           ssh_host=ssh_host, docker_context=docker_context)

    if plan.action == "skip":
        logger.info("エディタの自動オープンをスキップ: %s", plan.reason)
        return "skip"

    if plan.action == "print_command":
        # 提示コマンドは手元 (ローカル) で実行する前提。ローカルに code が無くても
        # 提示できるよう display (which 非依存) を用いる。
        quoted = " ".join(shlex.quote(c) for c in display)
        logger.info("SSH セッションを検出しました (%s)。", plan.reason)
        logger.info(
            "手元の VS Code で次を実行するか、VS Code の Remote-SSH 統合ターミナルから "
            "`devbase up` を実行すると自動で開きます:"
        )
        logger.info("  %s --folder-uri '%s'", quoted, uri)
        return "print_command"

    quoted = " ".join(shlex.quote(c) for c in editor)
    logger.info("[editor] %s を起動します (%s)", quoted, plan.reason)
    cmd = [*editor, "--folder-uri", uri]
    try:
        (launcher or _launch)(cmd, dict(env))
    except Exception as e:  # noqa: BLE001 - 起動失敗で up を倒さない
        logger.warning("エディタの起動に失敗しましたが処理は続行します: %s", e)
    return "launch"
