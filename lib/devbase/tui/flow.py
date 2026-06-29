"""TUI ナビゲーションのフロー制御 (番兵 → 例外変換と共通ループ)。

actions_* の各操作フローは共通のナビ規約を持つ:

- Ctrl-C (``None``) は全体中止としてトップループまで伝搬する
- Esc / ← (``menu.MENU_BACK`` / ``ARG_CANCEL``) は 1 つ前のメニューへ戻る
- 破壊的操作の確認で拒否されたら実行せずサブメニューへ戻る

これを戻り値の番兵チェックで実装すると、全プロンプトの直後に同じ分岐が並ぶ
(PLAN31_2 時点で約 30 回の反復)。本モジュールは番兵を例外へ変換する ``need`` と、
操作関数の境界で例外を番兵へ戻すデコレータ ``collect_args`` を提供し、
収集フローを「値を取り出すだけの直線コード」に保つ。

例外は ``collect_args`` を付けた操作関数の内側でのみ使い、モジュール間の
戻り値プロトコル (rc / ``ARG_CANCEL`` / ``MENU_BACK`` / ``None``) は従来どおり
番兵で受け渡す (テスト・呼び出し側の契約を変えない)。
"""

from __future__ import annotations

import functools

from devbase.log import get_logger
from devbase.tui import menu

logger = get_logger(__name__)

# 引数収集を Esc / 確認拒否で中止したことを示す番兵 (= サブメニューを再表示)。
# dispatch の rc (int) や ``None`` (= Ctrl-C 全体中止) と区別する。
ARG_CANCEL = object()

# 「空入力 = 既定動作 (None)」を許すプロンプトの Ctrl-C 番兵。``None`` が空入力と
# 衝突するため専用番兵で返し、呼び出し側 (``need_optional``) で全体中止へ変換する。
ABORT = object()


class CancelAll(Exception):
    """Ctrl-C による全体中止。``collect_args`` 境界で ``None`` へ変換される。"""


class BackOut(Exception):
    """Esc / 確認拒否による中止。``collect_args`` 境界で ``ARG_CANCEL`` へ変換される。"""


def need(value):
    """プロンプト戻り値の番兵を例外へ変換し、実値のみを返す。

    ``menu.*`` ヘルパおよび actions_* の選択ヘルパは「実値 / ``None`` (Ctrl-C) /
    ``MENU_BACK`` または ``ARG_CANCEL`` (Esc 系)」を返す。``collect_args`` 配下では
    本関数を通すことで、中止系の分岐を呼び出し元の境界へ集約できる。
    """
    if value is None:
        raise CancelAll
    if value is menu.MENU_BACK or value is ARG_CANCEL:
        raise BackOut
    return value


def back_as_cancel(value):
    """``MENU_BACK`` を ``ARG_CANCEL`` へ読み替える (選択ヘルパの番兵契約用)。

    actions_* の選択ヘルパは「Esc = 呼び出し元メニューの再表示」を ``ARG_CANCEL``
    で表現する契約を持つ (メニューループ自身の ``MENU_BACK`` = 1 つ上の階層へ、
    と区別するため)。実値と ``None`` (Ctrl-C) はそのまま通す。
    """
    return ARG_CANCEL if value is menu.MENU_BACK else value


def need_optional(value):
    """``optional_int`` の番兵 (``ABORT`` / ``ARG_CANCEL``) を例外へ変換する。

    実値 ``int`` と「空入力 = 既定動作」の ``None`` はそのまま返す。
    """
    if value is ABORT:
        raise CancelAll
    if value is ARG_CANCEL:
        raise BackOut
    return value


