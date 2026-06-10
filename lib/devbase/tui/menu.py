"""TUI メニューエンジン (questionary ラッパ + 引数収集ヘルパ)。

``commands/project.py`` にあった以下の資産を PLAN31_2 で集約・一般化した:

- ``MENU_BACK`` 番兵 (旧 ``_MENU_BACK``)
- Esc / ← のキーバインド (旧 ``_with_escape_cancel`` / ``_with_escape_back``)
- 選択メニュー ``select`` (旧 ``_show_menu`` / ``_show_action_menu`` の共通部)
- 引数収集ヘルパ ``text`` / ``confirm`` / ``integer`` / ``path``
  (PR2 以降の各カテゴリ操作が CLI と同じ属性値を集めるために使う)

questionary (prompt_toolkit ベース) は任意依存。未導入環境では ``HAVE_QUESTIONARY``
が ``False`` になり、選択メニューは利用できない (app 側で番号入力フォールバックへ
縮退する)。引数収集ヘルパは questionary 不在時 stdlib ``input()`` で代替する。

ナビ規約 (旧 project.py から踏襲):
- Esc = 1 つ前のメニューへ戻る (サブメニュー) / 中止 (トップメニュー)
- ← (Left) = 1 つ前のメニューへ戻る (検索絞り込みを使わないメニューのみ即時応答)
- Ctrl-C = 全体中止 (questionary 既定で ``ask()`` が ``None`` を返す)

テストではこのモジュールの関数を monkeypatch して questionary の実起動を避ける。
"""

from __future__ import annotations

from devbase.log import get_logger

logger = get_logger(__name__)

# questionary は任意依存。未導入時は選択メニュー不可 / 引数収集は input() 代替。
try:
    import questionary
    HAVE_QUESTIONARY = True
except ImportError:  # pragma: no cover - 未導入環境のフォールバック経路
    questionary = None
    HAVE_QUESTIONARY = False

# サブメニューで Esc / ← を押した際の「1 つ前のメニューへ戻る」シグナル。
# ``None`` (= Ctrl-C による全体中止) と区別するための番兵。
MENU_BACK = object()


# ---------------------------------------------------------------------------
# キーバインド (Esc / ←)
# ---------------------------------------------------------------------------

def _add_escape_binding(question, handler):
    """questionary の select に Esc 単独押下のハンドラを後付けする共通処理。

    questionary 2.x の select は Ctrl-C / Ctrl-Q しか割り当てないため、生成済み
    ``Question.application`` の key_bindings に Escape ハンドラを足す。

    Escape は矢印キー等のエスケープシーケンス (``\\x1b[A`` 等) の先頭バイトでも
    あるため、``eager=False`` で登録し prompt_toolkit のフラッシュ待ちで単独 Esc
    のみを拾う (矢印キー移動と衝突させない)。
    """
    from prompt_toolkit.keys import Keys

    question.application.key_bindings.add(Keys.Escape)(handler)
    return question


def with_escape_cancel(question):
    """Esc 単独押下で中止する select を返す (トップメニュー用)。

    Ctrl-C と同じく ``KeyboardInterrupt`` で抜けるので ``ask()`` は ``None``
    (= 中止) を返す。戻り先が無い最上位メニューで使う。
    """
    def _cancel(event):
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    return _add_escape_binding(question, _cancel)


def with_escape_back(question, *, bind_left: bool = True):
    """Esc (と任意で ←) 押下で ``MENU_BACK`` を返す select を返す (サブメニュー用)。

    Ctrl-C は questionary 既定どおり中止 (``ask()`` が ``None``) のまま残し、Esc
    (と ←) を「1 つ前のメニューへ戻る」シグナルに割り当てる。

    Esc (``\\x1b``) は矢印キーのエスケープシーケンスの先頭バイトと衝突するため
    prompt_toolkit のフラッシュ待ち分の遅延が体感される。左矢印 (``\\x1b[D``) は
    完結した曖昧さの無いシーケンスなので、これを主たる「戻る」キーとして即時に
    反応させる。ただし検索絞り込み (use_search_filter) を使うメニューでは ← が
    入力カーソル移動と衝突するため、``bind_left=False`` で Esc のみに留める。
    """
    from prompt_toolkit.keys import Keys

    def _back(event):
        event.app.exit(result=MENU_BACK)

    _add_escape_binding(question, _back)                         # Esc（互換・低速）
    if bind_left:
        question.application.key_bindings.add(Keys.Left)(_back)  # ←（即時）
    return question


# ---------------------------------------------------------------------------
# 選択メニュー
# ---------------------------------------------------------------------------

