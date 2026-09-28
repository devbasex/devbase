"""tests/ci で共有する `ci.yml` の置き場所と読み方。"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def load_workflow() -> dict:
    return yaml.safe_load(CI_YML.read_text())


def triggers(wf: dict) -> dict:
    # PyYAML は YAML 1.1 のため、キーの `on` を True として読む
    return wf[True] if True in wf else wf["on"]
