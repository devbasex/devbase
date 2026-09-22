"""env export / import がサーバ backend 越しに動くことの検証 (PLAN51 設計 2)"""

from __future__ import annotations

import pyrage
import pytest

from devbase.env import bundle, cipher
from devbase.env.io_export import ExportOptions, export
from devbase.env.io_import import ImportError as EnvImportError, ImportOptions, import_bundle


TEAM_GLOBAL = 'team/global'
TEAM_WEB = 'team/projects/web'
USER_GLOBAL = 'users/member01/global'
USER_WEB = 'users/member01/projects/web'


@pytest.fixture
def bundle_keys(tmp_path):
    identity = pyrage.x25519.Identity.generate()
    pub = tmp_path / 'bundle.pub'
    pub.write_text(str(identity.to_public()) + '\n')
    key = tmp_path / 'bundle.key'
    key.write_text(str(identity))
    return pub, key


def make_bundle(tmp_path, pub, members):
    """平文の tar.gz バンドルを組み立てて age で暗号化する"""
    entries = [bundle.BundleEntry(arcname=name, origin=name, data=data)
               for name, data in members.items()]
    blob = bundle.pack(entries)
    dest = tmp_path / 'in.dbenv'
    dest.write_bytes(cipher.encrypt(blob, recipients=[f'@{pub}']))
    return dest


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def test_export_collects_team_secrets_from_the_server(openbao_root, openbao, bundle_keys,
                                                      tmp_path):
    pub, key = bundle_keys
    openbao.put(TEAM_GLOBAL, {'GLOBAL': '1'})
    openbao.put(TEAM_WEB, {'WEB_KEY': 'x'})
    openbao.put(USER_GLOBAL, {'MINE': 'secret'})
    openbao.put(USER_WEB, {'MINE_WEB': 'secret'})
    dest = tmp_path / 'out.dbenv'

    assert export(openbao_root, ExportOptions(dest=str(dest), recipients=[f'@{pub}'])) == 0

    manifest, members = bundle.unpack(cipher.decrypt(dest.read_bytes(), identities=[str(key)]))
    assert {e['path'] for e in manifest['files']} == {'env/global.env', 'env/projects/web/.env'}
    assert manifest['version'] == 1
    assert members['env/global.env'] == b'GLOBAL=1\n'
    assert members['env/projects/web/.env'] == b'WEB_KEY=x\n'
    assert openbao.requests_to(USER_GLOBAL) == []
    assert openbao.requests_to(USER_WEB) == []
    raw = dest.read_bytes()
    assert b'MINE' not in raw


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def test_import_writes_to_the_server_and_keeps_an_encrypted_backup(openbao_root, openbao,
                                                                   bundle_keys, tmp_path):
    pub, key = bundle_keys
    openbao.put(TEAM_GLOBAL, {'EXISTING': 'old', 'KEEP': '1'})
    openbao.put(USER_GLOBAL, {'MINE': 'secret'})
    src = make_bundle(tmp_path, pub, {
        'env/global.env': b'EXISTING=new\nADDED=2\n',
        'env/projects/web/.env': b'WEB=1\n',
    })

    rc = import_bundle(openbao_root, ImportOptions(
        source=str(src), merge='prefer-incoming', identities=[str(key)]))

    assert rc == 0
    assert openbao.get(TEAM_GLOBAL) == {'EXISTING': 'new', 'KEEP': '1', 'ADDED': '2'}
    assert openbao.get(TEAM_WEB) == {'WEB': '1'}
    assert openbao.get(USER_GLOBAL) == {'MINE': 'secret'}
    assert not any(r.method == 'POST' for r in openbao.requests_to(USER_GLOBAL))

    backups = list((openbao_root / 'backups' / 'env-import').rglob('*'))
    files = [p for p in backups if p.is_file()]
    assert files and all(p.suffix == '.age' for p in files)
    assert all(b'old' not in p.read_bytes() for p in files)
    from devbase.env.secret_store import AgeBackend
    restored = [cipher.decrypt(p.read_bytes(), identities=AgeBackend(openbao_root).identities())
                for p in files]
    assert b'EXISTING=old\nKEEP=1\n' in restored


def test_import_replace_removes_keys_on_the_server(openbao_root, openbao, bundle_keys,
                                                   tmp_path):
    pub, key = bundle_keys
    openbao.put(TEAM_GLOBAL, {'GONE': 'x', 'KEEP': '1'})
    src = make_bundle(tmp_path, pub, {'env/global.env': b'KEEP=1\n'})

    rc = import_bundle(openbao_root, ImportOptions(
        source=str(src), replace=True, identities=[str(key)], include_metadata=False))

    assert rc == 0
    assert openbao.get(TEAM_GLOBAL) == {'KEEP': '1'}
    assert openbao.requests_of('DELETE') == []     # キー単位の削除は無く、丸ごと置き換える


