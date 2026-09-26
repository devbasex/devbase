"""shellcheck の指示が理由を持つかの規則 (bin/・install.sh と containers/base/ で共有する)。

指示は同じ行の後ろの `# 理由` か、直前の行の指示でないコメントを理由として持つ。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_DIRECTIVE = re.compile(r"^\s*#\s*shellcheck\s+\w+=")


def _is_reason(prev: str) -> bool:
    s = prev.strip()
    if not s.startswith("#") or s.startswith("#!") or _DIRECTIVE.match(prev):
        return False
    return bool(s.lstrip("#").strip())


def directives_without_reason(lines: list[str]) -> list[int]:
    found = []
    for i, line in enumerate(lines):
        if not _DIRECTIVE.match(line):
            continue
        body = line.split("shellcheck", 1)[1]
        if re.search(r"\s#\s*\S", body):
            continue
        if i > 0 and _is_reason(lines[i - 1]):
            continue
        found.append(i + 1)
    return found
