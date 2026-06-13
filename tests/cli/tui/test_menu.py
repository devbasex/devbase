"""PLAN31_2 PR1: tui.menu (メニューエンジン) のテスト。

旧 commands/project.py の Esc/← バインド・select 起動テストを移送し、引数収集
ヘルパ (text/confirm/integer/path) のフォールバック挙動を追加検証する。
"""

from __future__ import annotations

import types

import pytest

from devbase.tui import menu


# ---------------------------------------------------------------------------
# Esc / ← キーバインド
# ---------------------------------------------------------------------------

def test_with_escape_cancel_registers_escape_binding():
    """with_escape_cancel が select に単独 Esc 中止バインドを後付けすること。"""
    questionary = pytest.importorskip("questionary")
    from prompt_toolkit.keys import Keys

    q = questionary.select("t", choices=[questionary.Choice(title="a", value=0)])
    assert menu.with_escape_cancel(q) is q  # 同じ question を返す

    esc = [b for b in q.application.key_bindings.bindings if Keys.Escape in b.keys]
    assert len(esc) == 1
    # eager=False: 矢印キー等のエスケープシーケンス (\x1b[A 等) の先頭と衝突させない
    assert esc[0].eager() is False

    # ハンドラは Ctrl-C と同様 KeyboardInterrupt で app を抜ける (= ask() が None)
    captured = {}
    fake_app = types.SimpleNamespace(exit=lambda **kw: captured.update(kw))
    esc[0].handler(types.SimpleNamespace(app=fake_app))
    assert captured["exception"] is KeyboardInterrupt


def test_with_escape_back_returns_sentinel_on_escape_and_left():
    """with_escape_back の Esc / ← ハンドラは MENU_BACK を result として返すこと。"""
    questionary = pytest.importorskip("questionary")
    from prompt_toolkit.keys import Keys

    q = questionary.select("t", choices=[questionary.Choice(title="a", value="a")])
    assert menu.with_escape_back(q) is q

    esc = [b for b in q.application.key_bindings.bindings if Keys.Escape in b.keys]
    assert len(esc) == 1
    assert esc[0].eager() is False  # 矢印キーのエスケープシーケンスと衝突させない

    captured = {}
    fake_app = types.SimpleNamespace(exit=lambda **kw: captured.update(kw))
    esc[0].handler(types.SimpleNamespace(app=fake_app))
    assert captured == {"result": menu.MENU_BACK}

    # ← (Left) も「戻る」に割り当て、Esc のフラッシュ待ち遅延を回避して即応させる
    left = [b for b in q.application.key_bindings.bindings if Keys.Left in b.keys]
    assert len(left) == 1
    captured.clear()
    left[0].handler(types.SimpleNamespace(app=fake_app))
    assert captured == {"result": menu.MENU_BACK}


def test_with_escape_back_bind_left_false_skips_left():
    """bind_left=False (検索絞り込みメニュー) のとき ← はバインドしない。"""
    questionary = pytest.importorskip("questionary")
    from prompt_toolkit.keys import Keys

    q = questionary.select("t", choices=[questionary.Choice(title="a", value="a")])
    menu.with_escape_back(q, bind_left=False)

    esc = [b for b in q.application.key_bindings.bindings if Keys.Escape in b.keys]
    left = [b for b in q.application.key_bindings.bindings if Keys.Left in b.keys]
    assert len(esc) == 1
    assert left == [], "search 有効メニューでは ← を入力カーソル用に空ける"


def test_with_escape_back_works_on_merged_key_bindings(monkeypatch):
    """confirm/text/path の application は ``_MergedKeyBindings`` (``add`` 無し) を
    持つため、直接 ``add`` せず再マージ方式で Esc を後付けできること
    (実 TTY での AttributeError クラッシュの回帰検証。monkeypatch なしの実 question)。"""
    questionary = pytest.importorskip("questionary")
    from prompt_toolkit.keys import Keys

    monkeypatch.setenv("TERM", "dumb")  # CI 等の端末差異を吸収
    for q in (questionary.confirm("ok?", default=False),
              questionary.text("name?"),
              questionary.path("path?")):
        menu.with_escape_back(q, bind_left=False)  # AttributeError を出さないこと
        # text/path は auto-suggest 由来の (Escape, f) を持つため単独 Esc のみ数える
        esc = [b for b in q.application.key_bindings.bindings
               if tuple(b.keys) == (Keys.Escape,)]
        assert len(esc) == 1, f"{type(q)} に Esc が後付けされる"