def test_import_dry_run_does_not_touch_the_server(openbao_root, openbao, bundle_keys,
                                                  tmp_path):
    pub, key = bundle_keys
    src = make_bundle(tmp_path, pub, {'env/global.env': b'A=1\n'})

    assert import_bundle(openbao_root, ImportOptions(
        source=str(src), dry_run=True, identities=[str(key)])) == 0

    assert not any(r.kv_path for r in openbao.requests_of('POST'))
    assert not (openbao_root / 'backups').exists()


def test_import_without_a_recipient_key_writes_nothing(openbao_root, openbao, bundle_keys,
                                                       tmp_path, monkeypatch):
    from devbase.env import secret_store

    pub, key = bundle_keys
    openbao.put(TEAM_GLOBAL, {'KEEP': '1'})
    src = make_bundle(tmp_path, pub, {'env/global.env': b'A=1\n'})
    monkeypatch.setattr(secret_store.AgeBackend, 'encrypt_bytes',
                        lambda self, data: (_ for _ in ()).throw(
                            secret_store.SecretStoreError('no recipients')))

    with pytest.raises(EnvImportError, match='退避'):
        import_bundle(openbao_root, ImportOptions(
            source=str(src), identities=[str(key)], include_metadata=False))

    assert openbao.get(TEAM_GLOBAL) == {'KEEP': '1'}
    assert not any(r.kv_path for r in openbao.requests_of('POST'))


def test_import_rolls_back_applied_references_when_a_later_one_fails(openbao_root, openbao,
                                                                     bundle_keys, tmp_path):
    """先に書けた参照の巻き戻しは、自分の保存で進んだ版を基準にして通る"""
    pub, key = bundle_keys
    openbao.put(TEAM_GLOBAL, {'OLD': '1'})
    src = make_bundle(tmp_path, pub, {
        'env/global.env': b'OLD=1\nNEW=2\n',
        'env/projects/web/.env': b'WEB=1\n',
    })
    # 1 回目 (global の保存) は通し、2 回目 (web の保存) で落とす。3 回目以降 (巻き戻し) は通す
    openbao.fail_write_attempts = [2]

    with pytest.raises(EnvImportError):
        import_bundle(openbao_root, ImportOptions(
            source=str(src), merge='prefer-incoming', identities=[str(key)],
            include_metadata=False))

    assert openbao.get(TEAM_GLOBAL) == {'OLD': '1'}
    assert openbao.get(TEAM_WEB) == {}
    posts = [r for r in openbao.requests_of('POST') if r.kv_path == TEAM_GLOBAL]
    assert [p.cas for p in posts] == [1, 2]      # 取り込み → 巻き戻し (保存後の版)


def test_import_rolls_back_a_reference_whose_result_is_unknown(openbao_root, openbao,
                                                               bundle_keys, tmp_path):
    """応答が届かなかった参照も、控えた値で取り込み前へ戻す"""
    pub, key = bundle_keys
    openbao.put(TEAM_GLOBAL, {'OLD': '1'})
    src = make_bundle(tmp_path, pub, {'env/global.env': b'NEW1=1\nNEW2=2\nOLD=1\n'})
    openbao.drop_write_response = True

    with pytest.raises(EnvImportError):
        import_bundle(openbao_root, ImportOptions(
            source=str(src), merge='prefer-incoming', identities=[str(key)],
            include_metadata=False))

    # 結果が分からない参照は基準を捨てて読み直し、現在の版で控えた値を書き戻す
    # (偽サーバは応答を落とす前に反映しているので、巻き戻しもサーバへ届く)
    assert openbao.get(TEAM_GLOBAL) == {'OLD': '1'}
    posts = [r for r in openbao.requests_of('POST') if r.kv_path == TEAM_GLOBAL]
    assert [p.cas for p in posts] == [1, 2]
    # 計画の元 + 巻き戻し前の読み直し (結果不明の参照は基準を捨てる)。退避は取り直さない
    assert len(openbao.requests_of('GET')) == 2


