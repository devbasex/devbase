"""zsh の補完の実行テスト（#455）。

zsh を起動して ``etc/_devbase`` を補完として読み込み、行を打って補完が実際に出した候補
（``compadd`` が加えた語）を期待と比べる。一致テスト（test_name_context_consistency.py）が
分岐の内容を読んで比べるのに対し、ここでは候補そのものを見る。分岐の文字列がそろっていても
候補が出ない退行（#449 の ``devbase up <TAB>``）を落とす。

- 打つ行と期待は ``_requests()`` の 1 か所で作る。道の集合と補完の例外は一致テストの走査の結果
  （``NAME_PATHS``・``CONTEXT_PATHS``・``COMPLETION_NAME_EXCEPTIONS``）から読み、書き写さない
- すべての行を 1 つの zsh のセッションで流す。セッションを 1 回にするため、このモジュールの
  ケースは ``xdist_group`` で 1 つのワーカーへ寄せる
- zsh か ``zsh/zpty`` が無い環境では、印 ``DEVBASE_TEST_REQUIRE_ZSH=1`` が無ければ飛ばし、
  あれば落とす。どちらも欠けたものの名前を出す（飛ばすときは ``-q`` でも出る警告にも出す）
- プロジェクト名は一時の root の ``proj-a``・``proj-b`` から出す。``HOME`` も一時のディレクトリ
"""

from __future__ import annotations

import os
import shutil
import warnings
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.cli import zsh_completion_harness as harness
from tests.cli.test_name_context_consistency import (
    COMPLETION_NAME_EXCEPTIONS,
    CONTEXT_PATHS,
    NAME_PATHS,
    NO_CONTEXT_LEAF_PATHS,
)

pytestmark = pytest.mark.xdist_group("zsh_completion_run")

REQUIRE_ENV = "DEVBASE_TEST_REQUIRE_ZSH"
PROJECTS = frozenset({"proj-a", "proj-b"})


# ---------------------------------------------------------------------------
# 打つ行と期待の一覧（道を足すときはここに 1 行足す）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Request:
    invariant: str           # どの不変条件の行か（I1〜I5、余分は X1）
    line: str                # 打つ行（この後で Tab を押す）
    include: frozenset[str]  # 候補に含まれるべき語。empty が真なら使わない
    empty: bool = False      # 候補が 0 件であるべき
    exclude: frozenset[str] = frozenset()  # 候補に含まれてはならない語

    def expected(self) -> str:
        if self.empty:
            return "0 件"
        parts = []
        if self.include:
            parts.append(f"{', '.join(sorted(self.include))} を含む")
        if self.exclude:
            parts.append(f"{', '.join(sorted(self.exclude))} を含まない")
        return "・".join(parts)

    def met_by(self, candidates: frozenset[str]) -> bool:
        if self.empty:
            return not candidates
        return self.include <= candidates and not self.exclude & candidates


def _with_alias(paths):
    # `ct` で始めても `container` と同じ候補が出ること
    return sorted(paths | {("ct",) + p[1:] for p in paths if p[0] == "container"})


def _line(path, *rest):
    return " ".join(("devbase",) + tuple(path) + rest)


def _requests() -> list[Request]:
    named = NAME_PATHS - set(COMPLETION_NAME_EXCEPTIONS)
    reqs = [Request("I1", _line(p, ""), PROJECTS) for p in sorted(named)]
    # `login` は parser が `[name]` を持たない（走査に無い）。補完は番号を出す
    reqs.append(Request("I2", "devbase login ", frozenset({"1", "2"})))
    reqs += [Request("I3", _line(p, "-"), frozenset({"--context"})) for p in _with_alias(CONTEXT_PATHS)]
    reqs += [Request("I4", _line(p, "proj-a", "-"), frozenset({"--context"}))
             for p in sorted(named & CONTEXT_PATHS)]
    reqs += [Request("I5", _line(p, "--context", ""), frozenset(), empty=True)
             for p in _with_alias(CONTEXT_PATHS)]
    # 余分: `--context` を取らない末端の道の後の `-` で `--context` を勧めない（#453）
    reqs += [Request("X1", _line(p, "-"), frozenset(), exclude=frozenset({"--context"}))
             for p in sorted(NO_CONTEXT_LEAF_PATHS)]
    return reqs


REQUESTS = _requests()


# ---------------------------------------------------------------------------
# zsh の有無（I7）
# ---------------------------------------------------------------------------

