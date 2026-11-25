"""停止中のプロジェクトの「プロジェクト設定変更」メニュー。

``project.yml`` (``scale`` 以外)・``project.local.yml`` の ``docker`` 節・アカウントグループの
宣言 (``projects/<name>/env``) を、現在の値を添えて並べ、選んだ 1 項目を書き換える。
``scale`` は 1 つ上のメニューの「スケール変更」が持つ。

- 書き込みは :mod:`devbase.project.settings` と ``groups.write_declaration`` が行い、検証に
  通った内容だけをファイルへ書く。この画面はファイルを直接開かない
- 空で確定した項目はキーを消し、既定値に戻す
- コンテナには触らない。変更は次の起動 (up) から効く

Esc / ← は 1 つ前へ戻り、Ctrl-C は TUI 全体を終える。書き換えた後は一時停止せず、同じ
メニューを新しい値で出し直す。
"""

from __future__ import annotations

from pathlib import Path

from devbase.errors import ConfigError, DevbaseError
from devbase.log import get_logger
from devbase.project import settings
from devbase.project.config import load_project_config
from devbase.project.local_config import load_project_local_config
from devbase.tui import flow, menu
from devbase.utils.text_width import column_width, pad

logger = get_logger(__name__)

UNSET_LABEL = "(未設定)"
ADD_REPO = "add-repo"
TYPE_GROUP = object()     # グループの選択の「名前を入力」
_CLOSE = object()         # 実行後に今のメニューを閉じて 1 つ前へ戻る

# 真偽値の選択肢の値。``None`` は Ctrl-C と衝突するため文字列で持つ。
_BOOLS = {"true": True, "false": False, "unset": None}


# ---------------------------------------------------------------------------
# 共通部品
# ---------------------------------------------------------------------------

def _loop(select, run):
    """メニューを回す。戻り値: ``ARG_CANCEL`` (1 つ前へ) / ``None`` (Ctrl-C)。

    ``select(cursor)`` には直前に選んだ値を渡し、同じ項目へカーソルを戻せるようにする。
    設定ファイルを読めないとき・書けないときは理由を出す。読めなければ 1 つ前へ戻る。
    """
    cursor = None
    while True:
        try:
            op = select(cursor)
        except DevbaseError as e:
            logger.error("%s", e)
            return flow.ARG_CANCEL
        if op is menu.MENU_BACK:
            return flow.ARG_CANCEL
        if op is None:
            return None
        cursor = op
        result = _run_op(run, op)
        if result is None:
            return None
        if result is _CLOSE:
            return flow.ARG_CANCEL


@flow.collect_args
def _run_op(run, op):
    try:
        return run(op)
    except DevbaseError as e:
        logger.error("変更しませんでした: %s", e)
        return flow.ARG_CANCEL


def _menu(message: str, rows, cursor, extra=()):
    """``(ラベル, 現在の値, 値)`` の行を桁を揃えて選ばせる。``extra`` は値の列を持たない行。"""
    width = column_width(label for label, _current, _value in rows)
    choices = [(f"{pad(label, width)}  {current}", value)
               for label, current, value in rows] + list(extra)
    default = cursor if any(value == cursor for _title, value in choices) else None
    return menu.select(f"{message} {menu.HINT_BACK}:", choices, back=True, search=False,
                       default=default)


def _saved(changed: bool) -> object:
    logger.info("保存しました (次の起動から反映されます)。" if changed else "変更はありません。")
    return flow.ARG_CANCEL


def _ask_optional(message: str, current):
    """空で確定すると ``None`` (= キーを消す) を返す文字列の入力"""
    answer = flow.need(menu.text(f"{message} (空で未設定):", default=current or "",
                                 allow_empty=True))
    return answer or None


def _ask_bool(message: str, current, labels: dict):
    initial = next(key for key, value in _BOOLS.items() if value is current)
    return _BOOLS[flow.need(menu.select(
        f"{message} {menu.HINT_BACK}:", [(labels[key], key) for key in _BOOLS],
        back=True, search=False, default=initial))]


def _show(value) -> str:
    if value is None:
        return UNSET_LABEL
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ---------------------------------------------------------------------------
# 最上位
# ---------------------------------------------------------------------------

def _project_dir(devbase_root: Path, name: str) -> Path:
    return Path(devbase_root) / "projects" / name


def _group_label(devbase_root: Path, name: str) -> str:
    from devbase.env import groups

    try:
        return groups.declare(devbase_root, name).name
    except DevbaseError:
        return UNSET_LABEL