def test_import_does_not_roll_back_a_reference_the_server_refused(openbao_root, openbao,
                                                                  bundle_keys, tmp_path):
    """版の不一致で拒まれた参照は巻き戻さない (CAS が守った他の利用者の更新を消さない)"""
    import devbase.env.io_import as io_import_mod

    pub, key = bundle_keys
    openbao.put(TEAM_GLOBAL, {'OLD': '1'})
    openbao.put(TEAM_WEB, {'W': 'old'})
    src = make_bundle(tmp_path, pub, {
        'env/global.env': b'OLD=1\nNEW=2\n',
        'env/projects/web/.env': b'W=mine\n',
    })
    # 退避のための読み出しの後、web を他の利用者が書き換える → web の保存が CAS で落ちる
    original = io_import_mod._apply_via_backend

    def apply_with_race(store, plans, backups):
        openbao.put(TEAM_WEB, {'W': 'theirs'})
        return original(store, plans, backups)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(io_import_mod, '_apply_via_backend', apply_with_race)
    try:
        with pytest.raises(EnvImportError):
            import_bundle(openbao_root, ImportOptions(
                source=str(src), merge='prefer-incoming', identities=[str(key)],
                include_metadata=False))
    finally:
        monkey.undo()

    assert openbao.get(TEAM_GLOBAL) == {'OLD': '1'}          # 先に書けた参照は戻る
    assert openbao.get(TEAM_WEB) == {'W': 'theirs'}          # 他の利用者の更新は残る
    assert not any(r.kv_path == TEAM_WEB and r.cas == 2 for r in openbao.requests_of('POST'))


def test_import_backs_up_the_value_the_plan_was_built_from_without_refetching(
        openbao_root, openbao, bundle_keys, tmp_path):
    """計画の元と現物の間に入った他の利用者の更新は、CAS で止まる (取り直して上書きしない)"""
    import devbase.env.io_import as io_import_mod

    pub, key = bundle_keys
    openbao.put(TEAM_GLOBAL, {'OLD': '1'})
    src = make_bundle(tmp_path, pub, {'env/global.env': b'NEW=2\n'})
    original = io_import_mod._backup_via_backend

    def backup_after_a_race(store, plans, backup_dir):
        openbao.put(TEAM_GLOBAL, {'OLD': '1', 'THEIRS': 'x'})
        return original(store, plans, backup_dir)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(io_import_mod, '_backup_via_backend', backup_after_a_race)
    try:
        with pytest.raises(EnvImportError):
            import_bundle(openbao_root, ImportOptions(
                source=str(src), merge='prefer-incoming', identities=[str(key)],
                include_metadata=False))
    finally:
        monkey.undo()

    assert openbao.get(TEAM_GLOBAL) == {'OLD': '1', 'THEIRS': 'x'}
    assert len(openbao.requests_of('GET')) == 1


def test_import_does_not_commit_metadata_when_the_server_refuses(openbao_root, openbao,
                                                                 bundle_keys, tmp_path):
    """--merge-metadata の sources.yml はサーバへの適用が通ってから確定する"""
    pub, key = bundle_keys
    openbao.team_writable = False
    src = make_bundle(tmp_path, pub, {
        'env/global.env': b'NEW=1\n',
        'env/sources.yml': b'aws:\n  path: /tmp/x\n',
    })

    with pytest.raises(EnvImportError):
        import_bundle(openbao_root, ImportOptions(
            source=str(src), merge='prefer-incoming', identities=[str(key)],
            include_metadata=True, merge_metadata=True))

    assert not (openbao_root / '.env.sources.yml').exists()
    assert openbao.get(TEAM_GLOBAL) == {}
    assert not list(openbao_root.glob('*.import.tmp'))


def test_import_stops_before_writing_when_the_server_is_unreachable(openbao_root, openbao,
                                                                    bundle_keys, tmp_path):
    """控えがあっても、取り込みは現物を読めなければ始めない"""
    from devbase.env.secret_store import SecretRef, SecretStore

    pub, key = bundle_keys
    openbao.put(TEAM_GLOBAL, {'OLD': '1'})
    SecretStore(openbao_root).load(SecretRef.for_global())     # 控えを作る
    src = make_bundle(tmp_path, pub, {'env/global.env': b'NEW=2\n'})
    openbao.stop()

    with pytest.raises(EnvImportError) as exc:
        import_bundle(openbao_root, ImportOptions(
            source=str(src), merge='prefer-incoming', identities=[str(key)],
            include_metadata=False))

    assert '到達' in str(exc.value)
    assert not (openbao_root / 'backups').exists()     # 退避も作らず、書く前に止まる


