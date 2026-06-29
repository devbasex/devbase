"""project カテゴリの TUI 操作フロー (PLAN31_2 PR1: 既存挙動の非回帰移送)。

旧 ``commands/project.py`` の ``_tui_select_and_up`` / ``_show_menu`` /
``_show_action_menu`` / ``_fallback_select_and_up`` をこのモジュールへ移送し、
メニュー部品は ``tui.menu`` に、ハンドラ委譲は ``tui.dispatch`` に一般化した。

PR1 で **一覧選択 → (running なら操作サブメニュー) → それ以外は直接 up** を移送し、
PR2 で running 操作サブメニューを **up/down/login/ps/logs/scale/build/rebuild の全操作**
へ拡張した。login/ps/logs/scale は running 中コンテナを対象とするため running 行限定、
stopped/unknown は従来どおり直接 up (PR1 非回帰)。引数を要する操作は ``tui.menu`` の
収集ヘルパで CLI と同じ属性値を集める (plan 2.3 契約表)。down はデータを失わない
(volume 保持) ためメニュー選択を意思表示とみなし、確認プロンプトは出さない。

プロジェクト一覧の表示・選択は ``tui.app`` (トップ画面) が担い、本モジュールは
選択された 1 行の処理 (``handle_row``) と questionary 不在時のフォールバックを提供する。
中止系の伝搬 (Ctrl-C / Esc) は ``tui.flow`` のナビ規約に従う。
"""

from __future__ import annotations

from pathlib import Path

from devbase.log import get_logger
from devbase.tui import flow, menu
from devbase.tui.dispatch import dispatch_lifecycle

logger = get_logger(__name__)


# running 行で選べる操作 (表示順 = ハイライト既定順)。up を先頭に置き、PR1 同様
# Enter 連打で再起動へ到達できるようにする。各 value は cmd_project のサブコマンド名。
_RUNNING_OPS: list[tuple[str, str]] = [
    ("再起動 (up)", "up"),
    ("停止 (down)", "down"),
    ("ログイン (login)", "login"),
    ("コンテナ状態 (ps)", "ps"),
    ("ログ表示 (logs)", "logs"),
    ("スケール変更 (scale)", "scale"),
    ("イメージビルド (build)", "build"),
    ("再ビルド (rebuild --no-cache)", "rebuild"),
]

# 中止系番兵は flow と同一オブジェクトを再公開する (呼び出し側・テストの契約)。
_ARG_CANCEL = flow.ARG_CANCEL
_ABORT = flow.ABORT


def _select_action(name: str):
    """running 中プロジェクトの操作を選ぶサブメニュー。

    戻り値: サブコマンド文字列 / ``MENU_BACK`` (Esc・← → 一覧へ戻る) / ``None`` (Ctrl-C 中止)。
    """
    return menu.select(f"'{name}' は起動中です。操作を選択 {menu.HINT_BACK}:",
                       list(_RUNNING_OPS), back=True, search=False)


def _optional_int(message: str, *, min_value: int = 0):
    """空入力を許す整数収集 (logs --tail 等)。``flow.optional_int`` の再公開。

    ``min_value`` の既定は 0 で、logs --tail に負数を渡して docker compose を
    エラーにするのを防ぐ。戻り値の番兵契約は ``flow.optional_int`` 参照。
    """
    return flow.optional_int(message, min_value=min_value)


def _select_build_image(devbase_root: Path):
    """build 対象イメージを選ぶ。``containers/<image>/Dockerfile`` を列挙する。

    戻り値: イメージ名 (``str``) / ``""`` (compose.yml 全体ビルド。呼び出し側で
    ``None`` へ変換) / ``None`` (Ctrl-C → 全体中止を呼び出し元へ伝搬) /
    ``_ARG_CANCEL`` (Esc・← → サブメニューへ戻る)。``containers/`` が無い / 空なら
    compose.yml 全体ビルド (``""``) にフォールバックする。
    """
    containers_dir = Path(devbase_root) / "containers"
    images = sorted(
        d.name for d in containers_dir.iterdir()
        if d.is_dir() and (d / "Dockerfile").exists()
    ) if containers_dir.is_dir() else []

    if not images:
        # 個別イメージが無ければ compose.yml 全体ビルド ("" = image なし) のみ。
        return ""

    # value="" を「compose.yml 全体」に割り当て、選択メニューの None (Ctrl-C =
    # 全体中止) と衝突させない。呼び出し側で空文字 → None へ変換する。
    choices = [("compose.yml 全体をビルド", "")] + [(img, img) for img in images]
    return flow.back_as_cancel(menu.select(
        f"ビルドするイメージを選択 {menu.HINT_BACK}:",
        choices, back=True, search=False))


# ---------------------------------------------------------------------------
# 各操作の引数収集 + dispatch (引数を要する操作のみ。up/rebuild は即実行)
# ---------------------------------------------------------------------------

def _op_login(devbase_root: Path, name: str):
    # menu.text は空入力 (既定値を消して確定) で "" を返し、wrapper で --index=
    # と展開されてコマンドが失敗する。menu.integer なら空入力は default=1 を返し、
    # min_value=1 で正の整数を保証する。cmd_login の index は文字列契約なので str 化。
    index = flow.need(menu.integer("ログインするコンテナ番号", default=1, min_value=1))
    return dispatch_lifecycle("login", name, index=str(index))


def _op_logs(devbase_root: Path, name: str):
    # --follow は CLI 既定 (False) で実行する (非破壊操作の確認プロンプト廃止)。
    # 追従が必要な場合は CLI (`project logs --follow`) を使う想定。
    tail = flow.need_optional(_optional_int("末尾何行を表示しますか (空で全件)"))
    return dispatch_lifecycle("logs", name, follow=False, tail=tail)


