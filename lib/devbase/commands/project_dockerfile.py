"""プロジェクトの Dockerfile の場所を出す入口 (#415)。

``bin/devbase`` の通常のビルド (``cmd_build``) だけが呼ぶ内部の約束で、CLI のサブコマンドにしない
(help の一覧と前方一致の集合に値を増やさない)::

    python -m devbase.commands.project_dockerfile --service NAME [--context NAME]

カレントディレクトリはプロジェクトのディレクトリ。接続先を決めて機密を載せてから、compose の構成を
1 回だけ読み (``_compose_config_services``)、``--service`` のサービスの ``build`` から
:func:`~devbase.utils.dockerfile.project_dockerfile_path` でパスを決める。``--expires`` の判定と同じ
決め方である。

- 成功: 終了コード 0。標準出力に 1 行だけ書く (パス。プロジェクトの Dockerfile が無ければ空の行)
- 構成を読めない: 終了コード 1。compose の標準エラー (または JSON として読めない旨) を標準エラーへ
  書き、標準出力には何も書かない
- 引数の誤り: 終了コード 2 (argparse)

標準出力はパスの受け渡しだけに使う。ログは標準エラーへ出る。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from devbase import log
from devbase.errors import DevbaseError
from devbase.utils.dockerfile import project_dockerfile_path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m devbase.commands.project_dockerfile')
    parser.add_argument('--service', required=True)
    parser.add_argument('--context', default=None)
    args = parser.parse_args(argv)

    log.setup()
    from devbase.commands import container

    try:
        container._prepare_compose(args.context)
    except DevbaseError as e:
        sys.stderr.write(f"{e}\n")
        return 1
    try:
        returncode, services = container._compose_config_services(show_errors=True)
    except json.JSONDecodeError:
        sys.stderr.write("Unable to read the compose configuration as JSON\n")
        return 1
    if returncode != 0:
        return 1
    path = project_dockerfile_path(services.get(args.service) or {})
    print('' if path is None else str(path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
