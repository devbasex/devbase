"""export / import が暗号化された保存先を壊さないことの検証

移行後の環境で ``devbase env import`` が平文の ``.env`` を作ってしまうと、
暗号化ファイルと平文が同時に存在する状態になり、以後どちらが正か判断できなく
なる (plan35 §9)。往復しても保存形式が保たれることを確かめる。
"""

from __future__ import annotations

import pyrage
import pytest

from devbase.env import agekeys
from devbase.env.io_export import ExportOptions, export
from devbase.env.io_import import ImportOptions, import_bundle
from devbase.env.secret_store import SecretRef, SecretStore


GLOBAL = SecretRef.for_global()
WEB = SecretRef.for_project('web')


@pytest.fixture
def keypair(tmp_path):
    identity = pyrage.x25519.Identity.generate()
    path = tmp_path / 'bundle.key'
    path.write_text(str(identity))
    return str(identity.to_public()), str(path)


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = tmp_path / 'devbase'
    (root / 'projects' / 'web').mkdir(parents=True)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('PWD', str(root))
    monkeypatch.chdir(root)
    agekeys.generate_key_file()
    return root


def do_export(root, dest, recipient):
    return export(root, ExportOptions(dest=str(dest), recipients=[recipient]))


def do_import(root, source, identity, **kwargs):
    return import_bundle(root, ImportOptions(
        source=str(source), identities=[identity], **kwargs))


def test_export_reads_the_encrypted_store(root, keypair, tmp_path):
    public, key = keypair
    store = SecretStore(root)
    store.age.save(GLOBAL, {'TOKEN': 'sk-1'})
    store.age.save(WEB, {'DB_PASSWORD': 'pw'})

    bundle = tmp_path / 'b.dbenv'
    assert do_export(root, bundle, public) == 0

    # 別の空の DEVBASE_ROOT へ取り込むと平文で復元される (移行前と同じ形)
    other = tmp_path / 'other'
    (other / 'projects' / 'web').mkdir(parents=True)
    assert do_import(other, bundle, key) == 0
    assert SecretStore(other).load(GLOBAL) == {'TOKEN': 'sk-1'}
    assert SecretStore(other).load(WEB) == {'DB_PASSWORD': 'pw'}


def test_import_keeps_the_destination_encrypted(root, keypair, tmp_path):
    public, key = keypair
    store = SecretStore(root)
    store.age.save(GLOBAL, {'TOKEN': 'old', 'KEEP': '1'})

    bundle = tmp_path / 'b.dbenv'
    do_export(root, bundle, public)

    # バンドルの値を書き換えて取り込む
    store.age.save(GLOBAL, {'TOKEN': 'newer'})
    assert do_import(root, bundle, key, merge='prefer-incoming') == 0

    assert store.is_encrypted(GLOBAL)
    assert not (root / '.env').exists()          # 平文が生まれていない
    assert store.load(GLOBAL)['TOKEN'] == 'old'  # バンドル側が勝つ
    assert store.load(GLOBAL)['KEEP'] == '1'


def test_import_merges_into_the_encrypted_store(root, keypair, tmp_path):
    public, key = keypair
    store = SecretStore(root)
    store.age.save(GLOBAL, {'FROM_BUNDLE': 'b'})

    bundle = tmp_path / 'b.dbenv'
    do_export(root, bundle, public)

    store.age.save(GLOBAL, {'LOCAL_ONLY': 'l'})
    assert do_import(root, bundle, key) == 0

    merged = store.load(GLOBAL)
    assert merged == {'LOCAL_ONLY': 'l', 'FROM_BUNDLE': 'b'}


def test_import_backups_are_ciphertext(root, keypair, tmp_path):
    """取り込み前の控えが平文でディスクに残らない (plan35 §2.3)"""
    public, key = keypair
    store = SecretStore(root)
    store.age.save(GLOBAL, {'TOKEN': 'secret-value'})

    bundle = tmp_path / 'b.dbenv'
    do_export(root, bundle, public)
    do_import(root, bundle, key, merge='prefer-incoming')

    backups = list((root / 'backups' / 'env-import').rglob('*'))
    files = [p for p in backups if p.is_file()]
    assert files, '控えが作られていない'
    for path in files:
        assert b'secret-value' not in path.read_bytes(), path


def test_import_into_a_plaintext_store_stays_plaintext(root, keypair, tmp_path):
    """移行していない環境では従来どおり平文へ書く"""
    public, key = keypair
    store = SecretStore(root)
    store.plaintext.save(GLOBAL, {'TOKEN': 'sk-1'})

    bundle = tmp_path / 'b.dbenv'
    do_export(root, bundle, public)
    assert do_import(root, bundle, key, merge='prefer-incoming') == 0

    assert (root / '.env').exists()
    assert not (root / 'secrets' / 'global.env.age').exists()


def test_import_creates_plaintext_when_nothing_exists(root, keypair, tmp_path):
    """まだ何も無い参照は平文に落ちる (暗号化は encrypt の役目)"""
    public, key = keypair
    SecretStore(root).plaintext.save(GLOBAL, {'TOKEN': 'sk-1'})
    bundle = tmp_path / 'b.dbenv'
    do_export(root, bundle, public)

    other = tmp_path / 'fresh'
    other.mkdir()
    assert do_import(other, bundle, key) == 0
    assert (other / '.env').exists()
