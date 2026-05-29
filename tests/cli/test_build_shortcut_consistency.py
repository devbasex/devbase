"""bin/devbase の build dispatch と Python 側 build ショートカットの整合性テスト。

配布入口 bin/devbase は top-level `build` を shell 実装 (cmd_build: devbase-base
依存検出 + 2 段ビルド + --no-cache 対応) に委譲する。Python の project build は
単純な `compose build` であり実装が異なるため、Python 側が top-level `build` を
`project build` ショートカットとして広告すると、wrapper の実経路と乖離してしまう。

このテストは「Python は top-level build ショートカットを持たない / 広告しない」
ことを固定し、wrapper の build) ケースが shell 経路を保つことを検証する。
project build / container build サブコマンド自体は引き続き利用可能。
"""

from pathlib import Path

from devbase import cli


def test_build_not_in_shortcuts():
    # top-level build は SHORTCUTS から除外されている (wrapper が shell へ委譲するため)
    assert "build" not in cli.SHORTCUTS
    # 他のショートカットは維持されている
    for sc in ("up", "down", "login", "ps", "scale"):
        assert sc in cli.SHORTCUTS


def test_top_level_build_has_no_python_parser():
    # top-level `build` には Python parser が無く、parse_args はエラー終了する
    # (wrapper が build を shell の cmd_build に委譲し Python に渡さないため)
    parser = cli._create_parser()
    import pytest

    with pytest.raises(SystemExit):
        parser.parse_args(["build"])


def test_help_epilog_does_not_advertise_build_shortcut():
    parser = cli._create_parser()
    epilog = parser.epilog or ""
    # "build  project build" のショートカット広告が無いこと
    assert "project build" not in epilog
    # 残りのショートカット広告は維持
    assert "project up" in epilog
    assert "project scale" in epilog


def test_project_build_subcommand_still_available():
    # project build / container build サブコマンド自体は削除していない
    parser = cli._create_parser()
    ns = parser.parse_args(["project", "build", "myimage"])
    assert ns.command == "project"
    assert ns.subcommand == "build"
    assert ns.image == "myimage"


def test_wrapper_routes_build_to_shell_not_python():
    # bin/devbase の dispatch で build は shell の cmd_build に委譲され、
    # Python 用 run_python の case には含まれないことを確認する。
    wrapper = (Path(__file__).resolve().parents[2] / "bin" / "devbase").read_text()
    # build は専用の shell ケースへ
    assert "build)  shift; cmd_build" in wrapper or "build) shift; cmd_build" in wrapper
    # run_python に委譲する case 行に build が紛れ込んでいないこと
    for line in wrapper.splitlines():
        if "run_python" in line and "${_resolved_cmd}" in line:
            assert "build" not in line
