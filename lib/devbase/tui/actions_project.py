"""project カテゴリの TUI 操作フロー (PLAN31_2 PR1: 既存挙動の非回帰移送)。

旧 ``commands/project.py`` の ``_tui_select_and_up`` / ``_show_menu`` /
``_show_action_menu`` / ``_fallback_select_and_up`` をこのモジュールへ移送し、
メニュー部品は ``tui.menu`` に、ハンドラ委譲は ``tui.dispatch`` に一般化した。

PR1 で **一覧選択 → (running なら操作サブメニュー) → それ以外は直接 up** を移送し、
PR2 で running 操作サブメニューを **up/down/login/ps/logs/scale/build/rebuild の全操作**
へ拡張した。login/ps/logs/scale は running 中コンテナを対象とするため running 行限定、
stopped/unknown は従来どおり直接 up (PR1 非回帰)。引数を要する操作は ``tui.menu`` の
収集ヘルパで CLI と同じ属性値を集め、破壊的な down は ``menu.confirm`` で確認する
(plan 2.3 契約表 / 3.4 破壊的操作確認)。

一覧表示・整形 (``list_projects`` / ``_build_menu_entries``) は ``commands/project``
の純粋ロジックを再利用する (TUI からも CLI(table) からも共有)。
"""

from __future__ import annotations

from pathlib import Path

from devbase.commands.project import (
    _STATUS_COLOR,
    _build_menu_entries,
    list_projects,
)
from devbase.log import get_logger
from devbase.tui import menu
from devbase.tui.dispatch import dispatch_lifecycle

logger = get_logger(__name__)


def _select_project(rows: list[dict]):
    """一覧から 1 件選ばせ rows の index を返す。Esc → ``MENU_BACK`` / Ctrl-C → ``None``。

    件数が多いため文字入力での絞り込み (search=True) を有効にする。search 有効時は
    ← が入力カーソル移動と衝突するため戻る操作は Esc のみ (menu.select が調整する)。
    """
    entries = _build_menu_entries(rows, colorize=_STATUS_COLOR)
    choices = [(entry, i) for i, entry in enumerate(entries)]
    return menu.select(
        "操作するプロジェクトを選択 "
        "(↑↓ 移動 / 名前で絞り込み / Enter 決定 / Esc 戻る / Ctrl-C 中止):",
        choices, back=True, search=True)


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

# 引数収集を Esc/Ctrl-C で中止したことを示す番兵 (= サブメニューへ戻る)。
# dispatch の rc (int) や ``None`` (= 全体中止) と区別する。
_ARG_CANCEL = object()


def _select_action(name: str):
    """running 中プロジェクトの操作を選ぶサブメニュー。

    戻り値: サブコマンド文字列 / ``MENU_BACK`` (Esc・← → 一覧へ戻る) / ``None`` (Ctrl-C 中止)。
    """
    return menu.select(
        f"'{name}' は起動中です。操作を選択 "
        "(↑↓ 移動 / Enter 決定 / ←・Esc 戻る / Ctrl-C 中止):",
        list(_RUNNING_OPS), back=True, search=False)


def _optional_int(message: str, *, min_value: int = 0):
    """空入力を許す整数収集 (logs --tail 等)。

    戻り値: ``int`` / ``None`` (空入力 = 既定動作) / ``_ARG_CANCEL`` (Esc・Ctrl-C 中止)。
    非数値・``min_value`` 未満は再入力を促す。``menu.integer`` は空入力で既定値を返す
    仕様のため、空 = None を表現したい optional な数値はこちらで扱う。``min_value`` の
    既定は 0 で、logs --tail に負数を渡して docker compose をエラーにするのを防ぐ。
    """
    while True:
        raw = menu.text(message, allow_empty=True)
        if raw is None:
            return _ARG_CANCEL
        if raw == "":
            return None
        try:
            value = int(raw)
        except ValueError:
            logger.error("整数で指定してください: %r", raw)
            continue
        if value < min_value:
            logger.error("%d 以上で指定してください。", min_value)
            continue
        return value


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
    sel = menu.select(
        "ビルドするイメージを選択 (↑↓ 移動 / Enter 決定 / ←・Esc 戻る / Ctrl-C 中止):",
        choices, back=True, search=False)
    if sel is None:
        return None                    # Ctrl-C → 全体中止 (ナビ規約)
    if sel is menu.MENU_BACK:
        return _ARG_CANCEL             # Esc/← → サブメニューを再表示
    return sel                         # "" = compose 全体 (呼び出し側で None へ変換)