def _require_zsh(missing: str | None, required: bool) -> None:
    """欠けたものがあれば、印が無ければ飛ばし、印があれば落とす。"""
    if missing is None:
        return
    if required:
        pytest.fail(f"{REQUIRE_ENV}=1 なのに {missing} が無い。zsh の補完の実行テストを流せない")
    msg = f"{missing} が無いため、zsh の補完の実行テストを飛ばす"
    warnings.warn(msg, UserWarning, stacklevel=2)
    pytest.skip(msg)


def _isolated_dirs(base: Path) -> tuple[Path, Path]:
    root, home = base / "root", base / "home"
    for name in sorted(PROJECTS):
        (root / "projects" / name).mkdir(parents=True)
    home.mkdir()
    return root, home


@pytest.fixture(scope="module")
def completion_run(tmp_path_factory):
    _require_zsh(harness.missing_part(), os.environ.get(REQUIRE_ENV) == "1")
    root, home = _isolated_dirs(tmp_path_factory.mktemp("zsh-completion"))
    result = harness.run_completions([r.line for r in REQUESTS], devbase_root=root, home=home)
    return dict(zip((r.line for r in REQUESTS), result.lines)), result


def _assert_loaded(result: harness.RunResult) -> None:
    # 読み込めないとき包んだ compadd はどの行でも呼ばれない。行ごとの違いを並べる前に止める
    if result.setup_timed_out:
        raise AssertionError("zsh の起動か補完の読み込みが時間切れになった。端末の出力の末尾:\n"
                             + result.terminal_tail())
    if not result.loaded:
        raise AssertionError("etc/_devbase を補完として読み込めない（どの行でも compadd が呼ばれない）。"
                             "端末の出力の末尾:\n" + result.terminal_tail())


def _check(completion_run, invariant: str) -> None:
    by_line, result = completion_run
    _assert_loaded(result)
    reqs = [r for r in REQUESTS if r.invariant == invariant]
    assert reqs, f"{invariant} の行が 1 つも無い（走査の集合が空）"
    failures = []
    for req in reqs:
        got = by_line[req.line]
        if got.status != harness.OK:
            actual = f"（{got.status}）"
        elif req.met_by(got.candidates):
            continue
        else:
            actual = ", ".join(sorted(got.candidates)) or "（0 件）"
        failures.append(f"  打った行: {req.line!r}\n    期待: {req.expected()}\n    実際: {actual}")
    assert not failures, (f"etc/_devbase の補完が期待の候補を出さない（{len(failures)} 行）:\n"
                          + "\n".join(failures))


def test_project_names_after_name_path(completion_run):
    """I1: `[name]` を取る道（補完の例外を除く）の後でプロジェクト名が出る。"""
    _check(completion_run, "I1")


def test_login_numbers(completion_run):
    """I2: `devbase login ` で番号の 1 と 2 が出る。"""
    _check(completion_run, "I2")


def test_context_after_path(completion_run):
    """I3: `--context` を取る道（`ct` で始めても）の後の `-` で `--context` が出る。"""
    _check(completion_run, "I3")


def test_context_after_name(completion_run):
    """I4: `[name]` と `--context` の両方を取る道で、名前の後の `-` で `--context` が出る。"""
    _check(completion_run, "I4")


def test_context_value_has_no_candidates(completion_run):
    """I5: `--context ` の後（値の位置）では候補を出さない。"""
    _check(completion_run, "I5")


def test_no_extra_context_on_leaf_without_it(completion_run):
    """X1: `--context` を取らない末端の道の後の `-` で `--context` を出さない。"""
    _check(completion_run, "X1")


def test_requests_cover_acceptance_lines():
    # 要求の受け入れ条件 2〜5 の代表の行が一覧に入っていること（走査の変化で黙って抜けない）
    lines = {(r.invariant, r.line) for r in REQUESTS}
    for item in [("I1", "devbase up "), ("I1", "devbase project up "), ("I2", "devbase login "),
                 ("I3", "devbase up -"), ("I3", "devbase env exec -"), ("I3", "devbase ct profile up -"),
                 ("I3", "devbase project post-start -"), ("I4", "devbase up proj-a -"),
                 ("I5", "devbase up --context "), ("I5", "devbase env exec --context "),
                 ("X1", "devbase env set -")]:
        assert item in lines, f"打つ行の一覧に {item} が無い"


# ---------------------------------------------------------------------------
# 比べ方と harness そのもの
# ---------------------------------------------------------------------------

