"""グループ名の例 (#351)。エラー文・使い方の文言は groups.EXAMPLE_GROUP の 1 か所から例を出す"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from devbase.env import groups
from devbase.errors import DevbaseError
from devbase.volume.manager import validate_account_group

#: 定義を差し替えた値。5 か所の文言がこの値を出せば、定義を読んでいる
SWAPPED = 'zeta-example'


@pytest.fixture
def swapped(monkeypatch):
    monkeypatch.setattr(groups, 'EXAMPLE_GROUP', SWAPPED)
    return SWAPPED


def test_the_example_passes_the_group_name_check():
    """I3: 例の名前は予約語・数字だけ・使えない文字ではない"""
    assert validate_account_group(groups.EXAMPLE_GROUP) == groups.EXAMPLE_GROUP


def test_the_missing_declaration_shows_the_example(tmp_path, swapped):
    """M1: 宣言が無いときの書き方の例"""
    (tmp_path / 'projects' / 'api').mkdir(parents=True)
    (tmp_path / 'projects' / 'api' / 'env').write_text('FOO=1\n')

    with pytest.raises(groups.GroupDeclarationError) as exc:
        groups.declare(tmp_path, 'api')
    assert f'({swapped} / personal など)' in str(exc.value)


def test_the_reserved_default_shows_the_example(swapped):
    """M2: 予約語 default を拒むときの移し先の例"""
    with pytest.raises(DevbaseError) as exc:
        validate_account_group('default')
    assert f'({swapped} / personal など)' in str(exc.value)


def test_env_outside_a_project_shows_the_example(tmp_path, monkeypatch, swapped):
    """M3: env をプロジェクトの外で --group 無しに打ったときの例"""
    from devbase.commands import env

    monkeypatch.setattr(env, '_current_project_name', lambda root: None)

    with pytest.raises(env.GroupOptionError) as exc:
        env._target_group(tmp_path, SimpleNamespace(grouped=True), None)
    assert f'(例: --group {swapped})' in str(exc.value)


def test_snapshot_outside_a_project_shows_the_example(tmp_path, monkeypatch, caplog, swapped):
    """M4: snapshot create をプロジェクトの外で --group 無しに打ったときの例"""
    from devbase.commands import snapshot

    monkeypatch.setenv('PWD', str(tmp_path))
    monkeypatch.chdir(tmp_path)

    assert snapshot._create_group(tmp_path, None) == (None, snapshot.EXIT_USAGE)
    assert f'(例: --group {swapped})' in caplog.text


def test_migrate_volume_without_to_shows_the_example(tmp_path, caplog, swapped):
    """M5: project migrate-volume を --to 無しに打ったときの例"""
    from devbase.commands.project import cmd_project_migrate_volume

    assert cmd_project_migrate_volume(tmp_path, SimpleNamespace(to=None, dry_run=False)) == 2
    assert f'(例: --to {swapped})' in caplog.text