def _op_scale(devbase_root: Path, name: str):
    new_scale = flow.need(menu.integer(f"'{name}' の新しいコンテナ数", min_value=1))
    return dispatch_lifecycle("scale", name, new_scale=new_scale)


def _op_build(devbase_root: Path, name: str):
    image = flow.need(_select_build_image(devbase_root))
    return dispatch_lifecycle("build", name, image=image or None)


_OP_HANDLERS = {
    # up/down/ps は引数なしで即実行。up は scale 属性を参照する (常に None。他
    # コマンドは無視する)。down はデータを失わない (volume 保持・up で復旧可能)
    # ためメニュー選択を意思表示とみなし、確認プロンプトを出さない。
    # ps の --all は CLI 既定 (False) に揃える。
    "up": lambda root, name: dispatch_lifecycle("up", name, scale=None),
    # 「再ビルド (rebuild --no-cache)」はラベル通り base/project とも無条件 no-cache
    # で再ビルドする。CLI の `rebuild` は期限判定 (= build --expires=7) でキャッシュ
    # を使い、新しいイメージはスキップするため --no-cache を満たさない。no-cache の
    # 正規経路は `build --no-cache` なので build に no_cache=True を渡して委譲する。
    "rebuild": lambda root, name: dispatch_lifecycle("build", name, no_cache=True),
    "down": lambda root, name: dispatch_lifecycle("down", name),
    "login": _op_login,
    "ps": lambda root, name: dispatch_lifecycle("ps", name, all=False),
    "logs": _op_logs,
    "scale": _op_scale,
    "build": _op_build,
}


@flow.collect_args
def _run_operation(devbase_root: Path, name: str, op: str):
    """選択された操作の引数を収集して ``dispatch_lifecycle`` で起動する。

    戻り値: dispatch の rc (``int``) / ``_ARG_CANCEL`` (Esc・確認拒否で引数収集を
    中止 = サブメニューへ戻る) / ``None`` (選択・入力中の Ctrl-C → 全体中止)。
    """
    handler = _OP_HANDLERS.get(op)
    if handler is None:
        # 到達しない (メニュー値は _RUNNING_OPS に限定される)。保守的に no-op。
        logger.error("未知の操作です: %s", op)
        raise flow.BackOut
    return handler(devbase_root, name)


def _operation_menu(devbase_root: Path, name: str):
    """running 行の操作サブメニューを回す。

    戻り値 (``flow.menu_loop`` のプロトコル): ``menu.MENU_BACK`` (Esc・← で一覧へ
    戻る) / ``None`` (Ctrl-C 全体中止)。操作を実行した後はトップ一覧へ戻らず、
    出力を読めるよう一時停止してから**同じサブメニューを再表示する** (留まる)。
    引数収集を中止 (``_ARG_CANCEL``) した場合も同じサブメニューを再表示する。
    """
    return flow.menu_loop(
        lambda: _select_action(name),
        lambda op: _run_operation(devbase_root, name, op))


def handle_row(devbase_root: Path, row: dict):
    """一覧で選択された 1 プロジェクト行を処理する (トップ画面から呼ばれる)。

    戻り値プロトコル (トップループが ``is`` 同一性で判定する):
    - ``menu.MENU_BACK``: 操作サブメニューで Esc/← (一覧へ戻る)。running 行は操作を
      実行しても (出力確認の一時停止後) サブメニューに留まり、Esc/← で初めて一覧へ
      戻るため、この値を返す。
    - **直接 up を実行した場合** (stopped / unknown 行): ``dispatch_lifecycle`` の
      rc (``int``) を返す。トップループが rc を記憶し ``devbase list`` の終了コードへ
      伝搬する。
    - ``None``: サブメニューで Ctrl-C による全体中止。

    選択行が running 中なら ``_operation_menu`` で全操作を選ばせ (実行後もサブ
    メニューに留まる)、それ以外 (stopped / unknown) は従来どおり直接 ``project up``
    を起動して一覧へ戻る (PR1 非回帰)。
    """
    name = row["name"]
    if str(row.get("status", "")).startswith("running"):
        return _operation_menu(devbase_root, name)
    return dispatch_lifecycle("up", name, scale=None)


def fallback_select_and_up(rows: list[dict]) -> int:
    """番号入力で 1 件選択し ``project up <name>`` を起動する (questionary 未導入時)。

    旧 ``project.py:_fallback_select_and_up`` の非回帰移送。questionary 不在環境では
    トップ階層メニューを出さず、この従来フロー (番号入力 → up) に縮退して muscle-memory
    を保全する。外部依存を増やさず stdlib ``input()`` で実装する。空入力は中止、非 TTY
    (EOFError) はエラー終了 (rc=1)、Ctrl-C は中止 (rc=0)。
    """
    print("起動するプロジェクトを選択してください:")
    for i, r in enumerate(rows, 1):
        print(f"  [{i}] {r['name']}  ({r['plugin']}, {r['status']})")

    # 一覧取得が重い場合があるため、誤入力 (数値以外 / 範囲外) では即終了せず再入力を促す。
    while True:
        try:
            raw = input("番号 (空で中止): ").strip()
        except EOFError:
            logger.error("対話入力ができません (非 TTY 環境)。"
                         "`devbase project up <name>` で直接指定してください。")
            return 1
        except KeyboardInterrupt:
            print()
            logger.info("中止しました。")
            return 0

        if not raw:
            logger.info("中止しました。")
            return 0

        try:
            idx = int(raw)
        except ValueError:
            logger.error("番号で指定してください: %r", raw)
            continue

        if not (1 <= idx <= len(rows)):
            logger.error("範囲外の番号です: %d (1〜%d)", idx, len(rows))
            continue

        break

    return dispatch_lifecycle("up", rows[idx - 1]["name"], scale=None)
