"""環境の隔離 (#218) の漏れの検査と、隔離の fixture のテスト

``lib/devbase`` のソースを AST で読み、環境変数として読む変数名 (読み取りの集合) を集める。
集合の名前がすべて ``tests/conftest.py`` の隔離の一覧か隔離しない一覧にあることを確かめ、
新しく環境変数を読むコードを足したときに隔離の足し忘れで落とす。
"""

from __future__ import annotations

import ast
import os
import pwd
import re
from pathlib import Path
from typing import Dict, Optional, Set

import pytest

from tests.conftest import (
    ISOLATED_ENV,
    ISOLATED_ENV_PREFIXES,
    NOT_ISOLATED_ENV,
    _clear_inherited_env,
)

LIB = Path(__file__).resolve().parent.parent / 'lib' / 'devbase'
KEYS_FILE = LIB / 'env' / 'keys.py'

_ENV_NAME = re.compile(r'^[A-Z][A-Z0-9_]*$')
_RECEIVER_NAMES = ('env', 'environ')
_KEYS_MODULE_NAMES = ('keys', 'K')
_READ_METHODS = ('get', 'pop', 'setdefault')


def _top_level_strings(tree: ast.Module) -> Dict[str, str]:
    """最上位で文字列を代入した名前から、その文字列への dict"""
    out: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value.value
    return out


def _keys_constant_values(tree: ast.Module) -> Set[str]:
    """``keys.py`` の最上位の代入の右辺の文字列 (tuple なら要素の文字列の定数)"""
    values: Set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            values.add(value.value)
        elif isinstance(value, ast.Tuple):
            values.update(e.value for e in value.elts
                          if isinstance(e, ast.Constant) and isinstance(e.value, str))
    return values


def _is_environ(node: ast.AST) -> bool:
    """受け手が ``os.environ``、または名前が ``env`` / ``environ`` の変数か"""
    if isinstance(node, ast.Attribute):
        return node.attr == 'environ' and isinstance(node.value, ast.Name) \
            and node.value.id == 'os'
    return isinstance(node, ast.Name) and node.id in _RECEIVER_NAMES


def _is_getenv(func: ast.AST) -> bool:
    return isinstance(func, ast.Attribute) and func.attr == 'getenv' \
        and isinstance(func.value, ast.Name) and func.value.id == 'os'


def _resolve(node: ast.AST, local: Dict[str, str], keys: Dict[str, str]) -> Optional[str]:
    """変数名の書き方を文字列へ解決する。解決できなければ None"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return local.get(node.id, keys.get(node.id))
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
            and node.value.id in _KEYS_MODULE_NAMES:
        return keys.get(node.attr)
    return None


def _read_names(tree: ast.AST):
    """読み取りの形に置かれた変数名の式を、(式, 行) で順に出す"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if not node.args:
                continue
            if isinstance(func, ast.Attribute) and func.attr in _READ_METHODS \
                    and _is_environ(func.value):
                yield node.args[0], node.lineno
            elif _is_getenv(func):
                yield node.args[0], node.lineno
        elif isinstance(node, ast.Subscript) and _is_environ(node.value):
            yield node.slice, node.lineno
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 \
                and isinstance(node.ops[0], (ast.In, ast.NotIn)) \
                and _is_environ(node.comparators[0]):
            yield node.left, node.lineno