def test_import_into_an_explicit_age_backend_encrypts_new_references(tmp_path, monkeypatch,
                                                                     bundle_keys):
    """backend: age で保存先がまだ無い参照も、平文ではなく暗号文として保存される"""
    from devbase.env import agekeys, backend_config as bc
    from devbase.env.secret_store import SecretRef, SecretStore

    root = tmp_path / 'root'
    (root / 'projects' / 'web').mkdir(parents=True)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    agekeys.generate_key_file()
    bc.save(root, bc.BackendConfig(backend='age'))
    pub, key = bundle_keys
    src = make_bundle(tmp_path, pub, {
        'env/global.env': b'SECRET=plain-value\n',
        'env/projects/web/.env': b'WEB=plain-web\n',
    })

    assert import_bundle(root, ImportOptions(
        source=str(src), identities=[str(key)], include_metadata=False)) == 0

    for path in (root / 'secrets' / 'global.env.age', root / 'secrets' / 'projects' / 'web.env.age'):
        raw = path.read_bytes()
        assert raw.startswith(b'age-encryption.org/')
        assert b'plain-' not in raw
    store = SecretStore(root)
    assert store.load(SecretRef.for_global()) == {'SECRET': 'plain-value'}
    assert store.load(SecretRef.for_project('web')) == {'WEB': 'plain-web'}
    assert not (root / '.env').exists()


def test_import_of_unusable_name_into_server_names_the_store_not_projects(
        openbao_root, openbao, bundle_keys, tmp_path, caplog):
    """形に合わない名前もサーバへ保存し、保存先を1回知らせる現状を固定する。"""
    import logging

    pub, key = bundle_keys
    src = make_bundle(tmp_path, pub, {'env/projects/_foo/.env': b'FOO=plain-foo\n'})

    with caplog.at_level(logging.WARNING):
        assert import_bundle(openbao_root, ImportOptions(
            source=str(src), identities=[str(key)], include_global=False,
            include_metadata=False)) == 0

    assert not (openbao_root / 'projects' / '_foo').exists()
    assert openbao.get('team/projects/_foo') == {'FOO': 'plain-foo'}
    warnings = [r.getMessage() for r in caplog.records
                if r.levelno == logging.WARNING
                and 'プロジェクト名として使えない形の名前' in r.getMessage()]
    assert len(warnings) == 1
    [message] = warnings
    assert "'_foo'" in message
    assert "サーバのプロジェクト '_foo'" in message
    assert 'projects/ には何も作られません' in message


def test_import_of_unusable_name_into_age_names_the_store_not_projects(tmp_path, monkeypatch,
                                                                      bundle_keys, caplog):
    """PLAN66 受け入れ条件 9: age の保存先では ``projects/`` を作らず、知らせは保存先を名指しする"""
    import logging

    from devbase.env import agekeys, backend_config as bc

    root = tmp_path / 'root'
    root.mkdir()
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    agekeys.generate_key_file()
    bc.save(root, bc.BackendConfig(backend='age'))
    pub, key = bundle_keys
    src = make_bundle(tmp_path, pub, {'env/projects/_foo/.env': b'FOO=plain-foo\n'})

    with caplog.at_level(logging.WARNING):
        assert import_bundle(root, ImportOptions(
            source=str(src), identities=[str(key)], include_global=False,
            include_metadata=False)) == 0

    assert not (root / 'projects' / '_foo').exists()
    stored = root / 'secrets' / 'projects' / '_foo.env.age'
    assert stored.read_bytes().startswith(b'age-encryption.org/')
    warnings = [r.getMessage() for r in caplog.records
                if r.levelno == logging.WARNING
                and 'プロジェクト名として使えない形の名前' in r.getMessage()]
    assert len(warnings) == 1
    [message] = warnings
    assert "'_foo'" in message
    assert str(stored) in message
    assert 'projects/ には何も作られません' in message
    assert 'を作ります' not in message
    assert '改名' not in message


# ---------------------------------------------------------------------------
# グループ別の置き場 (PLAN56 受け入れ条件 13・決定 12・13)
# ---------------------------------------------------------------------------

GROUPED_SECRET = 'do-not-print-this-value'


@pytest.fixture
def grouped(openbao_root, openbao):
    """``version: 2`` (``default`` → ``nyle``)。``web`` は ``with``、``api`` は宣言なし"""
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, layout='group', group_aliases={'default': 'nyle'})
    (openbao_root / 'projects' / 'web' / 'env').write_text('DEVBASE_ACCOUNT_GROUP=with\n')
    (openbao_root / 'projects' / 'api').mkdir()
    return openbao_root


