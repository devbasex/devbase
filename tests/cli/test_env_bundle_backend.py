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
    # 計画の元 + 退避 + 巻き戻し前の読み直し (結果不明の参照は基準を捨てる)
    assert len(openbao.requests_of('GET')) == 3


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
