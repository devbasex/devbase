"""plugin.yml の requires.devbase の検証 (インストール時の互換チェック)"""

from __future__ import annotations

import pytest

from devbase.errors import PluginError
from devbase.plugin.models import PluginInfo
from devbase.plugin.requirements import (
    check_devbase_requirement,
    warn_unmet_devbase_requirement,
)


def info(requires=None, name="carmo-web") -> PluginInfo:
    return PluginInfo(name=name, version="1.0.0", requires_devbase=requires)


# ---------------------------------------------------------------------------
# 満たしている場合は何も起きない
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spec,current", [
    (">=3.0.0", "3.0.0"),
    (">=3.0.0", "3.1.2"),
    (">=3.0.0", "10.0.0"),      # 数値として比較する (文字列比較だと 10 < 3 になる)
    (">=3.0", "3.0.0"),         # 桁数が違っても比較できる
    (">3.0.0", "3.0.1"),
    ("<=3.0.0", "3.0.0"),
    ("<4.0.0", "3.9.9"),
    ("==3.0.0", "3.0.0"),
    ("3.0.0", "3.0.0"),         # 演算子なしは == 扱い
    (">=3.0.0,<4.0.0", "3.5.0"),
    (">= 3.0.0", "3.0.0"),      # 空白を許す
    (">=3.0.0.1", "3.0.0.1"),   # 4 要素以上でも比較できる
    (">=3.0.0.1", "3.0.1"),
])
def test_satisfied_requirements_pass(spec, current):
    check_devbase_requirement(info(spec), current_version=current)


@pytest.mark.parametrize("requires", [None, "", "   "])
def test_missing_requirement_is_not_checked(requires):
    """requires を書いていないプラグインは従来どおり素通しする"""
    check_devbase_requirement(info(requires), current_version="1.0.0")


def test_no_plugin_info_is_not_checked():
    check_devbase_requirement(None, current_version="1.0.0")


# ---------------------------------------------------------------------------
# 満たしていない場合はエラー
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spec,current", [
    (">=3.0.0", "2.2.0"),
    (">3.0.0", "3.0.0"),
    ("<3.0.0", "3.0.0"),
    ("==3.0.0", "3.0.1"),
    (">=3.0.0,<4.0.0", "4.0.0"),
    (">=3.0.0.1", "3.0.0"),     # 4 要素目を切り捨てると見逃す
    ("<3.0.0.1", "3.0.0.2"),
])
def test_unsatisfied_requirement_raises(spec, current):
    with pytest.raises(PluginError) as excinfo:
        check_devbase_requirement(info(spec), current_version=current)

    message = str(excinfo.value)
    assert "carmo-web" in message      # どのプラグインか
    assert spec in message             # 要求
    assert current in message          # 現在の版


def test_error_message_explains_how_to_proceed():
    with pytest.raises(PluginError, match="devbase"):
        check_devbase_requirement(info(">=3.0.0"), current_version="2.2.0")


# ---------------------------------------------------------------------------
# 解釈できない指定は落とさず警告に留める
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spec", ["~=3.0", "^3.0.0", "3.x", "latest", ">=abc"])
def test_unparsable_spec_warns_and_passes(spec, caplog):
    """独自記法でインストールを止めない (検証できないことを知らせるだけ)"""
    check_devbase_requirement(info(spec), current_version="3.0.0")

    assert any("requires" in r.message.lower() or "解釈" in r.message
               for r in caplog.records)


def test_unparsable_current_version_warns_and_passes(caplog):
    check_devbase_requirement(info(">=3.0.0"), current_version="dev")

    assert caplog.records


# ---------------------------------------------------------------------------
# 迂回手段
# ---------------------------------------------------------------------------

def test_env_override_skips_the_check(monkeypatch):
    """検証自体が誤っているときに詰まらないための逃げ道"""
    monkeypatch.setenv("DEVBASE_IGNORE_PLUGIN_REQUIRES", "1")

    check_devbase_requirement(info(">=3.0.0"), current_version="2.2.0")


def test_current_version_defaults_to_the_running_devbase():
    """引数を省略したら devbase 自身の版を見る"""
    from devbase import __version__

    check_devbase_requirement(info(f">={__version__}"))

    with pytest.raises(PluginError):
        check_devbase_requirement(info(f">{__version__}"))


# ---------------------------------------------------------------------------
# インストール経路への組み込み
# ---------------------------------------------------------------------------

def write_plugin(tmp_path, requires: str):
    plugin = tmp_path / "sample-plugin"
    (plugin / "projects").mkdir(parents=True)
    (plugin / "plugin.yml").write_text(
        f'name: sample-plugin\nversion: "1.0.0"\nrequires:\n  devbase: "{requires}"\n')
    return plugin


