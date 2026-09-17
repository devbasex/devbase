"""import のサーバ更新後にローカル確定が失敗する経路の現状固定。"""

import os
from pathlib import Path

import pytest

from devbase.env import bundle
from devbase.env.io_import import ImportError as EnvImportError, ImportOptions, import_bundle
from devbase.env.secret_store import SecretRef, SecretStore


def test_import_restores_server_and_metadata_when_local_commit_fails(
    openbao_root, openbao, monkeypatch,
):
    old_values = {'TOKEN': 'old'}
    openbao.put('team/global', old_values)
    sources = openbao_root / '.env.sources.yml'
    original_metadata = b'# existing metadata\nsources:\n  existing: {type: tar_base64}\n'
    sources.write_bytes(original_metadata)
    incoming = openbao_root / 'incoming.dbenv'
    incoming.write_bytes(bundle.pack([
        bundle.BundleEntry(
            arcname='env/global.env', origin='env/global.env', data=b'TOKEN=new\n'),
        bundle.BundleEntry(
            arcname='env/sources.yml', origin='env/sources.yml',
            data=b'sources:\n  incoming: {type: tar_base64}\n'),
    ]))

    real_replace = os.replace
    failed = False

    def fail_first_metadata_replace(src, dst, *args, **kwargs):
        nonlocal failed
        if Path(dst) == sources and not failed:
            failed = True
            # 障害を入れる時点で、サーバへの更新が成功したことも観測する。
            assert openbao.get('team/global') == {'TOKEN': 'new'}
            raise OSError('metadata replacement failed')
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, 'replace', fail_first_metadata_replace)

    with pytest.raises(EnvImportError):
        import_bundle(openbao_root, ImportOptions(
            source=str(incoming), merge='prefer-incoming', merge_metadata=True))

    assert openbao.get('team/global') == old_values
    assert SecretStore(openbao_root).load(SecretRef.for_global()) == old_values
    assert sources.read_bytes() == original_metadata
    assert list(openbao_root.rglob('*.import.tmp')) == []