def test_back_handler_sets_erase_when_done():
    """Esc/← の戻りは erase_when_done を立ててから exit し、未回答のまま collapse
    した質問行が残って次メニューと重なる「1 行ずれ」を防ぐこと。"""
    questionary = pytest.importorskip("questionary")
    from prompt_toolkit.keys import Keys

    q = questionary.select("t", choices=[questionary.Choice(title="a", value="a")])
    menu.with_escape_back(q)

    esc = [b for b in q.application.key_bindings.bindings if Keys.Escape in b.keys]
    captured = {}
    fake_app = types.SimpleNamespace(exit=lambda **kw: captured.update(kw),
                                     erase_when_done=False)
    esc[0].handler(types.SimpleNamespace(app=fake_app))
    assert fake_app.erase_when_done is True, "戻る時は描画を消去する"
    assert captured == {"result": menu.MENU_BACK}


def test_guard_after_done_wraps_app_key_bindings():
    """_guard_after_done が app の key_bindings を ~is_done 条件でラップすること。

    回答確定 (Application.exit) 後に同一バッチへ溜まったキー (Ctrl-C 連打 /
    Enter 直後の Ctrl-C 等) が questionary 組み込みハンドラへ届くと exit が
    二重に呼ばれ「Return value already set」でクラッシュする (実 TTY のみで
    再現)。ガード適用で確定後のキーが無視されることの構造検証。
    """
    questionary = pytest.importorskip("questionary")
    from prompt_toolkit.key_binding import ConditionalKeyBindings

    q = questionary.select("t", choices=[questionary.Choice(title="a", value="a")])
    inner = q.application.key_bindings
    assert menu._guard_after_done(q) is q
    wrapped = q.application.key_bindings
    assert isinstance(wrapped, ConditionalKeyBindings)
    assert wrapped.key_bindings is inner, "既存バインドを内包したままガードする"


# ---------------------------------------------------------------------------
# select: バインドの仕込みと戻り値
# ---------------------------------------------------------------------------

def _fake_select(monkeypatch, *, ask_result="sentinel"):
    """questionary.select を差し替え、生成された fake question を返すヘルパ。"""
    from prompt_toolkit.key_binding import KeyBindings

    holder = {}

    def _factory(message, **kwargs):
        kb = KeyBindings()
        q = types.SimpleNamespace(
            application=types.SimpleNamespace(key_bindings=kb),
            ask=lambda: ask_result,
        )
        holder["question"] = q
        holder["kwargs"] = kwargs
        return q

    monkeypatch.setattr(menu.questionary, "select", _factory)
    return holder


def test_select_back_false_wires_escape_cancel(monkeypatch):
    """back=False のトップメニューは Esc 中止バインドを仕込んでから ask する。"""
    pytest.importorskip("questionary")
    from prompt_toolkit.keys import Keys

    holder = _fake_select(monkeypatch)
    result = menu.select("t", [("a", 0)], back=False)
    assert result == "sentinel"

    kb = holder["question"].application.key_bindings
    esc = [b for b in kb.bindings if Keys.Escape in b.keys]
    assert len(esc) == 1
    # 中止ハンドラ: KeyboardInterrupt で抜ける
    captured = {}
    esc[0].handler(types.SimpleNamespace(
        app=types.SimpleNamespace(exit=lambda **kw: captured.update(kw))))
    assert captured["exception"] is KeyboardInterrupt


def test_select_back_true_search_false_binds_left(monkeypatch):
    """back=True / search=False (サブメニュー) は Esc と ← を戻るに割り当てる。"""
    pytest.importorskip("questionary")
    from prompt_toolkit.keys import Keys

    holder = _fake_select(monkeypatch)
    menu.select("t", [("a", 0)], back=True, search=False)

    kb = holder["question"].application.key_bindings
    assert [b for b in kb.bindings if Keys.Escape in b.keys]
    assert [b for b in kb.bindings if Keys.Left in b.keys]
    assert holder["kwargs"]["use_search_filter"] is False


