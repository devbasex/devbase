"""CHANGELOG の検査 (#335)。

main 宛ての Pull Request で、見張るパスを変えて CHANGELOG.md を変えていないときに
未記入の警告を出す。警告を出してもジョブは失敗にしない。差分を得られないときだけ失敗にする。

CI (`.github/workflows/ci.yml` の `changelog` ジョブ) から次の環境変数を与えて起動する:
  BASE_REF  宛先ブランチの名前 (github.base_ref)
  HEAD_SHA  Pull Request の head の commit (github.event.pull_request.head.sha)
"""

from __future__ import annotations

import os
import subprocess
import sys

CHANGELOG = "CHANGELOG.md"

# この検査の名前。エラー・通常出力・サマリーの見出しに使う
CHECK_TITLE = "CHANGELOG の検査"

# 見張るパス。利用者に見える変更の手がかりとして見る場所。ここだけで定義する (テストもここを読む)
WATCHED_PATHS: tuple[str, ...] = ("lib/", "bin/", "containers/", "etc/", "install.sh")


def is_watched(path: str) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in WATCHED_PATHS)


def unrecorded_changes(paths: list[str]) -> list[str]:
    """未記入の警告で示すファイル。CHANGELOG.md を変えていれば、または見張るパスの変更が無ければ空。"""
    if CHANGELOG in paths:
        return []
    return sorted(p for p in paths if is_watched(p))


def warning_message(files: list[str]) -> str:
    return (
        f"利用者に見える変更なら、この Pull Request で {CHANGELOG} の [Unreleased] を更新してください。"
        f" 見張るパスの変更: {', '.join(files)}"
    )


def _git(args: list[str], failure: str) -> str:
    """git を実行して標準出力を返す。失敗したら failure と stderr を添えて RuntimeError。"""
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{failure}: {proc.stderr.strip()}")
    return proc.stdout


def changed_paths(base_ref: str, head_sha: str) -> list[str]:
    """宛先ブランチとの merge base から head までに変えたパス。得られなければ RuntimeError。"""
    _git(
        ["fetch", "--no-tags", "origin", f"+refs/heads/{base_ref}:refs/remotes/origin/{base_ref}"],
        f"宛先ブランチ {base_ref} を取れない",
    )
    diff = _git(["diff", "--name-only", f"origin/{base_ref}...{head_sha}"], f"origin/{base_ref}...{head_sha} の差分を取れない")
    return [line for line in diff.splitlines() if line]


def _summary(body: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"### {CHECK_TITLE}\n\n{body}\n")


def main() -> int:
    base_ref = os.environ.get("BASE_REF", "")
    head_sha = os.environ.get("HEAD_SHA", "")
    if not base_ref or not head_sha:
        print(f"::error::{CHECK_TITLE}: BASE_REF と HEAD_SHA が要る (pull_request のイベントで起動する)")
        return 1
    try:
        paths = changed_paths(base_ref, head_sha)
    except RuntimeError as e:
        print(f"::error::{CHECK_TITLE}: {e}")
        return 1
    files = unrecorded_changes(paths)
    if files:
        message = warning_message(files)
        print(f"::warning title=CHANGELOG 未記入::{message}")
        _summary(message)
    else:
        print(f"{CHECK_TITLE}: 未記入の警告なし")
        _summary("未記入の警告なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