def _collect_env_reads(lib: Path = LIB) -> Dict[str, Set[str]]:
    """読み取りの集合: 変数名から読む場所 (``<lib からの相対パス>:<行>``) の集合への dict

    読み取りの形 (``os.environ`` / ``env`` / ``environ`` の ``.get`` ほか・``os.getenv``) で
    拾った名前と、``env/keys.py`` の定数の和。``__`` で終わる値は接頭辞なので外す。
    """
    keys_file = lib / 'env' / 'keys.py'
    keys_tree = ast.parse(keys_file.read_text(encoding='utf-8'))
    keys_names = _top_level_strings(keys_tree)
    reads: Dict[str, Set[str]] = {}

    def add(name: Optional[str], where: str) -> None:
        if name and _ENV_NAME.match(name) and not name.endswith('__'):
            reads.setdefault(name, set()).add(where)

    for path in sorted(lib.rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        local = _top_level_strings(tree)
        rel = path.relative_to(lib).as_posix()
        for expr, line in _read_names(tree):
            add(_resolve(expr, local, keys_names), f'{rel}:{line}')

    keys_rel = keys_file.relative_to(lib).as_posix()
    for value in _keys_constant_values(keys_tree):
        add(value, keys_rel)
    return reads


# --- 漏れの検査 ---

def test_every_env_read_is_listed():
    """lib/devbase が読む変数はすべて、隔離の一覧か隔離しない一覧にある (I1)"""
    listed = set(ISOLATED_ENV) | set(NOT_ISOLATED_ENV)
    missing = {name: where for name, where in _collect_env_reads().items()
               if name not in listed}
    lines = [f'{name}: {", ".join(sorted(where))}' for name, where in sorted(missing.items())]
    assert not missing, (
        f'隔離の一覧に無い環境変数が {len(missing)} 個ある\n' + '\n'.join(lines)
        + '\ntests/conftest.py の ISOLATED_ENV か NOT_ISOLATED_ENV（理由つき）に足す'
    )


def test_isolated_and_not_isolated_are_disjoint():
    """隔離の一覧と隔離しない一覧は同じ名前を持たない (I2)"""
    overlap = sorted(set(ISOLATED_ENV) & set(NOT_ISOLATED_ENV))
    assert not overlap, f'両方の一覧にある: {", ".join(overlap)}'


def test_collector_finds_known_reads():
    """集める処理は 3 つの形 (文字列・同じファイルの定数・keys の定数) で拾う (I6)"""
    reads = _collect_env_reads()
    assert any(w.startswith('volume/compose.py:') for w in reads['DEV_SERVICE_NAME'])
    assert any(w.startswith('env/agekeys.py:') for w in reads['DEVBASE_AGE_KEY_FILE'])
    assert any(w.startswith('env/gcp_auth.py:') for w in reads['GCP_AUTH_MODE'])


# --- 隔離の fixture ---

def test_clear_inherited_env_unsets_listed():
    """_clear_inherited_env は一覧の変数と接頭辞に合う変数をすべて消す (I3 の helper)"""
    prefixed = ISOLATED_ENV_PREFIXES[0] + 'x'
    mp = pytest.MonkeyPatch()
    try:
        for name in ISOLATED_ENV:
            mp.setenv(name, 'bogus')
        mp.setenv(prefixed, 'bogus')
        _clear_inherited_env(mp)
        left = [n for n in (*ISOLATED_ENV, prefixed) if n in os.environ]
        assert not left, f'消えていない: {", ".join(left)}'
    finally:
        mp.undo()


@pytest.fixture(scope='module')
def _module_sets_env():
    """function scope の autouse より先に立ち、隔離の対象を設定する"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv('DEV_SERVICE_NAME', 'bogusdev')
        mp.setenv(ISOLATED_ENV_PREFIXES[0] + 'x', 'bogus')
        yield


@pytest.fixture
def _env_at_test_fixture(_module_sets_env):
    """テストの側の function scope の fixture が立った時点の環境の写し"""
    return dict(os.environ)


def test_env_unset_before_test_fixtures(_env_at_test_fixture):
    """テストの側の fixture が立つ時点で、隔離の対象は未設定である (I3)"""
    assert 'DEV_SERVICE_NAME' not in _env_at_test_fixture
    assert ISOLATED_ENV_PREFIXES[0] + 'x' not in _env_at_test_fixture


def test_home_is_per_test_tmp(tmp_path_factory):
    """HOME は利用者のホームでなく、テストごとの tmp を指す (I7)"""
    home = Path(os.environ['HOME']).resolve()
    assert home != Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    assert home.is_relative_to(tmp_path_factory.getbasetemp().resolve())


def test_test_side_setenv_wins(monkeypatch):
    """テストの側の setenv は隔離の fixture より後に効く (I5)"""
    from devbase.volume.compose import get_dev_service_name

    monkeypatch.setenv('DEV_SERVICE_NAME', 'x')
    assert get_dev_service_name() == 'x'