def kv_paths(openbao):
    return {r.kv_path for r in openbao.received if r.kv_path}


def test_grouped_export_collects_only_the_target_groups_projects(grouped, openbao, bundle_keys,
                                                                 tmp_path, caplog):
    pub, key = bundle_keys
    openbao.put('team/nyle/global', {'GLOBAL': '1'})
    openbao.put('team/nyle/projects/api', {'API': '1'})
    openbao.put('team/with/projects/web', {'WEB': GROUPED_SECRET})
    dest = tmp_path / 'out.dbenv'

    assert export(grouped, ExportOptions(dest=str(dest), recipients=[f'@{pub}'])) == 0

    _, members = bundle.unpack(cipher.decrypt(dest.read_bytes(), identities=[str(key)]))
    assert set(members) == {'env/global.env', 'env/projects/api/.env'}
    assert kv_paths(openbao) == {'team/nyle/global', 'team/nyle/projects/api'}
    skipped = [r.getMessage() for r in caplog.records
               if r.levelno >= 30 and 'web' in r.getMessage()]
    assert len(skipped) == 1 and 'with' in skipped[0]
    assert GROUPED_SECRET not in caplog.text
    assert openbao.secret_id not in caplog.text


def test_grouped_export_bundles_the_target_groups_sources_file(grouped, openbao, bundle_keys,
                                                               tmp_path, monkeypatch):
    pub, key = bundle_keys
    openbao.put('team/with/global', {'GLOBAL': '1'})
    (grouped / '.env.sources.yml').write_text('sources: {flat: {}}\n')
    (grouped / '.env.sources.nyle.yml').write_text('sources: {nyle: {}}\n')
    (grouped / '.env.sources.with.yml').write_text('sources: {with: {}}\n')
    monkeypatch.setenv('PWD', str(grouped / 'projects' / 'web'))
    dest = tmp_path / 'out.dbenv'

    assert export(grouped, ExportOptions(dest=str(dest), recipients=[f'@{pub}'])) == 0

    _, members = bundle.unpack(cipher.decrypt(dest.read_bytes(), identities=[str(key)]))
    assert members['env/sources.yml'] == b'sources: {with: {}}\n'
    assert set(members) == {'env/global.env', 'env/sources.yml'}
    assert kv_paths(openbao) <= {'team/with/global', 'team/with/projects/web'}


def test_grouped_import_refuses_a_bundle_with_another_groups_project(grouped, openbao,
                                                                     bundle_keys, tmp_path):
    pub, key = bundle_keys
    src = make_bundle(tmp_path, pub, {
        'env/global.env': b'A=1\n',
        'env/projects/api/.env': b'API=1\n',
        'env/projects/web/.env': f'WEB={GROUPED_SECRET}\n'.encode(),
    })

    with pytest.raises(EnvImportError) as exc:
        import_bundle(grouped, ImportOptions(source=str(src), identities=[str(key)]))

    message = str(exc.value)
    assert 'web' in message and 'with' in message and '--exclude-project' in message
    assert 'api' not in message
    assert GROUPED_SECRET not in message
    assert openbao.received == []
    assert not (grouped / 'backups').exists()


def test_grouped_import_dry_run_is_refused_as_well(grouped, openbao, bundle_keys, tmp_path):
    pub, key = bundle_keys
    src = make_bundle(tmp_path, pub, {'env/projects/web/.env': b'WEB=1\n'})

    with pytest.raises(EnvImportError):
        import_bundle(grouped, ImportOptions(source=str(src), identities=[str(key)],
                                             dry_run=True))

    assert openbao.received == []


def test_grouped_import_with_the_other_group_excluded_writes_the_target_group(
        grouped, openbao, bundle_keys, tmp_path):
    pub, key = bundle_keys
    src = make_bundle(tmp_path, pub, {
        'env/global.env': b'A=1\n',
        'env/projects/api/.env': b'API=1\n',
        'env/projects/web/.env': b'WEB=1\n',
        'env/sources.yml': b'sources:\n  aws: {type: tar_base64}\n',
    })

    assert import_bundle(grouped, ImportOptions(
        source=str(src), identities=[str(key)], exclude_projects=['web'],
        merge_metadata=True)) == 0

    assert openbao.get('team/nyle/global') == {'A': '1'}
    assert openbao.get('team/nyle/projects/api') == {'API': '1'}
    assert kv_paths(openbao) == {'team/nyle/global', 'team/nyle/projects/api'}
    assert (grouped / '.env.sources.nyle.yml').is_file()
    assert not (grouped / '.env.sources.yml').exists()
