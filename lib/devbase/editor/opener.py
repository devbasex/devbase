"""`devbase up` 後に dev コンテナへ接続した VS Code を自動で開く。

設計の核心 (issues/PLAN31_3_up-open-editor.md §2):

- 一貫機構は **PATH 上の ``code`` への委譲**。VS Code 統合ターミナルでは
  ``VSCODE_IPC_HOOK_CLI`` 経由でクライアント側 VS Code に「このフォルダを開け」を
  IPC 委譲する。WSL では ``code`` ラッパが Windows 側 VS Code を起動する。
  Remote-SSH 統合ターミナルでは ``code`` シムがクライアント (例: Windows) に窓を
  開く。よって ``code --folder-uri <attach-uri>`` を叩くだけで実行コンテキストに
  応じた正しいクライアントへ開ける。
- ただし ``VSCODE_IPC_HOOK_CLI`` は **変数が残っていても接続先が死んでいる**ことがある
  (tmux/screen のセッション再利用、VS Code ウィンドウのリロード後の古い端末など)。
  死に方は 2 通りあり、**ファイルの実在確認だけでは後者を検出できない**:

  1. ソケットファイルごと消えている (VS Code が正常終了時に削除した)
  2. ソケットファイルは残っているが listen しているプロセスが居ない
     (クラッシュ・強制終了・OS 再起動などで後始末されなかった)

  2 の状態で ``code`` を叩くと ``ECONNREFUSED`` で失敗する。よって
  :func:`_ipc_socket_alive` では **実際に connect して**生死を判定してから
  ``in_vscode`` を立てる。
- コンテナ attach URI は ``{"containerName":"/<実コンテナ名>"}`` を hex 化した
  authority を持つ (:func:`build_attach_uri`)。
- **跨ホスト (手元 VS Code → Remote-SSH(host) → ssh 先の Docker 上コンテナ) では
  ネスト authority ``attached-container+...@ssh-remote+<host>`` を用いる**。これは
  実機で動作する (VS Code 1.124 / Dev Containers 0.459 で確認。当初 microsoft/vscode#242489
  を「未サポート」と解釈していたが誤りだった)。``<host>`` は手元クライアントの ssh 接続
  ラベルで、env には現れないため ssh 先の ``~/.vscode-server`` 等の File History から
  自動検出する (:func:`resolve_editor_ssh_host`)。``settings.context`` で ssh 先 docker
  context を指定する。
- VS Code 外の plain SSH は既存 ExecServer を前提にできずネスト URI が動かないため、
  自動検出は行わず (明示設定時のみ)、手元で叩くコマンドを提示する degrade に留める。

本モジュールの関数は実 docker / VS Code を必要とせず、``environ`` 等を引数で
差し替えてテストできるよう副作用を :func:`open_editor` に集約している。
"""

from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from devbase.log import get_logger

logger = get_logger(__name__)

# DEVBASE_OPEN_EDITOR を真と解釈する値 (大小無視)
_TRUTHY = {"1", "true", "yes", "on"}

# resource URI 中の ssh-remote authority ラベルを拾う ('+' は URL エンコードで %2B)。
_SSH_REMOTE_RE = re.compile(r"ssh-remote(?:\+|%2[Bb])([A-Za-z0-9._@-]+)")

# ssh host 自動検出で探索する VS Code 系サーバーディレクトリ (DEVBASE_EDITOR で
# code / code-insiders / cursor / vscodium 等を使い分けても拾えるよう横断する)。
_SERVER_DIR_CANDIDATES = (
    "~/.vscode-server",
    "~/.vscode-server-insiders",
    "~/.cursor-server",
    "~/.vscodium-server",
    "~/.windsurf-server",
)

# ssh host 自動検出で内容を読む entries.json の上限 (mtime 降順で新しい方から)。
# 該当ホストは直近接続のファイルにあるため、無マッチ時に全件 read するのを防ぐ。
_HISTORY_SCAN_LIMIT = 200


