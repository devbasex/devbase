"""env のキーの一覧と編集の画面 (#273 F1〜F4)

範囲 (共通 / プロジェクト) とグループを選び、キーの行を置き場 (持ち主・適用範囲・グループ) を
添えて並べる。行を選ぶと値の変更・削除、先頭の「キーを追加」で追加する。

- 行は :func:`devbase.commands.env_rows.collect_key_rows` から作る。値の平文は持たず、値の列は
  常に同じ伏せ字である (決定 4・10)
- 書き込みは ``cmd_env`` の ``set`` / ``delete`` へ、選んだ置き場を写した属性で委譲する (I1)。
  この画面は ``SecretStore`` へ書かない
- 値は伏せ字の欄 (``menu.secret``) で受け、同じプロセスの属性で渡す。ログへ出さない (I9)

Esc は 1 つ前へ戻り (キーの一覧の Esc は env メニューへ)、Ctrl-C は TUI 全体を終える。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from devbase.env import keys
from devbase.errors import DevbaseError
from devbase.log import get_logger
from devbase.tui import flow, menu

logger = get_logger(__name__)

SCOPE_GLOBAL = "global"
SCOPE_PROJECT = "project"
ADD = "add"
TYPE_GROUP = object()     # グループの選択の「名前を入力」
_RESELECT = object()      # グループを選び直す (--group に使えない名前)

MASK = "******"
KEY_WIDTH = 32

_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MSG_BAD_KEY = "キー名は英字か _ で始まり、英数字と _ だけにしてください"
MSG_ACCOUNT_GROUP = (f"{keys.DEVBASE_ACCOUNT_GROUP} は機密の置き場へは書けません"
                     "（projects/<name>/env か $DEVBASE_ROOT/env に書いてください）")
MSG_EMPTY_VALUE = "値を入力してください"
MSG_MULTILINE = "改行を含む値は TUI では扱えません。devbase env edit で編集してください"
MSG_SURROUNDING_SPACE = ("前後に空白を含む値は TUI では扱えません（空白は取り除かれて保存されます）。"
                         "devbase env edit で編集してください")


# ---------------------------------------------------------------------------
# 入力の検査 (TUI の側だけで行う。CLI の set の検査は変えない)
# ---------------------------------------------------------------------------

def key_error(key: str):
    """キー名が使えなければ理由、使えれば ``None``"""
    if key == keys.DEVBASE_ACCOUNT_GROUP:
        return MSG_ACCOUNT_GROUP
    if not _KEY_PATTERN.match(key):
        return MSG_BAD_KEY
    return None


def value_error(value: str):
    """値が使えなければ理由、使えれば ``None``"""
    if "\n" in value or "\r" in value:
        return MSG_MULTILINE
    if not value.strip():
        return MSG_EMPTY_VALUE
    if value != value.strip():
        return MSG_SURROUNDING_SPACE
    return None


def _ask_key() -> str:
    while True:
        key = flow.need(menu.text(f"キー名 {menu.HINT_BACK}:", allow_empty=True)).strip()
        why = key_error(key)
        if why is None:
            return key
        logger.error("%s", why)


def _ask_value(key: str) -> str:
    while True:
        value = flow.need(menu.secret(f"{key} の値 (伏せ字) {menu.HINT_BACK}:"))
        why = value_error(value)
        if why is None:
            return value
        logger.error("%s", why)


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------

def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def row_title(row, *, grouped: bool) -> str:
    """一覧の 1 行 (キー・持ち主・適用範囲・グループ・伏せ字の値)"""
    key = row.key if len(row.key) >= KEY_WIDTH else row.key.ljust(KEY_WIDTH)
    parts = [key, _pad(row.owner_label, 6), _pad(row.scope_label, 18)]
    if grouped:
        parts.append(_pad(row.group_label or "", 10))
    parts.append(MASK)
    return " ".join(parts)


def _place(ref) -> str:
    from devbase.commands import env_rows

    group = f"・グループ {ref.group}" if ref.group else ""
    return f"{env_rows.OWNER_LABELS[ref.owner]}・{env_rows.scope_label(ref)}{group}"


# ---------------------------------------------------------------------------
# 委譲 (設計「委譲の属性の写し」)
# ---------------------------------------------------------------------------

def delegate_attrs(ref) -> dict:
    """選んだ置き場を ``cmd_env`` の ``set`` / ``delete`` の属性へ写す。

    共通は ``project=False``・``group`` は参照のグループ (``version: 2`` でなければ ``None``)。
    プロジェクトは ``project=True``・``group=None`` で、実行時のディレクトリを ``projects/<name>``
    にして呼ぶ (``-p`` はそのプロジェクトのグループの置き場だけを読み書きするため)。
    """
    if ref.kind == "global":
        return {"project": False, "user": ref.is_user, "group": ref.group}
    return {"project": True, "user": ref.is_user, "group": None}


def _dispatch(devbase_root: Path, subcommand: str, ref, **attrs):
    from devbase.tui import actions_env

    attrs.update(delegate_attrs(ref))
    call = lambda: actions_env._dispatch(devbase_root, subcommand, **attrs)  # noqa: E731
    if ref.kind == "global":
        return call()
    rc = actions_env._run_in_project(devbase_root, ref.name, call)
    return 1 if rc is flow.ARG_CANCEL else rc


def _set(devbase_root: Path, ref, key: str, value: str) -> int:
    return _dispatch(devbase_root, "set", ref, assignment=f"{key}={value}")


def _delete(devbase_root: Path, ref, key: str) -> int:
    return _dispatch(devbase_root, "delete", ref, key=key)


# ---------------------------------------------------------------------------
# 範囲・グループの選択
# ---------------------------------------------------------------------------

def _has_projects(devbase_root: Path) -> bool:
    # list_projects は稼働状況を docker に尋ねるため、有無だけを見るここでは使わない
    from devbase.utils import names

    return bool(names.project_dirs(Path(devbase_root) / "projects"))


def _select_scope(devbase_root: Path):
    choices = [("共通", SCOPE_GLOBAL)]
    if _has_projects(devbase_root):
        choices.append(("プロジェクト", SCOPE_PROJECT))
    return menu.select(f"キーの範囲を選択 {menu.HINT_BACK}:", choices, back=True, search=False)


def _grouped(devbase_root: Path) -> bool:
    from devbase.commands import env_rows
    from devbase.env.secret_store import SecretStore

    return env_rows.is_grouped(SecretStore(devbase_root))


def _select_group(devbase_root: Path) -> str:
    from devbase.commands.env_rows import group_choices
    from devbase.env.secret_store import SecretStore

    settings = SecretStore(devbase_root).config.openbao
    choices = [(settings.display_group(name), name) for name in group_choices(devbase_root)]
    choices.append(("名前を入力", TYPE_GROUP))
    picked = flow.need(menu.select(f"グループを選択 {menu.HINT_BACK}:", choices,
                                   back=True, search=False))
    if picked is TYPE_GROUP:
        return flow.need(menu.text(f"グループ名 {menu.HINT_BACK}:", allow_empty=False)).strip()
    return picked


def project_title(name: str, count, width: int) -> str:
    """対象プロジェクトの選択の 1 行 (名前・キーの数。読めなければ ``?``)"""
    return f"{name.ljust(width)}  キー {'?' if count is None else count} 件"


def _select_project(devbase_root: Path) -> str:
    """対象プロジェクトを選ぶ。各行にキーの数を添え、稼働状況は出さない。

    ← と Esc で範囲の選択へ戻る (``flow.BackOut``)。プロジェクトは ``_select_scope`` が
    1 つ以上あるときだけ選ばせる。
    """
    from devbase.commands.env_rows import count_project_keys

    counts = count_project_keys(devbase_root)
    width = max(len(name) for name, _ in counts)
    choices = [(project_title(name, count, width), name) for name, count in counts]
    return flow.need(menu.select(f"対象プロジェクトを選択 {menu.HINT_SEARCH_LEFT}:", choices,
                                 back=True, search=True, left_back=True))


# ---------------------------------------------------------------------------
# 一覧と行の操作
# ---------------------------------------------------------------------------

def _describe_scope(project, group) -> str:
    scope = "共通" if project is None else f"プロジェクト {project}"
    return f"{scope}・グループ {group}" if group else scope


def _load(devbase_root: Path, project, group):
    """一覧を読む。``KeyListing`` / ``_RESELECT`` (使えないグループ) / ``int`` (読めない)"""
    from devbase.commands import env_rows
    from devbase.commands.env import GroupOptionError

    try:
        return env_rows.collect_key_rows(devbase_root, project=project, group=group)
    except GroupOptionError as e:
        logger.error("%s", e)
        return _RESELECT
    except DevbaseError as e:
        first = (str(e).splitlines() or [""])[0]
        logger.error("キーの一覧を読めません（%s）: %s", _describe_scope(project, group), first)
        return 1


def _heading(listing) -> str:
    return (f"キーの一覧 {len(listing.rows)} 件（参照 {len(listing.refs)} 件・"
            f"backend {listing.backend}）{menu.HINT_BACK}:")


def _after_write(rc: int) -> None:
    """保存・削除の後: 失敗なら読み直しを促し、出力を読ませてから一覧を読み直す"""
    if rc != 0:
        logger.warning("保存できませんでした。一覧を読み直します（版の食い違いなら、"
                       "読み直した内容でもう一度操作してください）")
    if not flow.pause_for_review():
        raise flow.CancelAll
    menu.clear_screen()


def _choose_owner(listing) -> str:
    from devbase.commands.env_rows import OWNER_LABELS

    if not listing.has_user_refs:
        return "team"
    return flow.need(menu.select(f"持ち主を選択 {menu.HINT_BACK}:",
                                 [(OWNER_LABELS[o], o) for o in ("team", "user")],
                                 back=True, search=False))


def _ref_for_add(listing, owner: str):
    # 一覧の参照は選んだ範囲 (共通かそのプロジェクト) だけなので、持ち主で 1 つに決まる
    for ref in listing.refs:
        if ref.owner == owner:
            return ref
    raise flow.BackOut      # 到達しない (選べる持ち主は listing.refs の中だけ)


def _add(devbase_root: Path, listing) -> int:
    key = _ask_key()
    owner = _choose_owner(listing)
    ref = _ref_for_add(listing, owner)
    value = _ask_value(key)
    return _set(devbase_root, ref, key, value)


def _operate(devbase_root: Path, row) -> int:
    op = flow.need(menu.select(f"{row.key}（{_place(row.ref)}）の操作を選択 {menu.HINT_BACK}:",
                               [("値を変更", "change"), ("削除", "delete")],
                               back=True, search=False))
    if op == "change":
        return _set(devbase_root, row.ref, row.key, _ask_value(row.key))
    flow.confirm_or_back(f"{row.key} を {_place(row.ref)} から削除しますか?", default=False)
    return _delete(devbase_root, row.ref, row.key)


def _list_loop(devbase_root: Path, project, group):
    """一覧を出して操作を受ける。戻り値: ``int`` / ``ARG_CANCEL`` / ``_RESELECT``"""
    while True:
        listing = _load(devbase_root, project, group)
        if listing is _RESELECT or isinstance(listing, int):
            return listing
        choices = [("＋ キーを追加", ADD)]
        choices += [(row_title(row, grouped=listing.grouped), i)
                    for i, row in enumerate(listing.rows)]
        picked = menu.select(_heading(listing), choices, back=True, search=False)
        if picked is None:
            raise flow.CancelAll
        if picked is menu.MENU_BACK:
            return flow.ARG_CANCEL       # env メニューへ
        try:
            rc = (_add(devbase_root, listing) if picked == ADD
                  else _operate(devbase_root, listing.rows[picked]))
        except flow.BackOut:
            continue                     # Esc・「いいえ」: 一覧へ戻る (書かない)
        _after_write(rc)


def _choose_target(devbase_root: Path, scope, grouped: bool):
    """範囲に応じてプロジェクトとグループを選ぶ。Esc は ``flow.BackOut`` のまま送る"""
    project = _select_project(devbase_root) if scope == SCOPE_PROJECT else None
    group = _select_group(devbase_root) if scope == SCOPE_GLOBAL and grouped else None
    return project, group


def _list_until_done(devbase_root: Path, project, group):
    """一覧を出し、使えないグループならグループを選び直す。

    戻り値は :func:`_list_loop` と同じ。グループの選び直しの Esc は ``_RESELECT``
    (範囲の選択へ戻る)。
    """
    while True:
        rc = _list_loop(devbase_root, project, group)
        if rc is not _RESELECT:
            return rc
        try:
            group = _select_group(devbase_root)
        except flow.BackOut:
            return _RESELECT


@flow.collect_args
def run(devbase_root: Path):
    """env メニューの「キーの一覧と編集」。

    戻り値: ``int`` (読めなかったときの 1。一時停止して env メニューへ) / ``ARG_CANCEL``
    (Esc で env メニューへ) / ``None`` (Ctrl-C で全体中止)。
    """
    try:
        grouped = _grouped(devbase_root)
    except DevbaseError as e:
        logger.error("%s", e)
        return 1
    while True:
        scope = flow.need(_select_scope(devbase_root))
        try:
            project, group = _choose_target(devbase_root, scope, grouped)
        except flow.BackOut:
            continue                     # 範囲の選択へ戻る
        rc = _list_until_done(devbase_root, project, group)
        if rc is not _RESELECT:
            return rc                    # _RESELECT なら範囲の選択へ戻る
