"""参照の表示の契約と、見出し用の表示の口 (PLAN64)

`SecretRef.label()` の既定は読み替える**前**のグループ名のままで、読み替えの前後
(`default → nyle`) を出すのは `SecretStore.display_label` を通った見出しだけである
(設計の決定 1・2・3)。
"""

from __future__ import annotations

import logging

import pytest

from devbase.env import backend_config as bc
from devbase.env.secret_store import SecretRef, SecretStore, SecretStoreError


ALIASES = {'default': 'nyle'}


def _settings(*, layout: str, aliases=None) -> bc.OpenBaoSettings:
    return bc.OpenBaoSettings(url='http://127.0.0.1:8200', user='member01', layout=layout,
                              group_aliases=dict(aliases or {}))


def _store(tmp_path, *, backend: str, layout: str, aliases=None) -> SecretStore:
    """設定を直接渡した店 (``secrets/backend.yml`` を読まない)"""
    version = 2 if layout == bc.LAYOUT_GROUP else 1
    config = bc.BackendConfig(backend=backend, openbao=_settings(layout=layout, aliases=aliases),
                              version=version)
    return SecretStore(tmp_path, config=config)


# ---------------------------------------------------------------------------
# SecretRef.label の契約
# ---------------------------------------------------------------------------

def test_label_without_arguments_keeps_the_group_before_the_alias():
    """決定 2: 引数なしの既定は読み替える前の名前"""
    assert SecretRef.for_global(group='default').label() == 'グローバル（グループ default）'


@pytest.mark.parametrize('ref, expected', [
    (SecretRef.for_global(group='default'), 'グローバル（グループ default → nyle）'),
    (SecretRef.for_global(owner='user', group='default'),
     '個人のグローバル（グループ default → nyle）'),
    (SecretRef.for_project('web', group='default'),
     "プロジェクト 'web'（グループ default → nyle）"),
    (SecretRef.for_project('web', owner='user', group='default'),
     "個人のプロジェクト 'web'（グループ default → nyle）"),
])
def test_label_puts_group_display_inside_the_parentheses(ref, expected):
    """決定 1: 括弧の中に入れる名前だけを外から受け、文言は label が持つ"""
    assert ref.label(group_display='default → nyle') == expected


@pytest.mark.parametrize('ref', [
    SecretRef.for_global(),
    SecretRef.for_global(owner='user'),
    SecretRef.for_project('web'),
])
def test_label_ignores_group_display_when_the_reference_has_no_group(ref):
    assert ref.label(group_display='default → nyle') == ref.label()
    assert '（グループ' not in ref.label(group_display='default → nyle')


# ---------------------------------------------------------------------------
# SecretStore.display_label の分岐
# ---------------------------------------------------------------------------

def test_display_label_shows_both_names_when_the_group_is_aliased(tmp_path):
    store = _store(tmp_path, backend='openbao', layout=bc.LAYOUT_GROUP, aliases=ALIASES)

    assert (store.display_label(SecretRef.for_global(group='default'))
            == 'グローバル（グループ default → nyle）')


def test_display_label_keeps_the_name_when_the_group_has_no_alias(tmp_path):
    """受け入れ条件 4: 対応が無いグループでは ``→`` を付けない"""
    store = _store(tmp_path, backend='openbao', layout=bc.LAYOUT_GROUP, aliases=ALIASES)
    ref = SecretRef.for_global(group='kkg')

    assert store.display_label(ref) == ref.label() == 'グローバル（グループ kkg）'


def test_display_label_of_a_reference_without_a_group_is_the_plain_label(tmp_path):
    store = _store(tmp_path, backend='openbao', layout=bc.LAYOUT_GROUP, aliases=ALIASES)

    assert store.display_label(SecretRef.for_global()) == 'グローバル'


def test_display_label_ignores_a_leftover_openbao_section_on_a_file_backend(tmp_path):
    """決定 3: backend の種類を ``config.openbao is None`` で判定してはならない

    ``backend: age`` に ``openbao:`` 節が残っていても ``config.openbao`` は ``None`` に
    ならない。判定は ``storage_group`` (backend の種類・設定の有無・layout をまとめて見る)
    に任せるため、グループの付いた参照を渡しても読み替えない。
    """
    store = _store(tmp_path, backend='age', layout=bc.LAYOUT_GROUP, aliases=ALIASES)

    assert store.display_label(SecretRef.for_global(group='default')) \
        == 'グローバル（グループ default）'


def test_display_label_does_not_map_on_a_flat_layout(tmp_path):
    store = _store(tmp_path, backend='openbao', layout=bc.LAYOUT_FLAT)

    assert store.display_label(SecretRef.for_global()) == 'グローバル'


# ---------------------------------------------------------------------------
# 受け入れ条件 7: 誤りを伝える文言は読み替えない
# ---------------------------------------------------------------------------

def test_the_flat_layout_refusal_names_the_group_before_the_alias(tmp_path):
    """``OpenBaoBackend._check_group`` の文言は引数なしの ``label()`` を通す

    ``layout: flat`` に ``group_aliases`` を置いた設定は ``validate()`` が拒むが、ここで
    見るのは文言が読み替えの解決を背負わないことである (決定 2)。``label()`` の既定を
    読み替え後にすると、この文言が ``default → nyle`` になる。
    """
    from devbase.env.openbao import OpenBaoBackend

    store = _store(tmp_path, backend='openbao', layout=bc.LAYOUT_FLAT, aliases=ALIASES)
    backend = OpenBaoBackend(store)

    with pytest.raises(SecretStoreError) as excinfo:
        backend.path_of(SecretRef.for_global(group='default'))

    assert '（グループ default）' in str(excinfo.value)
    assert '→' not in str(excinfo.value)


def test_the_account_group_warning_names_the_group_before_the_alias(openbao_root, openbao,
                                                                    caplog):
    """置き場の ``DEVBASE_ACCOUNT_GROUP`` を使わない旨の警告も読み替えない (PLAN62)"""
    from devbase.env import keys, runtime
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, layout=bc.LAYOUT_GROUP, group_aliases=ALIASES)
    openbao.put('team/nyle/global', {keys.DEVBASE_ACCOUNT_GROUP: 'kkg', 'A': '1'})

    with caplog.at_level(logging.WARNING):
        runtime.resolve(openbao_root, None)

    messages = [r.getMessage() for r in caplog.records
                if r.levelno == logging.WARNING and keys.DEVBASE_ACCOUNT_GROUP in r.getMessage()]
    assert len(messages) == 1
    assert '機密の置き場（グローバル（グループ default））' in messages[0]
    assert '→' not in messages[0]
