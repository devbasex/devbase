"""実行時に機密をメモリ上で合成し、子プロセスへ渡す

暗号化した機密は、恒久的な平文ファイルを介さずにコンテナへ届ける必要がある
(plan35 §4.2)。本モジュールは復号結果をプロセス内で合成し、

  - ``docker compose`` を起動する devbase 自身の環境変数へ載せる
  - コンテナへ渡すべき**変数名の一覧**を返す

の 2 つを提供する。値を持たない変数名の列挙を構成ファイルに書けば、Docker
Compose は自分を起動したプロセスの環境変数からその値を解決する。結果として
暗号文も平文ファイルも Compose には渡らない。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from devbase.env.secret_store import SecretRef, SecretStore
from devbase.env.store import EnvFile
from devbase.log import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# プロジェクトの特定
# ---------------------------------------------------------------------------

def current_project_name(devbase_root: Path, cwd: Optional[Path] = None) -> Optional[str]:
    """CWD が ``projects/<name>`` 配下ならプロジェクト名を返す。

    ``projects/<name>/sub/dir`` のような下位ディレクトリから実行された場合も
    ``<name>`` を返す。保存先はプロジェクトの直下に固定したい (コンテナ構成が
    参照するのはそこであり、実行時の CWD ではない) ため、末尾ではなく先頭の
    パス要素を採用する。

    判定は論理パス → 物理パスの順に 2 段で行う。両方が要るのは:

    - ``.resolve()`` だけだと、プラグイン経由で ``projects/<name>`` が
      シンボリックリンクになっているプロジェクト配下で実行したときに
      リンク先の実体を指してしまい、``projects/`` の外と判定される。
    - 論理パスだけだと、リンク先の実体パスで入ったときに ``projects/`` 配下と
      判定できない。

    ``PWD`` 由来のパスはシェルがシンボリックリンクを保った論理パスなので、
    まず ``resolve()`` せずそのまま突き合わせる。

    2 段で使う正規化が違うのは、それぞれ守りたい性質が違うため:

    - 論理パス側は ``os.path.abspath`` (= ``normpath``) で ``..`` を **文字列として**
      畳む。シンボリックリンクを解いてしまうと上記の症状が戻るので解かない。一方
      ``..`` を畳まないと ``projects/web/../../outside`` のような
      ``projects/`` の外を指すパスが ``relative_to`` を通ってしまい、プロジェクト外
      からの ``--project`` が ``web`` の設定を書き換える。``..`` を textual に畳む
      のはシェルの ``cd`` / ``PWD`` の意味論そのものなので、論理パス扱いと矛盾しない。
    - 物理パス側は ``.resolve()`` でリンクも ``..`` も実体まで解く。こちらは
      「実体パスで入られた場合」を拾うためのフォールバックなので、リンクを
      保つ理由が無い。
    """
    current = Path(cwd) if cwd is not None else Path(os.environ.get('PWD', os.getcwd()))
    projects_dir = Path(devbase_root) / 'projects'

    def to_logical(path: Path) -> Path:
        """シンボリックリンクは解かず、絶対パス化と ``..`` の畳み込みだけ行う"""
        return Path(os.path.abspath(path))

    for to_path in (to_logical, Path.resolve):
        try:
            relative = to_path(current).relative_to(to_path(projects_dir))
        except (ValueError, OSError):
            continue
        parts = relative.parts
        if parts:
            return parts[0]
    return None


# ---------------------------------------------------------------------------
# 機密の合成
# ---------------------------------------------------------------------------

@dataclass
class SecretEnv:
    """合成した機密と、コンテナへ渡すべき変数名

    変数名を**由来 (共通 / プロジェクト) ごとに分けて**持つ。構成生成側は、
    サービスが元々 ``env_file`` で参照していた由来のキーだけを列挙する必要が
    あり、全キーをまとめた一覧しか無いと、共通設定だけを読んでいたサービスへ
    プロジェクト固有のトークンまで渡ってしまうため (plan35 §4.3)。
    """

    values: Dict[str, str] = field(default_factory=dict)
    #: 共通機密 (``$DEVBASE_ROOT/.env``) 由来のキー
    global_names: List[str] = field(default_factory=list)
    #: プロジェクト機密 (``projects/<name>/.env``) 由来のキー
    project_names: List[str] = field(default_factory=list)

    @property
    def names(self) -> List[str]:
        """コンテナの構成へ列挙する変数名の全体 (共通 → プロジェクトの順)

        由来を問わず全件が要る場面 (dev サービス、注入した件数のログ) 向けの
        従来どおりの一覧。重複は先に現れた側の位置で 1 件に畳む。
        """
        return list(dict.fromkeys([*self.global_names, *self.project_names]))

    def __bool__(self) -> bool:
        return bool(self.global_names or self.project_names)


def _project_env_overrides(devbase_root: Path, project: str) -> Dict[str, str]:
    """プロジェクトの非機密設定 (``projects/<name>/env``) による上書き値。

    値そのものはファイルから読まず、既に環境変数へ載っているものだけを採用する。
    ``env`` は ``WORK_DIR=/work/$GIT_REPO`` のように同一ファイル内の変数を参照
    するため、起動ラッパー (または ``_load_project_env``) が展開した後の値が
    正しく、ここで生の行を読み直すと未展開の文字列を掴んでしまう。
    """
    path = Path(devbase_root) / 'projects' / project / 'env'
    if not path.is_file():
        return {}
    try:
        raw = path.read_bytes()
    except OSError as e:
        logger.warning("プロジェクト設定を読めませんでした (%s): %s", path, e)
        return {}
    try:
        keys = EnvFile.parse_bytes(raw).keys()
    except UnicodeDecodeError as e:
        logger.warning("プロジェクト設定を UTF-8 として読めませんでした (%s): %s", path, e)
        return {}
    return {key: os.environ[key] for key in keys if key in os.environ}


def resolve(devbase_root: Path, project: Optional[str] = None,
            *, store: Optional[SecretStore] = None) -> SecretEnv:
    """機密を合成して返す。

    重ね順は従来の ``env_file`` の並びを踏襲する:
    共通の機密 → プロジェクトの非機密設定 → プロジェクトの機密。

    コンテナへ列挙するのは共通機密とプロジェクト機密のキーだけで、非機密設定は
    構成ファイルが ``env_file`` として直接読むため列挙しない。ただし両方に同じ
    キーがある場合は、列挙した変数の**値**として非機密設定側を採用する。
    ``environment`` は ``env_file`` より優先されるため、こうしないと
    「プロジェクト設定が共通設定を上書きする」という従来の関係が反転する。
    """
    root = Path(devbase_root)
    store = store if store is not None else SecretStore(root)

    global_secrets = store.load(SecretRef.for_global())
    global_names = list(global_secrets)
    project_names: List[str] = []

    merged: Dict[str, str] = dict(global_secrets)

    if project:
        merged.update(_project_env_overrides(root, project))
        project_secrets = store.load(SecretRef.for_project(project))
        merged.update(project_secrets)
        project_names = list(project_secrets)

    resolved = SecretEnv(global_names=global_names, project_names=project_names)
    resolved.values = {
        name: merged[name] for name in resolved.names if name in merged
    }
    return resolved


#: この実行で :func:`inject` が載せた履歴。
#:
#: 値は ``(対象の環境マッピング, {変数名: 載せる**前**の値 (未設定なら None)})``。
#:
#: 「載せた変数名」だけでなく元の値まで控えるのは、解除時に利用者がシェルで
#: 設定していた同名の変数まで消さないため。元々あった変数は元の値へ戻し、
#: 元々無かった変数だけを削除する。
#:
#: さらに**対象マッピングごとに**分けて持つ。:func:`inject` / :func:`clear_injected`
#: は ``environ`` 引数で ``os.environ`` 以外のマッピングを渡され得る (テストや、
#: 将来「子プロセス用の辞書へ載せて後で戻す」ような呼び出し) ため。履歴が全体で
#: 1 つしか無いと、``inject(..., environ=A)`` の後に ``clear_injected(environ=B)``
#: を呼んだとき、A に対して記録した内容で B を書き換えてしまい (誤って B の値を
#: 「復元」し)、かつ A には機密が載ったまま残る。
#:
#: ``dict`` は hashable ではないのでキーには ``id()`` を使うが、対象そのものへの
#: 参照も一緒に保持する。参照を持つ限り対象オブジェクトは生存し続けるので、
#: 解放済みアドレスの ``id`` が別のマッピングへ再利用されて履歴が誤爆すること
#: がない。解除した時点でその対象の履歴ごと捨てる。
_injected_originals: Dict[int, Tuple[Any, Dict[str, Optional[str]]]] = {}


def _history_for(target) -> Dict[str, Optional[str]]:
    """対象マッピングに紐づく注入履歴を返す (無ければ作る)"""
    _, originals = _injected_originals.setdefault(id(target), (target, {}))
    return originals


def clear_injected(environ=None) -> List[str]:
    """この実行で載せた機密を取り除き、注入前の状態へ戻す。

    プロジェクトを切り替える経路 (TUI や ``project up <other>`` の直接起動) では、
    切替元プロジェクトの機密を載せた後に切替先の機密を載せ直すことになる。この
    とき**単に上書きするだけでは足りない**: 切替先に同名のキーが無ければ、切替元
    固有の機密が ``os.environ`` に残ったまま Compose や子プロセスへ引き継がれて
    しまうため。載せ直す前にここを通して、切替元の値を確実に落とす。

    非機密設定 (``env``) について起動ラッパーの ``_CALLER_ENV_KEYS`` や
    :func:`devbase.commands.container._resolve_project_name` が行っている
    「呼び出し元固有のキーを unset してから対象を読む」のと同じ性質を、機密に
    ついても満たすための関数。

    自分が **その対象マッピングへ** 載せたキーだけを対象にする。利用者がシェルで
    設定していた同名の変数は注入前の値へ戻すので、消えることはない。他の
    マッピングへの注入は、ここでは一切触らない。

    Returns:
        取り除いた (または元へ戻した) 変数名の一覧
    """
    target = environ if environ is not None else os.environ
    entry = _injected_originals.pop(id(target), None)
    if entry is None:
        return []
    _, originals = entry
    cleared = list(originals)
    for name, original in originals.items():
        if original is None:
            target.pop(name, None)
        else:
            target[name] = original
    if cleared:
        logger.debug("機密 %d 件を環境変数から取り除きました", len(cleared))
    return cleared


def inject(devbase_root: Path, project: Optional[str] = None,
           *, environ=None, store: Optional[SecretStore] = None) -> SecretEnv:
    """合成した機密を環境変数へ載せ、載せた内容を返す。

    ``docker compose`` は devbase 自身の環境変数から値を解決するため、Compose を
    起動する前にここを通す。

    載せた変数名と注入前の値を **載せた対象マッピングごとに** 記録し、
    :func:`clear_injected` で元へ戻せるようにする。プロジェクト切替時に切替元の
    機密を落とすために必要 (詳細は :func:`clear_injected` の説明を参照)。
    """
    resolved = resolve(devbase_root, project, store=store)
    target = environ if environ is not None else os.environ
    if resolved.values:
        # 履歴は対象マッピングごとに持つ (理由は _injected_originals の説明を参照)。
        # 載せるものが無いときは記録も作らない (空の履歴が対象への参照を抱え込む
        # のを避ける)。
        originals = _history_for(target)
        for name in resolved.values:
            # 既に記録済みなら上書きしない。記録したいのは「devbase が最初に載せる
            # 前の値」であって、前回の注入で載せた機密ではないため。
            if name not in originals:
                originals[name] = target.get(name)
    target.update(resolved.values)
    if resolved.names:
        logger.debug("機密 %d 件を環境変数へ載せました", len(resolved.names))
    return resolved


def child_env(devbase_root: Path, project: Optional[str] = None,
              *, base=None, store: Optional[SecretStore] = None) -> Dict[str, str]:
    """機密を載せた子プロセス用の環境変数辞書を作る (``os.environ`` は変えない)"""
    env = dict(base if base is not None else os.environ)
    env.update(resolve(devbase_root, project, store=store).values)
    return env
