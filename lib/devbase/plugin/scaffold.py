"""作成フローの雛形の生成と ``registry.yml`` への追記 (#514)。

外部へ出ない純粋な処理だけを置く。``registry.yml`` はコメントと引用符を保つため、YAML を
書き戻さず文字列で 1 項目を足し、読み直して確かめる (設計の決定 6・不変条件 I3)。
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Dict, List, Optional

import yaml

from devbase.env import keys
from devbase.errors import RepositoryError

from .github import GitHubRef

REGISTRY_FILE = 'registry.yml'

_PLUGINS_KEY = 'plugins:'
_EMPTY_LIST_VALUE = '[]'


def _scalar(value: str) -> str:
    """YAML の 1 行のスカラー。``null``・``1.0`` のような名前も文字列のまま読める形にする"""
    return yaml.safe_dump(value, default_flow_style=True).splitlines()[0]


def registry_yml(name: str) -> str:
    return f'name: {_scalar(name)}\ndescription: ""\nplugins: []\n'


def plugin_files(name: str) -> Dict[str, str]:
    """プラグインのファイル (リポジトリの直下からのパス → 中身)"""
    plugin_yml = (
        f'name: {_scalar(name)}\n'
        'version: "1.0.0"\n'
        'description: ""\n'
        'requires:\n'
        '  devbase: ">=3.0.0"\n'
        'projects:\n'
        '  mode: auto\n'
        'priority: 50\n'
    )
    return {f'{name}/plugin.yml': plugin_yml, f'{name}/projects/.gitkeep': ''}


def normal_plugin_path(path: str) -> str:
    """リポジトリの直下からのプラグインのパスを比べられる形にする。直下は空の文字列"""
    normal = PurePosixPath(path.rstrip('/')).as_posix()
    return '' if normal == '.' else normal


def project_dir(plugin_path: str, name: str) -> str:
    """プロジェクトのディレクトリ (リポジトリの直下からのパス)"""
    return PurePosixPath(plugin_path, 'projects', name).as_posix()


def project_files(plugin_path: str, name: str, ref: GitHubRef, group: str) -> Dict[str, str]:
    """プロジェクトのファイル (リポジトリの直下からのパス → 中身)。

    ``plugin_path`` が空なら、プラグインはリポジトリの直下にある。
    """
    project_yml = (
        'version: 1\n'
        'scale: 1\n'
        'open_editor: true\n'
        'repos:\n'
        f'  - owner: {_scalar(ref.owner)}\n'
        f'    repo: {_scalar(ref.repo)}\n'
    )
    compose_yml = f"""\
services:

  dev:
    image: {_scalar(name.lower() + ':latest')}
    build:
      context: ${{DEVBASE_ROOT}}/containers/general/
      dockerfile: Dockerfile
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    env_file:
      - ${{DEVBASE_ROOT}}/.env
      - env
      - .env
    group_add: ["${{DOCKER_GID}}"]
    command: tail -f /dev/null
    working_dir: /work
    networks:
      - devbase_net

networks:
  devbase_net:
    external: true
"""
    base = project_dir(plugin_path, name)
    return {
        f'{base}/project.yml': project_yml,
        f'{base}/compose.yml': compose_yml,
        f'{base}/env': f'{keys.DEVBASE_ACCOUNT_GROUP}={group}\n',
    }


def add_registry_entry(text: str, name: str) -> str:
    """``registry.yml`` の ``plugins`` へ 1 項目を足した文字列を返す。

    Raises:
        RepositoryError: 読めない・足せない形・足した結果が 1 項目の増加にならないとき
    """
    before = _load_registry(text)
    added = _insert_entry(text, name)
    after = _load_registry(added)
    expected = dict(before)
    expected['plugins'] = list(before.get('plugins') or []) + [
        {'name': name, 'path': name, 'description': ''}]
    if after != expected:
        raise RepositoryError(
            f"{REGISTRY_FILE} へ 1 項目だけを足せませんでした。手で足してください")
    return added


def registry_plugin_names(text: str) -> List[str]:
    """``registry.yml`` の ``plugins`` の名前"""
    return [entry.get('name', '') for entry in _load_registry(text).get('plugins') or []]


def registry_plugin_path(text: str, name: str) -> Optional[str]:
    """``registry.yml`` の ``name`` の項目の ``path``。項目が無ければ ``None``。

    ``path`` が無い項目は、導入と同じくリポジトリの直下 (空の文字列) として扱う。
    """
    for entry in _load_registry(text).get('plugins') or []:
        if entry.get('name') == name:
            return normal_plugin_path(entry.get('path') or '')
    return None


def _load_registry(text: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise RepositoryError(f"{REGISTRY_FILE} を読めません: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise RepositoryError(f"{REGISTRY_FILE} の直下がマッピングではありません")
    return data


def _entry_lines(name: str, indent: str) -> List[str]:
    return [f'{indent}- name: {_scalar(name)}',
            f'{indent}  path: {_scalar(name)}',
            f'{indent}  description: ""']


def _insert_entry(text: str, name: str) -> str:
    lines = text.splitlines()
    key_index = _find_plugins_key(lines)
    if key_index is None:
        return '\n'.join([*lines, _PLUGINS_KEY, *_entry_lines(name, '  ')]) + '\n'

    value = _value_of(lines[key_index])
    if value == _EMPTY_LIST_VALUE:
        lines[key_index:key_index + 1] = [_PLUGINS_KEY, *_entry_lines(name, '  ')]
        return '\n'.join(lines) + '\n'
    if value:
        raise RepositoryError(
            f"{REGISTRY_FILE} の plugins がブロックの形ではないので足せません。手で足してください")

    items = _block_lines(lines, key_index)
    if not items:
        lines[key_index + 1:key_index + 1] = _entry_lines(name, '  ')
        return '\n'.join(lines) + '\n'
    first = lines[items[0]]
    indent = first[:len(first) - len(first.lstrip())]
    lines[items[-1] + 1:items[-1] + 1] = _entry_lines(name, indent)
    return '\n'.join(lines) + '\n'


def _find_plugins_key(lines: List[str]) -> Optional[int]:
    for index, line in enumerate(lines):
        if line.startswith(_PLUGINS_KEY):
            return index
    return None


def _value_of(line: str) -> str:
    """``plugins:`` の行の値。行末のコメントは外す"""
    return line[len(_PLUGINS_KEY):].split(' #', 1)[0].strip()


def _is_blank_or_comment(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith('#')


def _block_lines(lines: List[str], key_index: int) -> List[int]:
    """``plugins`` のブロックの中身の行番号 (空行とコメントを除く)"""
    found = []
    for index in range(key_index + 1, len(lines)):
        line = lines[index]
        if _is_blank_or_comment(line):
            continue
        # 列 0 で `-` から始まる行は、字下げの無い列の項目である
        if line[0] not in ' \t-':
            break
        found.append(index)
    return found