def test_select_back_true_search_true_no_left(monkeypatch):
    """back=True / search=True (一覧) は ← を空け Esc のみ戻る、filter を有効化。"""
    pytest.importorskip("questionary")
    from prompt_toolkit.keys import Keys

    holder = _fake_select(monkeypatch)
    menu.select("t", [("a", 0)], back=True, search=True)

    kb = holder["question"].application.key_bindings
    assert [b for b in kb.bindings if Keys.Escape in b.keys]
    assert [b for b in kb.bindings if Keys.Left in b.keys] == []
    assert holder["kwargs"]["use_search_filter"] is True


def test_select_converts_tuple_choices(monkeypatch):
    """(title, value) タプルは questionary.Choice に変換されて渡る。"""
    questionary = pytest.importorskip("questionary")

    holder = _fake_select(monkeypatch)
    menu.select("t", [("ラベルA", "va"), ("ラベルB", "vb")], back=False)

    choices = holder["kwargs"]["choices"]
    assert all(isinstance(c, questionary.Choice) for c in choices)
    assert [c.value for c in choices] == ["va", "vb"]


# ---------------------------------------------------------------------------
# 引数収集ヘルパ: questionary 経路の Esc バインドと再入力ループ
# ---------------------------------------------------------------------------

def _fake_question(monkeypatch, factory_name, *, ask_result):
    """questionary.<factory_name> を差し替え、生成 question を holder に集めるヘルパ。"""
    from prompt_toolkit.key_binding import KeyBindings

    holder = {"questions": []}

    def _factory(message, **kwargs):
        kb = KeyBindings()
        ans = ask_result.pop(0) if isinstance(ask_result, list) else ask_result
        q = types.SimpleNamespace(
            application=types.SimpleNamespace(key_bindings=kb),
            ask=lambda ans=ans: ans,
        )
        holder["questions"].append(q)
        return q

    monkeypatch.setattr(menu.questionary, factory_name, _factory)
    return holder


def test_text_questionary_binds_escape_back(monkeypatch):
    """questionary 経路の text に Esc→戻る (MENU_BACK) バインドが付き、← は空けること。"""
    pytest.importorskip("questionary")
    from prompt_toolkit.keys import Keys

    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", True)
    holder = _fake_question(monkeypatch, "text", ask_result="hello")
    assert menu.text("名前") == "hello"

    kb = holder["questions"][0].application.key_bindings
    esc = [b for b in kb.bindings if Keys.Escape in b.keys]
    assert len(esc) == 1
    captured = {}
    esc[0].handler(types.SimpleNamespace(
        app=types.SimpleNamespace(exit=lambda **kw: captured.update(kw))))
    assert captured == {"result": menu.MENU_BACK}
    # ← は入力カーソル移動に使うためバインドしない (bind_left=False)
    assert [b for b in kb.bindings if Keys.Left in b.keys] == []


def test_confirm_questionary_binds_escape_back(monkeypatch):
    """questionary 経路の confirm に Esc→戻る (MENU_BACK) バインドが付くこと。"""
    pytest.importorskip("questionary")
    from prompt_toolkit.keys import Keys

    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", True)
    holder = _fake_question(monkeypatch, "confirm", ask_result=True)
    assert menu.confirm("本当に?") is True

    kb = holder["questions"][0].application.key_bindings
    esc = [b for b in kb.bindings if Keys.Escape in b.keys]
    assert len(esc) == 1
    captured = {}
    esc[0].handler(types.SimpleNamespace(
        app=types.SimpleNamespace(exit=lambda **kw: captured.update(kw))))
    assert captured == {"result": menu.MENU_BACK}


def test_path_questionary_binds_escape_back(monkeypatch):
    """questionary 経路の path に Esc バインドが付くこと。"""
    pytest.importorskip("questionary")
    from prompt_toolkit.keys import Keys

    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", True)
    holder = _fake_question(monkeypatch, "path", ask_result="/tmp/x")
    assert menu.path("dest") == "/tmp/x"

    kb = holder["questions"][0].application.key_bindings
    esc = [b for b in kb.bindings if Keys.Escape in b.keys]
    assert len(esc) == 1


def test_text_questionary_ctrl_c_returns_none(monkeypatch):
    """questionary 経路の text で Ctrl-C (ask が None) のとき None (全体中止) を返す。"""
    pytest.importorskip("questionary")
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", True)
    _fake_question(monkeypatch, "text", ask_result=None)
    assert menu.text("名前") is None


