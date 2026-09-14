"""plan_env_merge の現状固定テスト (3 モードの分類とバイト出力を固定)

_import_merge の内部集計 (added / overwritten / skipped) を引数オブジェクトへ
まとめる構造改善 (R2-002) の前に、現行の分類結果とシリアライズ結果をそのまま
記録しておく。正しさを主張するのではなく、書き換え後も同じ結果が出ることを
確かめるためのもの。
"""

from __future__ import annotations

from pathlib import Path

from devbase.env._import_merge import plan_env_merge


def _plan(existing: str, incoming: str, tmp_path: Path, **kwargs):
    target = tmp_path / ".env"
    target_exists = existing is not None
    if target_exists:
        target.write_text(existing)
    return plan_env_merge(
        target,
        incoming.encode("utf-8"),
        arcname="env/global.env",
        existing_bytes=(existing.encode("utf-8") if target_exists else None),
        target_exists=target_exists,
        **kwargs,
    )


# --- keep-existing -------------------------------------------------------

def test_keep_existing_classification(tmp_path):
    existing = "SAME=1\nDIFF=old\n"
    incoming = "SAME=1\nDIFF=new\nNEW=2\n"
    plan = _plan(existing, incoming, tmp_path, merge="keep-existing")

    assert plan.op == "merge"
    assert plan.added_keys == ["NEW"]
    # keep-existing は既存キーを一切上書きしない。値が違っても skipped 扱い。
    assert plan.overwritten_keys == []
    assert plan.skipped_keys == ["DIFF", "SAME"]
    assert plan.new_bytes == b"SAME=1\nDIFF=old\nNEW=2\n"


def test_keep_existing_empty_incoming(tmp_path):
    existing = "A=1\n"
    plan = _plan(existing, "", tmp_path, merge="keep-existing")
    assert plan.op == "merge"
    assert plan.added_keys == []
    assert plan.overwritten_keys == []
    assert plan.skipped_keys == []
    assert plan.new_bytes == b"A=1\n"


# --- prefer-incoming -----------------------------------------------------

def test_prefer_incoming_classification(tmp_path):
    existing = "SAME=1\nDIFF=old\n"
    incoming = "SAME=1\nDIFF=new\nNEW=2\n"
    plan = _plan(existing, incoming, tmp_path, merge="prefer-incoming")

    assert plan.op == "merge"
    assert plan.added_keys == ["NEW"]
    # 値が同じ既存キー (SAME) は overwritten に入らない。値が違う DIFF のみ。
    assert plan.overwritten_keys == ["DIFF"]
    assert plan.skipped_keys == []
    assert plan.new_bytes == b"SAME=1\nDIFF=new\nNEW=2\n"


# --- replace-keys --------------------------------------------------------

def test_replace_keys_classification(tmp_path):
    existing = "SAME=1\nDIFF=old\nKEEP=old\n"
    incoming = "SAME=1\nDIFF=new\nKEEP=new\nNEW=2\n"
    # DIFF と SAME を replace 対象に、KEEP は非対象 (keep-existing 相当)。
    plan = _plan(existing, incoming, tmp_path, replace_keys=["DIFF", "SAME"])

    assert plan.op == "merge"
    # NEW は指定外の新規キー -> added。DIFF は指定 & 値違い -> overwritten。
    # SAME は指定 & 値同一 -> どこにも入らない。KEEP は非指定 & 値違い -> skipped。
    assert plan.added_keys == ["NEW"]
    assert plan.overwritten_keys == ["DIFF"]
    assert plan.skipped_keys == ["KEEP"]
    assert plan.new_bytes == b"SAME=1\nDIFF=new\nKEEP=old\nNEW=2\n"


def test_replace_keys_new_key_in_replace_set(tmp_path):
    existing = "A=1\n"
    incoming = "A=1\nB=2\n"
    # B は指定 & 既存に無い -> added、merged にも入る。
    plan = _plan(existing, incoming, tmp_path, replace_keys=["B"])
    assert plan.added_keys == ["B"]
    assert plan.overwritten_keys == []
    assert plan.skipped_keys == []
    assert plan.new_bytes == b"A=1\nB=2\n"


# --- 新規作成 (target 不在) ---------------------------------------------

def test_create_when_target_absent(tmp_path):
    incoming = "A=1\nB=2\n"
    target = tmp_path / ".env"
    plan = plan_env_merge(
        target,
        incoming.encode("utf-8"),
        arcname="env/global.env",
        merge="keep-existing",
        existing_bytes=b"",
        target_exists=False,
    )
    assert plan.op == "create"
    # 新規作成では incoming_bytes をそのまま採用する。
    assert plan.new_bytes == incoming.encode("utf-8")
    # existing が空なので keep-existing 経路では全キーが added に入る
    # (現行の分類。new_bytes には影響しない)。
    assert plan.added_keys == ["A", "B"]
    assert plan.overwritten_keys == []
    assert plan.skipped_keys == []


def test_replace_mode_classification(tmp_path):
    existing = "SAME=1\nDIFF=old\n"
    incoming = "SAME=1\nDIFF=new\nNEW=2\n"
    plan = _plan(existing, incoming, tmp_path, replace=True)
    assert plan.op == "replace"
    assert plan.added_keys == ["NEW"]
    assert plan.overwritten_keys == ["DIFF"]
    assert plan.skipped_keys == []
    assert plan.new_bytes == incoming.encode("utf-8")