def _rows(devbase_root: Path, name: str):
    project_dir = _project_dir(devbase_root, name)
    config = load_project_config(project_dir)
    try:
        docker = load_project_local_config(project_dir).docker
        local = {key: _show(getattr(docker, key)) for key in ("context", "home", "gid")}
    except ConfigError:
        # 読めない理由は、選んで書き換えようとしたときに出る
        local = dict.fromkeys(("context", "home", "gid"), "(project.local.yml を読めません)")
    defaults = settings.read_project_document(project_dir).data.get("defaults") or {}
    repos = ", ".join(r.dir + (" (primary)" if r.primary and len(config.repos) > 1 else "")
                      for r in config.repos)
    return [
        ("リポジトリ (repos)", repos, "repos"),
        ("リポジトリの既定値 (defaults)",
         ", ".join(f"{k}={_show(v)}" for k, v in defaults.items()) or UNSET_LABEL,
         "defaults"),
        ("起動後にエディタを開く (open_editor)", _show(config.open_editor), "open_editor"),
        ("エディタが開くフォルダ (work_dir)", _show(config.work_dir), "work_dir"),
        ("アカウントグループ (DEVBASE_ACCOUNT_GROUP)",
         _group_label(devbase_root, name), "group"),
        ("この端末の接続先 (docker.context)", local["context"], "context"),
        ("この端末の接続先の HOME (docker.home)", local["home"], "home"),
        ("この端末の接続先の docker gid (docker.gid)", local["gid"], "gid"),
    ]


def run(devbase_root: Path, name: str):
    """設定変更メニューを回す。戻り値: ``ARG_CANCEL`` (1 つ前へ) / ``None`` (Ctrl-C)"""
    return _loop(
        lambda cursor: _menu(f"'{name}' の設定を選択", _rows(devbase_root, name), cursor),
        lambda op: _OPS[op](devbase_root, name))


def _op_open_editor(devbase_root: Path, name: str):
    project_dir = _project_dir(devbase_root, name)
    value = _ask_bool("起動後にエディタを開きますか",
                      load_project_config(project_dir).open_editor,
                      {"true": "開く", "false": "開かない",
                       "unset": "未設定 (DEVBASE_OPEN_EDITOR に従う)"})
    return _saved(settings.set_project_value(project_dir, "open_editor", value))


def _op_work_dir(devbase_root: Path, name: str):
    project_dir = _project_dir(devbase_root, name)
    config = load_project_config(project_dir)
    if len(config.repos) > 1:
        logger.info("work_dir が効くのはリポジトリが 1 件のときだけです。")
    value = _ask_optional("エディタが開くフォルダ", config.work_dir)
    return _saved(settings.set_project_value(project_dir, "work_dir", value))


def _op_group(devbase_root: Path, name: str):
    from devbase.env import groups

    current = _group_label(devbase_root, name)
    choices = [(group, group) for group in groups.declared_groups(devbase_root)]
    choices.append(("名前を入力", TYPE_GROUP))
    picked = flow.need(menu.select(
        f"アカウントグループを選択 (今は {current}) {menu.HINT_BACK}:", choices,
        back=True, search=False,
        default=current if any(value == current for _t, value in choices) else None))
    if picked is TYPE_GROUP:
        picked = flow.need(menu.text("グループ名:", allow_empty=False))
    if picked == current:
        return _saved(False)
    flow.confirm_or_back(
        f"'{name}' のグループを {current} から {picked} に変えますか? "
        "(ホームのボリューム・ログイン・グループ別の機密が切り替わります)")
    groups.write_declaration(devbase_root, name, picked)
    return _saved(True)


def _op_docker_text(key: str, message: str):
    def run_op(devbase_root: Path, name: str):
        project_dir = _project_dir(devbase_root, name)
        current = getattr(load_project_local_config(project_dir).docker, key)
        value = _ask_optional(message, current)
        return _saved(settings.set_docker_value(project_dir, key, value))
    return run_op


def _op_gid(devbase_root: Path, name: str):
    project_dir = _project_dir(devbase_root, name)
    current = load_project_local_config(project_dir).docker.gid
    raw = _ask_optional("接続先の docker グループの gid", None if current is None else str(current))
    if raw is not None and not (raw.isascii() and raw.isdigit()):
        raise ConfigError(f"docker.gid は 0 以上の整数です ({raw!r})")
    return _saved(settings.set_docker_value(
        project_dir, "gid", None if raw is None else int(raw)))


# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------

_INIT_LABELS = {"true": "実行する", "false": "実行しない", "unset": "未設定"}


