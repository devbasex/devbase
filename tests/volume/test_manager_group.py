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
# 既定の値を持たない (#315)
# ---------------------------------------------------------------------------

def test_unset_is_rejected_with_how_to_declare():
    """未設定なら既定のグループへ落ちずに止まり、宣言の書き方を示す。"""
    from devbase.env.groups import GroupDeclarationError

    with pytest.raises(GroupDeclarationError) as excinfo:
        manager.resolve_account_group()
    assert "DEVBASE_ACCOUNT_GROUP=" in str(excinfo.value)


def test_empty_and_whitespace_are_rejected(monkeypatch):
    """空文字・空白のみも未設定と同じく止まる (env に `KEY=` と書いた場合)。"""
    from devbase.env.groups import GroupDeclarationError

    for value in ("", "   ", "\t"):
        monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", value)
        with pytest.raises(GroupDeclarationError):
            manager.resolve_account_group()


def test_no_default_group_constant():
    """グループの既定の値を返す定数を持たない。旧既定のボリュームの名前だけを残す。"""
    assert not hasattr(manager, "DEFAULT_ACCOUNT_GROUP")
    assert manager.LEGACY_GROUP_VOLUME == "devbase_home_default"


def test_default_is_reserved():
    """`default` は予約語。移し先を書くよう示す。"""
    with pytest.raises(DevbaseError) as excinfo:
        manager.validate_account_group("default")
    message = str(excinfo.value)
    assert "default" in message
    assert "migrate-volume" in message


def test_validate_rejects_empty():
    with pytest.raises(DevbaseError):
        manager.validate_account_group("  ")


def test_explicit_none_reads_environment(monkeypatch):
    """引数省略時は環境変数を読む (前提 3: 3 レベルの解決結果が入っている)。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "globex")
    assert manager.resolve_account_group() == "globex"


def test_argument_wins_over_environment(monkeypatch):
    """引数が環境変数より優先される。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "globex")
    assert manager.resolve_account_group("initech") == "initech"


def test_surrounding_whitespace_is_stripped(monkeypatch):
    """env ファイル由来の前後空白は落とす。"""
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "  globex  ")
    assert manager.resolve_account_group() == "globex"


# ---------------------------------------------------------------------------
# 正常系
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("group", ["acme", "personal", "globex", "initech", "a", "a-b_c.d", "g1", "1g"])
def test_valid_group_names_are_accepted(group):
    assert manager.resolve_account_group(group) == group


def test_group_volume_name():
    assert manager.get_group_volume("globex") == "devbase_home_globex"


def test_group_volume_reads_environment(monkeypatch):
    monkeypatch.setenv("DEVBASE_ACCOUNT_GROUP", "personal")
    assert manager.get_group_volume() == "devbase_home_personal"


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
    "initech/slash",
    "initech:colon",
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


# ---------------------------------------------------------------------------
# Docker を触る前に弾く (AC7)
# ---------------------------------------------------------------------------

def test_ensure_volumes_rejects_bad_group_before_touching_docker(monkeypatch):
    """グループ名が不正なら Docker の状態を一切変えずに失敗する。

    検証が共有ボリュームの作成より後ろにあると、入力ミスだけで
    devbase_home_ubuntu が作られてしまう。
    """
    created: list[str] = []
    monkeypatch.setattr(
        manager.VolumeManager, "_volume_exists",
        lambda self, name: False)
    monkeypatch.setattr(
        manager.VolumeManager, "create_volume",
        lambda self, name: created.append(name) or True)

    with pytest.raises(DevbaseError):
        manager.VolumeManager().ensure_volumes(1, group="ubuntu")

    assert created == []


# ---------------------------------------------------------------------------
# グループの既定の値が残らない (#315)
# ---------------------------------------------------------------------------

def test_no_default_group_fallback_is_left_in_the_code():
    """lib/devbase と containers/ に、グループの既定として default を返す・渡すコードが無い。

    ``${GCP_ACTIVE_PROFILE:-default}`` は gcloud のプロファイル名でグループではないため除く。
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    pattern = re.compile(r'DEFAULT_ACCOUNT_GROUP|ACCOUNT_GROUP:-default|:-default\}')
    hits = []
    for base in (repo / 'lib' / 'devbase', repo / 'containers'):
        for path in base.rglob('*'):
            if not path.is_file() or path.suffix == '.pyc':
                continue
            try:
                text = path.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line) and 'GCP_ACTIVE_PROFILE' not in line:
                    hits.append(f'{path.relative_to(repo)}:{number}: {line.strip()}')
    assert hits == []