@dataclass(frozen=True)
class EditorContext:
    """エディタ起動先の判定に使う実行コンテキスト。"""

    is_tty: bool
    in_vscode: bool   # 生きている IPC ソケットが見つかっている
    is_wsl: bool
    is_ssh: bool
    is_darwin: bool
    # 実際に使う IPC ソケット。env の VSCODE_IPC_HOOK_CLI が死んでいて tmux の
    # セッション環境から拾い直した場合、env の値とは異なる (None は未解決)。
    ipc_socket: Optional[str] = None


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


# connect() の待ち時間上限 (秒)。相手が生きていれば UNIX ドメインソケットの接続は
# 即座に完了するため短くてよい。`devbase up` の最後に走るので体感を優先する。
_IPC_CONNECT_TIMEOUT = 0.5

# tmux show-environment の待ち時間上限 (秒)。ローカルの tmux サーバーへの
# 問い合わせなので即答するが、応答が無いときに up を止めないよう上限を置く。
_TMUX_TIMEOUT = 2.0


def _ipc_socket_alive(environ) -> bool:
    """``VSCODE_IPC_HOOK_CLI`` が **応答するソケット** を指しているか。

    「変数が設定されているか」だけでは不十分で、**ファイルの実在確認でも足りない**。
    VS Code はウィンドウごとに ``$TMPDIR/vscode-ipc-<uuid>.sock`` を作るが、変数が
    古いまま残る状況が日常的に起きる:

    - tmux / screen: サーバーがセッション作成時の環境変数を保持し続けるため、
      同じセッションに再アタッチした端末は死んだソケットのパスを引き継ぐ
      (``update-environment`` に ``VSCODE_IPC_HOOK_CLI`` を足すと緩和できる)
    - VS Code ウィンドウのリロード後に残った古いシェル
    - ``nohup`` / デーモン化して生き残ったプロセス

    このときソケットの死に方は 2 通りある:

    1. **ファイルごと消えている** — ウィンドウを正常に閉じると VS Code が削除する
    2. **ファイルは残っているが listen していない** — クラッシュ・強制終了・OS 再起動
       などで後始末されなかった場合。``$TMPDIR`` に孤児ソケットとして溜まる

    2 を ``in_vscode=True`` と誤判定すると :func:`decide_action` が ``launch`` を選び、
    ``code`` が ``ECONNREFUSED`` で失敗する。実在確認では 1 しか弾けないため、
    **実際に connect して**判定する。False に倒せば SSH 経路なら ``print_command`` へ
    degrade して、ユーザが手元で実行できるコマンドを提示できる。
    """
    return _socket_connectable(environ.get("VSCODE_IPC_HOOK_CLI"))


def _socket_connectable(sock: Optional[str]) -> bool:
    """UNIX ドメインソケットのパスへ実際に接続できるか。"""
    if not sock:
        return False
    family = getattr(socket, "AF_UNIX", None)
    if family is None:
        # AF_UNIX が無いプラットフォームでは接続確認ができないので実在確認に留める。
        return os.path.exists(sock)
    try:
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(_IPC_CONNECT_TIMEOUT)
            s.connect(sock)
        return True
    except (OSError, ValueError):
        # ファイル不在 (ENOENT) / listen 不在 (ECONNREFUSED) / 権限 (EACCES) /
        # ソケットでない (ECONNREFUSED, ENOTSOCK) / パス長超過 (ValueError) を
        # まとめて「使えない」と扱う。
        return False


def _tmux_env(name: str, environ) -> Optional[str]:
    """tmux の **セッション環境** から変数を 1 つ読む。tmux 外なら None。

    tmux サーバーはセッション作成時の環境変数を保持し続けるが、``update-environment``
    に登録された変数は **attach のたびに**接続してきたクライアントの値へ更新される。
    そのため「すでに動いているペインのシェルは古い値、tmux のセッション環境は新しい値」
    という状態が普通に起きる。ここはその新しい方を読むための口。

    ``tmux show-environment <NAME>`` は未設定の変数を ``-NAME`` の形で返すため、
    値として扱わないようにする。
    """
    if not environ.get("TMUX"):
        return None
    try:
        out = subprocess.run(  # noqa: S603,S607 - 引数は固定、PATH 上の tmux を使う
            ["tmux", "show-environment", name],
            # TMUX を見て判定した以上、tmux クライアントも同じ環境に向ける
            # (PATH 等は失わないよう os.environ に environ を上書き合成する)。
            env={**os.environ, **environ},
            capture_output=True, text=True, timeout=_TMUX_TIMEOUT, check=False,
        )
    except Exception:  # noqa: BLE001 - tmux 不在/非UTF-8出力等で up を倒さない
        return None
    if out.returncode != 0:
        return None
    line = out.stdout.strip()
    prefix = f"{name}="
    if not line.startswith(prefix):
        # "-NAME" (削除済み) や想定外の出力
        return None
    return line[len(prefix):] or None


