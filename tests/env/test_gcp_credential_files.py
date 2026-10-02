"""GCP の鍵ファイルの発見と、正規化・衝突の警告 (#334 I6〜I9)"""

from __future__ import annotations

import builtins
import json
import logging

import pytest

from devbase.env import keys
from devbase.env.collectors import google
from devbase.env.host_import import HostImport, ImportPolicy
from devbase.env.store import EnvFile

RENAMED = "プロファイル名 'my-proj' を 'my_proj' に正規化しました"


@pytest.fixture
def creds(tmp_path, monkeypatch):
    path = tmp_path / 'gcp-credentials'
    path.mkdir()
    monkeypatch.setattr(google, 'GCP_CREDENTIALS_DIR', path)
    monkeypatch.setattr(google, 'LEGACY_CREDENTIALS_FILE', tmp_path / 'google_credential.json')
    return path


def write_key(path, project='p'):
    path.write_text(json.dumps({'project_id': project}))


def warnings(caplog) -> list:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_discovery_prints_and_logs_nothing(creds, caplog, capsys):
    """I6: 発見は正規化と衝突を値として返し、何も出力しない"""
    for name in ('a-b.json', 'a_b.json', 'my-proj.json', 'plain.json', 'note.txt'):
        write_key(creds / name)
    (creds / 'broken.json').write_text('{')
    caplog.set_level(logging.DEBUG)

    found = google.find_credential_files()

    assert list(found.files) == ['a_b', 'broken', 'my_proj', 'plain']
    assert found.files['a_b'] == creds / 'a-b.json'
    assert found.renamed == {'a_b': 'a-b', 'my_proj': 'my-proj'}
    assert found.collisions == {'a_b': [creds / 'a_b.json']}
    assert caplog.records == []
    assert capsys.readouterr() == ('', '')


def test_lookup_falls_back_to_the_legacy_file_for_default(creds, tmp_path):
    """I9: ディレクトリに default が無ければ ~/google_credential.json を引く"""
    write_key(creds / 'plain.json')
    write_key(tmp_path / 'google_credential.json')

    found = google.find_credential_files()

    assert found.lookup('default') == tmp_path / 'google_credential.json'
    assert found.lookup('plain') == creds / 'plain.json'
    assert found.lookup('missing') is None


def test_the_legacy_file_is_found_when_the_directory_has_no_keys(creds, tmp_path):
    write_key(tmp_path / 'google_credential.json')

    assert google.find_credential_files().files == {
        'default': tmp_path / 'google_credential.json'}


def test_warnings_are_only_for_the_chosen_names(creds, caplog):
    """I7・I8: 取り込むと決めた名前についてだけ、1 回ずつ出す"""
    for name in ('a-b.json', 'a_b.json', 'my-proj.json', 'plain.json'):
        write_key(creds / name)
    found = google.find_credential_files()

    google._warn_for_chosen(['plain'], found)
    assert warnings(caplog) == []

    google._warn_for_chosen(['a_b', 'my_proj'], found)
    assert warnings(caplog) == [
        "プロファイル名 'a-b' を 'a_b' に正規化しました",
        f"プロファイル名 'a_b' が衝突しています: '{creds / 'a-b.json'}' と '{creds / 'a_b.json'}' "
        "(後者をスキップ)",
        RENAMED,
    ]


@pytest.mark.parametrize('policy, answer, expected', [
    (ImportPolicy.SKIP, '', 0),
    (ImportPolicy.ASK, '', 0),
    (ImportPolicy.ASK, '2', 0),   # plain だけ
    (ImportPolicy.ASK, '1', 1),   # my_proj
    (ImportPolicy.IMPORT, '', 1),
])
def test_the_collector_warns_once_after_choosing(creds, tmp_path, monkeypatch, caplog,
                                                 policy, answer, expected):
    """AC11〜AC15・I7: 警告は取り込むと決めた後に、決めた名前についてだけ出す"""
    write_key(creds / 'my-proj.json', 'proj-a')
    write_key(creds / 'plain.json', 'proj-b')
    monkeypatch.setattr(builtins, 'input',
                        lambda prompt='': answer if '取り込む番号' in prompt else '')
    env_file = EnvFile(tmp_path / '.env')

    google.collect_google_credentials(env_file, host=HostImport(policy, 'globex'))

    assert warnings(caplog).count(RENAMED) == expected
    if expected:
        assert env_file.get(keys.gcp_credentials_key('my_proj'))
        assert env_file.get(keys.GCP_ACTIVE_PROFILE) == 'my_proj'
        assert env_file.get(keys.GOOGLE_CLOUD_PROJECT) == 'proj-a'
