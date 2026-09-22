"""読み替えのあるグループでの見出しの表示 (PLAN64 / #188)

`group_aliases` のある置き場では、見出しのグループ名が読み替えの前と後
(`default → nyle`) になり、隣に並ぶパスと同じグループを指していると読める。
読み替えの対応が無いグループ・`version: 1` ・ファイル backend の出力は変わらない。
"""

from __future__ import annotations

import pytest

from devbase.commands import env as env_cmd
from devbase.commands import env_backend
from devbase.env import backend_config as bc


ALIASES = {'default': 'nyle'}
BOTH = 'default → nyle'


def at(monkeypatch, root, rel=''):
    monkeypatch.setenv('PWD', str(root / rel) if rel else str(root))


@pytest.fixture
def aliased(openbao_root, openbao):
    """``version: 2`` で ``default`` を ``nyle`` へ読み替える置き場 (``web`` は宣言なし)"""
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, layout=bc.LAYOUT_GROUP, group_aliases=ALIASES)
    return openbao_root


# ---------------------------------------------------------------------------
# 受け入れ条件 1: env backend test
# ---------------------------------------------------------------------------

def test_backend_test_headings_show_both_names_next_to_the_path(aliased, openbao, monkeypatch,
                                                                capsys):
    openbao.put('team/nyle/global', {'A': '1'})
    openbao.put('users/member01/nyle/global', {'B': '2'})
    at(monkeypatch, aliased, 'projects/web')

    assert env_backend.cmd_env_backend_test(aliased) == 0

    lines = capsys.readouterr().out.splitlines()
    for label, path in ((f'グローバル（グループ {BOTH}）', 'devbase/team/nyle/global'),
                        (f'個人のグローバル（グループ {BOTH}）',
                         'devbase/users/member01/nyle/global'),
                        (f"プロジェクト 'web'（グループ {BOTH}）",
                         'devbase/team/nyle/projects/web')):
        # 見出しとパスが同じ行に並び、同じグループを指していることを見る (#188)
        row = [line for line in lines if line.startswith(f'  {label} ')]
        assert len(row) == 1, label
        assert path in row[0]