def resolve_ipc_socket(environ) -> Optional[str]:
    """実際に使える VS Code IPC ソケットのパスを返す (無ければ None)。

    1. ``VSCODE_IPC_HOOK_CLI`` が生きていればそれを使う
    2. 死んでいて tmux 内なら、tmux のセッション環境の値を試す

    2 が要るのは、tmux のセッション環境だけが新しく、ペインのシェルが古い値を
    抱えたままという状態が頻繁に起きるため (:func:`_tmux_env` 参照)。シェル側の
    プロンプトフックで追随させる運用もあるが、それが入っていない環境でも
    ``devbase up --open`` が自動で開けるように devbase 側でも拾いにいく。
    """
    current = environ.get("VSCODE_IPC_HOOK_CLI")
    if current and _socket_connectable(current):
        return current
    candidate = _tmux_env("VSCODE_IPC_HOOK_CLI", environ)
    if candidate and candidate != current and _socket_connectable(candidate):
        return candidate
    return None


def detect_context(environ=None, isatty: Optional[bool] = None,
                   system: Optional[str] = None,
                   ipc_alive: Optional[bool] = None) -> EditorContext:
    """env / OS からエディタ起動先判定に必要なコンテキストを抽出する。

    引数はテスト用の差し替え口。未指定なら ``os.environ`` / ``sys.stdout`` /
    ``platform.system()`` / :func:`resolve_ipc_socket` を用いる。

    ``ipc_alive`` を明示した場合はソケット解決を一切行わない。``True`` を渡した
    ときの ``ipc_socket`` は env の値をそのまま入れる (テスト用の差し替え口)。
    """
    env = os.environ if environ is None else environ
    if isatty is None:
        isatty = _stdout_isatty()
    if system is None:
        system = platform.system()
    if ipc_alive is None:
        sock = resolve_ipc_socket(env)
        ipc_alive = sock is not None
    else:
        sock = env.get("VSCODE_IPC_HOOK_CLI") if ipc_alive else None
    return EditorContext(
        is_tty=bool(isatty),
        in_vscode=bool(ipc_alive),
        is_wsl=_detect_wsl(env),
        is_ssh=any(env.get(k) for k in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")),
        is_darwin=(system == "Darwin"),
        ipc_socket=sock,
    )


def is_open_enabled(environ=None, config=None) -> bool:
    """エディタを自動で開くかどうか。

    プロジェクト設定 (``project.yml`` の ``open_editor``) が指定されていればそれを
    採る。未指定なら env ``DEVBASE_OPEN_EDITOR`` (グローバル ``.env`` の既定値)
    を見る。どちらも無ければ開かない。
    """
    if config is not None and config.open_editor is not None:
        return config.open_editor
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


def resolve_workspace(environ=None) -> Optional[str]:
    """開く VS Code ワークスペースファイル (``*.code-workspace``) のコンテナ内パス。

    ``DEVBASE_WORKSPACE`` env にコンテナ内の絶対パス (例
    ``/home/ubuntu/share/work/uttarov2-doc.workspace``) が指定されていればそれを返す。
    未設定・空文字なら None を返し、呼び出し側 (:func:`open_editor`) はフォルダを
    ``--folder-uri`` で開く。

    複数リポジトリのプロジェクトでは ``devbase up`` が ``project.yml`` から
    workspace パスを決めて :func:`open_editor` の ``workspace`` 引数で直接渡す。
    この env はそれを手動で上書きしたい場合の口として残している。

    ワークスペースファイルはコンテナ内に実在するパスを指す前提 (attach 先は
    コンテナ authority のため)。``/home/ubuntu/share`` 等の共有マウント配下に置けば
    全コンテナで共用できる。env 名はホスト CI が設定し得る汎用 ``WORKSPACE`` との
    衝突を避けるため ``DEVBASE_*`` 接頭辞付きにしている。
    """
    env = os.environ if environ is None else environ
    value = env.get("DEVBASE_WORKSPACE")
    if value is None:
        return None
    return value.strip() or None


def _detect_ssh_host_from_dirs(server_dirs) -> Optional[str]:
    """複数の VS Code 系サーバーディレクトリの File History を横断して ssh-remote
    authority ラベルを推測する。

    Remote-SSH / attached-container 窓で開いたファイルの resource URI が
    ``<server>/data/User/History/*/entries.json`` に ``ssh-remote%2B<host>`` (URL
    エンコード) / ``ssh-remote+<host>`` 形で残るため、そこから ``<host>`` (= クライアントの
    接続ラベル。例 ``mac2``) を回収する。

    全ディレクトリの ``entries.json`` 候補を **mtime 降順**で集め、**新しい方から 1 ファイル
    ずつ読み、最初に ssh-remote ホストが見つかった時点で即 return** する (History が数千
    ファイルに膨れても全読み込みを避け、devbase up の遅延を防ぐ)。mtime 収集は stat のみで安価。
    見つからなければ None。

    .. note:: VS Code 内部データ依存のヒューリスティックで、バージョン差や multi-host 運用で
       外し得る。確実性が要る場合は ``DEVBASE_EDITOR_SSH_HOST`` を明示する (本関数より優先)。
    """
    candidates = []  # (mtime, path)
    for base in server_dirs:
        history = os.path.join(base, "data", "User", "History")
        # ローカル履歴は History/<hash>/entries.json の固定深さなので、os.walk で
        # 全階層を再帰せず os.scandir で 1 階層下のみ走査して I/O を抑える。
        try:
            with os.scandir(history) as it:
                subdirs = [e.path for e in it if e.is_dir()]
        except OSError:
            continue
        for sub in subdirs:
            path = os.path.join(sub, "entries.json")  # resource authority はここに載る
            try:
                candidates.append((os.path.getmtime(path), path))
            except OSError:
                continue
    newest_first = sorted(candidates, key=lambda t: t[0], reverse=True)
    for _mtime, path in newest_first[:_HISTORY_SCAN_LIMIT]:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        match = _SSH_REMOTE_RE.search(text)
        if match:
            return match.group(1)
    return None


def _detect_ssh_host_from_vscode(vscode_server_dir: str) -> Optional[str]:
    """単一サーバーディレクトリ版 (:func:`_detect_ssh_host_from_dirs` の薄ラッパ)。"""
    return _detect_ssh_host_from_dirs([vscode_server_dir])


def resolve_editor_ssh_host(environ=None,
                            vscode_server_dir: Optional[str] = None,
                            auto_detect: bool = True) -> Optional[str]:
    """Remote-SSH ネスト URI 用の ssh ホスト名 (authority ラベル) を解決する。

    優先順位:

    1. ``DEVBASE_EDITOR_SSH_HOST`` 明示 (最優先・確実)
    2. VS Code 系サーバーディレクトリ (``~/.vscode-server`` / ``~/.cursor-server`` /
       ``~/.vscode-server-insiders`` 等) の File History からの自動推測
       (:func:`_detect_ssh_host_from_dirs`)

    ネスト attach は新規 ssh 接続を張らず **既存 Remote-SSH 接続 (ExecServer) の authority
    ラベルと完全一致**する必要がある (実機確認: IP / user@IP は "Parent authority found
    without ExecServer" で不可)。そのラベル (例 ``mac2``) はクライアント側の名前で SSH_CONNECTION
    等の env には現れない (IP のみ) ため、自動取得は VS Code が残す痕跡からの回収に頼る。
    どちらでも得られなければ None で :func:`build_attach_uri` はフラット URI に degrade する。

    ``vscode_server_dir`` はテスト用の単一ディレクトリ差し替え口 (指定時はそれだけを探索)。
    ``auto_detect`` を False にすると 2 (自動推測) を行わず明示設定のみで判定する。plain SSH
    (VS Code 外) は既存 ExecServer を前提にできずネスト URI が動かないため、呼び出し側
    (:func:`open_editor`) は ``in_vscode`` の時だけ ``auto_detect=True`` で呼ぶ。
    """
    env = os.environ if environ is None else environ
    explicit = env.get("DEVBASE_EDITOR_SSH_HOST")
    if explicit is not None:
        # 明示設定を最優先。空文字 ("") は **自動推測のオプトアウト** (= None →
        # フラット URI 強制) として扱い、`~/.vscode-server` 探索へ進ませない。
        return explicit.strip() or None
    if not auto_detect:
        return None
    if vscode_server_dir is not None:
        server_dirs = [vscode_server_dir]
    else:
        server_dirs = [os.path.expanduser(d) for d in _SERVER_DIR_CANDIDATES]
    try:
        return _detect_ssh_host_from_dirs(server_dirs)
    except Exception:  # noqa: BLE001 - 自動推測失敗で up を倒さない
        return None


def resolve_docker_context(environ=None, runner: Optional[Callable] = None) -> Optional[str]:
    """ssh 先で使う docker context を解決する。

    ``DEVBASE_EDITOR_DOCKER_CONTEXT`` 明示があればそれ。無ければ devbase up を実行して
    いるホスト (= コンテナのある Mac) の現在の docker context を ``docker context show``
    で取得する。docker 不在・非0・例外・空はすべて None (settings.context を付けない)。
    """
    env = os.environ if environ is None else environ
    explicit = env.get("DEVBASE_EDITOR_DOCKER_CONTEXT")
    if explicit is not None:
        # 空文字 ("") は明示的オプトアウト (settings.context を付けない) として扱い、
        # `docker context show` を呼ばない。
        return explicit.strip() or None
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
    """エディタを非ブロッキングで起動する (up プロセスを待たせない)。

    stdout は捨てるが **stderr は握り潰さない** (親へ継承する)。``code`` は IPC 接続に
    失敗すると stderr にのみ理由を出すため、ここを DEVNULL にすると「何も起きないが
    エラーも出ない」という最も切り分けづらい失敗になる。非ブロッキング起動なので
    メッセージは up の出力に遅れて混ざり得るが、無言よりは有用。
    """
    subprocess.Popen(  # noqa: S603 - argv はコード生成で外部入力を渡さない
        cmd, env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )


def open_editor(*, project_name: str, dev_service_name: str, workdir: str,
                workspace: Optional[str] = None,
                index: int = 1, compose_file=None,
                environ=None,
                isatty: Optional[bool] = None, system: Optional[str] = None,
                ipc_alive: Optional[bool] = None,
                launcher: Optional[Callable[[list, dict], None]] = None) -> str:
    """dev コンテナへ接続した VS Code を開く / コマンド提示 / スキップする。

    戻り値は実行された action ('launch' | 'print_command' | 'skip')。例外は
    握り潰して warning にし、``up`` 本体を絶対に失敗させない。``isatty`` /
    ``system`` / ``ipc_alive`` は :func:`detect_context` への差し替え口 (テスト用)。
    ``compose_file`` は実コンテナ名問い合わせ時に起動と同じ override compose を
    ``-f`` で渡すため。``workspace`` は複数リポジトリ構成で開く
    ``*.code-workspace`` のコンテナ内パス (未指定なら env ``DEVBASE_WORKSPACE``)。
    """
    env = os.environ if environ is None else environ
    ctx = detect_context(env, isatty=isatty, system=system, ipc_alive=ipc_alive)
    # tmux のセッション環境から拾い直せた場合は、起動する code にもその値を渡す。
    # 変数を差し替えないと code 自身が古いソケットへ繋ぎに行って失敗する。
    stale_ipc = env.get("VSCODE_IPC_HOOK_CLI")
    if ctx.ipc_socket and ctx.ipc_socket != stale_ipc:
        env = dict(env)
        env["VSCODE_IPC_HOOK_CLI"] = ctx.ipc_socket
        logger.info(
            "VSCODE_IPC_HOOK_CLI が古かったため tmux のセッション環境から拾い直しました "
            "(%s → %s)。ペインのシェルに追随させたい場合は環境変数ガイドの "
            "tmux 設定を参照してください。",
            stale_ipc or "(未設定)", ctx.ipc_socket,
        )
    # 変数だけ残って接続先が死んでいる IPC ソケットは無言の失敗になりやすいので
    # 明示する (tmux セッション再利用・VS Code ウィンドウのリロード後など)。
    if stale_ipc and not ctx.in_vscode:
        logger.warning(
            "VSCODE_IPC_HOOK_CLI が指すソケットに接続できません (%s)。VS Code 統合"
            "ターミナルとしては扱いません。tmux/screen のセッションを再利用している"
            "場合や VS Code のウィンドウをリロードした後の古い端末で起きます。"
            "ソケットファイルが残っていても、VS Code の異常終了後は listen して"
            "おらず接続を拒否します。",
            stale_ipc,
        )
    editor = resolve_editor_cmd(env)        # launch 用 (which 込み・None あり得る)
    display = resolve_editor_display(env)   # print 用 (必ず非 None)
    plan = decide_action(ctx, editor_available=bool(editor))

    # skip は URI 解決の前に early return する。skip 経路 (非 TTY/CI・code 不在等) で
    # docker compose ps / docker context show 等の外部コマンドを無駄に叩かないため。
    if plan.action == "skip":
        logger.info("エディタの自動オープンをスキップ: %s", plan.reason)
        return "skip"

    container = resolve_container_name(
        dev_service_name, project_name, index, compose_file=compose_file)
    # SSH コンテキストでのみネスト authority (@ssh-remote+host) を組む。自動推測は
    # VS Code Remote-SSH 統合端末 (in_vscode) の時だけ有効にする — plain SSH (VS Code 外)
    # は既存 ExecServer を前提にできずネスト URI が動かないため、明示設定時のみ採用する。
    ssh_host = (resolve_editor_ssh_host(env, auto_detect=ctx.in_vscode)
                if ctx.is_ssh else None)
    docker_context = resolve_docker_context(env) if ssh_host else None
    # DEVBASE_WORKSPACE があれば *.code-workspace をワークスペースとして開く。VS Code は
    # `--file-uri` に渡したパスが .code-workspace 拡張子なら multi-root ワークスペースとして
    # 開くため、フォルダを開く `--folder-uri` と URI ターゲット・フラグの両方を切り替える。
    workspace = workspace or resolve_workspace(env)
    open_target = workspace or workdir
    uri_flag = "--file-uri" if workspace else "--folder-uri"
    uri = build_attach_uri(container, open_target,
                           ssh_host=ssh_host, docker_context=docker_context)

    if plan.action == "print_command":
        # 提示コマンドは手元 (ローカル) で実行する前提。ローカルに code が無くても
        # 提示できるよう display (which 非依存) を用いる。
        quoted = " ".join(shlex.quote(c) for c in display)
        logger.info("SSH セッションを検出しました (%s)。", plan.reason)
        logger.info(
            "手元の VS Code で次を実行するか、VS Code の Remote-SSH 統合ターミナルから "
            "`devbase up` を実行すると自動で開きます:"
        )
        logger.info("  %s %s '%s'", quoted, uri_flag, uri)
        return "print_command"

    quoted = " ".join(shlex.quote(c) for c in editor)
    logger.info("[editor] %s を起動します (%s)", quoted, plan.reason)
    cmd = [*editor, uri_flag, uri]
    try:
        (launcher or _launch)(cmd, dict(env))
    except Exception as e:  # noqa: BLE001 - 起動失敗で up を倒さない
        logger.warning("エディタの起動に失敗しましたが処理は続行します: %s", e)
    return "launch"
