"""AI CLI の起動定義 (PLAN50)

``containers/base/ai-cli-aliases.sh`` を対話シェル相当 (``shopt -s expand_aliases``) で
source し、PATH の先頭へ置いたスタブが受け取る引数と環境を突き合わせる。Docker には
依存しない (``tests/containers/test_entrypoint_*.py`` と同じ方式)。

固定する契約:

- 起動定義は認証方式を決めない。``GOOGLE_GENAI_USE_VERTEXAI`` を設定する記述を持たず、
  環境の値をそのまま素通しする
- 定義に ``$@`` を含まない。引数は alias の展開で末尾へ付く
- 各 CLI が起動する実体と固定オプションが変わらない
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ALIASES = Path(__file__).resolve().parents[2] / "containers" / "base" / "ai-cli-aliases.sh"


def _statements() -> str:
    """コメントと空行を除いた、実際に実行される行だけを返す。

    「書かない」ことを確かめる assertion がコメントに反応すると、なぜ書かないのかを
    説明した注記まで禁じることになる。
    """
    return "\n".join(
        line for line in ALIASES.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )

#: 定義名 -> (実体, 固定オプション)
LAUNCHERS = {
    "claude": ("claude", ["--dangerously-skip-permissions"]),
    "claudb": ("claude", ["--dangerously-skip-permissions"]),
    "gemini": ("gemini", ["--yolo"]),
    "codex": ("codex", ["--dangerously-bypass-approvals-and-sandbox"]),
    "kiro": ("kiro-cli", ["chat", "--trust-all-tools"]),
    "agy": ("agy", ["--dangerously-skip-permissions"]),
}

#: 起動時に前置される環境変数。認証方式を選ぶものはここに現れてはならない。
EXPECTED_PREFIXED_ENV = {
    "claudb": {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-west-2"},
}


def _stub_dir(tmp_path: Path, *names: str) -> Path:
    """受け取った引数と環境を出力するスタブを PATH 用ディレクトリへ作る。"""
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    for name in names:
        stub = d / name
        stub.write_text(
            "#!/bin/sh\n"
            'printf "ARGS:%s\\n" "$*"\n'
            'printf "VERTEXAI:%s\\n" "${GOOGLE_GENAI_USE_VERTEXAI-<unset>}"\n'
            'printf "BEDROCK:%s\\n" "${CLAUDE_CODE_USE_BEDROCK-<unset>}"\n'
            'printf "AWSREGION:%s\\n" "${AWS_REGION-<unset>}"\n'
        )
        stub.chmod(0o755)
    return d


def _run(script: str, tmp_path: Path, env: dict | None = None,
         stubs: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    """対話シェル相当で起動定義を読み込み ``script`` を実行する。"""
    bin_dir = _stub_dir(tmp_path, *stubs) if stubs else tmp_path / "bin"
    base = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("GOOGLE_", "CLAUDE_", "AWS_", "DEVBASE_"))
    }
    base["PATH"] = f"{bin_dir}:{base.get('PATH', '')}"
    base.update(env or {})
    return subprocess.run(
        ["bash", "-c", f'shopt -s expand_aliases; . "{ALIASES}"\n{script}'],
        capture_output=True, text=True, env=base,
    )


def _field(result: subprocess.CompletedProcess, key: str) -> str | None:
    for line in result.stdout.splitlines():
        if line.startswith(f"{key}:"):
            return line[len(key) + 1:]
    return None


# ===========================================================================
# AC1: 認証方式を決める記述を持たない
# ===========================================================================

def test_aliases_file_does_not_set_vertexai():
    """起動定義に GOOGLE_GENAI_USE_VERTEXAI を書かない。

    書くと、プロジェクトが環境で選んだ認証方式を起動定義が上書きしてしまう。
    """
    assert "GOOGLE_GENAI_USE_VERTEXAI" not in _statements()


# ===========================================================================
# AC2 / AC3: 環境を素通しする
# ===========================================================================

@pytest.mark.parametrize("given,expected", [
    ({"GOOGLE_GENAI_USE_VERTEXAI": "true"}, "true"),
    ({"GOOGLE_GENAI_USE_VERTEXAI": "false"}, "false"),
    ({"GOOGLE_GENAI_USE_VERTEXAI": ""}, ""),
    ({}, "<unset>"),
])
def test_gemini_passes_through_vertexai_env(tmp_path, given, expected):
    """gemini は GOOGLE_GENAI_USE_VERTEXAI を足しも引きもしない。"""
    result = _run("gemini", tmp_path, env=given, stubs=("gemini",))

    assert _field(result, "VERTEXAI") == expected


# ===========================================================================
# AC4 / AC7: 引数がそのまま届く
# ===========================================================================

@pytest.mark.parametrize("name", sorted(LAUNCHERS))
def test_launcher_forwards_arguments(tmp_path, name):
    """固定オプションの後ろに、渡した引数がその順序で並ぶ。"""
    real, options = LAUNCHERS[name]
    result = _run(f"{name} alpha beta", tmp_path, stubs=(real,))

    assert _field(result, "ARGS") == " ".join([*options, "alpha", "beta"])


@pytest.mark.parametrize("name", sorted(LAUNCHERS))
def test_launcher_runs_without_arguments(tmp_path, name):
    """引数なしでも固定オプションだけで起動する。"""
    real, options = LAUNCHERS[name]
    result = _run(name, tmp_path, stubs=(real,))

    assert _field(result, "ARGS") == " ".join(options)


# ===========================================================================
# AC5: 定義に $@ を含まない
# ===========================================================================

@pytest.mark.parametrize("name", sorted(LAUNCHERS))
def test_definition_has_no_positional_parameters(tmp_path, name):
    """alias の "$@" はシェルの位置パラメータに展開され、引数を渡す働きをしない。"""
    result = _run(f'printf "ARGS:%s\\n" "$(alias {name})"', tmp_path)
    definition = _field(result, "ARGS")

    # 定義が読めていないと "$@" を含まないことが自明に成り立つため、先に確かめる
    assert definition, f"{name} の定義を読めていない: {result.stderr}"
    assert "$@" not in definition


def test_aliases_file_has_no_positional_parameters():
    assert "$@" not in _statements()


# ===========================================================================
# AC6: 実体と前置する環境が変わらない
# ===========================================================================

@pytest.mark.parametrize("name", sorted(LAUNCHERS))
def test_launcher_invokes_expected_binary(tmp_path, name):
    """定義名ではなく、想定した実体を起動する (kiro -> kiro-cli など)。"""
    real, _ = LAUNCHERS[name]
    # 実体のスタブだけを置く。別の名前を呼んでいれば command not found になる。
    result = _run(name, tmp_path, stubs=(real,))

    assert result.returncode == 0, result.stderr
    assert _field(result, "ARGS") is not None


@pytest.mark.parametrize("name", sorted(LAUNCHERS))
def test_launcher_prefixes_only_expected_env(tmp_path, name):
    """Bedrock を選ぶ claudb 以外は、環境を前置しない。"""
    real, _ = LAUNCHERS[name]
    expected = EXPECTED_PREFIXED_ENV.get(name, {})
    result = _run(name, tmp_path, stubs=(real,))

    assert _field(result, "BEDROCK") == expected.get("CLAUDE_CODE_USE_BEDROCK", "<unset>")
    assert _field(result, "AWSREGION") == expected.get("AWS_REGION", "<unset>")


# ===========================================================================
# AC8: 補完の登録
# ===========================================================================

def test_completion_is_registered(tmp_path):
    result = _run('complete -p claudb kiro 2>&1 | tr "\\n" " " | sed "s/^/ARGS:/"', tmp_path)
    out = _field(result, "ARGS") or ""

    # `complete -p` は未登録だと "not found" を返す。登録の有無をここで分ける
    assert "not found" not in out, out
    assert "claudb" in out
    assert "kiro" in out
