"""アカウントグループの解決と検証 (PLAN39 Task 1)

`DEVBASE_ACCOUNT_GROUP` は「使用する Google / AWS アカウントの単位」を宣言する
公開設定キー。解決結果はグループボリューム名 (`devbase_home_<group>`) になるため、
Docker のボリューム名として使えない文字列と、既存のボリューム名前空間
(`devbase_home_ubuntu` / `devbase_home_<index>`) と衝突する名前を起動前に弾く。
"""

from __future__ import annotations

import pytest

from devbase.errors import DevbaseError
from devbase.volume import manager


@pytest.fixture(autouse=True)
def _clean_group_env(monkeypatch):
    """外部環境の DEVBASE_ACCOUNT_GROUP に左右されないよう既定で未設定にする。"""
    monkeypatch.delenv("DEVBASE_ACCOUNT_GROUP", raising=False)


# ---------------------------------------------------------------------------
# フォールバック (AC5)
# ---------------------------------------------------------------------------

def test_unset_falls_back_to_default():
    """未設定なら default。既存プロジェクトは何も書かずに起動できる。"""
    assert manager.resolve_account_group() == "default"


def test_empty_and_whitespace_fall_back_to_default(monkeypatch):
    """空文字・空白のみも「未設定」として扱う (env に `KEY=` と書いた場合)。"""
    for value in ("", "   ", "\t"):
        monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", value)
        assert manager.resolve_account_group() == "default"


def test_explicit_none_reads_environment(monkeypatch):
    """引数省略時は環境変数を読む (前提 3: 3 レベルの解決結果が入っている)。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")
    assert manager.resolve_account_group() == "kkg"


def test_argument_wins_over_environment(monkeypatch):
    """引数が環境変数より優先される。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "kkg")
    assert manager.resolve_account_group("with") == "with"


def test_surrounding_whitespace_is_stripped(monkeypatch):
    """env ファイル由来の前後空白は落とす。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "  kkg  ")
    assert manager.resolve_account_group() == "kkg"


# ---------------------------------------------------------------------------
# 正常系
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("group", ["default", "kkg", "with", "a", "a-b_c.d", "g1", "1g"])
def test_valid_group_names_are_accepted(group):
    assert manager.resolve_account_group(group) == group


def test_group_volume_name():
    assert manager.get_group_volume("kkg") == "devbase_home_kkg"


def test_group_volume_falls_back_to_default():
    assert manager.get_group_volume() == "devbase_home_default"


def test_group_volume_validates_its_argument():
    """ボリューム名の生成でも検証を通す (検証を迂回する経路を作らない)。"""
    with pytest.raises(DevbaseError):
        manager.get_group_volume("ubuntu")


# ---------------------------------------------------------------------------
# 拒否ケース (AC7)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("group", [
    "-leading-hyphen",   # 先頭が英数字でない
    ".leading-dot",
    "_leading-underscore",
    "with space",
    "with/slash",
    "with:colon",
    "日本語",
    "bad!name",          # 記号を含む
])
def test_invalid_characters_are_rejected(group):
    with pytest.raises(DevbaseError) as excinfo:
        manager.resolve_account_group(group)
    # 何が悪いのか分かるメッセージにする
    assert group in str(excinfo.value)


def test_reserved_ubuntu_is_rejected():
    """`ubuntu` は共通ボリューム devbase_home_ubuntu と衝突する。"""
    with pytest.raises(DevbaseError) as excinfo:
        manager.resolve_account_group("ubuntu")
    message = str(excinfo.value)
    assert "ubuntu" in message
    assert manager.HOME_UBUNTU_VOLUME in message


@pytest.mark.parametrize("group", ["1", "2", "042"])
def test_numeric_only_is_rejected(group):
    """数字のみは devbase_home_<index> と衝突する (前提 6)。"""
    with pytest.raises(DevbaseError) as excinfo:
        manager.resolve_account_group(group)
    assert "devbase_home_" in str(excinfo.value)


def test_invalid_environment_value_is_rejected(monkeypatch):
    """環境変数経由でも同じ検証が効く (起動前に弾く)。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "ubuntu")
    with pytest.raises(DevbaseError):
        manager.resolve_account_group()


# ---------------------------------------------------------------------------
# 死んだ API の削除 (前提 6)
# ---------------------------------------------------------------------------

def test_ai_volume_prefix_is_gone():
    """未使用の AI_VOLUME_PREFIX は削除済み。命名系統を 2 つ並べない。"""
    assert not hasattr(manager, "AI_VOLUME_PREFIX")
