"""スナップショットの控えから外すパス (除外の一覧) の組み立て (#368)

既定の除外 (``DEFAULT_EXCLUDES``) と、利用者が ``backups/exclude.yml`` に書いた除外を
合わせて除外の一覧にし、GNU tar の引数へ変える。世代の状態に依らず、Docker も
``meta.yml`` も使わない。世代に関わる判断 (記録・比較・積み先) は ``manager.py`` が持つ。

除外のパターンはボリュームの中の相対のパスで、``*`` は 1 つの要素の中の任意の文字列に
当たる。当たったパスとその下を控えない。各ボリュームの頭 (``ai/``・``group/``・旧レイアウトの
``./``) からだけ当たり、ボリュームの下の深い場所の同じ名前には当たらない。
"""

from __future__ import annotations

import shlex
from pathlib import Path

import yaml

from devbase.log import get_logger

logger = get_logger(__name__)

# 利用者の除外の設定。devbase のルートの ``backups/`` に 1 つだけ置き、devbase は読むだけ。
USER_CONFIG = 'exclude.yml'

# 既定の除外。作り直せる一時ファイル・ログ・取り直せる取得物だけを挙げる。
# 会話の記録 (``.claude/projects``・``.codex/sessions``・``.gemini/tmp/*/chats`` など)・
# プラグインの状態のファイル (``.claude/plugins/installed_plugins.json`` など)・
# ``.serena/language_servers`` には当てない。
DEFAULT_EXCLUDES: tuple = (
    '.gemini/tmp/*/logs.json',
    '.gemini/tmp/*/logs',
    '.codex/logs_2.sqlite',
    '.codex/logs_2.sqlite-wal',
    '.codex/logs_2.sqlite-shm',
    '.codex/.tmp',
    '.codex/cache',
    '.claude/plugins/cache',
    '.claude/plugins/marketplaces',
    '.serena/logs',
)

# ``*`` 以外のワイルドカードとエスケープの文字。除外のパターンは持たない。
_FORBIDDEN_CHARS = ('?', '[', ']', '\\')


def normalize(value) -> str:
    """除外のパターンを正規化して返す。使えない値は理由つきの ``ValueError``。"""
    if not isinstance(value, str):
        raise ValueError('文字列ではありません')
    text = value.strip()
    if not text:
        raise ValueError('空です')
    if text.startswith('/'):
        raise ValueError('絶対パスです')
    if any(ch in text for ch in _FORBIDDEN_CHARS):
        raise ValueError('使えない文字を含みます (ワイルドカードは * だけ)')
    parts = text.split('/')
    if '..' in parts:
        raise ValueError('ボリュームの外を指します (.. を含みます)')
    parts = [p for p in parts if p not in ('', '.')]
    if not parts:
        raise ValueError('空です')
    return '/'.join(parts)


def canonical(patterns) -> tuple:
    """除外の一覧を比べられる形 (重複を除いて並べ替えたタプル) にする。"""
    return tuple(sorted(set(patterns)))


def _read_exclude_values(path: Path) -> list:
    """設定ファイルの ``exclude`` の値を正規化せずに返す。形が違えば ``ValueError``。"""
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError('最上位が辞書ではありません')
    values = data.get('exclude')
    if values is None:
        values = []
    if not isinstance(values, list):
        raise ValueError('exclude がリストではありません')
    return values


def _load_user_excludes(backups_dir: Path) -> tuple:
    """利用者の除外を読む。``(パターンのリスト, 設定を読めたか)`` を返す。"""
    path = backups_dir / USER_CONFIG
    if not path.exists():
        return [], True
    try:
        values = _read_exclude_values(path)
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as e:
        logger.warning(
            "backups/%s を読めないため、今の世代の除外の一覧のまま控えます"
            "（記録の無い世代だけなら既定の除外だけで作ります）: %s", USER_CONFIG, e)
        return [], False

    patterns = []
    for value in values:
        try:
            patterns.append(normalize(value))
        except ValueError as e:
            logger.warning("backups/%s の除外 %r を採りません: %s", USER_CONFIG, value, e)
    return patterns, True


def resolve_excludes(backups_dir: Path) -> tuple:
    """既定の除外と利用者の除外を合わせる。

    Returns:
        ``(除外の一覧, 設定を読めたか)``。一覧は重複を除いて並べ替えたタプル。
        設定ファイルが無いときは読めたとみなす。
    """
    user, readable = _load_user_excludes(backups_dir)
    return canonical(DEFAULT_EXCLUDES + tuple(user)), readable


def tar_exclude_args(excludes, members) -> str:
    """除外の一覧から GNU tar の引数を組み立てる。

    ``members`` は控えるメンバー名 (``ai``・``group``。旧レイアウトは ``.``)。各パターンに
    メンバー名を頭に付け、``--anchored --no-wildcards-match-slash`` で頭からだけ・1 つの要素の
    中だけに当てる。これらのオプションはメンバーより前に置かないと効かない。
    一覧が空なら空文字を返す。
    """
    if not excludes:
        return ''
    args = ['--anchored', '--no-wildcards-match-slash']
    for member in members:
        for pattern in excludes:
            args.append(shlex.quote(f'--exclude={member}/{pattern}'))
    return ' '.join(args)
