"""env export / import がサーバ backend 越しに動くことの検証 (PLAN51 設計 2)"""

from __future__ import annotations

import pyrage
import pytest

from devbase.env import bundle, cipher
from devbase.env.io_export import ExportOptions, export
from devbase.env.io_import import ImportError as EnvImportError, ImportOptions, import_bundle


TEAM_GLOBAL = '/team/global'
TEAM_WEB = '/team/projects/web'
USER_GLOBAL = '/users/member01/global'
USER_WEB = '/users/member01/projects/web'


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

def test_export_collects_team_secrets_from_the_server(infisical_root, infisical, bundle_keys,
                                                      tmp_path):
    pub, key = bundle_keys
    infisical.put(TEAM_GLOBAL, {'GLOBAL': '1'})
    infisical.put(TEAM_WEB, {'WEB_KEY': 'x'})
    infisical.put(USER_GLOBAL, {'MINE': 'secret'})
    infisical.put(USER_WEB, {'MINE_WEB': 'secret'})
    dest = tmp_path / 'out.dbenv'

    assert export(infisical_root, ExportOptions(dest=str(dest), recipients=[f'@{pub}'])) == 0

    manifest, members = bundle.unpack(cipher.decrypt(dest.read_bytes(), identities=[str(key)]))
    assert {e['path'] for e in manifest['files']} == {'env/global.env', 'env/projects/web/.env'}
    assert manifest['version'] == 1
    assert members['env/global.env'] == b'GLOBAL=1\n'
    assert members['env/projects/web/.env'] == b'WEB_KEY=x\n'
    assert infisical.requests_to(USER_GLOBAL) == []
    assert infisical.requests_to(USER_WEB) == []
    raw = dest.read_bytes()
    assert b'MINE' not in raw


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def test_import_writes_to_the_server_and_keeps_an_encrypted_backup(infisical_root, infisical,
                                                                   bundle_keys, tmp_path):
    pub, key = bundle_keys
    infisical.put(TEAM_GLOBAL, {'EXISTING': 'old', 'KEEP': '1'})
    infisical.put(USER_GLOBAL, {'MINE': 'secret'})
    src = make_bundle(tmp_path, pub, {
        'env/global.env': b'EXISTING=new\nADDED=2\n',
        'env/projects/web/.env': b'WEB=1\n',
    })

    rc = import_bundle(infisical_root, ImportOptions(
        source=str(src), merge='prefer-incoming', identities=[str(key)]))

    assert rc == 0
    assert infisical.get(TEAM_GLOBAL) == {'EXISTING': 'new', 'KEEP': '1', 'ADDED': '2'}
    assert infisical.get(TEAM_WEB) == {'WEB': '1'}
    assert infisical.get(USER_GLOBAL) == {'MINE': 'secret'}
    assert not any(r.secret_name for r in infisical.requests_to(USER_GLOBAL))

    backups = list((infisical_root / 'backups' / 'env-import').rglob('*'))
    files = [p for p in backups if p.is_file()]
    assert files and all(p.suffix == '.age' for p in files)
    assert all(b'old' not in p.read_bytes() for p in files)
    from devbase.env.secret_store import AgeBackend
    restored = [cipher.decrypt(p.read_bytes(), identities=AgeBackend(infisical_root).identities())
                for p in files]
    assert b'EXISTING=old\nKEEP=1\n' in restored


def test_import_replace_removes_keys_on_the_server(infisical_root, infisical, bundle_keys,
                                                   tmp_path):
    pub, key = bundle_keys
    infisical.put(TEAM_GLOBAL, {'GONE': 'x', 'KEEP': '1'})
    src = make_bundle(tmp_path, pub, {'env/global.env': b'KEEP=1\n'})

    rc = import_bundle(infisical_root, ImportOptions(
        source=str(src), replace=True, identities=[str(key)], include_metadata=False))

    assert rc == 0
    assert infisical.get(TEAM_GLOBAL) == {'KEEP': '1'}
    assert [r.secret_name for r in infisical.requests_of('DELETE')] == ['GONE']


def test_import_dry_run_does_not_touch_the_server(infisical_root, infisical, bundle_keys,
                                                  tmp_path):
    pub, key = bundle_keys
    src = make_bundle(tmp_path, pub, {'env/global.env': b'A=1\n'})

    assert import_bundle(infisical_root, ImportOptions(
        source=str(src), dry_run=True, identities=[str(key)])) == 0

    assert not any(r.secret_name for r in infisical.received)
    assert not (infisical_root / 'backups').exists()


def test_import_without_a_recipient_key_writes_nothing(infisical_root, infisical, bundle_keys,
                                                       tmp_path, monkeypatch):
    from devbase.env import secret_store

    pub, key = bundle_keys
    infisical.put(TEAM_GLOBAL, {'KEEP': '1'})
    src = make_bundle(tmp_path, pub, {'env/global.env': b'A=1\n'})
    monkeypatch.setattr(secret_store.AgeBackend, 'encrypt_bytes',
                        lambda self, data: (_ for _ in ()).throw(
                            secret_store.SecretStoreError('no recipients')))

    with pytest.raises(EnvImportError, match='退避'):
        import_bundle(infisical_root, ImportOptions(
            source=str(src), identities=[str(key)], include_metadata=False))

    assert infisical.get(TEAM_GLOBAL) == {'KEEP': '1'}
    assert not any(r.secret_name for r in infisical.received)


def test_import_rolls_back_applied_references_when_a_later_one_fails(infisical_root, infisical,
                                                                     bundle_keys, tmp_path):
    pub, key = bundle_keys
    infisical.put(TEAM_GLOBAL, {'OLD': '1'})
    src = make_bundle(tmp_path, pub, {
        'env/global.env': b'OLD=1\nNEW=2\n',
        'env/projects/web/.env': b'WEB=1\n',
    })
    # 1 回目 (global へ POST NEW) は通し、2 回目 (web へ POST WEB) で落とす。
    # 3 回目以降 (巻き戻しの DELETE NEW) は通す
    infisical.fail_write_attempts = [2]

    with pytest.raises(EnvImportError):
        import_bundle(infisical_root, ImportOptions(
            source=str(src), merge='prefer-incoming', identities=[str(key)],
            include_metadata=False))

    assert infisical.get(TEAM_GLOBAL) == {'OLD': '1'}
    assert infisical.get(TEAM_WEB) == {}


def test_import_rolls_back_a_reference_that_failed_halfway(infisical_root, infisical,
                                                           bundle_keys, tmp_path):
    """1 つの参照の途中で失敗しても、その参照の反映済みキーも取り込み前へ戻る"""
    pub, key = bundle_keys
    infisical.put(TEAM_GLOBAL, {'OLD': '1'})
    src = make_bundle(tmp_path, pub, {'env/global.env': b'NEW1=1\nNEW2=2\nOLD=1\n'})
    # POST NEW1 (1 回目) は通し、POST NEW2 (2 回目) で落とす。巻き戻しの DELETE NEW1 は通す
    infisical.fail_write_attempts = [2]

    with pytest.raises(EnvImportError):
        import_bundle(infisical_root, ImportOptions(
            source=str(src), merge='prefer-incoming', identities=[str(key)],
            include_metadata=False))

    assert infisical.get(TEAM_GLOBAL) == {'OLD': '1'}


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