def collect_args(fn):
    """操作関数の境界で ``CancelAll``/``BackOut`` を番兵へ戻すデコレータ。

    付与した関数は「dispatch の rc (``int``) / ``ARG_CANCEL`` (Esc・確認拒否 =
    サブメニュー再表示) / ``None`` (Ctrl-C = 全体中止)」を返す従来プロトコルを保つ。
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except CancelAll:
            return None
        except BackOut:
            return ARG_CANCEL
    return wrapper


def confirm_or_back(message: str, *, default: bool = False) -> None:
    """破壊的操作の実行前確認 (plan 3.4)。拒否 / Esc なら ``BackOut`` で戻る。

    ``menu.confirm`` の ``MENU_BACK`` は truthy のため、素の bool 判定では
    「Esc = 承認」と誤読する。番兵判定を ``need`` に集約した本ヘルパを使うこと。
    """
    if not need(menu.confirm(message, default=default)):
        raise BackOut


def optional_int(message: str, *, min_value: int = 0):
    """空入力を許す整数収集 (logs --tail / restore --point 等)。

    ``menu.integer`` は空入力で既定値を返す仕様のため、「空 = 既定動作 (None)」を
    表現したい optional な数値はこちらで扱う。非数値・``min_value`` 未満は再入力を促す。

    戻り値: ``int`` / ``None`` (空入力 = 既定動作) / ``ARG_CANCEL`` (Esc → サブ
    メニューへ戻る) / ``ABORT`` (Ctrl-C → 全体中止。``need_optional`` で変換する)。
    """
    while True:
        raw = menu.text(message, allow_empty=True)
        if raw is None:
            return ABORT
        if raw is menu.MENU_BACK:
            return ARG_CANCEL
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


def pause_for_review() -> bool:
    """操作出力を読めるよう、メニュー再表示の前に Enter を待つ。

    操作実行直後にメニューを再描画すると、list 等の表示系操作の出力が一瞬で
    流れて読めない。questionary 系プロンプトは画面を書き換えるため、stdlib の
    ``input()`` で素朴に待ち、出力をそのまま画面に残す。

    戻り値: ``True`` = 続行 (メニュー再表示) / ``False`` = Ctrl-C (全体中止)。
    非 TTY 等で stdin を読めない場合 (EOFError/OSError) は待たずに続行する。
    """
    try:
        input("Enter キーで操作メニューへ戻ります...")
    except KeyboardInterrupt:
        print()
        return False
    except (EOFError, OSError):
        pass
    return True


def menu_loop(select_op, run_op):
    """「操作選択 → 実行」のサブメニューループ (actions_* の ``run`` 共通骨格)。

    Parameters
    ----------
    select_op: 操作値 / ``MENU_BACK`` / ``None`` を返す選択関数。
    run_op:    選択された操作値を受け取り rc / ``ARG_CANCEL`` / ``None`` を返す実行関数。

    Returns
    -------
    ``menu.MENU_BACK`` (Esc・← で 1 つ上へ戻る) / ``None`` (Ctrl-C 全体中止)。

    操作を実行した後はトップへ戻らず、出力を読めるよう ``pause_for_review`` で
    Enter を待ってから**同じサブメニューを再表示する** (サブメニューに留まる)。
    上位 (トップ一覧) へ戻るのは Esc/← (``MENU_BACK``) を押したときだけ。
    ``run_op`` が ``ARG_CANCEL`` を返したら一時停止せず同じメニューを再表示する。
    判定は必ず ``is`` 同一性で行う (rc=0 を番兵と誤マッチさせない)。
    """
    while True:
        op = select_op()
        if op is menu.MENU_BACK:
            return menu.MENU_BACK
        if op is None:
            return None
        rc = run_op(op)
        if rc is ARG_CANCEL:
            continue           # 引数収集中止 → 一時停止せず同じメニューを再表示
        if rc is None:
            return None        # run_op 内の Ctrl-C → 全体中止
        # 操作を実行した: 出力を読めるよう一時停止し、同じサブメニューを再表示する
        # (実行のたびにトップ一覧へ戻らない)。一時停止中の Ctrl-C は全体中止。
        if not pause_for_review():
            return None
