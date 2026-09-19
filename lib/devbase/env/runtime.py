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
from typing import Any, Dict, List, Optional, Set, Tuple

from devbase.env import keys
from devbase.env.secret_store import SecretRef, SecretStore
from devbase.env.store import EnvFile
from devbase.log import get_logger

logger = get_logger(__name__)

#: 機密の置き場に :data:`~devbase.env.keys.DEVBASE_ACCOUNT_GROUP` があることを警告した
#: 参照の集合 (PLAN62 決定 4)。
#:
#: 1 回の起動で :func:`resolve` は何度も呼ばれる (dispatch 前の注入・各コマンドの
#: ``_inject_secrets``・``env exec``)。同じ置き場について警告を 1 回にするため、出した参照を
#: ここに控える。:class:`SecretStore` に持たせないのは、ストアが :func:`release_store` で
#: 捨てられる (``up`` の中の ``env init`` の後など) たびに警告が出直すため。プロセスが終わる
#: まで持つ。
_warned_account_group_refs: Set[SecretRef] = set()


# ---------------------------------------------------------------------------
# SecretStore の持ち回り (PLAN55)
# ---------------------------------------------------------------------------

#: 1 回のライフサイクル操作の間、持ち回る :class:`SecretStore` とその ``root``。
#:
#: 注入は ``cli._load_secret_env`` (dispatch 前) / ``_dispatch_lifecycle`` (切替後) /
#: ``_run_deploy_pipeline`` (起動直前) の 3 か所で行われ、それぞれ別の理由で置かれている。
#: 注入のたびに ``SecretStore`` を作り直すとサーバ backend では認証と取得が繰り返される
#: ため、インスタンスの寿命を操作 1 回に揃えて 2 度目以降の解決を控え (``_seen``) から
#: 返す。捨てる契機は呼び出し側 (``_dispatch_lifecycle`` の ``finally`` など) が持つ。
_store: Optional[SecretStore] = None
_store_root: Optional[Path] = None


def store_for(devbase_root: Path) -> SecretStore:
    """持ち回っている :class:`SecretStore` を返す。無ければ作り、``root`` が違えば作り直す。"""
    global _store, _store_root
    root = Path(devbase_root)
    if _store is None or _store_root != root:
        _store = SecretStore(root)
        _store_root = root
    return _store


def release_store() -> None:
    """持ち回っている :class:`SecretStore` を捨てる。次の :func:`store_for` は作り直す。

    子プロセス (``env init``) がストアへ書いた後や、TUI の操作の入口で呼ぶ。控えを
    持ったまま続けると、現物と違う値で起動する。
    """
    global _store, _store_root
    _store = None
    _store_root = None


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
    ``env`` は ``APP_ROOT=$APP_HOME/app`` のように同一ファイル内の変数を参照
    できるため、起動ラッパー (または ``_load_project_env``) が展開した後の値が
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


def _account_group_delete_hint(ref: SecretRef) -> str:
    """置き場 ``ref`` の ``DEVBASE_ACCOUNT_GROUP`` を消す ``env delete`` の引数と実行場所 (PLAN62 決定 5)。

    ``env delete`` の宛先は ``-p`` (適用範囲) / ``--user`` (持ち主) / ``--group`` (グループ) の
    3 軸で決まり、参照の 3 つのフィールドと 1 対 1 に対応する。グループを持つ参照には共通の
    参照でも ``--group`` を付ける。省くと ``env delete`` は実行した場所のグループを宛先にし、
    警告を出した置き場と違う置き場を指すことがある。``-p`` はプロジェクトのディレクトリで
    しか使えないため、実行場所を添える。
    """
    args = ''
    if ref.kind == 'project':
        args += ' -p'
    if ref.is_user:
        args += ' --user'
    if ref.group:
        args += f' --group {ref.group}'
    where = f'（projects/{ref.name} で実行）' if ref.kind == 'project' else ''
    return f'{args}{where}'


def _without_account_group(ref: SecretRef, data: Dict[str, str]) -> Dict[str, str]:
    """置き場から読んだ ``data`` から ``DEVBASE_ACCOUNT_GROUP`` を除いた辞書を返す (PLAN62)。

    キーが無ければ ``data`` をそのまま返す。受け取った辞書は変えない。あれば、その置き場
    (``ref``) についてまだ警告していないときだけ、無視したことと消し方を警告で知らせる
    (値は出さない。決定 3・4)。
    """
    if keys.DEVBASE_ACCOUNT_GROUP not in data:
        return data
    if ref not in _warned_account_group_refs:
        _warned_account_group_refs.add(ref)
        logger.warning(
            "機密の置き場（%s）にある %s は使いません。アカウントグループは env ファイル"
            "（projects/<name>/env・$DEVBASE_ROOT/env）で決まります。"
            "消すには: devbase env delete %s%s",
            ref.label(), keys.DEVBASE_ACCOUNT_GROUP, keys.DEVBASE_ACCOUNT_GROUP,
            _account_group_delete_hint(ref))
    return {key: value for key, value in data.items() if key != keys.DEVBASE_ACCOUNT_GROUP}


