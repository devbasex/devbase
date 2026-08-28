"""compose.py: volume マウント置換の現状固定テスト (characterization)

`generate_scaled_compose()` が dev サービスの volumes をインスタンス別の
外部ボリュームへ置き換える現状の振る舞いを、公開入口経由でそのまま記録する。
期待値の根拠は仕様ではなく**現在の実装の出力**であり、内部表現の構造改善
(str/dict パースの値オブジェクト化など) で振る舞いが変わらないことを守る。
"""

from __future__ import annotations

import yaml
import pytest

from devbase.volume import compose


@pytest.fixture
def in_tmp_cwd(tmp_path, monkeypatch):
    """生成物 (.docker-compose.scale.yml) が散らからないよう CWD を tmp に移す。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEV_SERVICE_NAME", raising=False)
    return tmp_path


def _generate(tmp_path, volumes, scale=1):
    (tmp_path / "compose.yml").write_text(
        yaml.safe_dump(
            {"services": {"dev": {"image": "dev:latest", "volumes": volumes}}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    compose.generate_scaled_compose(scale=scale)
    return yaml.safe_load(
        (tmp_path / ".docker-compose.scale.yml").read_text())["services"]


def test_string_work_mount_source_is_replaced(in_tmp_cwd):
    """文字列形式の /work マウントは source がインスタンスの work volume になる。"""
    services = _generate(in_tmp_cwd, ["./src:/work"])
    assert services["dev-1"]["volumes"] == [
        "devbase_work_1:/work",
        "devbase_home_ubuntu:/persistent/ai",
    ]


def test_string_mount_options_are_preserved(in_tmp_cwd):
    """文字列形式の 3 要素目 (options) は置換後も保たれる。"""
    services = _generate(in_tmp_cwd, [
        "./src:/work:cached",
        "aivol:/persistent/ai:ro",
    ])
    assert services["dev-1"]["volumes"] == [
        "devbase_work_1:/work:cached",
        "devbase_home_ubuntu:/persistent/ai:ro",
    ]


def test_dict_mount_is_rewritten_to_volume_type(in_tmp_cwd):
    """dict 形式は source/type が書き換わり、その他のキーは保たれる。"""
    services = _generate(in_tmp_cwd, [
        {"type": "bind", "source": "./src", "target": "/work",
         "read_only": True},
    ])
    assert services["dev-1"]["volumes"] == [
        {"type": "volume", "source": "devbase_work_1", "target": "/work",
         "read_only": True},
        "devbase_home_ubuntu:/persistent/ai",
    ]


def test_deprecated_home_ubuntu_mounts_are_dropped(in_tmp_cwd):
    """/home/ubuntu への旧マウントは文字列・dict どちらの形式でも除かれる。"""
    services = _generate(in_tmp_cwd, [
        "./home:/home/ubuntu",
        {"type": "bind", "source": "./home2", "target": "/home/ubuntu"},
        "./src:/work",
    ])
    assert services["dev-1"]["volumes"] == [
        "devbase_work_1:/work",
        "devbase_home_ubuntu:/persistent/ai",
    ]


def test_unrelated_mounts_are_kept_as_is(in_tmp_cwd):
    """置換対象外の target を持つマウントは元の形のまま残る。"""
    services = _generate(in_tmp_cwd, [
        "./data:/data",
        {"type": "bind", "source": "./conf", "target": "/etc/conf"},
    ])
    assert services["dev-1"]["volumes"] == [
        "./data:/data",
        {"type": "bind", "source": "./conf", "target": "/etc/conf"},
        "devbase_home_ubuntu:/persistent/ai",
        "devbase_work_1:/work",
    ]


def test_target_less_entry_is_kept_as_is(in_tmp_cwd):
    """target を持たないエントリ (名前のみの volume 等) はそのまま残る。"""
    services = _generate(in_tmp_cwd, ["cache"])
    assert services["dev-1"]["volumes"] == [
        "cache",
        "devbase_home_ubuntu:/persistent/ai",
        "devbase_work_1:/work",
    ]


def test_missing_mounts_are_appended_when_volumes_absent(in_tmp_cwd):
    """volumes が空でも /persistent/ai と /work のマウントが補完される。"""
    services = _generate(in_tmp_cwd, [])
    assert services["dev-1"]["volumes"] == [
        "devbase_home_ubuntu:/persistent/ai",
        "devbase_work_1:/work",
    ]


def test_each_instance_gets_its_own_work_volume(in_tmp_cwd):
    """scale>1 では work volume だけがインスタンス別になり ai volume は共有。"""
    services = _generate(in_tmp_cwd, ["./src:/work"], scale=2)
    assert services["dev-1"]["volumes"] == [
        "devbase_work_1:/work",
        "devbase_home_ubuntu:/persistent/ai",
    ]
    assert services["dev-2"]["volumes"] == [
        "devbase_work_2:/work",
        "devbase_home_ubuntu:/persistent/ai",
    ]