def test_failure_lists_line_expected_and_actual():
    # 落ちたときの出力に、打った行・期待・実際の候補が並ぶ（受け入れ条件 6）
    req = next(r for r in REQUESTS if r.line == "devbase up ")
    fake = harness.RunResult([harness.LineResult(r.line, harness.OK, frozenset({"--context"}), True)
                              for r in REQUESTS], "", "")
    by_line = {r.line: lr for r, lr in zip(REQUESTS, fake.lines)}
    with pytest.raises(AssertionError) as info:
        _check((by_line, fake), "I1")
    text = str(info.value)
    assert f"打った行: {req.line!r}" in text
    assert "期待: proj-a, proj-b を含む" in text
    assert "実際: --context" in text


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("missing", ["zsh", "zsh/zpty"])
def test_missing_zsh_skips_or_fails(missing, required):
    # I7: 印が無ければ飛ばし、あれば落とす。どちらも欠けたものの名前を出す
    expected = pytest.fail.Exception if required else pytest.skip.Exception
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(expected) as info:
            _require_zsh(missing, required)
    assert f"{missing} が無い" in str(info.value)
    if not required:
        assert any(f"{missing} が無い" in str(w.message) for w in caught)


def test_missing_part_names_what_is_missing(tmp_path):
    # PATH に zsh が無ければ "zsh"、zsh/zpty を読めない zsh なら "zsh/zpty"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert harness.missing_part(str(empty)) == "zsh"
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "zsh").write_text("#!/bin/sh\nexit 1\n")
    (fake / "zsh").chmod(0o755)
    assert harness.missing_part(str(fake)) == "zsh/zpty"


def test_present_zsh_is_not_missing():
    if shutil.which("zsh") is None:
        pytest.skip("zsh が無い")
    assert harness.missing_part() in (None, "zsh/zpty")


def test_default_timeout_is_ten_seconds():
    assert harness.DEFAULT_TIMEOUT == 10.0


@pytest.fixture
def zsh_env(tmp_path):
    _require_zsh(harness.missing_part(), os.environ.get(REQUIRE_ENV) == "1")
    return _isolated_dirs(tmp_path)


def test_hanging_completion_times_out_and_leaves_no_process(zsh_env, tmp_path):
    # I6: 応答しない補完を打ち切り、時間切れの行と流さなかった行を返し、プロセスを残さない
    root, home = zsh_env
    fp = tmp_path / "hang"
    fp.mkdir()
    (fp / "_devbase").write_text("#compdef devbase\nsleep 600\n")
    result = harness.run_completions(["devbase up ", "devbase down "], devbase_root=root, home=home,
                                     fpath=[fp], timeout=1.0)
    assert [r.status for r in result.lines] == [harness.TIMEOUT, harness.NOT_RUN]
    assert len(result.pids) >= 3, f"外側・内側の zsh と sleep を数えていない: {result.pids}"
    left = [pid for pid in result.pids if harness.alive(pid)]
    assert not left, f"起動したプロセスが残っている: {left}"


def test_unloadable_completion_fails_before_comparing(zsh_env, tmp_path):
    # E3: 補完を読み込めなければ、行ごとの違いでなく「読み込めない」と端末の出力を挙げて落ちる
    root, home = zsh_env
    fp = tmp_path / "broken"
    fp.mkdir()
    (fp / "_devbase").write_text(harness.COMPLETION_DIR.joinpath("_devbase").read_text() + "\nif (\n")
    result = harness.run_completions(["devbase up ", "devbase up -"], devbase_root=root, home=home,
                                     fpath=[fp])
    with pytest.raises(AssertionError, match="読み込めない") as info:
        _assert_loaded(result)
    assert "打った行" not in str(info.value)


def test_user_rc_is_not_read(zsh_env, tmp_path):
    # I8: 一時の HOME に置いた rc を読まず、候補は一時の root の名前だけ
    root, home = zsh_env
    marker = tmp_path / "rc-was-read"
    for rc in (".zshenv", ".zshrc", ".zprofile", ".zlogin"):
        (home / rc).write_text(f"print -r -- {rc} >> {marker}\n")
    result = harness.run_completions(["devbase up "], devbase_root=root, home=home)
    assert not marker.exists(), f"利用者の rc を読んだ: {marker.read_text()}"
    assert result.lines[0].candidates == PROJECTS, sorted(result.lines[0].candidates)