def resolve(devbase_root: Path, project: Optional[str] = None,
            *, store: Optional[SecretStore] = None) -> SecretEnv:
    """機密を合成して返す。

    重ね順は従来の ``env_file`` の並びを踏襲し、持ち主の軸をその内側へ足す
    (PLAN51 設計 2「重ね順」/ 決定 12):

    1. チーム共通の機密
    2. 個人共通の機密 (チーム共通に勝つ)
    3. プロジェクトの非機密設定 (``projects/<name>/env``。共通の 2 層に勝つ)
    4. プロジェクトのチーム機密 (非機密設定に勝つ)
    5. プロジェクトの個人機密 (同じプロジェクトのチーム機密に勝つ)

    規則は 2 つ。**適用範囲が狭いものが勝つ** (プロジェクトが共通に勝つ) と、
    **同じ適用範囲では個人単位がチーム単位に勝つ**。前者は現行の重ね順そのままで、
    後者を内側へ足した形になる。

    コンテナへ列挙するのは機密のキーだけで、非機密設定は構成ファイルが ``env_file``
    として直接読むため列挙しない。ただし両方に同じキーがある場合は、列挙した変数の
    **値**として非機密設定側を採用する。``environment`` は ``env_file`` より優先される
    ため、こうしないと「プロジェクト設定が共通設定を上書きする」という従来の関係が
    反転する。

    個人単位の参照を持たない backend (``age`` / ``plaintext``) では 2 と 5 が空になり、
    結果は従来と同じになる (前提 3)。

    4 参照はプロジェクトのアカウントグループ (:meth:`SecretStore.ref_group`) を持つ
    (PLAN56)。グループ別の置き場 (``layout: group``) では、``project`` のグループの
    置き場だけを読み、他のグループのパスへは要求しない。それ以外の設定ではグループが
    ``None`` で、参照は今と同じ値になる (決定 5)。

    4 つの置き場にある ``DEVBASE_ACCOUNT_GROUP`` は合成しない (PLAN62 決定 1・2)。
    アカウントグループ (ボリューム ``devbase_home_<group>`` と ``layout: group`` の置き場)
    を決める値は ``env`` ファイルとシェルの環境変数からだけ来るべきで、置き場の値を
    載せるとプロセスの環境変数を読む :func:`~devbase.volume.manager.resolve_account_group`
    が置き場の値でグループを変えてしまう。``inject`` / :func:`child_env` / コンテナへ列挙する
    変数名はどれもこの結果から作られるため、読み取りの直後の 1 か所で外す。重ね順 3
    (``projects/<name>/env``) は変えない: ``names`` に無いキーは ``values`` に採らないため、
    ``env`` ファイルの値も ``values`` には現れず、プロセスの環境変数に元からある値が残る。
    """
    root = Path(devbase_root)
    store = store if store is not None else store_for(root)
    # 重ね順だけを見る差し替えの店 (テストなど) は ref_group を持たない。持たなければ
    # グループの無い参照 = 今と同じ参照で読む
    ref_group = getattr(store, 'ref_group', None)
    group = ref_group(project) if callable(ref_group) else None

    def load(ref: SecretRef) -> Dict[str, str]:
        return _without_account_group(ref, store.load(ref))

    team_global = load(SecretRef.for_global(group=group))
    user_global = load(SecretRef.for_global(owner='user', group=group))
    global_names = list(dict.fromkeys([*team_global, *user_global]))
    project_names: List[str] = []

    merged: Dict[str, str] = dict(team_global)
    merged.update(user_global)

    if project:
        merged.update(_project_env_overrides(root, project))
        team_project = load(SecretRef.for_project(project, group=group))
        user_project = load(SecretRef.for_project(project, owner='user', group=group))
        merged.update(team_project)
        merged.update(user_project)
        project_names = list(dict.fromkeys([*team_project, *user_project]))

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


def snapshot_injected(environ=None) -> Optional[Dict[str, Optional[str]]]:
    """対象マッピングの注入履歴の複製を返す。履歴が無ければ None。

    環境変数の**値**を控えて後で戻す呼び出し側 (TUI の
    ``tui.dispatch._preserve_cwd_env``) が、値と一緒に履歴も戻すために使う。値だけを
    戻すと、戻った機密を次の :func:`clear_injected` が知らずに残してしまう。
    """
    target = environ if environ is not None else os.environ
    entry = _injected_originals.get(id(target))
    return None if entry is None else dict(entry[1])


def restore_injected(snapshot: Optional[Dict[str, Optional[str]]], environ=None) -> None:
    """:func:`snapshot_injected` で控えた履歴を書き戻す。None なら履歴を消す。"""
    target = environ if environ is not None else os.environ
    if snapshot is None:
        _injected_originals.pop(id(target), None)
    else:
        _injected_originals[id(target)] = (target, dict(snapshot))


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