def test_backend_test_only_reads_the_current_group_and_labels_skipped_projects(
        aliased, openbao, monkeypatch, capsys):
    """現状固定: with の参照を表示し、別の置き場の api は読み替え名で案内する。"""
    (aliased / 'projects' / 'api').mkdir()
    (aliased / 'projects' / 'web' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=with\n')
    openbao.put('team/nyle/global', {'A': '1'})
    openbao.put('team/nyle/projects/api', {'B': '2'})
    openbao.put('users/member01/nyle/projects/api', {'C': '3'})
    openbao.put('team/with/global', {'D': '4'})
    openbao.put('users/member01/with/global', {'E': '5'})
    openbao.put('team/with/projects/web', {'F': '6'})
    openbao.put('users/member01/with/projects/web', {'G': '7'})
    at(monkeypatch, aliased, 'projects/web')

    assert env_backend.cmd_env_backend_test(aliased) == 0

    out = capsys.readouterr().out
    skipped, read = out.split('読めた参照:', 1)
    skipped_rows = [line for line in skipped.splitlines() if '調べていません' in line]
    assert len(skipped_rows) == 1
    assert 'api' in skipped_rows[0]
    assert 'nyle' in skipped_rows[0]
    assert 'api' not in read
    for label, path in (
            ('グローバル（グループ with）', 'devbase/team/with/global'),
            ('個人のグローバル（グループ with）', 'devbase/users/member01/with/global'),
            ("プロジェクト 'web'（グループ with）", 'devbase/team/with/projects/web'),
            ("個人のプロジェクト 'web'（グループ with）",
             'devbase/users/member01/with/projects/web')):
        rows = [line for line in read.splitlines() if line.startswith(f'  {label} ')]
        assert len(rows) == 1, label
        assert path in rows[0]


# ---------------------------------------------------------------------------
# 受け入れ条件 2: env list
# ---------------------------------------------------------------------------

def test_list_headings_and_counts_show_both_names(aliased, openbao, monkeypatch, capsys):
    openbao.put('team/nyle/global', {'A': '1'})
    openbao.put('users/member01/nyle/global', {'B': '2'})
    openbao.put('team/nyle/projects/web', {'C': '3'})
    openbao.put('users/member01/nyle/projects/web', {'D': '4'})
    at(monkeypatch, aliased, 'projects/web')

    assert env_cmd.cmd_env_list(aliased, keys_only=True) == 0

    out = capsys.readouterr().out
    for heading in (f'=== グローバル（グループ {BOTH}） (',
                    f'=== 個人のグローバル（グループ {BOTH}） (',
                    f'=== プロジェクト: web（グループ {BOTH}） (',
                    f'=== 個人のプロジェクト: web（グループ {BOTH}） ('):
        assert heading in out
    for count in (f'グローバル（グループ {BOTH}）: 1変数',
                  f'個人のグローバル（グループ {BOTH}）: 1変数',
                  f'プロジェクト（グループ {BOTH}）: 1変数',
                  f'個人のプロジェクト（グループ {BOTH}）: 1変数'):
        assert count in out


# ---------------------------------------------------------------------------
# 受け入れ条件 3: env backend migrate の 2 つの一覧 (決定 4)
# ---------------------------------------------------------------------------

def test_migration_plan_listing_shows_both_names(aliased, openbao, capsys):
    """移行の計画の一覧 (``_MigrationPlan._heading``)"""
    openbao.put('team/nyle/global', {'A': '1'})

    assert env_backend.cmd_env_backend_migrate(aliased, to='age', dry_run=True) == 0

    out = capsys.readouterr().out
    assert f'グローバル（グループ {BOTH}）' in out
    assert 'devbase/team/nyle/global' in out


def test_completion_listing_after_migrating_to_age_shows_both_names(aliased, openbao, capsys):
    """``--to age`` の完了後の「サーバ上の機密はそのまま残っています」の一覧"""
    openbao.put('team/nyle/global', {'A': '1'})

    assert env_backend.cmd_env_backend_migrate(aliased, to='age', assume_yes=True) == 0

    out = capsys.readouterr().out
    tail = out[out.index('サーバ上の機密はそのまま残っています'):]
    assert f'グローバル（グループ {BOTH}）' in tail
    assert 'devbase/team/nyle/global' in tail


# ---------------------------------------------------------------------------
# 受け入れ条件 4: 読み替えの対応が無いグループ
# ---------------------------------------------------------------------------

def test_a_group_without_an_alias_keeps_its_name(aliased, openbao, monkeypatch, capsys):
    (aliased / 'projects' / 'web' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=kkg\n')
    openbao.put('team/kkg/global', {'A': '1'})
    at(monkeypatch, aliased, 'projects/web')

    assert env_cmd.cmd_env_list(aliased, keys_only=True) == 0
    assert env_backend.cmd_env_backend_test(aliased) == 0

    out = capsys.readouterr().out
    assert '（グループ kkg）' in out
    assert '→' not in out


# ---------------------------------------------------------------------------
# 受け入れ条件 5・6: 変わらないこと
# ---------------------------------------------------------------------------

def test_version_one_shows_no_group_at_all(openbao_root, openbao, monkeypatch, capsys):
    """``version: 1`` (``layout: flat``) の見出しにはグループが付かない"""
    openbao.put('team/global', {'A': '1'})
    at(monkeypatch, openbao_root, 'projects/web')

    assert env_cmd.cmd_env_list(openbao_root, keys_only=True) == 0
    assert env_backend.cmd_env_backend_test(openbao_root) == 0

    out = capsys.readouterr().out
    assert '=== グローバル (' in out
    assert '（グループ' not in out


def test_a_file_backend_with_a_leftover_openbao_section_shows_no_group(openbao_root, openbao,
                                                                      monkeypatch, capsys):
    """決定 3: ``backend: age`` に ``openbao:`` 節が残っていてもグループは出ない

    ``config.openbao`` はこの設定では ``None`` にならない。backend の種類を
    ``config.openbao is None`` で判定すると、この端末をグループ別の置き場として扱う。
    """
    from tests.conftest import configure_openbao

    settings = configure_openbao(openbao_root, openbao, layout=bc.LAYOUT_GROUP,
                                 group_aliases=ALIASES).openbao
    bc.save(openbao_root, bc.BackendConfig(backend='age', openbao=settings, version=2))
    assert bc.load(openbao_root).openbao is not None
    at(monkeypatch, openbao_root, 'projects/web')

    assert env_cmd.cmd_env_list(openbao_root, keys_only=True) == 0

    out = capsys.readouterr().out
    assert '=== グローバル (' in out
    assert '（グループ' not in out
