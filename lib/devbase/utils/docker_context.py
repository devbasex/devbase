"""docker context の解決と、その接続先を docker 呼び出しへ届ける仕組み (PLAN52)。

devbase は ``docker`` / ``docker compose`` を ``subprocess`` で叩くだけなので、接続先の
切り替えは **環境変数 ``DOCKER_CONTEXT``** で全呼び出しへ伝える (決定 1)。``--context`` を
引数へ足す形では、30 か所近い呼び出しの 1 つが漏れただけで「一部だけ手元の daemon を触る」
事故になる。

3 段階で扱う:

1. :func:`choose_context` — CLI / env / ``project.local.yml`` / 未指定の順で 1 つに決める。
   docker を呼ばない純粋な処理
2. :func:`resolve_target` — 現在の context (``docker context show``) と比べて**リモート
   扱い**かを決め、``home`` / ``gid`` を添える (決定 3・4)
3. :func:`apply` — ``os.environ`` へ反映する。冪等で、機密の注入が同名キーを上書きした
   後に :func:`reapply` で戻せる (決定 13)。操作の前後で :func:`reset` が元へ戻す

**問い合わせの環境から ``DOCKER_CONTEXT`` と ``DOCKER_HOST`` を外す**のは、docker が
``DOCKER_CONTEXT`` が残ると設定先自身を、``DOCKER_HOST`` が残ると ``default`` を返すため
である (実測、Docker 29.4.3)。載せた後に呼んでも判定が変わらないようにここで外す。

**``DOCKER_HOST`` は context を解決したときに取り除く。** docker は ``DOCKER_HOST`` を
``DOCKER_CONTEXT`` より優先するため、残すと設定した context が効かない。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, MutableMapping, Optional, Union

from devbase.errors import DevbaseError
from devbase.log import get_logger
from devbase.project.local_config import DockerSettings

logger = get_logger(__name__)

#: 上書き用の環境変数 (グローバル ``.env`` / プロジェクト ``env`` / shell)
DEVBASE_DOCKER_CONTEXT = "DEVBASE_DOCKER_CONTEXT"
#: docker CLI と compose が読む接続先
DOCKER_CONTEXT = "DOCKER_CONTEXT"
DOCKER_HOST = "DOCKER_HOST"
DOCKER_GID = "DOCKER_GID"

#: gid 取得に使う公開イメージ (決定 5)。小さく、``stat -c`` を受け付ける
GID_PROBE_IMAGE = "alpine:3"
#: gid の控えの置き場 (``$DEVBASE_ROOT/.cache/`` 配下)
GID_CACHE_SUBDIR = "docker-gid"

_PROTECTED = (DOCKER_CONTEXT, DOCKER_HOST, DOCKER_GID)


@dataclass(frozen=True)
class ContextChoice:
    """優先順位で決めた context と出所 (``cli`` / ``env`` / ``file`` / ``default``)。"""

    context: Optional[str]
    source: str


@dataclass(frozen=True)
class DockerTarget:
    """接続先の確定結果。``remote`` が偽なら ``home`` / ``gid`` は ``None``。"""

    context: Optional[str]
    source: str
    remote: bool
    home: Optional[str]
    gid: Optional[int]


Applicable = Union[ContextChoice, DockerTarget]
Runner = Callable[..., subprocess.CompletedProcess]


# ---------------------------------------------------------------------------
# 1. 解決
# ---------------------------------------------------------------------------

def choose_context(settings: DockerSettings, cli_context: Optional[str] = None,
                   environ: Optional[MutableMapping[str, str]] = None) -> ContextChoice:
    """CLI ``--context`` > env ``DEVBASE_DOCKER_CONTEXT`` > ``docker.context`` > 未指定。

    env の空文字 (空白のみを含む) は「未指定」として扱い、ファイルの値へ落ちる。
    """
    env = os.environ if environ is None else environ
    if cli_context is not None and cli_context.strip():
        return ContextChoice(cli_context.strip(), "cli")
    from_env = (env.get(DEVBASE_DOCKER_CONTEXT) or "").strip()
    if from_env:
        return ContextChoice(from_env, "env")
    if settings.context:
        return ContextChoice(settings.context, "file")
    return ContextChoice(None, "default")


# ---------------------------------------------------------------------------
# 2. 確定
# ---------------------------------------------------------------------------

def current_context(environ: Optional[MutableMapping[str, str]] = None,
                    runner: Optional[Runner] = None) -> Optional[str]:
    """``docker context show`` で現在の context を取る。取れなければ ``None``。

    ``DOCKER_CONTEXT`` と ``DOCKER_HOST`` を外した環境で実行する (モジュール docstring)。
    """
    env = dict(os.environ if environ is None else environ)
    env.pop(DOCKER_CONTEXT, None)
    env.pop(DOCKER_HOST, None)
    run = runner or subprocess.run
    try:
        proc = run(["docker", "context", "show"], capture_output=True, text=True,
                   timeout=10, env=env)
    except Exception as e:  # noqa: BLE001 - docker 不在等はリモート扱いへ倒す
        logger.debug("docker context show を実行できません: %s", e)
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    return (proc.stdout or "").strip() or None


def resolve_target(choice: ContextChoice, settings: DockerSettings,
                   environ: Optional[MutableMapping[str, str]] = None,
                   runner: Optional[Runner] = None) -> DockerTarget:
    """解決した context を現在の context と比べ、リモート扱いかを決める。

    - 未指定 (``None``) は docker を呼ばずローカル扱い
    - 現在の context と同じならローカル扱い。取得できなければリモート扱い (決定 3)
    - リモート扱いでも、CLI / env でファイルと**別の名前**へ向けたときはファイルの
      ``home`` / ``gid`` を使わない (前提 4・決定 4)
    """
    if choice.context is None:
        return DockerTarget(None, choice.source, False, None, None)

    current = current_context(environ, runner)
    remote = current is None or current != choice.context
    if not remote:
        return DockerTarget(choice.context, choice.source, False, None, None)

    home, gid = settings.home, settings.gid
    if choice.source in ("cli", "env") and settings.context and settings.context != choice.context:
        if home is not None or gid is not None:
            logger.warning(
                "docker context を %s で '%s' に上書きしたため、project.local.yml の "
                "docker.home / docker.gid ('%s' 向けの値) は使いません。",
                choice.source, choice.context, settings.context)
        home, gid = None, None
    return DockerTarget(choice.context, choice.source, True, home, gid)


# ---------------------------------------------------------------------------
# 3. 反映
# ---------------------------------------------------------------------------

#: いま有効な接続先と、最初の適用時に控えた元の値。lifecycle 操作の単位で生き、
#: :func:`reset` が捨てる。TUI は 1 プロセスで操作を続けるため、前の操作の接続先を
#: 次へ持ち越さないためにある。
_active: Optional[Applicable] = None
_originals: Optional[Dict[str, Optional[str]]] = None


def apply(target: Applicable, environ: Optional[MutableMapping[str, str]] = None,
          *, track: bool = True) -> None:
    """接続先を環境へ反映する。冪等。

    - context が ``None`` なら何も触らない (従来どおり CLI に委ねる)
    - ``DOCKER_CONTEXT`` を載せ、``DOCKER_HOST`` があれば警告して取り除く
    - :class:`DockerTarget` でリモート扱いかつ gid が決まっていれば ``DOCKER_GID`` も載せる

    ``track=False`` は子プロセス用の辞書へ当てるだけで、モジュールの控えを持たない
    (``env exec``)。控えを持つと、その後の :func:`reset` が別の辞書の元の値を
    ``os.environ`` へ書き戻す。
    """
    global _active, _originals
    env = os.environ if environ is None else environ
    if target.context is None:
        return
    if track:
        if _originals is None:
            _originals = {name: env.get(name) for name in _PROTECTED}
        _active = target

    env[DOCKER_CONTEXT] = target.context
    if DOCKER_HOST in env:
        logger.warning(
            "DOCKER_HOST (%s) が設定されていますが、docker は DOCKER_HOST を DOCKER_CONTEXT "
            "より優先するため、context '%s' を使う間は取り除きます。",
            env[DOCKER_HOST], target.context)
        del env[DOCKER_HOST]
    if isinstance(target, DockerTarget) and target.remote and target.gid is not None:
        env[DOCKER_GID] = str(target.gid)


def reapply(environ: Optional[MutableMapping[str, str]] = None) -> None:
    """控えた接続先があれば :func:`apply` を呼び直す (機密注入の後に使う)。"""
    if _active is not None:
        env = os.environ if environ is None else environ
        # DOCKER_HOST の警告は最初の適用で出しているので、再適用では黙って外す
        env.pop(DOCKER_HOST, None)
        apply(_active, env)


def reset(environ: Optional[MutableMapping[str, str]] = None) -> None:
    """控えた接続先を捨て、3 変数を最初の適用時の値へ戻す。控えが無ければ何もしない。"""
    global _active, _originals
    if _originals is None:
        _active = None
        return
    env = os.environ if environ is None else environ
    for name, value in _originals.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    _active = None
    _originals = None


def active_target() -> Optional[Applicable]:
    return _active


# ---------------------------------------------------------------------------
# gid
# ---------------------------------------------------------------------------

def ensure_remote_gid(target: DockerTarget, cache_dir: Path,
                      environ: Optional[MutableMapping[str, str]] = None,
                      runner: Optional[Runner] = None) -> int:
    """リモート側の docker グループ gid を決める (決定 5)。

    ``docker.gid`` 明示があればそれ。無ければ ``<cache_dir>/docker-gid/<context>`` の
    控えを読み、無ければ ``DOCKER_CONTEXT`` 付きの ``docker run`` で docker.sock の gid を
    取って控える。

    Raises:
        DevbaseError: docker が非ゼロ、または出力が整数でない。
    """
    if target.gid is not None:
        return target.gid

    cache_file = Path(cache_dir) / GID_CACHE_SUBDIR / str(target.context)
    cached = _read_cached_gid(cache_file)
    if cached is not None:
        return cached

    gid = _probe_remote_gid(target, environ, runner)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(f"{gid}\n", encoding="utf-8")
    logger.info("context '%s' の docker gid を取得しました: %d (控え: %s)",
                target.context, gid, cache_file)
    return gid


def _probe_remote_gid(target: DockerTarget,
                      environ: Optional[MutableMapping[str, str]] = None,
                      runner: Optional[Runner] = None) -> int:
    """``DOCKER_CONTEXT`` 付きの ``docker run`` で docker.sock の gid を取る (決定 5)。

    Docker 用の環境を組み、subprocess を実行し、失敗を DevbaseError へ変換し、標準
    出力を整数化する。gid が 0 のときは socket が root 所有 / rootless の可能性を
    警告する (値はそのまま返す)。

    Raises:
        DevbaseError: docker が非ゼロ、実行例外、または出力が整数でない。
    """
    env = dict(os.environ if environ is None else environ)
    env[DOCKER_CONTEXT] = str(target.context)
    env.pop(DOCKER_HOST, None)
    cmd = ["docker", "run", "--rm", "-v", "/var/run/docker.sock:/s",
           GID_PROBE_IMAGE, "stat", "-c", "%g", "/s"]
    run = runner or subprocess.run
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=120, env=env)
    except Exception as e:  # noqa: BLE001 - docker 不在・タイムアウトも同じ案内へ
        raise DevbaseError(_gid_failure_message(target, str(e))) from e
    if proc.returncode != 0:
        raise DevbaseError(_gid_failure_message(target, (proc.stderr or "").strip()))
    out = (proc.stdout or "").strip()
    try:
        gid = int(out)
    except ValueError:
        raise DevbaseError(_gid_failure_message(target, f"出力が整数ではありません: {out!r}"))
    if gid == 0:
        logger.warning(
            "context '%s' の docker.sock の gid は 0 でした。socket が root 所有か rootless "
            "Docker の可能性があります。コンテナから docker を使えない場合は "
            "project.local.yml の docker.gid を明示してください。", target.context)
    return gid


def _read_cached_gid(cache_file: Path) -> Optional[int]:
    try:
        return int(cache_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _gid_failure_message(target: DockerTarget, detail: str) -> str:
    return (
        f"context '{target.context}' の docker グループ gid を取得できません: {detail}\n"
        f"  リモート側の gid を確かめて project.local.yml の docker.gid に書いてください:\n"
        f"    docker:\n      context: {target.context}\n      gid: <getent group docker の値>"
    )


__all__ = [
    "DEVBASE_DOCKER_CONTEXT",
    "DOCKER_CONTEXT",
    "DOCKER_GID",
    "DOCKER_HOST",
    "GID_PROBE_IMAGE",
    "ContextChoice",
    "DockerTarget",
    "active_target",
    "apply",
    "choose_context",
    "current_context",
    "ensure_remote_gid",
    "reapply",
    "reset",
    "resolve_target",
]
