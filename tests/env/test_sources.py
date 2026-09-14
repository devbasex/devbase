"""SourcesManager.check_changed の現在の三値判定を固定する。"""

from pathlib import Path

import pytest

from devbase.env import sources


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'exists', lambda self: False)
    return sources.SourcesManager(tmp_path)


@pytest.mark.parametrize('source_type', ['file_base64', 'tar_base64'])
@pytest.mark.parametrize(('current_hash', 'expected'), [
    ('saved-hash', False),
    ('changed-hash', True),
    (None, None),
])
def test_check_changed_hash_comparison(manager, monkeypatch, source_type,
                                       current_hash, expected):
    monkeypatch.setattr(sources, 'file_hash', lambda path: current_hash)
    monkeypatch.setattr(sources, 'dir_hash', lambda directory, filenames: current_hash)
    manager.set_source('credentials', source_type, ['credentials.json'],
                       'CREDENTIALS_BASE64', 'saved-hash')

    assert manager.check_changed('credentials') is expected


def test_check_changed_unregistered_source(manager):
    assert manager.check_changed('missing') is None


@pytest.mark.parametrize('source_type', ['file_base64', 'tar_base64'])
def test_check_changed_empty_saved_hash(manager, source_type):
    manager.set_source('credentials', source_type, ['credentials.json'],
                       'CREDENTIALS_BASE64', '')

    assert manager.check_changed('credentials') is None


@pytest.mark.parametrize('source_type', ['file_base64', 'tar_base64'])
def test_check_changed_empty_files(manager, source_type):
    manager.set_source('credentials', source_type, [],
                       'CREDENTIALS_BASE64', 'saved-hash')

    assert manager.check_changed('credentials') is None


def test_check_changed_unsupported_source_type(manager):
    manager.set_source('credentials', 'unsupported', ['credentials.json'],
                       'CREDENTIALS_BASE64', 'saved-hash')

    assert manager.check_changed('credentials') is None
