"""``EnvFile.render_updated_bytes`` の原文保持・変更行整形・新規キー順序の検証

import の merge 経路が依存している ``.env`` の表現規則を、``store`` 側の公開
メソッドとしてバイト列で固定する。
"""

from __future__ import annotations

from devbase.env.store import EnvFile


def test_preserves_comments_blank_lines_and_unchanged_raw_lines():
    existing = (
        b"# header comment\n"
        b"\n"
        b"PATH=$HOME/bin\n"
        b"  KEEP = 'single quoted' \n"
        b"not a kv line\n"
        b"TOKEN=old\n"
    )
    out = EnvFile.render_updated_bytes(existing, {
        'PATH': '$HOME/bin',
        'KEEP': 'single quoted',
        'TOKEN': 'old',
    })
    # 全キーが未変更なら原文と 1 バイトも変わらない
    assert out == existing


def test_reformats_only_changed_keys_in_place():
    existing = (
        b"# c\n"
        b"A=1\n"
        b"B=$HOME\n"
        b"C=3\n"
    )
    out = EnvFile.render_updated_bytes(existing, {
        'A': '1',
        'B': 'has space',
        'C': '3',
    })
    assert out == (
        b"# c\n"
        b"A=1\n"
        b'B="has space"\n'
        b"C=3\n"
    )


def test_appends_new_keys_sorted_after_existing_lines():
    existing = b"Z=1\n# tail comment\n"
    out = EnvFile.render_updated_bytes(existing, {
        'Z': '1',
        'M': 'm',
        'A': 'a',
    })
    assert out == (
        b"Z=1\n"
        b"# tail comment\n"
        b"A=a\n"
        b"M=m\n"
    )


def test_drops_keys_missing_from_values():
    existing = b"A=1\nB=2\n# keep\nC=3\n"
    out = EnvFile.render_updated_bytes(existing, {'A': '1', 'C': '3'})
    assert out == b"A=1\n# keep\nC=3\n"


def test_empty_existing_yields_sorted_values():
    out = EnvFile.render_updated_bytes(b"", {'B': 'x', 'A': 'y z'})
    assert out == b'A="y z"\nB=x\n'


def test_changed_value_uses_dump_bytes_formatting():
    existing = b"K=old\n"
    new_value = 'a "quoted" $val\nline2'
    out = EnvFile.render_updated_bytes(existing, {'K': new_value})
    assert out == EnvFile.dump_bytes({'K': new_value})
    # round-trip: 再パースすると元の値に戻る
    assert EnvFile.parse_bytes(out) == {'K': new_value}