def test_text_questionary_escape_returns_menu_back(monkeypatch):
    """questionary 経路の text で Esc (ask が MENU_BACK) のとき MENU_BACK を返す。

    allow_empty=False でも番兵を strip せずそのまま返す (PR #55 round4 major:
    Esc=戻る と Ctrl-C=全体中止 を呼び出し側で区別できるようにする)。
    """
    pytest.importorskip("questionary")
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", True)
    _fake_question(monkeypatch, "text", ask_result=menu.MENU_BACK)
    assert menu.text("名前", allow_empty=False) is menu.MENU_BACK


def test_confirm_questionary_escape_returns_menu_back(monkeypatch):
    """questionary 経路の confirm で Esc のとき MENU_BACK を返す (None=Ctrl-C と区別)。"""
    pytest.importorskip("questionary")
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", True)
    _fake_question(monkeypatch, "confirm", ask_result=menu.MENU_BACK)
    assert menu.confirm("本当に?") is menu.MENU_BACK


def test_path_questionary_escape_returns_menu_back(monkeypatch):
    """questionary 経路の path で Esc のとき MENU_BACK を返す (None=Ctrl-C と区別)。"""
    pytest.importorskip("questionary")
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", True)
    _fake_question(monkeypatch, "path", ask_result=menu.MENU_BACK)
    assert menu.path("dest", allow_empty=False) is menu.MENU_BACK


def test_integer_questionary_escape_returns_menu_back(monkeypatch):
    """integer は text の MENU_BACK (Esc) を int 変換せずそのまま伝搬する。"""
    pytest.importorskip("questionary")
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", True)
    _fake_question(monkeypatch, "text", ask_result=menu.MENU_BACK)
    assert menu.integer("scale") is menu.MENU_BACK


def test_text_questionary_reprompts_on_empty_via_loop(monkeypatch):
    """allow_empty=False で空入力は while ループで再入力を促す (自己再帰しない)。"""
    pytest.importorskip("questionary")
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", True)
    holder = _fake_question(monkeypatch, "text", ask_result=["", "  valid  "])
    assert menu.text("名前", allow_empty=False) == "valid"
    assert len(holder["questions"]) == 2, "空入力で 1 度再プロンプトされる"


def test_path_questionary_reprompts_on_empty_via_loop(monkeypatch):
    """path も allow_empty=False の空入力で while ループ再入力する。"""
    pytest.importorskip("questionary")
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", True)
    holder = _fake_question(monkeypatch, "path", ask_result=["", "/tmp/ok"])
    assert menu.path("dest", allow_empty=False) == "/tmp/ok"
    assert len(holder["questions"]) == 2


# ---------------------------------------------------------------------------
# 引数収集ヘルパ: input() フォールバック (questionary 不在経路)
# ---------------------------------------------------------------------------

def test_text_fallback_returns_input(monkeypatch):
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "  hello  ")
    assert menu.text("名前") == "hello"


def test_text_fallback_default_on_empty(monkeypatch):
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    assert menu.text("名前", default="dflt") == "dflt"


def test_text_fallback_abort_on_eof(monkeypatch):
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", False)

    def _eof(*a, **k):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert menu.text("名前") is None


def test_confirm_fallback_yes_no(monkeypatch):
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", False)
    answers = iter(["y"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    assert menu.confirm("本当に?") is True

    answers = iter(["n"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
    assert menu.confirm("本当に?") is False


def test_confirm_fallback_empty_uses_default(monkeypatch):
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    assert menu.confirm("本当に?", default=True) is True
    assert menu.confirm("本当に?", default=False) is False


def test_confirm_fallback_abort_on_ctrl_c(monkeypatch):
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", False)

    def _interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _interrupt)
    assert menu.confirm("本当に?") is None


def test_integer_fallback_valid(monkeypatch):
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "3")
    assert menu.integer("scale") == 3


def test_integer_fallback_reprompts_on_non_numeric_and_range(monkeypatch):
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", False)
    inputs = iter(["abc", "0", "5"])  # 非数値 → 範囲外(min=1) → 有効
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
    assert menu.integer("scale", min_value=1) == 5


def test_integer_fallback_default_on_empty(monkeypatch):
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", False)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    assert menu.integer("keep", default=3) == 3


def test_integer_fallback_abort(monkeypatch):
    monkeypatch.setattr(menu, "HAVE_QUESTIONARY", False)

    def _eof(*a, **k):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert menu.integer("scale") is None
