"""固有の語の検査 (#353)。

語の一覧を読み、git が追跡するファイルから固有の語を含む行を探す。語は正規表現でなく文字列として、
大文字小文字を区別せずに部分一致で探す。出力に出すのはパス・行番号・件数・定型文だけで、
一覧の語と当たった行の本文は出さない。

手元でも CI でも同じコマンドで打つ:
  python3 .github/scripts/proper_term_check.py [--terms PATH]

語の一覧の出所は次の順で最初に当たった 1 つだけを使う:
  1. --terms PATH のファイル (指定したのに読めなければ終了コード 2)
  2. 環境変数 PROPER_TERMS の中身 (CI の `proper-terms` ジョブが secret から入れる。空なら次へ)
  3. ${XDG_CONFIG_HOME:-$HOME/.config}/devbase/proper-terms.txt (無ければ飛ばす)

一覧は 1 行 1 語の UTF-8 の平文で、空の行と `#` で始まる行を読み飛ばす。

終了コード:
  0  当たり 0 件、または一覧が無い・空で飛ばした
  1  当たりが 1 件以上ある
  2  一覧を読めない、git が使えない・リポジトリの外、引数の誤り
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# この検査の名前。出力・注記・サマリーの見出しに使う
CHECK_TITLE = "固有の語の検査"

# CI が secret の中身を入れる環境変数
TERMS_ENV = "PROPER_TERMS"

EXIT_OK = 0
EXIT_HITS = 1
EXIT_ERROR = 2


@dataclass(frozen=True)
class ExceptionRule:
    """当たりにしない行。パスと行の形の組で定め、語を含まない。"""

    path: str
    pattern: re.Pattern

    def covers(self, path: str, text: str) -> bool:
        return path == self.path and bool(self.pattern.search(text))


# 例外の位置。ここだけで定義する (テストもここを読む)
EXCEPTION_RULES: tuple[ExceptionRule, ...] = (
    # MIT License の著作権表示の行
    ExceptionRule("LICENSE", re.compile(r"^Copyright \(c\) \d{4} ")),
    # NDF の承認の記録の approved_by の行
    ExceptionRule(".ndf/mvv.json", re.compile(r'^\s*"approved_by":\s*"')),
)


@dataclass(frozen=True, order=True)
class Hit:
    path: str
    line: int


@dataclass
class ScanResult:
    hits: list[Hit] = field(default_factory=list)
    excluded: int = 0
    scanned: int = 0


class TermsError(Exception):
    """語の一覧を読めない。本文は定型文で、一覧の中身や例外の文言を含めない。"""


class GitError(Exception):
    """git が使えない・リポジトリの外。本文は定型文。"""


def load_terms(text: str) -> frozenset[str]:
    """一覧の中身から、casefold 済みで重複を除いた語の組を返す。"""
    text = text.removeprefix("﻿")
    lines = (line.strip() for line in text.splitlines())
    return frozenset(line.casefold() for line in lines if line and not line.startswith("#"))


def default_terms_path(env: dict[str, str]) -> Path:
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return Path(base) / "devbase" / "proper-terms.txt"


def _read_terms_file(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        raise TermsError(f"語の一覧を読めない: {path}") from None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise TermsError(f"語の一覧が UTF-8 でない: {path}") from None


def terms_source(terms_arg: str | None, env: dict[str, str]) -> tuple[str | None, str]:
    """一覧の中身と、見た出所の説明を返す。出所が無ければ中身は None。"""
    if terms_arg is not None:
        path = Path(terms_arg)
        if not path.exists():
            raise TermsError(f"--terms で指定した語の一覧が無い: {path}")
        return _read_terms_file(path), f"--terms {path}"
    value = env.get(TERMS_ENV, "")
    if value:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise TermsError(f"{TERMS_ENV} が UTF-8 でない") from None
        return value, TERMS_ENV
    path = default_terms_path(env)
    seen = f"--terms なし・{TERMS_ENV} が空・既定の置き場 {path}"
    if not path.exists():
        return None, f"{seen} が無い"
    return _read_terms_file(path), seen


def _git(args: list[str], cwd: str | os.PathLike) -> bytes:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    except OSError:
        raise GitError("git を起動できない") from None
    if proc.returncode != 0:
        raise GitError("git のリポジトリの外で打った、または git が失敗した")
    return proc.stdout


def tracked_files(cwd: str | os.PathLike) -> tuple[Path, list[str]]:
    """リポジトリのトップと、追跡されたパスの並びを返す。"""
    top = Path(os.fsdecode(_git(["rev-parse", "--show-toplevel"], cwd)).rstrip("\n"))
    out = _git(["ls-files", "-z"], top)
    return top, [os.fsdecode(p) for p in out.split(b"\0") if p]


def _read_text(full: Path) -> str | None:
    """通常のファイルを UTF-8 で読む。読めない・通常のファイルでないなら None。"""
    if full.is_symlink() or not full.is_file():
        return None
    try:
        return full.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _is_hit(path: str, line: str, terms: frozenset[str], rules: tuple[ExceptionRule, ...]) -> bool:
    folded = line.casefold()
    if not any(term in folded for term in terms):
        return False
    return not any(rule.covers(path, line) for rule in rules)


def scan(root: Path, paths: list[str], terms: frozenset[str], rules: tuple[ExceptionRule, ...] = EXCEPTION_RULES) -> ScanResult:
    result = ScanResult()
    for path in paths:
        text = _read_text(root / path)
        if text is None:
            result.excluded += 1
            continue
        result.scanned += 1
        lines = (line.rstrip("\r") for line in text.split("\n"))
        result.hits.extend(Hit(path, n) for n, line in enumerate(lines, 1) if _is_hit(path, line, terms, rules))
    result.hits.sort()
    return result


def _escape_property(value: str) -> str:
    """GitHub のワークフローコマンドの属性の値のエスケープ。"""
    for src, dst in (("%", "%25"), ("\r", "%0D"), ("\n", "%0A"), (":", "%3A"), (",", "%2C")):
        value = value.replace(src, dst)
    return value


def _escape_data(value: str) -> str:
    for src, dst in (("%", "%25"), ("\r", "%0D"), ("\n", "%0A")):
        value = value.replace(src, dst)
    return value


class _Reporter:
    """出力の窓口。載せてよいもの (パス・行番号・件数・定型文) だけを受ける。"""

    def __init__(self, env: dict[str, str]):
        self.actions = env.get("GITHUB_ACTIONS") == "true"
        self.summary_path = env.get("GITHUB_STEP_SUMMARY") if self.actions else None

    def summary(self, body: str) -> None:
        if self.summary_path:
            with open(self.summary_path, "a", encoding="utf-8") as f:
                f.write(f"### {CHECK_TITLE}\n\n{body}\n")

    def error(self, reason: str) -> None:
        message = f"{CHECK_TITLE}: {reason}"
        print(message, file=sys.stderr)
        if self.actions:
            print(f"::error::{_escape_data(message)}")
        self.summary(reason)

    def skipped(self, seen: str) -> None:
        message = f"{CHECK_TITLE}: 語の一覧が無いため飛ばした（見た出所: {seen}）"
        print(message)
        if self.actions:
            print(f"::notice::{_escape_data(message)}")
        self.summary(message)

    def result(self, result: ScanResult, term_count: int) -> None:
        for hit in result.hits:
            print(f"{hit.path}:{hit.line}")
            if self.actions:
                print(
                    f"::error file={_escape_property(hit.path)},line={hit.line},title={CHECK_TITLE}::固有の語を含む行がある"
                )
        files = len({hit.path for hit in result.hits})
        line = (
            f"{CHECK_TITLE}: 当たり {len(result.hits)} 件（{files} ファイル）。"
            f"語 {term_count} 語・対象 {result.scanned} ファイル・外したファイル {result.excluded} 件"
        )
        print(line)
        body = line
        if result.hits:
            listing = "\n".join(f"{hit.path}:{hit.line}" for hit in result.hits)
            body = f"{line}\n\n```text\n{listing}\n```"
        self.summary(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="追跡されたファイルから固有の語を含む行を探す")
    parser.add_argument("--terms", metavar="PATH", help="語の一覧のファイル (既定の出所より先に使う)")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    out = _Reporter(env)
    try:
        text, seen = terms_source(args.terms, env)
        terms = load_terms(text) if text is not None else frozenset()
        if not terms:
            out.skipped(seen if text is None else f"{seen}（語が 0 語）")
            return EXIT_OK
        root, paths = tracked_files(os.getcwd())
    except (TermsError, GitError) as e:
        out.error(str(e))
        return EXIT_ERROR
    result = scan(root, paths, terms)
    out.result(result, len(terms))
    return EXIT_HITS if result.hits else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
