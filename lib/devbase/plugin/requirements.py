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
    unmet = _unmet_requirement(info, current_version)
    if unmet is None:
        return
    spec, current_version = unmet
    raise PluginError(
        f"プラグイン '{info.name}' は devbase {spec} を要求していますが、"
        f"現在の devbase は {current_version} です。"
        "devbase を更新してから再度インストールしてください "
        f"(検証を飛ばす場合は {IGNORE_ENV}=1)。"
    )


def warn_unmet_devbase_requirement(info: Optional[PluginInfo],
                                   current_version: Optional[str] = None) -> None:
    """要件違反を **警告だけ** で知らせる (中止できない場面向け)。

    ``devbase plugin update`` は git pull 済みの作業ツリーを追認するだけなので、
    要求が上がっていても止められない。気づけないまま ``devbase up`` で失敗するより、
    更新の場で本体の更新を促す方がよい。
    """
    unmet = _unmet_requirement(info, current_version)
    if unmet is None:
        return
    spec, current_version = unmet
    logger.warning(
        "プラグイン '%s' は devbase %s を要求していますが、現在の devbase は "
        "%s です。devbase 本体を更新してください "
        "(この警告を止める場合は %s=1)。",
        info.name, spec, current_version, IGNORE_ENV)


def _unmet_requirement(info: Optional[PluginInfo],
                       current_version: Optional[str]) -> Optional[Tuple[str, str]]:
    """要件を満たさないとき ``(要求, 現在の版)`` を返す。満たす/検証不能なら ``None``。"""
    # plugin.yml に `devbase: 3.0` とクォート無しで書くと YAML が float にする。
    # 型を信用せず str へ寄せてから扱う。
    spec = str(info.requires_devbase or "").strip() if info else ""
    if not spec:
        return None
    if os.environ.get(IGNORE_ENV, "").strip().lower() not in ("", "0", "false", "no"):
        logger.warning(
            "%s が設定されているため、プラグイン '%s' の requires.devbase (%s) を"
            "検証しませんでした", IGNORE_ENV, info.name, spec)
        return None

    if current_version is None:
        from devbase import __version__
        current_version = __version__

    clauses = _parse_spec(spec)
    if clauses is None:
        logger.warning(
            "プラグイン '%s' の requires.devbase (%s) を解釈できないため、"
            "互換性を検証せずに続行します", info.name, spec)
        return None

    current = _parse_version(current_version)
    if current is None:
        logger.warning(
            "devbase の版 '%s' を解釈できないため、プラグイン '%s' の "
            "requires.devbase (%s) を検証せずに続行します",
            current_version, info.name, spec)
        return None

    for operator, required in clauses:
        left, right = _align(current, required)
        if not _OPERATORS[operator](left, right):
            return spec, current_version
    return None


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
    """``"3.0.0.1"`` → ``(3, 0, 0, 1)``。数値以外が混ざっていたら ``None``。

    要素は切り捨てない。``">=3.0.0.1"`` を ``3.0.0`` が満たすと誤判定しないよう、
    桁合わせは比較時に :func:`_align` で行う。
    """
    parts = str(version).strip().split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _align(a: tuple, b: tuple) -> Tuple[tuple, tuple]:
    """短い方を 0 で埋めて桁数を揃える (``">=3.0"`` と ``"3.0.0"`` の比較用)。"""
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)), b + (0,) * (width - len(b))


__all__ = ["IGNORE_ENV", "check_devbase_requirement",
           "warn_unmet_devbase_requirement"]
