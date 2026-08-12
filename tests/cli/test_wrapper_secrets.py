"""起動ラッパーが機密ファイルを読まないことの回帰テスト

暗号化の前提は「シェルから読める場所に機密を置かない」こと。ラッパーが
``$DEVBASE_ROOT/.env`` を ``source`` に戻ると、暗号化していても起動のたびに
平文が必要になり、方針そのものが崩れる (plan35 §4.4)。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / 'bin' / 'devbase'


def wrapper_lines():
    return [
        line for line in WRAPPER.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]


def test_wrapper_does_not_source_the_global_secret_file():
    sourced = [
        line for line in wrapper_lines()
        if re.search(r'source\s+"?\$\{?DEVBASE_ROOT\}?/\.env', line)
    ]
    assert sourced == [], (
        "起動ラッパーが共通の機密ファイルを source しています: " + repr(sourced))


def test_wrapper_sources_the_non_secret_settings():
    sourced = [
        line for line in wrapper_lines()
        if re.search(r'source\s+"?\$\{?DEVBASE_ROOT\}?/env"?', line)
    ]
    assert len(sourced) == 1, sourced


def test_compose_build_goes_through_the_secret_injection():
    """`docker compose build` は機密を必要としうるので env exec 経由で呼ぶ"""
    lines = wrapper_lines()
    direct = [line for line in lines
              if 'docker compose build' in line
              and 'compose_with_secrets' not in line]
    assert direct == [], ("機密注入を経ずに compose build を呼んでいます: "
                          + repr(direct))

    wrapped = [line for line in lines if 'compose_with_secrets docker compose build' in line]
    assert len(wrapped) == 3, wrapped


def test_secret_injection_helper_uses_env_exec():
    text = WRAPPER.read_text(encoding='utf-8')
    assert 'compose_with_secrets()' in text
    assert 'devbase.cli env exec --' in text
