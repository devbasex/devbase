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
from typing import Dict, List, Optional

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
    """合成した機密と、コンテナへ渡すべき変数名"""

    values: Dict[str, str] = field(default_factory=dict)
    #: コンテナの構成へ列挙する変数名 (共通機密 + プロジェクト機密のキー)
    names: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.names)


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
    names = list(global_secrets)

    merged: Dict[str, str] = dict(global_secrets)

    if project:
        merged.update(_project_env_overrides(root, project))
        project_secrets = store.load(SecretRef.for_project(project))
        merged.update(project_secrets)
        for key in project_secrets:
            if key not in names:
                names.append(key)

    values = {name: merged[name] for name in names if name in merged}
    return SecretEnv(values=values, names=names)


def inject(devbase_root: Path, project: Optional[str] = None,
           *, environ=None, store: Optional[SecretStore] = None) -> SecretEnv:
    """合成した機密を環境変数へ載せ、載せた内容を返す。

    ``docker compose`` は devbase 自身の環境変数から値を解決するため、Compose を
    起動する前にここを通す。
    """
    resolved = resolve(devbase_root, project, store=store)
    target = environ if environ is not None else os.environ
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
