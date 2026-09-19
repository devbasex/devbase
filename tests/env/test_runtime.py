"""runtime.py: 機密の合成とコンテナへ渡す変数名"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pyrage
import pytest

from devbase.env import keys, runtime
from devbase.env.secret_store import SecretRef, SecretStore
from devbase.errors import DevbaseError
from devbase.volume.manager import resolve_account_group


@pytest.fixture
def root(tmp_path):
    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    return tmp_path


@pytest.fixture
def store(root, tmp_path):
    identity = pyrage.x25519.Identity.generate()
    key = tmp_path / 'id.key'
    key.write_text(str(identity))
    return SecretStore(root, recipients=[str(identity.to_public())],
                       identities=[str(key)])


@pytest.fixture(autouse=True)
def _isolate_injection_state(monkeypatch):
    """注入記録 (モジュールレベル) をテストごとに独立させる"""
    monkeypatch.setattr(runtime, '_injected_originals', {})


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')
API = SecretRef.for_project('api')


# ---------------------------------------------------------------------------
# 重ね順
# ---------------------------------------------------------------------------

def test_global_secrets_are_listed_for_the_container(root, store):
    store.age.save(GLOBAL, {'ANTHROPIC_API_KEY': 'sk-1'})

    resolved = runtime.resolve(root, None, store=store)

    assert resolved.values == {'ANTHROPIC_API_KEY': 'sk-1'}
    assert resolved.names == ['ANTHROPIC_API_KEY']


def test_project_secrets_override_global(root, store):
    store.age.save(GLOBAL, {'TOKEN': 'global', 'ONLY_GLOBAL': 'g'})
    store.age.save(WEB, {'TOKEN': 'project'})

    resolved = runtime.resolve(root, 'web', store=store)

    assert resolved.values['TOKEN'] == 'project'
    assert resolved.values['ONLY_GLOBAL'] == 'g'
    assert sorted(resolved.names) == ['ONLY_GLOBAL', 'TOKEN']


def test_names_are_kept_per_origin(root, store):
    """由来ごとに分けて持つ (構成生成側がサービスごとに絞り込むため)"""
    store.age.save(GLOBAL, {'TOKEN': 'global', 'ONLY_GLOBAL': 'g'})
    store.age.save(WEB, {'TOKEN': 'project', 'ONLY_PROJECT': 'p'})

    resolved = runtime.resolve(root, 'web', store=store)

    assert sorted(resolved.global_names) == ['ONLY_GLOBAL', 'TOKEN']
    assert sorted(resolved.project_names) == ['ONLY_PROJECT', 'TOKEN']
    # 両方にあるキーは全体としては 1 件に畳む
    assert sorted(resolved.names) == ['ONLY_GLOBAL', 'ONLY_PROJECT', 'TOKEN']


def test_no_secrets_is_falsy(root, store):
    resolved = runtime.resolve(root, None, store=store)

    assert not resolved
    assert resolved.names == []


def test_project_env_overrides_global_for_the_same_key(root, store, monkeypatch):
    """非機密設定が共通設定を上書きする従来の関係を保つ"""
    (root / 'projects' / 'web' / 'env').write_text('AWS_DEFAULT_REGION=us-east-1\n')
    monkeypatch.setenv('AWS_DEFAULT_REGION', 'us-east-1')
    store.age.save(GLOBAL, {'AWS_DEFAULT_REGION': 'ap-northeast-1'})

    resolved = runtime.resolve(root, 'web', store=store)

    assert resolved.values['AWS_DEFAULT_REGION'] == 'us-east-1'


def test_project_env_only_keys_are_not_listed(root, store, monkeypatch):
    """非機密設定は env_file が直接読むので変数名を列挙しない"""
    (root / 'projects' / 'web' / 'env').write_text('APP_NAME=web\n')
    monkeypatch.setenv('APP_NAME', 'web')
    store.age.save(GLOBAL, {'TOKEN': 't'})

    resolved = runtime.resolve(root, 'web', store=store)

    assert resolved.names == ['TOKEN']
    assert 'APP_NAME' not in resolved.values


def test_project_env_value_comes_from_the_environment(root, store, monkeypatch):
    """展開済みの値を採用する (生の行を読み直さない)"""
    (root / 'projects' / 'web' / 'env').write_text('APP_ROOT=/srv/$APP_NAME\n')
    monkeypatch.setenv('APP_ROOT', '/srv/web')
    store.age.save(GLOBAL, {'APP_ROOT': '/srv/unset'})

    resolved = runtime.resolve(root, 'web', store=store)

    assert resolved.values['APP_ROOT'] == '/srv/web'


def test_project_env_is_ignored_when_not_in_the_environment(root, store, monkeypatch):
    monkeypatch.delenv('APP_ROOT', raising=False)
    (root / 'projects' / 'web' / 'env').write_text('APP_ROOT=/srv/$APP_NAME\n')
    store.age.save(GLOBAL, {'APP_ROOT': '/srv/global'})

    resolved = runtime.resolve(root, 'web', store=store)

    assert resolved.values['APP_ROOT'] == '/srv/global'


def test_resolve_without_any_secrets_is_empty(root, store):
    resolved = runtime.resolve(root, 'web', store=store)
    assert resolved.values == {}
    assert resolved.names == []
    assert not resolved


def test_plaintext_secrets_are_resolved_too(root, store):
    """移行前 (平文のまま) でも同じ経路で読める"""
    store.plaintext.save(GLOBAL, {'TOKEN': 'plain'})

    resolved = runtime.resolve(root, None, store=store)

    assert resolved.values == {'TOKEN': 'plain'}


# ---------------------------------------------------------------------------
# 注入
# ---------------------------------------------------------------------------

def test_inject_puts_values_into_the_given_environ(root, store):
    store.age.save(GLOBAL, {'TOKEN': 'sk-1'})
    environ = {}

    resolved = runtime.inject(root, None, environ=environ, store=store)

    assert environ == {'TOKEN': 'sk-1'}
    assert resolved.names == ['TOKEN']


class _GlobalOkProjectFailsStore:
    """共通機密は取れるが、プロジェクト機密の取得で例外を送出する店。

    重ね順どおり共通 (``global``) を先に読み、プロジェクト (``project``) の読み取りで
    :class:`DevbaseError` を投げることで「共通の取得後にプロジェクトの取得が失敗する」
    経路を再現する。``runtime`` の内部関数は置き換えず、``store`` 引数で渡す。
    """

    def load(self, ref):
        if ref.kind == 'project':
            raise DevbaseError('project secrets unavailable')
        return {'TOKEN': 'new-secret'}


class _GlobalOnlyStore:
    """共通機密だけを持つ正常な店 (事前注入で ``TOKEN=old-secret`` を載せる用)"""

    def load(self, ref):
        if ref.kind == 'project':
            return {}
        return {'TOKEN': 'old-secret'}


@pytest.mark.parametrize('preinjected, cleared_after_failure', [
    (False, []),
    (True, ['TOKEN']),
])
def test_inject_failing_project_leaves_target_and_history_intact(
        root, preinjected, cleared_after_failure):
    """現状固定: 共通機密の取得後にプロジェクト機密の取得が失敗する経路。

    現状は例外をそのまま伝播し、対象マッピングも既存の復元履歴も変更しない。
    - 初回注入では対象は変わらず、``clear_injected`` は空を返す。
    - 別の正常な店で ``TOKEN=old-secret`` を注入済みの場合は、対象はその失敗前の
      状態のまま (``TOKEN=old-secret``) で、``clear_injected`` は ``TOKEN`` を注入前の
      ``TOKEN=shell`` へ戻す。
    いずれも ``TOKEN=shell`` と無関係なキーが残る (private な履歴には触れない)。
    """
    (root / 'projects' / 'api').mkdir()
    target = {'TOKEN': 'shell', 'UNRELATED': 'keep'}

    if preinjected:
        runtime.inject(root, 'web', environ=target, store=_GlobalOnlyStore())
        assert target['TOKEN'] == 'old-secret'

    before = dict(target)
    with pytest.raises(DevbaseError):
        runtime.inject(root, 'web', environ=target, store=_GlobalOkProjectFailsStore())

    # 失敗した注入は対象を変えない (共通の値も載せない)
    assert target == before

    cleared = runtime.clear_injected(target)

    assert cleared == cleared_after_failure
    # どちらの経路でも注入前の値と無関係なキーが残る
    assert target == {'TOKEN': 'shell', 'UNRELATED': 'keep'}


# ---------------------------------------------------------------------------
# 注入の解除 (プロジェクト切替時の残留対策)
# ---------------------------------------------------------------------------

def test_switching_projects_drops_the_source_only_secret(root, store):
    """切替元にしか無い機密は、切替先の機密を載せ直すと消える。

    単に上書きするだけでは、切替先に同名キーが無い機密が残ってしまう。
    """
    (root / 'projects' / 'api').mkdir()
    store.age.save(GLOBAL, {'SHARED': 'common'})
    store.age.save(WEB, {'WEB_ONLY': 'w'})
    store.age.save(API, {'API_ONLY': 'a'})
    environ = {}

    runtime.inject(root, 'web', environ=environ, store=store)
    assert environ['WEB_ONLY'] == 'w'

    runtime.clear_injected(environ)
    runtime.inject(root, 'api', environ=environ, store=store)

    # 切替元固有の機密は残らない
    assert 'WEB_ONLY' not in environ
    assert environ['API_ONLY'] == 'a'
    # 共通の機密は切替後も残る
    assert environ['SHARED'] == 'common'


def test_clear_injected_restores_the_users_own_value(root, store):
    """利用者がシェルで設定していた同名の変数は消さず元の値へ戻す"""
    store.age.save(GLOBAL, {'TOKEN': 'from-secret'})
    environ = {'TOKEN': 'from-shell', 'PATH': '/bin'}

    runtime.inject(root, None, environ=environ, store=store)
    assert environ['TOKEN'] == 'from-secret'

    cleared = runtime.clear_injected(environ)

    assert environ['TOKEN'] == 'from-shell'
    assert environ['PATH'] == '/bin'
    assert cleared == ['TOKEN']


def test_clear_injected_removes_keys_that_did_not_exist(root, store):
    store.age.save(GLOBAL, {'TOKEN': 'from-secret'})
    environ = {}

    runtime.inject(root, None, environ=environ, store=store)
    runtime.clear_injected(environ)

    assert environ == {}


def test_repeated_injection_keeps_the_original_value(root, store):
    """載せ直しても記録するのは「最初に載せる前の値」"""
    store.age.save(GLOBAL, {'TOKEN': 'from-secret'})
    environ = {'TOKEN': 'from-shell'}

    runtime.inject(root, None, environ=environ, store=store)
    runtime.inject(root, None, environ=environ, store=store)
    runtime.clear_injected(environ)

    assert environ['TOKEN'] == 'from-shell'


def test_clear_injected_without_injection_is_noop(root):
    environ = {'TOKEN': 'from-shell'}

    assert runtime.clear_injected(environ) == []
    assert environ == {'TOKEN': 'from-shell'}


def test_clear_injected_only_touches_the_given_mapping(root, store):
    """履歴は注入先ごとに持つ (別のマッピングを巻き込まない)

    履歴が全体で 1 つしか無いと、A へ注入した記録で B を「復元」してしまい、
    B の値が壊れるうえ A には機密が残る。
    """
    store.age.save(GLOBAL, {'TOKEN': 'from-secret'})
    a = {'TOKEN': 'a-shell'}
    b = {'TOKEN': 'b-shell'}

    runtime.inject(root, None, environ=a, store=store)
    runtime.inject(root, None, environ=b, store=store)

    assert runtime.clear_injected(a) == ['TOKEN']

    # A だけが元へ戻り、B は注入したままで壊れない
    assert a == {'TOKEN': 'a-shell'}
    assert b == {'TOKEN': 'from-secret'}

    # B の履歴は残っているので、後から解除すれば B も元へ戻る
    assert runtime.clear_injected(b) == ['TOKEN']
    assert b == {'TOKEN': 'b-shell'}


def test_clearing_one_mapping_keeps_secrets_out_of_the_other(root, store):
    """A の解除が B の機密を消し残さない (逆に A には機密を残さない)"""
    store.age.save(GLOBAL, {'ONLY_SECRET': 's'})
    a = {}
    b = {}

    runtime.inject(root, None, environ=a, store=store)
    runtime.inject(root, None, environ=b, store=store)
    runtime.clear_injected(b)

    assert b == {}
    assert a == {'ONLY_SECRET': 's'}

    runtime.clear_injected(a)
    assert a == {}


def test_inject_and_clear_default_to_os_environ(root, store, monkeypatch):
    """既定の対象は従来どおり os.environ"""
    monkeypatch.delenv('TOKEN', raising=False)
    store.age.save(GLOBAL, {'TOKEN': 'from-secret'})
    other = {'TOKEN': 'other'}

    runtime.inject(root, None, store=store)
    assert os.environ['TOKEN'] == 'from-secret'

    # 別マッピングへの注入は os.environ の履歴に混ざらない
    runtime.inject(root, None, environ=other, store=store)

    assert runtime.clear_injected() == ['TOKEN']
    assert 'TOKEN' not in os.environ
    assert other == {'TOKEN': 'from-secret'}


def test_child_env_does_not_touch_os_environ(root, store, monkeypatch):
    monkeypatch.delenv('TOKEN', raising=False)
    store.age.save(GLOBAL, {'TOKEN': 'sk-1'})

    env = runtime.child_env(root, None, store=store)

    assert env['TOKEN'] == 'sk-1'
    assert 'TOKEN' not in os.environ


def test_child_env_keeps_the_existing_environment(root, store):
    store.age.save(GLOBAL, {'TOKEN': 'sk-1'})

    env = runtime.child_env(root, None, base={'PATH': '/bin'}, store=store)

    assert env['PATH'] == '/bin'
    assert env['TOKEN'] == 'sk-1'


def test_restore_injected_brings_back_the_history_of_the_snapshot(root, store):
    """控えた履歴を書き戻すと、その後の解除は控えた時点で載っていた機密を落とす"""
    (root / 'projects' / 'api').mkdir()
    store.age.save(WEB, {'WEB_ONLY': 'w'})
    store.age.save(API, {'API_ONLY': 'a'})
    environ = {}

    runtime.inject(root, 'web', environ=environ, store=store)
    snapshot = runtime.snapshot_injected(environ)
    saved_values = dict(environ)

    # 控えた後の切替で履歴が入れ替わる
    runtime.clear_injected(environ)
    runtime.inject(root, 'api', environ=environ, store=store)

    # 値と履歴を揃えて戻す
    environ.clear()
    environ.update(saved_values)
    runtime.restore_injected(snapshot, environ)

    assert runtime.clear_injected(environ) == ['WEB_ONLY']
    assert environ == {}


def test_snapshot_is_not_changed_by_later_injection(root, store):
    """控えは複製なので、後の注入で書き足された履歴が混ざらない"""
    store.age.save(WEB, {'WEB_ONLY': 'w'})
    environ = {}

    runtime.inject(root, 'web', environ=environ, store=store)
    snapshot = runtime.snapshot_injected(environ)
    store.age.save(GLOBAL, {'LATER': 'l'})
    runtime.inject(root, 'web', environ=environ, store=store)

    runtime.restore_injected(snapshot, environ)

    assert runtime.clear_injected(environ) == ['WEB_ONLY']


def test_restoring_an_empty_snapshot_drops_the_history(root, store):
    """履歴が無い時点の控えを書き戻すと、その後に作られた履歴は消える"""
    store.age.save(GLOBAL, {'TOKEN': 'from-secret'})
    environ = {}

    snapshot = runtime.snapshot_injected(environ)
    runtime.inject(root, None, environ=environ, store=store)

    runtime.restore_injected(snapshot, environ)

    assert runtime.clear_injected(environ) == []
    assert id(environ) not in runtime._injected_originals


# ---------------------------------------------------------------------------
# プロジェクトの特定
# ---------------------------------------------------------------------------

def test_current_project_name_from_a_subdirectory(root):
    sub = root / 'projects' / 'web' / 'src'
    sub.mkdir()
    assert runtime.current_project_name(root, sub) == 'web'


def test_current_project_name_outside_projects(root):
    assert runtime.current_project_name(root, root) is None


def test_current_project_name_rejects_paths_escaping_projects(root):
    escaped = root / 'projects' / 'web' / '..' / '..' / 'outside'
    assert runtime.current_project_name(root, escaped) is None


def test_current_project_name_follows_a_symlinked_project(root, tmp_path):
    target = tmp_path / 'linked-target'
    target.mkdir()
    (root / 'projects' / 'linked').symlink_to(target)

    assert runtime.current_project_name(root, root / 'projects' / 'linked') == 'linked'


# ---------------------------------------------------------------------------
# 持ち主の軸を足した 4 層の重ね順 (PLAN51 決定 12)
# ---------------------------------------------------------------------------

class _FourLayerStore:
    """4 参照を持つ最小の店 (backend を問わず重ね順だけを見る)"""

    def __init__(self, layers):
        self._layers = layers

    def load(self, ref):
        return dict(self._layers.get((ref.kind, ref.name, ref.owner), {}))


USER_GLOBAL = SecretRef.for_global(owner='user')
USER_WEB = SecretRef.for_project('web', owner='user')


def _layers(**kw):
    table = {
        'team_global': ('global', None, 'team'),
        'user_global': ('global', None, 'user'),
        'team_web': ('project', 'web', 'team'),
        'user_web': ('project', 'web', 'user'),
    }
    return {table[name]: data for name, data in kw.items()}


def test_user_project_wins_over_all_other_layers(root):
    store = _FourLayerStore(_layers(
        team_global={'K': 'tg'}, user_global={'K': 'ug'},
        team_web={'K': 'tw'}, user_web={'K': 'uw'}))

    assert runtime.resolve(root, 'web', store=store).values['K'] == 'uw'


def test_layers_fall_back_in_the_documented_order(root):
    layers = _layers(team_global={'K': 'tg'}, user_global={'K': 'ug'},
                     team_web={'K': 'tw'}, user_web={'K': 'uw'})
    order = [('project', 'web', 'user'), ('project', 'web', 'team'),
             ('global', None, 'user'), ('global', None, 'team')]
    expected = ['uw', 'tw', 'ug', 'tg']

    for key, value in zip(order, expected):
        assert runtime.resolve(root, 'web', store=_FourLayerStore(layers)).values['K'] == value
        del layers[key]


def test_project_env_beats_global_layers_and_loses_to_project_layers(root, monkeypatch):
    (root / 'projects' / 'web' / 'env').write_text('K=from-env\n')
    monkeypatch.setenv('K', 'from-env')

    store = _FourLayerStore(_layers(team_global={'K': 'tg'}, user_global={'K': 'ug'}))
    assert runtime.resolve(root, 'web', store=store).values['K'] == 'from-env'

    store = _FourLayerStore(_layers(user_global={'K': 'ug'}, team_web={'K': 'tw'}))
    assert runtime.resolve(root, 'web', store=store).values['K'] == 'tw'


def test_names_are_listed_once_across_the_four_layers(root):
    store = _FourLayerStore(_layers(
        team_global={'A': '1', 'K': 'tg'}, user_global={'K': 'ug', 'B': '2'},
        team_web={'K': 'tw', 'C': '3'}, user_web={'K': 'uw', 'D': '4'}))

    resolved = runtime.resolve(root, 'web', store=store)

    assert resolved.global_names == ['A', 'K', 'B']
    assert resolved.project_names == ['K', 'C', 'D']
    assert resolved.names == ['A', 'K', 'B', 'C', 'D']


def test_invalid_utf8_project_env_preserves_secret_values_and_origins(root, monkeypatch):
    """現状固定: 不正 UTF-8 があれば有効な先頭行も含め上書きを無視する。"""
    (root / 'projects' / 'web' / 'env').write_bytes(b'TOKEN=override\nINVALID=\xff\n')
    monkeypatch.setenv('TOKEN', 'override')
    store = _FourLayerStore(_layers(
        team_global={'TOKEN': 'secret'}, user_global={'USER_GLOBAL': 'ug'},
        team_web={'PROJECT_ONLY': 'p'}, user_web={'USER_PROJECT': 'up'}))

    resolved = runtime.resolve(root, 'web', store=store)

    assert resolved.values == {
        'TOKEN': 'secret', 'USER_GLOBAL': 'ug',
        'PROJECT_ONLY': 'p', 'USER_PROJECT': 'up',
    }
    assert set(resolved.global_names) == {'TOKEN', 'USER_GLOBAL'}
    assert set(resolved.project_names) == {'PROJECT_ONLY', 'USER_PROJECT'}
    assert set(resolved.names) == {'TOKEN', 'USER_GLOBAL', 'PROJECT_ONLY', 'USER_PROJECT'}


def test_unreadable_project_env_preserves_secret_values_and_origins(
        root, monkeypatch, caplog):
    """現状固定: 読み取り不能な設定の上書きを無視し、機密と由来を保持する。"""
    env_path = root / 'projects' / 'web' / 'env'
    env_path.write_text('TOKEN=override\n')
    monkeypatch.setenv('TOKEN', 'override')
    store = _FourLayerStore(_layers(
        team_global={'TOKEN': 'secret'}, team_web={'PROJECT_ONLY': 'p'}))
    read_bytes = Path.read_bytes

    def read_with_permission_error(path):
        if path == env_path:
            raise PermissionError('project env is unreadable')
        return read_bytes(path)

    monkeypatch.setattr(Path, 'read_bytes', read_with_permission_error)

    with caplog.at_level(logging.WARNING, logger=runtime.__name__):
        resolved = runtime.resolve(root, 'web', store=store)

    assert resolved.values == {'TOKEN': 'secret', 'PROJECT_ONLY': 'p'}
    assert set(resolved.global_names) == {'TOKEN'}
    assert set(resolved.project_names) == {'PROJECT_ONLY'}
    assert set(resolved.names) == {'TOKEN', 'PROJECT_ONLY'}
    assert any(record.levelno == logging.WARNING and str(env_path) in record.getMessage()
               for record in caplog.records)


def test_file_backends_resolve_exactly_as_before(root, store):
    """個人単位の参照を持たない backend では、結果が 2 層のときと同じ"""
    store.age.save(GLOBAL, {'TOKEN': 'global', 'ONLY_GLOBAL': 'g'})
    store.age.save(WEB, {'TOKEN': 'project'})

    resolved = runtime.resolve(root, 'web', store=store)

    assert resolved.values == {'TOKEN': 'project', 'ONLY_GLOBAL': 'g'}
    assert resolved.global_names == ['ONLY_GLOBAL', 'TOKEN']   # 保存時に昇順へ正規化される
    assert resolved.project_names == ['TOKEN']


# ---------------------------------------------------------------------------
# グループ別の置き場 (PLAN56)
# ---------------------------------------------------------------------------

class _GroupedStore(_FourLayerStore):
    """``ref_group`` が決めたグループを、読んだ参照ごとに記録する"""

    def __init__(self, layers, group):
        super().__init__(layers)
        self._group = group
        self.asked = []
        self.loaded = []

    def ref_group(self, project):
        self.asked.append(project)
        return self._group

    def load(self, ref):
        self.loaded.append(ref)
        return super().load(ref)


def test_resolve_passes_the_projects_group_to_the_four_references(root):
    store = _GroupedStore(_layers(team_global={'K': 'tg'}), 'with')

    runtime.resolve(root, 'web', store=store)

    assert store.asked == ['web']
    assert store.loaded == [
        SecretRef.for_global(group='with'), SecretRef.for_global(owner='user', group='with'),
        SecretRef.for_project('web', group='with'),
        SecretRef.for_project('web', owner='user', group='with')]


def test_resolve_without_a_group_builds_the_same_references_as_before(root):
    """決定 5: ``ref_group`` が ``None`` なら参照は今と同じ値"""
    store = _GroupedStore({}, None)

    runtime.resolve(root, None, store=store)

    assert store.asked == [None]
    assert store.loaded == [GLOBAL, USER_GLOBAL]


def test_resolve_with_the_group_layout_requests_only_the_group_paths(openbao_root, openbao):
    """受け入れ条件 1 (単体): ``with`` のプロジェクトは ``with`` の 4 パスだけを取得する"""
    from tests.conftest import configure_openbao

    root = openbao_root
    configure_openbao(root, openbao, layout='group', group_aliases={'default': 'nyle'})
    (root / 'projects' / 'web' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=with\n')
    openbao.put('team/with/global', {'A': 'with'})
    openbao.put('team/global', {'A': 'flat'})
    openbao.put('team/nyle/global', {'A': 'nyle'})

    resolved = runtime.resolve(root, 'web', store=SecretStore(root))

    assert resolved.values == {'A': 'with'}
    assert openbao.logins == 1
    assert sorted(r.kv_path for r in openbao.requests_of('GET')) == sorted([
        'team/with/global', 'users/member01/with/global',
        'team/with/projects/web', 'users/member01/with/projects/web'])


# ---------------------------------------------------------------------------
# 機密の置き場の DEVBASE_ACCOUNT_GROUP は合成しない (PLAN62)
# ---------------------------------------------------------------------------

ACCOUNT_GROUP = keys.DEVBASE_ACCOUNT_GROUP


@pytest.fixture
def account_group_root(root, monkeypatch):
    """DEVBASE_ROOT を tmp へ向け、警告の集合を空にし、プロセスのグループを外す。

    この節のテストは ``environ`` を渡さずに :func:`runtime.inject` を呼ぶため、機密が
    本物の ``os.environ`` へ載る。載せたままにすると後続のテストとその子プロセスへ
    漏れるので、ここで注入前の環境へ戻す。``_isolate_injection_state`` が差し替えた
    注入履歴が ``monkeypatch`` の後始末で戻るより**前**に通す必要がある (戻った後では
    履歴を引けず、載せた値が残る)。autouse の ``_isolate_injection_state`` が先に組み
    立てられる分、この fixture の後始末はそれより先に走る。
    """
    monkeypatch.setenv('DEVBASE_ROOT', str(root))
    monkeypatch.delenv(ACCOUNT_GROUP, raising=False)
    monkeypatch.setattr(runtime, '_warned_account_group_refs', set())
    yield root
    runtime.clear_injected()


def _save(store, backend, ref, data):
    getattr(store, backend).save(ref, data)


@pytest.mark.parametrize('backend', ['age', 'plaintext'])
def test_inject_does_not_put_the_stores_account_group_into_the_environment(
        account_group_root, store, backend):
    """受け入れ条件 1: 置き場の値はプロセスへ載らず、ボリュームのグループは default"""
    _save(store, backend, GLOBAL, {ACCOUNT_GROUP: 'kkg', 'TOKEN': 't'})

    runtime.inject(account_group_root, 'web', store=store)

    assert ACCOUNT_GROUP not in os.environ
    assert os.environ['TOKEN'] == 't'
    assert resolve_account_group() == 'default'


@pytest.mark.parametrize('backend', ['age', 'plaintext'])
def test_inject_keeps_the_environments_account_group(account_group_root, store, backend,
                                                     monkeypatch):
    """受け入れ条件 2: env ファイル由来の値 (プロセスの環境変数) はそのまま"""
    monkeypatch.setenv(ACCOUNT_GROUP, 'with')
    _save(store, backend, GLOBAL, {ACCOUNT_GROUP: 'kkg', 'TOKEN': 't'})

    runtime.inject(account_group_root, 'web', store=store)

    assert os.environ[ACCOUNT_GROUP] == 'with'
    assert resolve_account_group() == 'with'


def test_inject_keeps_the_projects_env_declaration(account_group_root, store, monkeypatch):
    """受け入れ条件 2: projects/web/env の宣言 (重ね順 3 が働く場合) でも同じ"""
    (account_group_root / 'projects' / 'web' / 'env').write_text(f'{ACCOUNT_GROUP}=with\n')
    monkeypatch.setenv(ACCOUNT_GROUP, 'with')
    store.age.save(GLOBAL, {ACCOUNT_GROUP: 'kkg'})

    resolved = runtime.inject(account_group_root, 'web', store=store)

    assert os.environ[ACCOUNT_GROUP] == 'with'
    assert ACCOUNT_GROUP not in resolved.values


@pytest.mark.parametrize('layer', ['team_global', 'user_global', 'team_web', 'user_web'])
def test_every_store_layer_is_dropped_from_the_environment(account_group_root, layer):
    """受け入れ条件 3: 4 つの置き場のどれにあっても載らない"""
    store = _FourLayerStore(_layers(**{layer: {ACCOUNT_GROUP: 'kkg', 'K': 'v'}}))

    runtime.inject(account_group_root, 'web', store=store)

    assert ACCOUNT_GROUP not in os.environ
    assert os.environ['K'] == 'v'
    assert resolve_account_group() == 'default'


@pytest.mark.parametrize('layer', ['team_global', 'user_global', 'team_web', 'user_web'])
def test_every_store_layer_loses_to_the_environment(account_group_root, layer, monkeypatch):
    """受け入れ条件 3: プロジェクトの置き場 (重ね順 4・5) にあっても env ファイルの値が残る"""
    (account_group_root / 'projects' / 'web' / 'env').write_text(f'{ACCOUNT_GROUP}=with\n')
    monkeypatch.setenv(ACCOUNT_GROUP, 'with')
    store = _FourLayerStore(_layers(**{layer: {ACCOUNT_GROUP: 'kkg'}}))

    runtime.inject(account_group_root, 'web', store=store)

    assert os.environ[ACCOUNT_GROUP] == 'with'
    assert resolve_account_group() == 'with'


def test_resolve_never_lists_the_account_group(account_group_root):
    """受け入れ条件 5: names / global_names / project_names / values のどれにも無い"""
    store = _FourLayerStore(_layers(
        team_global={ACCOUNT_GROUP: 'kkg', 'A': '1'}, user_global={ACCOUNT_GROUP: 'kkg'},
        team_web={ACCOUNT_GROUP: 'kkg', 'C': '3'}, user_web={ACCOUNT_GROUP: 'kkg'}))

    resolved = runtime.resolve(account_group_root, 'web', store=store)

    assert resolved.values == {'A': '1', 'C': '3'}
    assert resolved.global_names == ['A']
    assert resolved.project_names == ['C']
    assert resolved.names == ['A', 'C']


def test_child_env_does_not_carry_the_stores_account_group(account_group_root):
    store = _FourLayerStore(_layers(team_global={ACCOUNT_GROUP: 'kkg', 'A': '1'}))

    env = runtime.child_env(account_group_root, 'web', base={'PATH': '/bin'}, store=store)

    assert env == {'PATH': '/bin', 'A': '1'}


# ---------------------------------------------------------------------------
# 置き場にあったときの警告 (PLAN62 受け入れ条件 4)
# ---------------------------------------------------------------------------

def _warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and ACCOUNT_GROUP in r.getMessage()]


DELETE = f'devbase env delete {ACCOUNT_GROUP}'


@pytest.mark.parametrize('layer, label, how', [
    ('team_global', 'グローバル', DELETE),
    ('user_global', '個人のグローバル', f'{DELETE} --user'),
    ('team_web', "プロジェクト 'web'", f'{DELETE} -p（projects/web で実行）'),
    ('user_web', "個人のプロジェクト 'web'", f'{DELETE} -p --user（projects/web で実行）'),
])
def test_warns_once_with_the_store_and_how_to_delete(account_group_root, caplog,
                                                     layer, label, how):
    """受け入れ条件 4: キー名・置き場の種類・消し方を含み、値は含まない"""
    store = _FourLayerStore(_layers(**{layer: {ACCOUNT_GROUP: 'kkg'}}))

    with caplog.at_level(logging.WARNING):
        runtime.resolve(account_group_root, 'web', store=store)

    messages = _warnings(caplog)
    assert len(messages) == 1
    message = messages[0]
    assert f'機密の置き場（{label}）' in message
    assert 'projects/<name>/env' in message and '$DEVBASE_ROOT/env' in message
    assert message.endswith(how)
    assert 'kkg' not in message


def test_warning_for_a_grouped_reference_names_the_group(account_group_root, caplog):
    """グループを持つ参照は label にグループが付き、--group を添える"""
    store = _GroupedStore(_layers(user_web={ACCOUNT_GROUP: 'kkg'}), 'with')

    with caplog.at_level(logging.WARNING):
        runtime.resolve(account_group_root, 'web', store=store)

    messages = _warnings(caplog)
    assert len(messages) == 1
    assert "機密の置き場（個人のプロジェクト 'web'（グループ with））" in messages[0]
    assert messages[0].endswith(f'{DELETE} -p --user --group with（projects/web で実行）')


def test_warning_for_a_grouped_global_reference_still_adds_the_group(account_group_root,
                                                                     caplog):
    """決定 5: 共通の参照でも --group を付け、どこで打っても同じ置き場を指す"""
    store = _GroupedStore(_layers(team_global={ACCOUNT_GROUP: 'kkg'}), 'with')

    with caplog.at_level(logging.WARNING):
        runtime.resolve(account_group_root, None, store=store)

    messages = _warnings(caplog)
    assert len(messages) == 1
    assert '機密の置き場（グローバル（グループ with））' in messages[0]
    assert messages[0].endswith(f'{DELETE} --group with')


def test_warning_is_not_repeated_across_stores_and_release(account_group_root, caplog):
    """受け入れ条件 4: 同じ参照は release_store と別の store をまたいでも 1 回"""
    layers = _layers(team_global={ACCOUNT_GROUP: 'kkg'})

    with caplog.at_level(logging.WARNING):
        runtime.resolve(account_group_root, 'web', store=_FourLayerStore(layers))
        runtime.resolve(account_group_root, 'web', store=_FourLayerStore(layers))
        runtime.release_store()
        runtime.resolve(account_group_root, None, store=_FourLayerStore(layers))

    assert len(_warnings(caplog)) == 1


def test_each_reference_warns_separately(account_group_root, caplog):
    store = _FourLayerStore(_layers(team_global={ACCOUNT_GROUP: 'kkg'},
                                    user_web={ACCOUNT_GROUP: 'kkg'}))

    with caplog.at_level(logging.WARNING):
        runtime.resolve(account_group_root, 'web', store=store)
        runtime.resolve(account_group_root, 'web', store=store)

    messages = _warnings(caplog)
    assert len(messages) == 2
    assert '（グローバル）' in messages[0]
    assert "（個人のプロジェクト 'web'）" in messages[1]


def test_empty_value_still_warns_and_no_key_does_not(account_group_root, caplog):
    with caplog.at_level(logging.WARNING):
        runtime.resolve(account_group_root, 'web',
                        store=_FourLayerStore(_layers(team_global={'K': 'v'})))
    assert _warnings(caplog) == []

    with caplog.at_level(logging.WARNING):
        runtime.resolve(account_group_root, 'web',
                        store=_FourLayerStore(_layers(team_global={ACCOUNT_GROUP: ''})))
    assert len(_warnings(caplog)) == 1
