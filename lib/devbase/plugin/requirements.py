"""``plugin.yml`` の ``requires.devbase`` を devbase 本体の版と突き合わせる。

プラグインは devbase の機能に依存する。例えば PLAN32 (devbase 3.0.0) 以降の
``project.yml`` 形式のプロジェクト定義は 2.x では読めず、インストールできて
しまうと ``devbase up`` の段階で初めて失敗する。要求を宣言だけで終わらせず、
インストール時に確かめて先に止める。

対応する書式は PEP 440 の部分集合::

    ">=3.0.0"          # 以上
    ">=3.0.0,<4.0.0"   # 範囲 (カンマ区切りは AND)
    "==3.0.0" / "3.0.0"  # 一致 (演算子なしは == 扱い)
    ">" / "<" / "<=" / "!="

解釈できない書式や版番号は**エラーにしない**。独自記法を書いたプラグインを
インストール不能にするより、検証できなかったことを警告で知らせて先へ進める方が
実害が小さい (依存が本当に足りなければ後段で失敗する)。
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from devbase.errors import PluginError
from devbase.log import get_logger
from devbase.plugin.models import PluginInfo

logger = get_logger(__name__)

#: 検証を丸ごと無効化する環境変数 (検証側の誤りで作業が詰まらないための逃げ道)
IGNORE_ENV = "DEVBASE_IGNORE_PLUGIN_REQUIRES"

_CLAUSE = re.compile(r'^\s*(>=|<=|==|!=|>|<)?\s*([0-9][0-9.]*)\s*$')

_OPERATORS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def check_devbase_requirement(info: Optional[PluginInfo],
                              current_version: Optional[str] = None) -> None:
    """``requires.devbase`` を満たしていなければ :class:`PluginError` を送出する。

    Args:
        info: ``plugin.yml`` の内容 (``None`` や要求未指定なら何もしない)
        current_version: 比較に使う devbase の版 (既定は動作中の devbase)
    """
    if info is None or not (info.requires_devbase or "").strip():
        return
    if os.environ.get(IGNORE_ENV, "").strip() not in ("", "0", "false", "no"):
        logger.warning(
            "%s が設定されているため、プラグイン '%s' の requires.devbase (%s) を"
            "検証しませんでした", IGNORE_ENV, info.name, info.requires_devbase)
        return

    spec = info.requires_devbase.strip()
    if current_version is None:
        from devbase import __version__
        current_version = __version__

    clauses = _parse_spec(spec)
    if clauses is None:
        logger.warning(
            "プラグイン '%s' の requires.devbase (%s) を解釈できないため、"
            "互換性を検証せずに続行します", info.name, spec)
        return

    current = _parse_version(current_version)
    if current is None:
        logger.warning(
            "devbase の版 '%s' を解釈できないため、プラグイン '%s' の "
            "requires.devbase (%s) を検証せずに続行します",
            current_version, info.name, spec)
        return

    for operator, required in clauses:
        if not _OPERATORS[operator](current, required):
            raise PluginError(
                f"プラグイン '{info.name}' は devbase {spec} を要求していますが、"
                f"現在の devbase は {current_version} です。"
                "devbase を更新してから再度インストールしてください "
                f"(検証を飛ばす場合は {IGNORE_ENV}=1)。"
            )


def _parse_spec(spec: str) -> Optional[List[Tuple[str, tuple]]]:
    """``">=3.0.0,<4.0.0"`` を ``[(">=", (3,0,0)), ("<", (4,0,0))]`` にする。"""
    clauses = []
    for part in spec.split(","):
        matched = _CLAUSE.match(part)
        if not matched:
            return None
        version = _parse_version(matched.group(2))
        if version is None:
            return None
        clauses.append((matched.group(1) or "==", version))
    return clauses or None


def _parse_version(version: str) -> Optional[tuple]:
    """``"3.0"`` → ``(3, 0, 0)``。数値以外が混ざっていたら ``None``。

    桁数を 3 に揃えるのは ``">=3.0"`` と ``"3.0.0"`` を比べられるようにするため。
    """
    parts = str(version).strip().split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts][:3]
    numbers += [0] * (3 - len(numbers))
    return tuple(numbers)


__all__ = ["IGNORE_ENV", "check_devbase_requirement"]