def select(message: str, choices, *, back: bool = False, search: bool = False):
    """questionary の select を起動し、選択値を返す共通関数。

    Parameters
    ----------
    message: プロンプト文言。
    choices: ``questionary.Choice`` のリスト、または ``(title, value)`` タプルの
             リスト。後者は内部で ``Choice`` に変換する。
    back:    True ならサブメニュー扱いで Esc/← → ``MENU_BACK`` を返す
             (``with_escape_back``)。False ならトップメニュー扱いで Esc → 中止
             (``with_escape_cancel``)。
    search:  True なら文字入力での部分一致絞り込み (use_search_filter) を有効化する。
             件数の多い一覧 (プロジェクト選択等) 向け。search 有効時は ← が入力
             カーソル移動と衝突するため、back の ← バインドは無効化し Esc のみで戻る。

    Returns
    -------
    選択された Choice の ``value`` / ``MENU_BACK`` (back かつ Esc・←) / ``None``
    (Ctrl-C、または back=False で Esc 中止)。

    テストではこの関数自体を monkeypatch して questionary の実起動を避ける。
    """
    norm = [
        c if isinstance(c, questionary.Choice)
        else questionary.Choice(title=c[0], value=c[1])
        for c in choices
    ]
    question = questionary.select(
        message,
        choices=norm,
        use_arrow_keys=True,
        # use_search_filter と use_jk_keys は併用不可。検索有効時のみ filter を使う。
        use_jk_keys=False,
        use_search_filter=search,
        use_shortcuts=False,
    )
    if back:
        # search 有効時は ← を入力カーソル用に空けておく (Esc のみで戻る)。
        question = with_escape_back(question, bind_left=not search)
    else:
        question = with_escape_cancel(question)
    return question.ask()


# ---------------------------------------------------------------------------
# 引数収集ヘルパ (PR2 以降の各カテゴリ操作が CLI 相当の属性値を集めるのに使う)
# ---------------------------------------------------------------------------

def text(message: str, *, default: str | None = None,
         allow_empty: bool = True) -> str | None:
    """自由入力 (1 行) を収集する。中止 (Ctrl-C / Esc) は ``None`` を返す。

    ``allow_empty=False`` のとき空文字は受け付けず再入力を促す。questionary 不在時は
    stdlib ``input()`` で代替する。
    """
    if HAVE_QUESTIONARY:
        ans = questionary.text(message, default=default or "").ask()
        if ans is None:
            return None
        ans = ans.strip()
        if not ans and not allow_empty:
            logger.error("値を入力してください。")
            return text(message, default=default, allow_empty=allow_empty)
        return ans
    return _input_text(message, default=default, allow_empty=allow_empty)


def confirm(message: str, *, default: bool = False) -> bool | None:
    """y/n 確認を取る。中止 (Ctrl-C / Esc) は ``None`` を返す。

    破壊的操作 (down / delete / uninstall 等) の実行前確認に使う。
    """
    if HAVE_QUESTIONARY:
        return questionary.confirm(message, default=default).ask()
    return _input_confirm(message, default=default)


def integer(message: str, *, default: int | None = None,
            min_value: int | None = None, max_value: int | None = None) -> int | None:
    """整数を収集する (scale 等)。範囲外・非数値は再入力を促す。中止は ``None``。"""
    default_str = "" if default is None else str(default)
    while True:
        raw = text(message, default=default_str, allow_empty=default is not None)
        if raw is None:
            return None
        if raw == "" and default is not None:
            return default
        try:
            value = int(raw)
        except ValueError:
            logger.error("整数で指定してください: %r", raw)
            continue
        if min_value is not None and value < min_value:
            logger.error("%d 以上で指定してください。", min_value)
            continue
        if max_value is not None and value > max_value:
            logger.error("%d 以下で指定してください。", max_value)
            continue
        return value


def path(message: str, *, default: str | None = None,
         allow_empty: bool = True) -> str | None:
    """ファイル / ディレクトリパスを収集する (export/import の dest/source 等)。

    questionary 利用時は ``path`` プロンプト (補完付き)、不在時は ``input()`` 代替。
    存在チェックは呼び出し側 (各ハンドラ) に委ねる。中止は ``None``。
    """
    if HAVE_QUESTIONARY:
        ans = questionary.path(message, default=default or "").ask()
        if ans is None:
            return None
        ans = ans.strip()
        if not ans and not allow_empty:
            logger.error("パスを入力してください。")
            return path(message, default=default, allow_empty=allow_empty)
        return ans
    return _input_text(message, default=default, allow_empty=allow_empty)


# ---------------------------------------------------------------------------
# input() フォールバック (questionary 不在時)
# ---------------------------------------------------------------------------

def _input_text(message: str, *, default: str | None,
                allow_empty: bool) -> str | None:
    """``input()`` ベースの自由入力。EOF / Ctrl-C は中止 (``None``)。"""
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            raw = input(f"{message}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not raw and default is not None:
            return default
        if not raw and not allow_empty:
            logger.error("値を入力してください。")
            continue
        return raw


def _input_confirm(message: str, *, default: bool) -> bool | None:
    """``input()`` ベースの y/n 確認。EOF / Ctrl-C は中止 (``None``)。"""
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            raw = input(f"{message} [{hint}]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        logger.error("y / n で答えてください: %r", raw)