def test_link_install_refuses_an_incompatible_plugin(tmp_path, monkeypatch):
    from devbase.plugin import installer

    monkeypatch.setattr("devbase.__version__", "2.2.0", raising=False)
    plugin = write_plugin(tmp_path, ">=99.0.0")
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    with pytest.raises(PluginError, match="sample-plugin"):
        installer._link_plugin(None, "sample-plugin", plugin, "local", plugins_dir)

    assert list(plugins_dir.iterdir()) == []


def test_link_install_keeps_the_existing_plugin_when_refused(tmp_path, monkeypatch):
    """入れ替えに失敗しても、既に入っているプラグインを壊さない"""
    from devbase.plugin import installer

    plugin = write_plugin(tmp_path, ">=99.0.0")
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    existing = plugins_dir / "sample-plugin"
    existing.mkdir()
    (existing / "plugin.yml").write_text('name: sample-plugin\n')

    with pytest.raises(PluginError):
        installer._link_plugin(None, "sample-plugin", plugin, "local", plugins_dir)

    assert (existing / "plugin.yml").exists()


# ---------------------------------------------------------------------------
# plugin.yml の型ゆれ (クォート無しの版は YAML が数値にする)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("requires", [3.0, 3, ">=3.0"])
def test_numeric_requirement_does_not_crash(requires):
    """`devbase: 3.0` とクォート無しで書かれても AttributeError にしない"""
    check_devbase_requirement(info(requires), current_version="3.0.0")


def test_numeric_requirement_is_still_compared():
    with pytest.raises(PluginError):
        check_devbase_requirement(info(4.0), current_version="3.0.0")


def test_plugin_yml_numeric_requirement_is_loaded_as_str(tmp_path):
    from devbase.plugin.syncer import load_plugin_info

    plugin = tmp_path / "sample-plugin"
    plugin.mkdir()
    (plugin / "plugin.yml").write_text(
        "name: sample-plugin\nversion: \"1.0.0\"\nrequires:\n  devbase: 3.0\n")

    loaded = load_plugin_info(plugin)

    assert loaded.requires_devbase == "3.0"
    check_devbase_requirement(loaded, current_version="3.0.0")


# ---------------------------------------------------------------------------
# 迂回手段の値ゆれ
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["0", "false", "FALSE", "No", "no", " ", ""])
def test_env_override_is_case_insensitive_and_off_by_default(value, monkeypatch):
    """FALSE / No のような書き方で検証が黙って無効にならない"""
    monkeypatch.setenv("DEVBASE_IGNORE_PLUGIN_REQUIRES", value)

    with pytest.raises(PluginError):
        check_devbase_requirement(info(">=3.0.0"), current_version="2.2.0")


# ---------------------------------------------------------------------------
# 更新時は止められないので警告に留める
# ---------------------------------------------------------------------------

def test_warn_variant_does_not_raise(caplog):
    warn_unmet_devbase_requirement(info(">=99.0.0"), current_version="3.0.0")

    assert any("carmo-web" in r.getMessage() for r in caplog.records)


def test_warn_variant_is_silent_when_satisfied(caplog):
    warn_unmet_devbase_requirement(info(">=3.0.0"), current_version="3.0.0")

    assert not caplog.records


def test_update_warns_when_the_pulled_plugin_needs_a_newer_devbase(
        tmp_path, monkeypatch, caplog):
    """git pull で要求が上がっても更新自体は通し、警告で気づけるようにする"""
    import yaml

    from devbase.plugin.models import InstalledPlugin
    from devbase.plugin.registry import PluginRegistry
    from devbase.plugin.updater import _update_repo_plugins

    monkeypatch.setattr("devbase.__version__", "2.2.0", raising=False)
    (tmp_path / "projects").mkdir()
    registry = PluginRegistry(tmp_path)

    url = "https://github.com/testorg/testrepo.git"
    clone_dir = tmp_path / "repos" / "github.com--testorg--testrepo"
    plugin_dir = clone_dir / "sample-plugin"
    plugin_dir.mkdir(parents=True)
    (clone_dir / "registry.yml").write_text(yaml.dump({
        "name": "testrepo",
        "plugins": [{"name": "sample-plugin", "path": "sample-plugin",
                     "description": ""}],
    }))
    (plugin_dir / "plugin.yml").write_text(
        'name: sample-plugin\nversion: "2.0.0"\nrequires:\n  devbase: ">=99.0.0"\n')

    registry.add(InstalledPlugin(
        name="sample-plugin", version="1.0.0", source=url,
        installed_at=registry.now_iso(),
        path="repos/github.com--testorg--testrepo/sample-plugin",
    ))

    errors = _update_repo_plugins(registry, url, clone_dir)

    assert errors == []                                   # 更新は止めない
    assert registry.get("sample-plugin").version == "2.0.0"
    assert any("99.0.0" in r.getMessage() for r in caplog.records)
