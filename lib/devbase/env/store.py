"""Environment variable file store"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Union, List

from devbase.log import get_logger

logger = get_logger(__name__)


def safe_input(prompt: str, default: str = "") -> str:
    """EOFを安全に処理するinput関数"""
    try:
        value = input(prompt).strip()
        return value if value else default
    except EOFError:
        return default


def collect_key(env_file, key, *, auto_value=None, prompt=None, mask_after=10):
    """collectors間で共通の「既存チェック→自動取得→手動入力→設定」パターン

    Args:
        env_file: EnvFileインスタンス
        key: 環境変数キー
        auto_value: 自動取得値（Noneでなければ自動設定）
        prompt: 手動入力用プロンプト（Noneでデフォルト）
        mask_after: 表示時のマスク文字数（0/Falseで全表示）

    Returns:
        True: 値が設定済み or 新規設定された
        False: スキップされた
    """
    existing = env_file.get(key)
    if existing:
        display = f"{existing[:mask_after]}..." if mask_after and len(existing) > mask_after else existing
        logger.info("%s: 設定済み (%s)", key, display)
        return True
    if auto_value is not None:
        env_file.set(key, auto_value)
        logger.info("%s: 自動取得完了", key)
        return True
    value = safe_input(prompt or f"{key} (空でスキップ): ")
    if value:
        env_file.set(key, value)
        return True
    return False


@dataclass
class EnvEntry:
    """``.env`` ファイルの 1 行を表すトークン。

    - ``kind='kv'`` のとき ``key`` / ``value`` が有効 (``raw`` は元の行全体)
    - ``kind='comment'`` / ``kind='blank'`` のとき ``raw`` のみ有効

    コメント・空行を保持してマージ出力するために使う (PR #15 gemini 指摘)。
    """
    kind: str  # 'kv' | 'comment' | 'blank'
    raw: str = ''
    key: Optional[str] = None
    value: Optional[str] = None


# ``EnvFile.dump_bytes`` / :meth:`EnvFile.save` で値を quote する閾値となる文字集合。
# シェル ``source`` 時に展開・解釈されうる metachar をすべて含める。``$`` を含む値も
# ``\$`` にエスケープして出力するため、ここで quoting 対象として捕捉する
# (PR #15 gemini 指摘)。
_NEEDS_QUOTE_CHARS = (' ', '"', "'", '$', '`', '\\', '<', '>', '|', '&', ';',
                      '(', ')', '#')


def _escape_double_quoted(value: str) -> str:
    """``"..."`` 内で安全な escape を施す。

    - ``\\`` → ``\\\\``
    - ``"``  → ``\\"``
    - ``\n`` → ``\\n`` (改行をリテラル化)
    - ``$``  → ``\\$`` (``.env`` を ``source`` した際の変数展開を抑止)
    """
    return (value.replace('\\', '\\\\')
                 .replace('"', '\\"')
                 .replace('\n', '\\n')
                 .replace('$', '\\$'))


class EnvFile:
    """
    .envファイルの読み書き・バックアップ・バリデーションを管理する。
    """

    def __init__(self, file_path: Union[str, Path]):
        self.file_path = Path(file_path)
        self._data: Dict[str, str] = {}
        self._loaded = False

    @staticmethod
    def parse_bytes(data: bytes) -> Dict[str, str]:
        """bytes 列を load と同じ規則でパースして dict を返す (ファイル不要)

        ``save`` / :meth:`EnvFile.dump_bytes` の inverse として振る舞うため、
        ダブルクオート内の ``\\\\`` / ``\\"`` / ``\\n`` / ``\\$`` を unescape する。
        formatter と round-trip 整合性が取れていないと、parse → format で
        二重エスケープが発生する (PR #15 codex 指摘)。
        """
        result: Dict[str, str] = {}
        for entry in EnvFile.parse_entries(data):
            if entry.kind == 'kv' and entry.key is not None:
                result[entry.key] = entry.value or ''
        return result

    @staticmethod
    def parse_entries(data: bytes) -> List[EnvEntry]:
        """``.env`` の各行をトークン化して返す。

        コメント (``#`` 始まり) と空行は ``EnvEntry(kind='comment'|'blank', raw=...)``
        として保持される。これにより merge 出力時に元のコメント/空白構造を残せる
        (PR #15 gemini 指摘)。
        """
        entries: List[EnvEntry] = []
        for raw_line in data.decode('utf-8').splitlines():
            stripped = raw_line.strip()
            if not stripped:
                entries.append(EnvEntry(kind='blank', raw=raw_line))
                continue
            if stripped.startswith('#'):
                entries.append(EnvEntry(kind='comment', raw=raw_line))
                continue
            if '=' not in stripped:
                # ``key=value`` 形式でない行は (滅多に無いが) 原文保持する
                entries.append(EnvEntry(kind='comment', raw=raw_line))
                continue
            key, _, value = stripped.partition('=')
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                quote = value[0]
                value = value[1:-1]
                if quote == '"':
                    value = EnvFile._unescape_double_quoted(value)
            entries.append(EnvEntry(kind='kv', raw=raw_line, key=key, value=value))
        return entries

    @staticmethod
    def _unescape_double_quoted(s: str) -> str:
        """``save`` が double-quote 値に対して施した escape を 1 パスで戻す。

        単純な逐次 ``replace`` は ``"\\\\n"`` (リテラル ``\\\\`` + ``n``) と
        ``"\\n"`` (改行) の区別が付かないため state machine で処理する。
        未知のエスケープ文字 (``\\x`` 等) はバックスラッシュごとそのまま保持する。
        """
        out: list = []
        i = 0
        n = len(s)
        while i < n:
            c = s[i]
            if c == '\\' and i + 1 < n:
                nxt = s[i + 1]
                if nxt == '\\':
                    out.append('\\')
                    i += 2
                elif nxt == '"':
                    out.append('"')
                    i += 2
                elif nxt == 'n':
                    out.append('\n')
                    i += 2
                elif nxt == '$':
                    out.append('$')
                    i += 2
                else:
                    out.append(c)
                    i += 1
            else:
                out.append(c)
                i += 1
        return ''.join(out)

    @staticmethod
    def _format_kv_line(key: str, value: str) -> str:
        """1 つの ``key=value`` を ``.env`` 行 (末尾 ``\\n`` 含む) にフォーマットする"""
        needs_quote = (
            '\n' in value
            or any(c in value for c in _NEEDS_QUOTE_CHARS)
        )
        if needs_quote:
            return f'{key}="{_escape_double_quoted(value)}"\n'
        return f'{key}={value}\n'

    @staticmethod
    def dump_bytes(data: Dict[str, str]) -> bytes:
        """``save`` と同一フォーマットで dict をバイト列化する (ファイル不要)。

        ``io_import`` 側でも merge 結果を bytes として持つ必要があるため、
        フォーマット規則を 1 箇所 (このメソッド) に集約する (PR #15 gemini 指摘)。
        """
        lines = [EnvFile._format_kv_line(k, data[k]) for k in sorted(data)]
        return ''.join(lines).encode('utf-8')

    @staticmethod
    def dump_entries_bytes(entries: List[EnvEntry]) -> bytes:
        """``parse_entries`` で得た entries を ``.env`` バイト列に戻す。

        ``kv`` エントリは現在の ``value`` を ``dump_bytes`` と同じ規則で再フォーマット
        する。``comment`` / ``blank`` は ``raw`` をそのまま保持して出力する。
        """
        lines: List[str] = []
        for e in entries:
            if e.kind == 'kv' and e.key is not None:
                lines.append(EnvFile._format_kv_line(e.key, e.value or ''))
            else:
                lines.append(e.raw + '\n')
        return ''.join(lines).encode('utf-8')

    def load(self) -> Dict[str, str]:
        """ファイルを読み込みkey=valueをパースする"""
        if not self.file_path.exists():
            self._data = {}
        else:
            self._data = self.parse_bytes(self.file_path.read_bytes())
        self._loaded = True
        return self._data

    def save(self) -> None:
        """現在のデータを.envファイルに保存する"""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_bytes(self.dump_bytes(self._data))
        os.chmod(self.file_path, 0o600)

    def backup(self) -> Optional[Path]:
        """既存ファイルのバックアップを作成する"""
        if not self.file_path.exists():
            return None

        backup_path = Path(str(self.file_path) + '.backup')
        shutil.copy2(self.file_path, backup_path)
        return backup_path

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if not self._loaded:
            self.load()
        return self._data.get(key, default)

    def set(self, key: str, value: str) -> None:
        if not self._loaded:
            self.load()
        self._data[key] = value

    def exists(self, key: str) -> bool:
        if not self._loaded:
            self.load()
        return key in self._data

    def get_all(self) -> Dict[str, str]:
        if not self._loaded:
            self.load()
        return self._data.copy()

    def delete(self, key: str) -> bool:
        if not self._loaded:
            self.load()
        if key in self._data:
            del self._data[key]
            return True
        return False

    def count(self) -> int:
        """変数の数を返す"""
        if not self._loaded:
            self.load()
        return len(self._data)

    @property
    def path(self) -> Path:
        return self.file_path

    def __repr__(self) -> str:
        return f"EnvFile('{self.file_path}')"
