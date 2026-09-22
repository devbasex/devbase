"""import のサーバ更新後にローカル確定が失敗する経路の現状固定。"""

import logging
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


# ---------------------------------------------------------------------------
# 名前の形の知らせ (PLAN66 受け入れ条件 6〜8)。import は今と同じく通す (決定 1・4)
# ---------------------------------------------------------------------------

_NAME_FORM_WARNING = 'プロジェクト名として使えない形の名前'


def _name_form_warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and _NAME_FORM_WARNING in r.getMessage()]


def _write_project_bundle(tmp_path, names):
    src = tmp_path / 'incoming.dbenv'
    src.write_bytes(bundle.pack([
        bundle.BundleEntry(arcname=f'env/projects/{name}/.env',
                           origin=f'env/projects/{name}/.env', data=b'KEY=value\n')
        for name in names
    ]))
    return src


def _project_options(src, **kwargs):
    return ImportOptions(source=str(src), include_global=False, include_metadata=False,
                         **kwargs)


def test_import_creates_unusable_project_and_warns_once(tmp_path, caplog):
    """6: 今と同じく 2 つのディレクトリを作って 0 で終わり、``_foo`` だけを 1 回知らせる"""
    root = tmp_path / 'root'
    root.mkdir()
    src = _write_project_bundle(tmp_path, ['_foo', 'ok-name'])

    with caplog.at_level(logging.WARNING):
        assert import_bundle(root, _project_options(src)) == 0

    assert (root / 'projects' / '_foo' / '.env').read_bytes() == b'KEY=value\n'
    assert (root / 'projects' / 'ok-name' / '.env').read_bytes() == b'KEY=value\n'
    warnings = _name_form_warnings(caplog)
    assert len(warnings) == 1
    [message] = warnings
    assert "'_foo'" in message
    assert 'ok-name' not in message
    assert 'projects/_foo/ を作ります' in message
    assert '名前なしに打てば動きます' in message
    assert 'projects/_foo を改名' in message


def test_import_dry_run_warns_without_writing(tmp_path, caplog):
    """7: ``--dry-run`` では書き込まないが、知らせは出す"""
    root = tmp_path / 'root'
    root.mkdir()
    src = _write_project_bundle(tmp_path, ['_foo', 'ok-name'])

    with caplog.at_level(logging.WARNING):
        assert import_bundle(root, _project_options(src, dry_run=True)) == 0

    assert not (root / 'projects').exists()
    warnings = _name_form_warnings(caplog)
    assert len(warnings) == 1
    assert "'_foo'" in warnings[0]


def test_import_of_usable_names_does_not_warn(tmp_path, caplog):
    """8: 名前の形に合う名前だけなら、名前の形の警告は 1 行も出ない"""
    root = tmp_path / 'root'
    root.mkdir()
    src = _write_project_bundle(tmp_path, ['ok-name', 'carmo_ai'])

    with caplog.at_level(logging.WARNING):
        assert import_bundle(root, _project_options(src)) == 0

    assert (root / 'projects' / 'ok-name' / '.env').is_file()
    assert _name_form_warnings(caplog) == []