def _op_defaults(devbase_root: Path, name: str):
    project_dir = _project_dir(devbase_root, name)

    def raw():
        return settings.read_project_document(project_dir).data.get("defaults") or {}

    def select(cursor):
        values = raw()
        return _menu(f"'{name}' のリポジトリの既定値",
                     [(label, _show(values.get(key)), key)
                      for key, label in _DEFAULT_FIELDS], cursor)

    def edit(key):
        value = _ask_field(key, raw().get(key))
        return _saved(settings.set_default_value(project_dir, key, value))

    return _loop(select, edit)


# ---------------------------------------------------------------------------
# repos
# ---------------------------------------------------------------------------

_FIELD_LABELS = {
    "host": "Git ホスト (host)",
    "owner": "所有者 (owner)",
    "repo": "リポジトリ名 (repo)",
    "dir": "/work の下の名前 (dir)",
    "branch": "clone 直後のブランチ (branch)",
    "init": "起動のたびに ./init.sh を実行 (init)",
}
_DEFAULT_FIELDS = [(key, _FIELD_LABELS[key]) for key in ("host", "owner", "branch", "init")]


def _ask_field(key: str, current):
    if key == "init":
        return _ask_bool(_FIELD_LABELS[key],
                         current if isinstance(current, bool) else None, _INIT_LABELS)
    if key == "repo":
        return flow.need(menu.text(f"{_FIELD_LABELS[key]}:", default=current,
                                   allow_empty=False))
    return _ask_optional(_FIELD_LABELS[key], current)


def _op_repos(devbase_root: Path, name: str):
    project_dir = _project_dir(devbase_root, name)

    def select(cursor):
        config = load_project_config(project_dir)
        rows = [(f"{r.owner}/{r.repo}",
                 f"/work/{r.dir}{'  primary' if r.primary else ''}", i)
                for i, r in enumerate(config.repos)]
        return _menu(f"'{name}' のリポジトリを選択", rows, cursor,
                     extra=[("リポジトリを追加", ADD_REPO)])

    def run_op(op):
        if op == ADD_REPO:
            return _add_repo(project_dir)
        return _repo_menu(project_dir, op)

    return _loop(select, run_op)


def _add_repo(project_dir: Path):
    defaults = settings.read_project_document(project_dir).data.get("defaults") or {}
    repo = flow.need(menu.text(f"{_FIELD_LABELS['repo']}:", allow_empty=False))
    if defaults.get("owner"):
        owner = flow.need(menu.text(
            f"{_FIELD_LABELS['owner']} (空で既定値 {defaults['owner']}):",
            allow_empty=True)) or None
    else:
        owner = flow.need(menu.text(f"{_FIELD_LABELS['owner']}:", allow_empty=False))
    return _saved(settings.add_repo(project_dir, repo, owner))


def _repo_menu(project_dir: Path, index: int):
    def select(cursor):
        config = load_project_config(project_dir)
        spec = config.repos[index]
        raw = settings.read_project_document(project_dir).data["repos"][index]
        rows = []
        for key, label in _FIELD_LABELS.items():
            value = getattr(spec, key)
            inherited = key not in raw and value is not None
            rows.append((label, _show(value) + ("  (既定値)" if inherited else ""), key))
        extra = []
        if not spec.primary:
            extra.append(("primary にする (ログイン直後の作業ディレクトリ)", "primary"))
        if len(config.repos) > 1:
            extra.append(("この一覧から外す", "remove"))
        return _menu(f"{spec.owner}/{spec.repo} の項目を選択", rows, cursor, extra=extra)

    def edit(op):
        if op == "primary":
            return _saved(settings.set_primary(project_dir, index))
        if op == "remove":
            spec = load_project_config(project_dir).repos[index]
            flow.confirm_or_back(
                f"{spec.owner}/{spec.repo} を project.yml から外しますか? "
                f"(clone 済みの /work/{spec.dir} は消えません)")
            _saved(settings.remove_repo(project_dir, index))
            return _CLOSE
        raw = settings.read_project_document(project_dir).data["repos"][index]
        return _saved(settings.set_repo_value(
            project_dir, index, op, _ask_field(op, raw.get(op))))

    return _loop(select, edit)


_OPS = {
    "repos": _op_repos,
    "defaults": _op_defaults,
    "open_editor": _op_open_editor,
    "work_dir": _op_work_dir,
    "group": _op_group,
    "context": _op_docker_text("context", "docker context の名前"),
    "home": _op_docker_text("home", "接続先の HOME (絶対パス)"),
    "gid": _op_gid,
}
