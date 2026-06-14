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


def test_load_project_env_diverges_from_shell_source(tmp_path, monkeypatch):
    """shell ``source`` との仕様乖離を固定する回帰テスト (docstring の note 対応)。

    変数展開 (``$VAR`` / ``${VAR}``) は shell ``source`` 同様にサポートするが、
    コマンド置換・行中クォート除去・インラインコメントは解釈しない。この境界を pin する。
    """
    monkeypatch.setattr(os, "environ", os.environ.copy())
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

    実 env の ``WORK_DIR=/work/$GIT_REPO`` (同一ファイル内で先に定義した変数を参照)
    が TUI (``list``) 経路で未展開のまま VS Code に渡る不具合の回帰防止。
    単一引用符値はリテラル扱いで展開しないことも併せて pin する。
    """
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for k in ("GIT_REPO", "WORK_DIR", "WORK_DIR_BRACE", "SINGLE_Q"):
        monkeypatch.delenv(k, raising=False)
    env_path = tmp_path / "env"
    env_path.write_text(
        "GIT_REPO=adminer\n"
        "WORK_DIR=/work/$GIT_REPO\n"        # 行順に解決済みの GIT_REPO を展開
        "WORK_DIR_BRACE=/work/${GIT_REPO}\n"  # ${VAR} 形式も展開
        "SINGLE_Q='/work/$GIT_REPO'\n"      # 単一引用符はリテラル
    )

    container._load_project_env(env_path)

    assert os.environ["GIT_REPO"] == "adminer"
    assert os.environ["WORK_DIR"] == "/work/adminer"
    assert os.environ["WORK_DIR_BRACE"] == "/work/adminer"
    assert os.environ["SINGLE_Q"] == "/work/$GIT_REPO"


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


def test_wrapper_ct_up_name_cds_and_strips(wrapper_root):
    """`ct up carmo` は container alias として name 解決される (codex 指摘 #319)。

    `ct` は cli.py で container の alias (add_parser('container', aliases=['ct']))
    のため、wrapper の name 解決 case でも `container` と同じ strip/chdir 経路を
    通す。`ct` 自体は strip せず Python へ渡し、name のみ strip する。
    """
    r = _run_wrapper(["ct", "up", "carmo"], wrapper_root)
    assert "unknown command" not in r.stderr.lower(), r.stderr
    assert "unrecognized arguments" not in r.stderr.lower(), r.stderr
    assert _pwd(r).endswith("/projects/carmo"), r.stdout
    # name は strip されるが alias `ct` は保持して Python へ渡す
    assert _python_args(r) == "ct up", r.stdout


def test_wrapper_project_login_keeps_index_positional(wrapper_root):
    """`project login carmo` の carmo は index positional として素通しする。

    `project login` parser は name を持たず index を取る。実在プロジェクト名と
    一致しても name strip せず、Python パーサに委ねる (codex 指摘の衝突回避)。
    """
    r = _run_wrapper(["project", "login", "carmo"], wrapper_root)
    assert not _pwd(r).endswith("/projects/carmo"), r.stdout
    assert _python_args(r) == "project login carmo", r.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
