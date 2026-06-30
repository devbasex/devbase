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
- Esc = 1 つ前のメニューへ戻る (サブメニュー / 引数入力プロンプト) / 中止 (トップメニュー)
- ← (Left) = 1 つ前のメニューへ戻る (検索絞り込みを使わないメニューのみ即時応答)
- Ctrl-C = 全体中止 (questionary 既定で ``ask()`` が ``None`` を返す)

引数収集ヘルパ (text/confirm/path/integer) も選択メニューと同じく Esc (``MENU_BACK``)
と Ctrl-C (``None``) を区別して返す。呼び出し側 (actions_*) は ``None`` をトップ
ループまで伝搬して全体中止し、``MENU_BACK`` でサブメニューを再表示する。

テストではこのモジュールの関数を monkeypatch して questionary の実起動を避ける。
"""

from __future__ import annotations

import sys

from devbase.log import get_logger

logger = get_logger(__name__)


def clear_screen() -> None:
    """端末をクリアしてカーソルを先頭行へ戻す。

    メニュー (トップ一覧 / サブメニュー) を画面の先頭行から表示するため、再描画の
    直前に呼ぶ。``\\033[2J`` で表示領域を消去、``\\033[3J`` でスクロールバックも
    消去、``\\033[H`` でカーソルを左上へ移動する。stdout が非 TTY の場合は何もしない。
    """
    if not sys.stdout.isatty():
        return
    sys.stdout.write("\033[3J\033[2J\033[H")
    sys.stdout.flush()

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

# 選択メニューのプロンプト文言に添えるキー操作ヒント (各 actions_* で共通)。
# search 有効メニューは ← が入力カーソルと衝突するため Esc のみを案内する。
HINT_BACK = "(↑↓ 移動 / Enter 決定 / ←・Esc 戻る / Ctrl-C 中止)"
HINT_SEARCH = "(↑↓ 移動 / 名前で絞り込み / Enter 決定 / Esc 戻る / Ctrl-C 中止)"


# ---------------------------------------------------------------------------
# キーバインド (Esc / ←)
# ---------------------------------------------------------------------------

def _merge_app_bindings(question, kb):
    """生成済み ``Question.application`` に ``KeyBindings`` を後付けマージする。

    select の application は素の ``KeyBindings`` を持つが、confirm/text/path は
    ``merge_key_bindings`` 済みの ``_MergedKeyBindings`` (``add`` を持たない) の
    ため、直接 ``add`` せず再マージする。後からマージしたバインドは同一キーで
    既存より優先される (prompt_toolkit は ``matches[-1]`` を呼ぶ)。
    """
    from prompt_toolkit.key_binding import merge_key_bindings

    existing = question.application.key_bindings
    question.application.key_bindings = (
        merge_key_bindings([existing, kb]) if existing is not None else kb)
    return question


def _add_key_binding(question, key, handler):
    """生成済み ``Question.application`` にキーハンドラを 1 つ後付けする共通処理。"""
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()
    kb.add(key)(handler)
    return _merge_app_bindings(question, kb)


def _add_escape_binding(question, handler):
    """questionary の question に Esc 単独押下のハンドラを後付けする共通処理。

    questionary 2.x は Ctrl-C / Ctrl-Q しか割り当てないため、生成済み
    ``Question.application`` の key_bindings に Escape ハンドラを足す。

    Escape は矢印キー等のエスケープシーケンス (``\\x1b[A`` 等) の先頭バイトでも
    あるため、``eager=False`` で登録し prompt_toolkit のフラッシュ待ちで単独 Esc
    のみを拾う (矢印キー移動と衝突させない)。
    """
    from prompt_toolkit.keys import Keys

    return _add_key_binding(question, Keys.Escape, handler)


def with_escape_cancel(question):
    """Esc 単独押下で中止する question を返す (トップメニュー用)。

    Ctrl-C と同じく ``KeyboardInterrupt`` で抜けるので ``ask()`` は ``None``
    (= 中止) を返す。戻り先が無い最上位メニューで使う。
    """
    def _cancel(event):
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    return _add_escape_binding(question, _cancel)


def _guard_after_done(question):
    """回答確定後 (``Application.exit`` 済み) のキー処理を無効化する。

    prompt_toolkit は 1 回の読み取りで複数キーを同一バッチとして処理するため、
    確定キーの直後に入力が溜まっていると (例: Ctrl-C 連打 / Enter 直後の Ctrl-C)、
    1 つ目のキーで exit して戻り値が確定した後も残りのキーが同じバッチ内で
    処理され、questionary 組み込みの Ctrl-C ハンドラ等が再度 exit を呼んで
    「Return value already set. Application.exit() failed.」のクラッシュになる
    (実 TTY でのみ再現)。アプリ単位の key_bindings (questionary 組み込み + 本
    モジュールが後付けする Esc/← を含む) を ``~is_done`` でガードし、確定後の
    キーは無視する。
    """
    from prompt_toolkit.filters import is_done
    from prompt_toolkit.key_binding import ConditionalKeyBindings

    kb = question.application.key_bindings
    if kb is not None:
        question.application.key_bindings = ConditionalKeyBindings(kb, ~is_done)
    return question


def _ask_erased(question):
    """``erase_when_done`` を立ててから ``ask()`` する共通ヘルパ (全プロンプト用)。

    questionary は回答確定時に「質問 + 回答」の collapse 行を画面へ残す。TUI は
    ループでメニューを再描画するため、回答のたびにこの行が蓄積して画面全体が
    下へずれていく (実 TTY でのみ再現する残留・行ずれ不具合)。回答後に描画ごと
    消去することで、メニューを常に同じ位置へ再描画する。

    併せて ``_guard_after_done`` で確定後のキー処理を無効化する (全プロンプトが
    本ヘルパを通るため、ここが単一の適用点)。
    """
    question.application.erase_when_done = True
    return _guard_after_done(question).ask()


def _ask_with_escape(question):
    """Esc→``MENU_BACK`` を仕込んでから ``ask()`` する共通ヘルパ (text/confirm/path 用)。

    questionary の text/confirm/path は既定で Esc バインドを持たないため、サブメニューの
    ナビ規約 (Esc=1 つ前へ戻る / Ctrl-C=全体中止) と整合させるべく ``with_escape_back``
    を適用してから問い合わせる。← は入力カーソル移動と衝突するためバインドしない。
    戻り値: 入力値 / ``MENU_BACK`` (Esc) / ``None`` (Ctrl-C)。
    """
    return _ask_erased(with_escape_back(question, bind_left=False))


def with_escape_back(question, *, bind_left: bool = True):
    """Esc (と任意で ←) 押下で ``MENU_BACK`` を返す question を返す (サブメニュー /
    引数収集プロンプト用)。

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
        # 戻る操作で残る「質問行 (未回答のまま collapse した行)」は次のメニュー描画と
        # 重なり 1 行ずれの原因になるため、exit 前に erase_when_done を立てて
        # プロンプト描画ごと消去する。通常回答時も ``_ask_erased`` が同フラグを立てる
        # ため冗長だが、本関数を ``ask()`` 直呼びと組み合わせても安全なよう残す。
        event.app.erase_when_done = True
        event.app.exit(result=MENU_BACK)

    _add_escape_binding(question, _back)                  # Esc（互換・低速）
    if bind_left:
        _add_key_binding(question, Keys.Left, _back)      # ←（即時）
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
    return _ask_erased(question)


# ---------------------------------------------------------------------------
# 最下部メニューバー付き select (トップ画面用)
# ---------------------------------------------------------------------------

def _build_menubar_question(message: str, choices, menu_items, default=None):
    """一覧 select の最下部に横並びメニューバーを組み込んだ question を構築する。

    ``select_with_menubar`` の構築部分。テストが実 TTY なしでキーバインドと
    バー描画を検証できるよう、ask せずに ``(question, focus)`` を返す。
    ``focus["tab"]`` が ``None`` なら一覧、``int`` ならバーの該当項目に
    フォーカスがある。

    ``default`` は一覧で初期ハイライトする choice の value (一覧へ戻ったときの
    カーソル復元用)。``None`` なら questionary 既定の先頭ハイライト。
    """
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    norm = [
        c if isinstance(c, questionary.Choice)
        else questionary.Choice(title=c[0], value=c[1])
        for c in choices
    ]
    # questionary.select の default は choice の value で初期カーソルを指定する。
    # 一致する value が無いと例外になるため、呼び出し側 (app) で範囲検証済みの
    # value のみ渡す契約とし、ここでは None のとき引数を省く。
    select_kwargs = {} if default is None else {"default": default}
    question = questionary.select(
        message,
        choices=norm,
        use_arrow_keys=True,
        use_jk_keys=False,
        use_search_filter=True,
        use_shortcuts=False,
        **select_kwargs,
    )

    count = len(menu_items)
    focus: dict = {"tab": None}
    tab_focused = Condition(lambda: focus["tab"] is not None)

    kb = KeyBindings()

    # questionary select は ←/→ を明示バインドしない (Keys.Any の catch-all のみ)
    # ため、後付けマージで安全に奪える。search 絞り込みの入力カーソル移動は
    # 失われるが、絞り込みは短文入力なので追記・Backspace で十分。
    @kb.add(Keys.Right, eager=True)
    def _tab_next(event):
        focus["tab"] = 0 if focus["tab"] is None else (focus["tab"] + 1) % count
        event.app.invalidate()

    @kb.add(Keys.Left, eager=True)
    def _tab_prev(event):
        focus["tab"] = (count - 1 if focus["tab"] is None
                        else (focus["tab"] - 1) % count)
        event.app.invalidate()

    # バーから ↑/↓ で一覧へフォーカスを戻す (一覧内の移動は questionary 既定)。
    @kb.add(Keys.Up, filter=tab_focused, eager=True)
    @kb.add(Keys.Down, filter=tab_focused, eager=True)
    def _tab_leave(event):
        focus["tab"] = None
        event.app.invalidate()

    # バーにフォーカスがあるときの Enter はバー項目の value で確定する
    # (一覧フォーカス時は questionary 既定の Enter が choice value を返す)。
    @kb.add(Keys.ControlM, filter=tab_focused, eager=True)
    def _tab_accept(event):
        event.app.exit(result=menu_items[focus["tab"]][1])

    def _bar_fragments():
        frags = [("", " ")]
        for i, (label, _value) in enumerate(menu_items):
            style = "bold reverse" if focus["tab"] == i else "class:text"
            frags.append((style, f" {label} "))
            if i < count - 1:
                frags.append(("", "  "))
        return frags

    app = question.application
    bar = HSplit([
        Window(height=1, char="─", style="class:separator"),
        Window(FormattedTextControl(_bar_fragments), height=1,
               dont_extend_height=True),
    ])
    # 既存レイアウト全体の下にバーを常設する (一覧の件数・絞り込みに関わらず
    # プロンプト描画の最下部に固定される)。フォーカス可能要素は一覧のみなので
    # Layout の既定フォーカス解決に任せる。
    app.layout = Layout(HSplit([app.layout.container, bar]))
    _merge_app_bindings(question, kb)
    return question, focus


def select_with_menubar(message: str, choices, menu_items, default=None):
    """最下部に常設メニューバーを付けた選択メニュー (トップ画面用)。

    Parameters
    ----------
    message:    プロンプト文言。
    choices:    一覧部分の選択肢 (``select`` と同じ形式)。
    menu_items: バー項目の ``(label, value)`` リスト。
    default:    一覧で初期ハイライトする choice の value (カーソル復元用。``None``
                なら先頭)。

    キー操作:
    - ↑↓ / 文字入力: 一覧の移動・絞り込み (questionary 既定)
    - ← →: バーへフォーカスを移して項目間を巡回 (← は末尾から、→ は先頭から)
    - ↑↓ (バー上): 一覧へフォーカスを戻す
    - Enter: フォーカス位置で確定
    - Esc / Ctrl-C: 中止 (トップ画面専用のため戻り先なし)

    Returns
    -------
    一覧の choice value / バー項目の value / ``None`` (Esc・Ctrl-C 中止)。
    テストではこの関数自体を monkeypatch して questionary の実起動を避ける。
    """
    question, _focus = _build_menubar_question(message, choices, menu_items,
                                               default=default)
    return _ask_erased(with_escape_cancel(question))


# ---------------------------------------------------------------------------
# 引数収集ヘルパ (PR2 以降の各カテゴリ操作が CLI 相当の属性値を集めるのに使う)
# ---------------------------------------------------------------------------

def _collect_stripped(make_question, *, allow_empty: bool, empty_error: str):
    """text/path 共通の収集ループ。strip した入力を返す。

    ``allow_empty=False`` のとき空文字は受け付けず再入力を促す
    (自己再帰を避け while で回す)。戻り値: 入力文字列 / ``MENU_BACK`` (Esc →
    1 つ前のメニューへ戻る) / ``None`` (Ctrl-C → 全体中止)。
    """
    while True:
        ans = _ask_with_escape(make_question())
        if ans is None or ans is MENU_BACK:
            return ans                 # None=Ctrl-C 全体中止 / MENU_BACK=Esc 戻る
        ans = ans.strip()
        if not ans and not allow_empty:
            logger.error(empty_error)
            continue
        return ans


def text(message: str, *, default: str | None = None,
         allow_empty: bool = True):
    """自由入力 (1 行) を収集する。

    戻り値: 入力文字列 / ``MENU_BACK`` (Esc → 1 つ前のメニューへ戻る) / ``None``
    (Ctrl-C → 全体中止)。``allow_empty=False`` のとき空文字は受け付けず再入力を促す。
    questionary 不在時は stdlib ``input()`` で代替する (Esc は検出できないため
    EOF / Ctrl-C のどちらも ``None`` = 中止)。
    """
    if HAVE_QUESTIONARY:
        return _collect_stripped(
            lambda: questionary.text(message, default=default or ""),
            allow_empty=allow_empty, empty_error="値を入力してください。")
    return _input_text(message, default=default, allow_empty=allow_empty)


def confirm(message: str, *, default: bool = False):
    """y/n 確認を取る。

    戻り値: ``bool`` / ``MENU_BACK`` (Esc → 1 つ前のメニューへ戻る) / ``None``
    (Ctrl-C → 全体中止)。破壊的操作 (down / delete / uninstall 等) の実行前確認に
    使う。``MENU_BACK`` は truthy のため、呼び出し側は ``not ok`` 判定の前に必ず
    ``is`` 同一性で番兵を判定すること。
    """
    if HAVE_QUESTIONARY:
        return _ask_with_escape(questionary.confirm(message, default=default))
    return _input_confirm(message, default=default)


def integer(message: str, *, default: int | None = None,
            min_value: int | None = None, max_value: int | None = None):
    """整数を収集する (scale 等)。範囲外・非数値は再入力を促す。

    戻り値: ``int`` / ``MENU_BACK`` (Esc → 1 つ前のメニューへ戻る) / ``None``
    (Ctrl-C → 全体中止)。
    """
    default_str = "" if default is None else str(default)
    while True:
        raw = text(message, default=default_str, allow_empty=default is not None)
        if raw is None or raw is MENU_BACK:
            return raw                 # None=Ctrl-C 全体中止 / MENU_BACK=Esc 戻る
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
         allow_empty: bool = True):
    """ファイル / ディレクトリパスを収集する (export/import の dest/source 等)。

    questionary 利用時は ``path`` プロンプト (補完付き)、不在時は ``input()`` 代替。
    存在チェックは呼び出し側 (各ハンドラ) に委ねる。戻り値: パス文字列 /
    ``MENU_BACK`` (Esc → 1 つ前のメニューへ戻る) / ``None`` (Ctrl-C → 全体中止)。
    """
    if HAVE_QUESTIONARY:
        return _collect_stripped(
            lambda: questionary.path(message, default=default or ""),
            allow_empty=allow_empty, empty_error="パスを入力してください。")
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