def _run_operation(devbase_root: Path, name: str, op: str):
    """選択された操作の引数を収集して ``dispatch_lifecycle`` で起動する。

    戻り値: dispatch の rc (``int``) / ``_ARG_CANCEL`` (引数収集を中止 = サブメニューへ
    戻る) / ``None`` (選択メニューで Ctrl-C → 全体中止)。
    引数を要さない up/rebuild は即実行。down は破壊的のため ``menu.confirm`` で確認する。
    """
    if op in ("up", "rebuild"):
        # up は scale 属性を参照する (常に None。他コマンドは無視する)。
        return dispatch_lifecycle(op, name, scale=None)

    if op == "down":
        ok = menu.confirm(f"'{name}' のコンテナを停止しますか?", default=False)
        if not ok:                     # False (拒否) / None (中止) → 実行しない
            return _ARG_CANCEL
        return dispatch_lifecycle("down", name)

    if op == "login":
        # menu.text は空入力 (既定値を消して確定) で "" を返し、wrapper で --index=
        # と展開されてコマンドが失敗する。menu.integer なら空入力は default=1 を返し、
        # min_value=1 で正の整数を保証する。cmd_login の index は文字列契約なので str 化。
        index = menu.integer("ログインするコンテナ番号", default=1, min_value=1)
        if index is None:
            return _ARG_CANCEL
        return dispatch_lifecycle("login", name, index=str(index))

    if op == "ps":
        all_c = menu.confirm("停止中も含め全コンテナを表示しますか (--all)?", default=False)
        if all_c is None:
            return _ARG_CANCEL
        return dispatch_lifecycle("ps", name, all=all_c)

    if op == "logs":
        follow = menu.confirm("ログを追従表示しますか (--follow)?", default=False)
        if follow is None:
            return _ARG_CANCEL
        tail = _optional_int("末尾何行を表示しますか (空で全件)")
        if tail is _ARG_CANCEL:
            return _ARG_CANCEL
        return dispatch_lifecycle("logs", name, follow=follow, tail=tail)

    if op == "scale":
        new_scale = menu.integer(f"'{name}' の新しいコンテナ数", min_value=1)
        if new_scale is None:
            return _ARG_CANCEL
        return dispatch_lifecycle("scale", name, new_scale=new_scale)

    if op == "build":
        image = _select_build_image(devbase_root)
        if image is None:
            return None                # Ctrl-C → 全体中止
        if image is _ARG_CANCEL:
            return _ARG_CANCEL
        return dispatch_lifecycle("build", name, image=image or None)

    # 到達しない (メニュー値は _RUNNING_OPS に限定される)。保守的に no-op。
    logger.error("未知の操作です: %s", op)
    return _ARG_CANCEL


def _operation_menu(devbase_root: Path, name: str):
    """running 行の操作サブメニューを回す。

    戻り値プロトコル (run と同じ ``is`` 同一性判定):
    - dispatch の rc (``int``): 操作を実行 → 呼び出し元へ (最終的にトップへ復帰)。
    - ``menu.MENU_BACK``: Esc/← で一覧へ戻る。
    - ``None``: Ctrl-C で全体中止。

    引数収集を中止 (``_ARG_CANCEL``) した場合はサブメニューを再表示する。
    """
    while True:
        op = _select_action(name)
        if op is menu.MENU_BACK:
            return menu.MENU_BACK
        if op is None:
            return None
        rc = _run_operation(devbase_root, name, op)
        if rc is _ARG_CANCEL:
            continue                   # 引数収集を中止 → サブメニューへ戻る
        return rc                      # 実行 rc → 呼び出し元へ


def run(devbase_root: Path):
    """プロジェクト操作カテゴリ。一覧選択 → (running は操作サブメニュー / 他は up)。

    戻り値プロトコル (トップループが ``is`` 同一性で判定する):
    - **操作を実行した場合**: ``dispatch_lifecycle`` の rc (``int``) を返す。
      「実行したのでトップへ戻る、rc は呼び出し側が記憶」の意味。これにより
      project 操作の失敗が ``devbase list`` の終了コードへ伝搬する。
    - ``menu.MENU_BACK``: 一覧で Esc/← (操作なしでトップへ) / プロジェクト無し。
    - ``None``: 一覧・サブメニューで Ctrl-C による全体中止。

    選択行が running 中なら ``_operation_menu`` で全操作を選ばせ、それ以外
    (stopped / unknown) は従来どおり直接 ``project up`` を起動する (PR1 非回帰)。
    操作完了後はトップメニューへ復帰する (plan 3.5 状態遷移: Exec → Top)。
    """
    projects_dir = Path(devbase_root) / "projects"
    while True:
        rows = list_projects(projects_dir)
        if not rows:
            logger.info("プロジェクトがありません (%s)。", projects_dir)
            return menu.MENU_BACK

        idx = _select_project(rows)
        if idx is menu.MENU_BACK:
            return menu.MENU_BACK
        if idx is None:
            return None  # Ctrl-C → 全体中止

        row = rows[idx]
        name = row["name"]
        if str(row.get("status", "")).startswith("running"):
            rc = _operation_menu(devbase_root, name)
            if rc is menu.MENU_BACK:
                continue          # 一覧へ戻る
            if rc is None:
                return None       # Ctrl-C → 全体中止
        else:
            rc = dispatch_lifecycle("up", name, scale=None)

        # 操作完了 → トップメニューへ復帰。rc は呼び出し側 (top loop) が記憶し
        # 最終的な devbase の終了コードへ伝搬させる。
        return rc


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
