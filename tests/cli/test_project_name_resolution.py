"""PLAN06 Task 2: プロジェクト名解決 (wrapper cd + Python フォールバック) のテスト。

検証対象:
  - Python `container._resolve_project_name`: $DEVBASE_ROOT/projects/<name> への
    chdir + COMPOSE_PROJECT_NAME 上書き、存在しない name のエラー + 候補提示。
  - wrapper (bin/devbase): project/container サブコマンド及びトップレベルシノニムで
    実在するプロジェクト名のみ cd + argv strip し、login <index> / build <image> /
    scale <N> の既存 positional と曖昧にならないこと (存在性ベースの判定)。

wrapper テストは実際の `uv run` を避けるため run_python / cmd_build をスタブに
差し替え、DEVBASE_ROOT を一時ディレクトリへ向けた薄いハーネスで dispatch のみ
実行する (wrapper 末尾の DEVBASE_ROOT 自動解決行も sed で除去する)。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from devbase.commands import container

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "bin" / "devbase"


@pytest.fixture(autouse=True)
def _isolate_os_environ():
    """各テストの os.environ 変更を in-place で退避・復元する (後続テストへの漏出防止)。

    os.environ を os._Environ のまま扱う (dict で置換しない) ため putenv 同期は保たれ、
    subprocess へ環境が伝わらなくなる問題を避ける。
    """
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ===========================================================================
# Python: _resolve_project_name
# ===========================================================================

@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """projects/carmo を持つ一時 DEVBASE_ROOT を用意し、CWD/環境を復元する。"""
    (tmp_path / "projects" / "carmo").mkdir(parents=True)
    (tmp_path / "projects" / "shop").mkdir(parents=True)
    monkeypatch.setenv("DEVBASE_ROOT", str(tmp_path))
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    origin = Path.cwd()
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    os.chdir(origin)


def test_resolve_chdirs_into_project(fake_root):
    assert container._resolve_project_name("carmo") is True
    assert Path.cwd().resolve() == (fake_root / "projects" / "carmo").resolve()
    assert os.environ["COMPOSE_PROJECT_NAME"] == "carmo"


def test_resolve_unknown_name_errors_with_candidates(fake_root, caplog):
    with caplog.at_level(logging.ERROR, logger="devbase.commands.container"):
        assert container._resolve_project_name("nope") is False
    messages = " ".join(r.message for r in caplog.records)
    assert "nope" in messages
    # 候補一覧に既存プロジェクトが提示される
    assert "carmo" in messages and "shop" in messages


def test_report_unknown_truncates_many_candidates(tmp_path, monkeypatch, caplog):
    """候補が上限を超える場合は先頭 N 件 + 「... 他 M 件」に truncate される。"""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    total = container._MAX_PROJECT_CANDIDATES + 5
    # ゼロ埋めで sorted 順を安定させる (p000, p001, ...)。
    for i in range(total):
        (projects_dir / f"p{i:03d}").mkdir()

    monkeypatch.setenv("DEVBASE_ROOT", str(tmp_path))
    with caplog.at_level(logging.ERROR, logger="devbase.commands.container"):
        container._report_unknown_project("nope", projects_dir)

    messages = " ".join(r.message for r in caplog.records)
    # 先頭 N 件は表示される
    assert "p000" in messages
    assert f"p{container._MAX_PROJECT_CANDIDATES - 1:03d}" in messages
    # 上限超過分は表示されず、省略表記に集約される
    assert f"p{container._MAX_PROJECT_CANDIDATES:03d}" not in messages
    assert f"... 他 {total - container._MAX_PROJECT_CANDIDATES} 件" in messages


def test_report_unknown_no_truncation_when_within_limit(tmp_path, monkeypatch, caplog):
    """候補が上限以内なら省略表記は付かず全件表示される。"""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    for n in ("carmo", "shop"):
        (projects_dir / n).mkdir()

    monkeypatch.setenv("DEVBASE_ROOT", str(tmp_path))
    with caplog.at_level(logging.ERROR, logger="devbase.commands.container"):
        container._report_unknown_project("nope", projects_dir)

    messages = " ".join(r.message for r in caplog.records)
    assert "carmo" in messages and "shop" in messages
    assert "他" not in messages


def test_resolve_without_devbase_root(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("DEVBASE_ROOT", raising=False)
    with caplog.at_level(logging.ERROR, logger="devbase.commands.container"):
        assert container._resolve_project_name("carmo") is False
    assert any("DEVBASE_ROOT" in r.message for r in caplog.records)


INVALID_NAMES = ["../etc", "a/b", ".", ".."]


@pytest.mark.parametrize("name", INVALID_NAMES)
def test_resolve_rejects_malformed_name_without_chdir(fake_root, monkeypatch, caplog, name):
    """受け入れ条件 2 (単体): 形に合わない名前は chdir せず、env も読まず、候補も出さず False。

    `projects/<name>` へそのまま連結すると `..` で `projects/` の外へ出るため、連結の前に
    名前の形 (`devbase.utils.names.is_single_segment_name`) で弾く (#146)。
    """
    (fake_root / "etc").mkdir()
    (fake_root / "etc" / "env").write_text("MARKER=leaked\n")
    monkeypatch.delenv("MARKER", raising=False)
    called = []
    monkeypatch.setattr(container.os, "chdir", lambda p: called.append(p))

    with caplog.at_level(logging.ERROR, logger="devbase.commands.container"):
        assert container._resolve_project_name(name) is False

    assert called == [], "形に合わない名前で chdir を呼んではならない"
    assert "MARKER" not in os.environ
    messages = " ".join(r.message for r in caplog.records)
    assert "プロジェクト名に使えない形" in messages
    assert name in messages
    # 候補の一覧は出さない
    assert "carmo" not in messages and "shop" not in messages


def test_cli_project_up_rejects_malformed_name(fake_root, monkeypatch, caplog):
    """受け入れ条件 3: wrapper を経ない `python -m devbase.cli project up ../etc` は chdir せず 1。"""
    from devbase import cli

    (fake_root / "etc").mkdir()
    (fake_root / "etc" / "env").write_text("MARKER=leaked\n")
    monkeypatch.delenv("MARKER", raising=False)
    monkeypatch.setattr(container, "cmd_up",
                        lambda *a, **k: pytest.fail("cmd_up を呼んではならない"))
    monkeypatch.setattr("sys.argv", ["devbase", "project", "up", "../etc"])
    before = Path.cwd()

    with caplog.at_level(logging.ERROR, logger="devbase.commands.container"):
        assert cli.main() == 1

    assert Path.cwd() == before
    assert "MARKER" not in os.environ
    assert "プロジェクト名に使えない形" in caplog.text


def test_resolve_noop_when_already_in_target(fake_root, monkeypatch):
    """wrapper が既に cd 済みなら chdir を呼ばない (冪等)。"""
    target = fake_root / "projects" / "carmo"
    monkeypatch.chdir(target)

    called = []
    monkeypatch.setattr(container.os, "chdir", lambda p: called.append(p))
    assert container._resolve_project_name("carmo") is True
    assert called == [], "既に対象ディレクトリにいる場合 chdir は呼ばれない"
    assert os.environ["COMPOSE_PROJECT_NAME"] == "carmo"


def test_resolve_loads_project_env(fake_root, monkeypatch):
    """wrapper を経ない直接起動でも project env が os.environ へ反映される。

    gemini round2 minor 指摘 (wrapper の `source ./env` 相当) の回帰テスト。
    """
    monkeypatch.delenv("CONTAINER_SCALE", raising=False)
    monkeypatch.delenv("CUSTOM_VAR", raising=False)
    env_path = fake_root / "projects" / "carmo" / "env"
    env_path.write_text(
        "# comment line\n"
        "\n"
        "CONTAINER_SCALE=5\n"
        "export CUSTOM_VAR=hello\n"
        'QUOTED="dq value"\n'
        "SQUOTED='sq value'\n"
    )

    assert container._resolve_project_name("carmo") is True
    assert os.environ["CONTAINER_SCALE"] == "5"
    assert os.environ["CUSTOM_VAR"] == "hello"
    assert os.environ["QUOTED"] == "dq value"
    assert os.environ["SQUOTED"] == "sq value"
    # name 指定は env 由来値より優先される
    assert os.environ["COMPOSE_PROJECT_NAME"] == "carmo"


def test_resolve_env_name_overrides_env_file_compose_project_name(fake_root, monkeypatch):
    """env に COMPOSE_PROJECT_NAME があっても name 指定が優先される。"""
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "stale")
    env_path = fake_root / "projects" / "carmo" / "env"
    env_path.write_text("COMPOSE_PROJECT_NAME=from_env\n")

    assert container._resolve_project_name("carmo") is True
    assert os.environ["COMPOSE_PROJECT_NAME"] == "carmo"


def test_resolve_missing_env_file_is_noop(fake_root):
    """env ファイルが無くても解決は成功する (フォールバックの堅牢性)。"""
    assert not (fake_root / "projects" / "carmo" / "env").exists()
    assert container._resolve_project_name("carmo") is True
    assert os.environ["COMPOSE_PROJECT_NAME"] == "carmo"


def test_resolve_clears_caller_only_env_keys(fake_root, monkeypatch):
    """別プロジェクトから直接起動した際、呼び出し元固有の env キーが残留しない。

    codex 指摘 (bin/devbase:235 / _load_project_env) の回帰テスト。呼び出し元
    プロジェクト caller の env にしか無い ``DEV_SERVICE_NAME`` が対象プロジェクト
    other へ誤って引き継がれないこと、共通キーは対象側の値が勝つことを固定する。
    """
    for k in ("DEV_SERVICE_NAME", "SHARED"):
        monkeypatch.delenv(k, raising=False)
    caller = fake_root / "projects" / "caller"
    caller.mkdir()
    (caller / "env").write_text("DEV_SERVICE_NAME=caller_svc\nSHARED=caller_shared\n")
    other = fake_root / "projects" / "other"
    other.mkdir()
    (other / "env").write_text("SHARED=other_shared\n")

    # 呼び出し元プロジェクト内から起動した状況を再現 (env を os.environ へ反映)。
    monkeypatch.chdir(caller)
    container._load_project_env(Path("env"))
    assert os.environ["DEV_SERVICE_NAME"] == "caller_svc"

    assert container._resolve_project_name("other") is True
    # 呼び出し元固有キーは unset され残留しない
    assert "DEV_SERVICE_NAME" not in os.environ
    # 共通キーは対象プロジェクトの値が勝つ
    assert os.environ["SHARED"] == "other_shared"
    assert os.environ["COMPOSE_PROJECT_NAME"] == "other"


def test_switching_projects_drops_caller_only_secrets(fake_root, monkeypatch):
    """切替経路 (`_resolve_project_name` → `_inject_secrets`) で機密が残留しない。

    codex 指摘の回帰テスト。`cli._load_secret_env` は dispatch 前に現在地
    (呼び出し元) の機密を載せるため、`project up <other>` の直接起動では切替元
    固有の機密が os.environ に残り Compose や子プロセスへ引き継がれてしまう。
    切替後の載せ直しでこれが落ちること、共通の機密は残ることを固定する。
    """
    from devbase.env import runtime

    monkeypatch.setattr(runtime, "_injected_originals", {})
    for k in ("CALLER_TOKEN", "OTHER_TOKEN", "SHARED_SECRET"):
        monkeypatch.delenv(k, raising=False)

    # 平文の機密 (移行前と同じ配置) を用意する。鍵が無くても同じ経路を通る。
    (fake_root / ".env").write_text("SHARED_SECRET=common\n")
    caller = fake_root / "projects" / "caller"
    caller.mkdir()
    (caller / ".env").write_text("CALLER_TOKEN=caller_only\n")
    other = fake_root / "projects" / "other"
    other.mkdir()
    (other / ".env").write_text("OTHER_TOKEN=other_only\n")

    # 呼び出し元プロジェクト内で起動した状況 (cli._load_secret_env 相当)。
    monkeypatch.chdir(caller)
    monkeypatch.setenv("PWD", str(caller))
    container._inject_secrets(required=False)
    assert os.environ["CALLER_TOKEN"] == "caller_only"

    assert container._resolve_project_name("other") is True
    container._inject_secrets(required=False)

    # 切替元固有の機密は残らない / 切替先の機密が載る / 共通の機密は残る
    assert "CALLER_TOKEN" not in os.environ
    assert os.environ["OTHER_TOKEN"] == "other_only"
    assert os.environ["SHARED_SECRET"] == "common"


def test_load_project_env_diverges_from_shell_source(tmp_path, monkeypatch):
    """shell ``source`` との仕様乖離を固定する回帰テスト (docstring の note 対応)。

    変数展開 (``$VAR`` / ``${VAR}``) は shell ``source`` 同様にサポートするが、
    コマンド置換・行中クォート除去・インラインコメントは解釈しない。この境界を pin する。
    """
    for k in ("LIT_CMD", "INNER_Q", "INLINE_C"):
        monkeypatch.delenv(k, raising=False)
    env_path = tmp_path / "env"
    env_path.write_text(
        "LIT_CMD=$(echo x)\n"    # コマンド置換しない (リテラル "$(echo x)")
        'INNER_Q=a"b"c\n'        # 行中クォートは除去しない
        "INLINE_C=bar # note\n"  # 行頭以外の # はコメント扱いしない
    )

    container._load_project_env(env_path)

    assert os.environ["LIT_CMD"] == "$(echo x)"
    assert os.environ["INNER_Q"] == 'a"b"c'
    assert os.environ["INLINE_C"] == "bar # note"


def test_load_project_env_expands_variable_references(tmp_path, monkeypatch):
    """``$VAR`` / ``${VAR}`` を shell ``source`` 同様に展開する回帰テスト。

    env は ``APP_ROOT=/srv/$APP_NAME`` のように同一ファイル内で先に定義した変数を参照
    が TUI (``list``) 経路で未展開のまま VS Code に渡る不具合の回帰防止。
    単一引用符値はリテラル扱いで展開しないことも併せて pin する。
    """
    for k in ("APP_NAME", "APP_ROOT", "APP_ROOT_BRACE", "SINGLE_Q"):
        monkeypatch.delenv(k, raising=False)
    env_path = tmp_path / "env"
    env_path.write_text(
        "APP_NAME=adminer\n"
        "APP_ROOT=/srv/$APP_NAME\n"          # 行順に解決済みの APP_NAME を展開
        "APP_ROOT_BRACE=/srv/${APP_NAME}\n"  # ${VAR} 形式も展開
        "SINGLE_Q='/srv/$APP_NAME'\n"        # 単一引用符はリテラル
    )

    container._load_project_env(env_path)

    assert os.environ["APP_NAME"] == "adminer"
    assert os.environ["APP_ROOT"] == "/srv/adminer"
    assert os.environ["APP_ROOT_BRACE"] == "/srv/adminer"
    assert os.environ["SINGLE_Q"] == "/srv/$APP_NAME"


def test_load_project_env_escaped_dollar_and_undefined(tmp_path, monkeypatch):
    """`\\$` はリテラル `$`、未定義参照は空 (shell source 準拠)。$(...) は別テストで担保。"""
    for k in ("DEFINED", "ESCAPED", "UNDEF_REF", "NOPE"):
        monkeypatch.delenv(k, raising=False)
    env_path = tmp_path / "env"
    env_path.write_text(
        "DEFINED=x\n"
        "ESCAPED=a\\$DEFINED\n"     # \\$ → リテラル $ (展開しない)
        "UNDEF_REF=/p/$NOPE/q\n"    # 未定義は空
    )

    container._load_project_env(env_path)

    assert os.environ["DEFINED"] == "x"
    assert os.environ["ESCAPED"] == "a$DEFINED"
    assert os.environ["UNDEF_REF"] == "/p//q"


# ===========================================================================
# wrapper: cd + argv strip + 存在性ベースの曖昧性回避
# ===========================================================================

def _run_wrapper(args, devbase_root):
    """run_python / cmd_build をスタブ化し wrapper の dispatch だけを実行する。

    - run_python  -> "PWD:<cwd>" と "PYTHON:<args>" を出力
    - cmd_build   -> "PWD:<cwd>" と "BUILD:<args>" を出力
    実際の wrapper が DEVBASE_ROOT を自身のパスから再計算してしまうため、その
    代入行 (`DEVBASE_ROOT=...`) も sed で除去し、環境変数で渡した値を使わせる。
    """
    harness = (
        'run_python() { echo "PWD:$PWD"; echo "PYTHON:$*"; exit 0; }\n'
        'cmd_build() { echo "PWD:$PWD"; echo "BUILD:$*"; exit 0; }\n'
        'ensure_uv() { :; }\n'
        'eval "$(sed -e \'/^run_python()/,/^}/d\' '
        '            -e \'/^ensure_uv()/,/^}/d\' '
        '            -e \'/^cmd_build()/,/^}/d\' '
        '            -e \'/^DEVBASE_ROOT=/d\' "$WRAPPER_PATH")"\n'
    )
    env = {
        **os.environ,
        "DEVBASE_ROOT": str(devbase_root),
        "WRAPPER_PATH": str(WRAPPER),
    }
    return subprocess.run(
        ["bash", "-c", harness, "devbase", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


@pytest.fixture
def wrapper_root(tmp_path):
    (tmp_path / "projects" / "carmo").mkdir(parents=True)
    return tmp_path


def _pwd(result):
    for line in result.stdout.splitlines():
        if line.startswith("PWD:"):
            return line[len("PWD:"):]
    return None


def _python_args(result):
    for line in result.stdout.splitlines():
        if line.startswith("PYTHON:"):
            return line[len("PYTHON:"):]
    return None


def _build_args(result):
    for line in result.stdout.splitlines():
        if line.startswith("BUILD:"):
            return line[len("BUILD:"):]
    return None


def _run_wrapper_from(args, devbase_root, cwd):
    """`_run_wrapper` と同じだが任意の CWD から起動し env 残留を検証できる版。

    run_python スタブが ``DEV_SERVICE_NAME`` の値も出力するため、呼び出し元 env の
    残留有無を判定できる。
    """
    harness = (
        'run_python() { echo "PWD:$PWD"; echo "PYTHON:$*"; '
        'echo "DEV_SERVICE_NAME:${DEV_SERVICE_NAME:-<unset>}"; '
        'echo "SHARED:${SHARED:-<unset>}"; exit 0; }\n'
        'cmd_build() { echo "PWD:$PWD"; echo "BUILD:$*"; exit 0; }\n'
        'ensure_uv() { :; }\n'
        'eval "$(sed -e \'/^run_python()/,/^}/d\' '
        '            -e \'/^ensure_uv()/,/^}/d\' '
        '            -e \'/^cmd_build()/,/^}/d\' '
        '            -e \'/^DEVBASE_ROOT=/d\' "$WRAPPER_PATH")"\n'
    )
    env = {
        **os.environ,
        "DEVBASE_ROOT": str(devbase_root),
        "WRAPPER_PATH": str(WRAPPER),
    }
    env.pop("DEV_SERVICE_NAME", None)
    env.pop("SHARED", None)
    return subprocess.run(
        ["bash", "-c", harness, "devbase", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
    )


def _stdout_field(result, prefix):
    for line in result.stdout.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


def test_wrapper_clears_caller_only_env_on_project_switch(tmp_path):
    """別プロジェクト内から `up <name>` した際、呼び出し元固有 env が残らない。

    codex 指摘 (bin/devbase:235) の回帰テスト。呼び出し元 caller の env にしか無い
    ``DEV_SERVICE_NAME`` が対象 carmo へ引き継がれず、共通キー ``SHARED`` は対象側の
    値が勝つことを wrapper 経路で固定する。
    """
    root = tmp_path
    carmo = root / "projects" / "carmo"
    carmo.mkdir(parents=True)
    (carmo / "env").write_text("SHARED=carmo_shared\n")
    caller = root / "projects" / "caller"
    caller.mkdir(parents=True)
    (caller / "env").write_text("DEV_SERVICE_NAME=caller_svc\nSHARED=caller_shared\n")

    r = _run_wrapper_from(["up", "carmo"], root, caller)
    assert _pwd(r).endswith("/projects/carmo"), r.stdout
    # 呼び出し元固有キーは残留しない
    assert _stdout_field(r, "DEV_SERVICE_NAME:") == "<unset>", r.stdout
    # 共通キーは対象プロジェクトの値が勝つ
    assert _stdout_field(r, "SHARED:") == "carmo_shared", r.stdout


def test_wrapper_project_up_name_cds_and_strips(wrapper_root):
    r = _run_wrapper(["project", "up", "carmo"], wrapper_root)
    assert "unknown command" not in r.stderr.lower(), r.stderr
    assert _pwd(r).endswith("/projects/carmo"), r.stdout
    # name は strip され Python へは渡らない
    assert _python_args(r) == "project up", r.stdout


def test_wrapper_shortcut_up_name_cds_and_strips(wrapper_root):
    r = _run_wrapper(["up", "carmo"], wrapper_root)
    assert _pwd(r).endswith("/projects/carmo"), r.stdout
    assert _python_args(r) == "up", r.stdout


def test_wrapper_unknown_name_not_stripped_no_cd(wrapper_root):
    """存在しない name は cd せず素通し (Python 側でエラー処理させる)。"""
    r = _run_wrapper(["up", "bogus"], wrapper_root)
    assert not _pwd(r).endswith("/projects/bogus"), r.stdout
    assert _python_args(r) == "up bogus", r.stdout


def test_wrapper_build_name_cds_via_shell(wrapper_root):
    """build は shell cmd_build 経路。wrapper cd で対象プロジェクトへ移動する。"""
    r = _run_wrapper(["build", "carmo"], wrapper_root)
    assert _pwd(r).endswith("/projects/carmo"), r.stdout
    assert _build_args(r) == "", r.stdout  # name は strip


def test_wrapper_build_flag_not_treated_as_name(wrapper_root):
    """`build --no-cache` のフラグは name とみなさず CWD でビルド。"""
    r = _run_wrapper(["build", "--no-cache"], wrapper_root)
    assert not _pwd(r).endswith("/projects/"), r.stdout
    assert _build_args(r) == "--no-cache", r.stdout


def test_wrapper_scale_name_disambiguation(wrapper_root):
    """`scale carmo 3` は name+N、`scale 3` は N のみ (存在性で判定)。"""
    r1 = _run_wrapper(["scale", "carmo", "3"], wrapper_root)
    assert _pwd(r1).endswith("/projects/carmo"), r1.stdout
    assert _python_args(r1) == "scale 3", r1.stdout

    r2 = _run_wrapper(["scale", "3"], wrapper_root)
    assert not _pwd(r2).endswith("/projects/3"), r2.stdout
    assert _python_args(r2) == "scale 3", r2.stdout


def test_wrapper_login_index_not_treated_as_name(wrapper_root):
    """`login 2` の 2 は index。projects/2 が無いので cd せず素通し。"""
    r = _run_wrapper(["login", "2"], wrapper_root)
    assert _python_args(r) == "login 2", r.stdout

    # 一方 `login carmo` は実在プロジェクトなので cd + strip (index=1 既定)
    r2 = _run_wrapper(["login", "carmo"], wrapper_root)
    assert _pwd(r2).endswith("/projects/carmo"), r2.stdout
    assert _python_args(r2) == "login", r2.stdout


def test_wrapper_project_scale_name_strips_keeps_subcommand(wrapper_root):
    r = _run_wrapper(["project", "scale", "carmo", "3"], wrapper_root)
    assert _pwd(r).endswith("/projects/carmo"), r.stdout
    assert _python_args(r) == "project scale 3", r.stdout


def test_wrapper_no_name_uses_cwd(wrapper_root):
    """name を渡さなければ cd せず従来通り (引数素通し)。"""
    r = _run_wrapper(["project", "up"], wrapper_root)
    assert _python_args(r) == "project up", r.stdout


def test_wrapper_project_build_keeps_image_positional(wrapper_root):
    """`project build carmo` の carmo は image positional。

    `project build` parser は name を持たず image を取る (cli.py 参照)。実在
    プロジェクト名 carmo が image と衝突しても name strip せず素通しし、Python
    側で image=carmo として解釈させる (codex 指摘の衝突回避)。
    """
    r = _run_wrapper(["project", "build", "carmo"], wrapper_root)
    # cd せず (image 解決は Python 側)、carmo を strip しない
    assert not _pwd(r).endswith("/projects/carmo"), r.stdout
    assert _python_args(r) == "project build carmo", r.stdout


def test_wrapper_project_login_keeps_index_positional(wrapper_root):
    """`project login carmo` の carmo は index positional として素通しする。

    `project login` parser は name を持たず index を取る。実在プロジェクト名と
    一致しても name strip せず、Python パーサに委ねる (codex 指摘の衝突回避)。
    """
    r = _run_wrapper(["project", "login", "carmo"], wrapper_root)
    assert not _pwd(r).endswith("/projects/carmo"), r.stdout
    assert _python_args(r) == "project login carmo", r.stdout


# ===========================================================================
# wrapper (実プロセス): 名前の形 (PLAN61 / #146)
#
# `exec_wrapper` (conftest.py) は bin/devbase を tmp へ複製して起動し、`uv` だけを PATH で
# 差し替える。maybe_cd_project は本物のまま動く (受け入れ条件 15)。
# ===========================================================================

from tests.cli.conftest import stdout_field  # noqa: E402

TOP_LEVEL_NAME_COMMANDS = ["up", "down", "ps", "scale", "login", "rebuild", "open"]
PROJECT_NAME_SUBCOMMANDS = ["up", "down", "ps", "logs", "scale", "rebuild", "open"]
MALFORMED_NAMES = ["../etc", "a/b", ".", ".."]


def _name_commands():
    for cmd in TOP_LEVEL_NAME_COMMANDS:
        yield [cmd]
    for sub in PROJECT_NAME_SUBCOMMANDS:
        yield ["project", sub]


@pytest.mark.parametrize("name", MALFORMED_NAMES)
@pytest.mark.parametrize("command", list(_name_commands()), ids=" ".join)
def test_wrapper_malformed_name_stays_put_and_reads_no_outside_env(exec_wrapper, command, name):
    """受け入れ条件 2: `..` や `/` を含む名前で projects/ の外へ cd せず、外の env を読まない。

    `<tmp>/etc/env` に `MARKER=leaked` を置く。`projects/../etc` へ cd してしまうと wrapper が
    それを source し、偽の `uv` が `MARKER:leaked` を出す。名前は wrapper が取り除かず、
    そのまま Python へ渡る (前提 2)。
    """
    exec_wrapper.etc_env()
    exec_wrapper.project("carmo")

    r = exec_wrapper([*command, name])

    assert stdout_field(r, "PWD:") == str(exec_wrapper.work), r.stdout
    assert stdout_field(r, "MARKER:") == "<unset>", r.stdout
    uv = stdout_field(r, "UV:")
    assert uv is not None and uv.endswith(f" {' '.join(command)} {name}"), r.stdout


@pytest.mark.parametrize("name", ["carmo", "github_work_time", "carmo-ai"])
def test_wrapper_well_formed_existing_name_cds_and_strips(exec_wrapper, name):
    """受け入れ条件 5: 形に合う実在の名前は今と同じく cd して取り除かれる。"""
    exec_wrapper.project(name)

    r = exec_wrapper(["up", name])

    assert stdout_field(r, "PWD:") == str(exec_wrapper.root / "projects" / name), r.stdout
    uv = stdout_field(r, "UV:")
    assert uv is not None and uv.endswith(" devbase.cli up"), r.stdout


def test_wrapper_non_ascii_name_is_not_resolved(exec_wrapper):
    """決定 4: shell の比較は LC_ALL=C で行い、`café` は名前の形に当たらない。"""
    exec_wrapper.project("café")

    r = exec_wrapper(["up", "café"])

    assert stdout_field(r, "PWD:") == str(exec_wrapper.work), r.stdout
    uv = stdout_field(r, "UV:")
    assert uv is not None and uv.endswith(" devbase.cli up café"), r.stdout


CONTAINER_SUBCOMMANDS = ["up", "down", "ps", "logs", "scale", "rebuild", "open"]


@pytest.mark.parametrize("group", ["container", "ct"])
@pytest.mark.parametrize("sub", CONTAINER_SUBCOMMANDS)
def test_wrapper_container_group_does_not_resolve_names(exec_wrapper, group, sub):
    """受け入れ条件 12: `container <sub> <name>` / `ct <sub> <name>` は実在する名前でも cd しない。

    `container` の parser は `[name]` を持たない (決定 10 / #200)。wrapper が名前を取り除かず
    そのまま渡し、argparse の usage エラー (終了コード 2) になる。旧テスト
    `test_wrapper_ct_up_name_cds_and_strips` の置き換え。
    """
    exec_wrapper.project("carmo")

    r = exec_wrapper([group, sub, "carmo"])

    assert stdout_field(r, "PWD:") == str(exec_wrapper.work), r.stdout
    uv = stdout_field(r, "UV:")
    assert uv is not None and uv.endswith(f" devbase.cli {group} {sub} carmo"), r.stdout


def test_wrapper_container_up_without_name_uses_cwd(exec_wrapper):
    """受け入れ条件 13: `container up` (名前なし) は今と同じく現在のディレクトリで動く。

    非推奨の警告は Python 側 (`test_cmd_container_warns_and_delegates`)。
    """
    r = exec_wrapper(["container", "up"])

    assert stdout_field(r, "PWD:") == str(exec_wrapper.work), r.stdout
    uv = stdout_field(r, "UV:")
    assert uv is not None and uv.endswith(" devbase.cli container up"), r.stdout


def test_wrapper_name_regex_is_synced_with_python():
    """決定 2: bin/devbase の `_SINGLE_SEGMENT_NAME_RE` は Python の定義と同じ正規表現。"""
    import re

    from devbase.utils.names import SINGLE_SEGMENT_NAME_PATTERN

    found = re.findall(r"^_SINGLE_SEGMENT_NAME_RE='([^']*)'$", WRAPPER.read_text(), re.M)
    assert found, "bin/devbase から _SINGLE_SEGMENT_NAME_RE を抜き出せない"
    assert found == ["^" + SINGLE_SEGMENT_NAME_PATTERN + "$"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
