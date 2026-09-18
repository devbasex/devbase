"""動いている dev インスタンスの列挙 (PLAN59 決定 8)

``devbase open`` の起動中の判定と ``devbase env token`` の配り先が同じ列挙を使う。
失敗 (``None``) と 0 個 (``[]``) を区別することが、``open`` が停止中と誤って ``up`` へ
進まないための契約である (決定 2)。
"""

from __future__ import annotations

import subprocess

from devbase.utils.docker import running_dev_instances


def _ps(stdout='', returncode=0, stderr=''):
    calls = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    run.calls = calls
    return run


def test_lists_dev_instances_in_index_order():
    run = _ps('web-dev-2\tdev-2\nweb-dev-1\tdev-1\nweb-dev-10\tdev-10\n')
    assert running_dev_instances('web', 'dev', runner=run) == [
        (1, 'web-dev-1'), (2, 'web-dev-2'), (10, 'web-dev-10')]


def test_filters_by_the_compose_project_label():
    run = _ps('')
    running_dev_instances('web', 'dev', runner=run)
    argv = run.calls[0]
    assert argv[:2] == ['docker', 'ps']
    assert 'label=com.docker.compose.project=web' in argv
    # 止まっているコンテナを数えない (`-a` を付けない)
    assert '-a' not in argv and '--all' not in argv


def test_ignores_services_other_than_dev_instances():
    run = _ps('web-db-1\tdb\nweb-dev-1\tdev-1\nweb-devx-1\tdevx-1\nweb-dev-0\tdev-0\n'
              'web-dev\tdev\n')
    assert running_dev_instances('web', 'dev', runner=run) == [(1, 'web-dev-1')]


def test_nothing_running_is_an_empty_list():
    assert running_dev_instances('web', 'dev', runner=_ps('')) == []


def test_non_zero_exit_is_none():
    assert running_dev_instances('web', 'dev', runner=_ps(returncode=1, stderr='boom')) is None


def test_os_error_is_none():
    def run(argv, **kwargs):
        raise FileNotFoundError('docker')

    assert running_dev_instances('web', 'dev', runner=run) is None


def test_current_parser_ignores_missing_separator_and_container_name():
    """現状固定: 不完全な行を混ぜても、有効な行だけを返す。"""
    run = _ps('dev-1\n\tdev-2\nweb-dev-3\tdev-3\n')
    assert running_dev_instances('web', 'dev', runner=run) == [(3, 'web-dev-3')]


def test_current_parser_matches_literal_service_name_and_positive_index():
    """現状固定: サービス名のドットは文字通り扱い、先頭ゼロの番号は除く。"""
    run = _ps('web-dev.app-2\tdev.app-2\nweb-devXapp-1\tdevXapp-1\n'
              'web-dev.app-01\tdev.app-01\n')
    assert running_dev_instances('web', 'dev.app', runner=run) == [(2, 'web-dev.app-2')]
