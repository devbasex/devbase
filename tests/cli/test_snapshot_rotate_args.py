"""snapshot rotate の引数解析の現状固定テスト。"""

from devbase.cli import _create_parser


def test_rotate_explicit_limits():
    args = _create_parser().parse_args(
        ["snapshot", "rotate", "--keep", "2", "--max-total", "5"]
    )

    assert args.subcommand == "rotate"
    assert args.keep == 2
    assert type(args.keep) is int
    assert args.max_total == 5
    assert type(args.max_total) is int


def test_rotate_default_limits():
    args = _create_parser().parse_args(["snapshot", "rotate"])

    assert args.subcommand == "rotate"
    assert args.keep == 3
    assert args.max_total is None


def test_rotate_alias_max_total():
    args = _create_parser().parse_args(["ss", "rotate", "--max-total", "4"])

    assert args.subcommand == "rotate"
    assert args.max_total == 4
    assert type(args.max_total) is int
