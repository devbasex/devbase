"""取り込みの方針 (`devbase.env.host_import`) と AWS の切り出し (`devbase.env.aws_profiles`) (#314)"""

from __future__ import annotations

import logging

import pytest

from devbase.env import aws_profiles, host_import
from devbase.env.host_import import ImportPolicy


def write(root, text):
    path = root / 'secrets' / 'host-import.yml'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# ---------------------------------------------------------------------------
# 設定と方針
# ---------------------------------------------------------------------------

def test_no_file_means_every_group_asks(tmp_path):
    assert host_import.load(tmp_path) == {}
    assert host_import.resolve({}, 'kkg', interactive=True).policy is ImportPolicy.ASK


@pytest.mark.parametrize('text', ['', 'groups:\n', 'groups: {}\n'])
def test_an_empty_file_means_every_group_asks(tmp_path, text):
    write(tmp_path, text)
    assert host_import.load(tmp_path) == {}


def test_named_groups_get_their_policy(tmp_path):
    write(tmp_path, 'groups:\n  nyle: import\n  kkg: skip\n  with: ask\n')
    policies = host_import.load(tmp_path)

    assert host_import.resolve(policies, 'nyle', interactive=False).policy is ImportPolicy.IMPORT
    assert host_import.resolve(policies, 'kkg', interactive=True).policy is ImportPolicy.SKIP
    assert host_import.resolve(policies, 'with', interactive=True).policy is ImportPolicy.ASK
    assert host_import.resolve(policies, 'other', interactive=True).policy is ImportPolicy.ASK


def test_no_group_asks(tmp_path):
    """前提 4: 対象のグループが決まらなければ ask"""
    assert host_import.resolve({'nyle': ImportPolicy.IMPORT}, None,
                               interactive=True).policy is ImportPolicy.ASK


def test_ask_without_a_terminal_falls_back_to_skip(caplog):
    caplog.set_level(logging.INFO)
    host = host_import.resolve({}, 'kkg', interactive=False)

    assert host.policy is ImportPolicy.SKIP
    assert host.fell_back
    assert host.confirm('Git認証', '取り込みますか?') is False
    assert not host.choose('GCP認証', 't', ['a'], 'h')


@pytest.mark.parametrize('raw, indexes, all_', [
    ('', [], False),
    ('   ', [], False),
    ('2', [1], False),
    ('1,3', [0, 2], False),
    ('3 1 3', [2, 0], False),
    ('ALL', [0, 1, 2], True),
    ('4', [], False),
    ('0', [], False),
    ('1,x', [], False),
])
def test_parse_selection(raw, indexes, all_):
    selection = host_import.parse_selection(raw, 3)
    assert selection.indexes == indexes
    assert selection.all is all_


def test_import_selects_everything_without_asking(monkeypatch):
    monkeypatch.setattr('builtins.input', lambda prompt='': pytest.fail('asked'))
    host = host_import.HostImport(ImportPolicy.IMPORT, 'nyle')

    assert host.choose('AWS認証', 't', ['a', 'b'], 'h').all
    assert host.confirm('Git認証', 'q') is True


# ---------------------------------------------------------------------------
# AWS の切り出し
# ---------------------------------------------------------------------------

CONFIG = """\
# comment kept out
[default]
region = a

[profile  x]
sso_session = s
source_profile = y
note = 100%

[profile y]
source_profile = x

[sso-session s]
sso_start_url = u
"""


def test_build_copies_the_original_lines():
    payload = aws_profiles.build(CONFIG, None, ['x'])

    assert payload.config.decode() == (
        "[profile  x]\nsso_session = s\nsource_profile = y\nnote = 100%\n\n"
        "[sso-session s]\nsso_start_url = u\n"
        "[profile y]\nsource_profile = x\n\n")
    assert payload.credentials is None
    # 循環した source_profile は 1 度だけ数える
    assert [(i.section, i.reason) for i in payload.included] == [
        ('sso-session s', 'profile x の sso_session'),
        ('profile y', 'profile x の source_profile'),
    ]


def test_missing_targets_are_reported_and_skipped():
    payload = aws_profiles.build('[profile a]\nsso_session = gone\nsource_profile = nope\n',
                                 None, ['a', 'zzz'])

    assert payload.missing == ['sso-session gone', 'profile nope']
    assert payload.unknown == ['zzz']


def test_a_source_profile_only_in_credentials_is_included():
    payload = aws_profiles.build('[profile a]\nsource_profile = base\n',
                                 '[base]\nk = v\n[other]\nk = w\n', ['a'])

    assert payload.credentials.decode() == '[base]\nk = v\n'
    assert payload.included[0].section == 'base'


def test_a_header_with_trailing_text_starts_its_own_section():
    # configparser (AWS の CLI) は `[private] trailing` を private の節と読む。
    # 直前の dev の節へ取り込むと、dev だけを選んでも private の秘密鍵が送られる
    credentials = ('[dev]\naws_secret_access_key = d\n'
                   '[private] trailing\naws_secret_access_key = p\n')
    payload = aws_profiles.build(None, credentials, ['dev'])

    assert payload.credentials.decode() == '[dev]\naws_secret_access_key = d\n'
    assert aws_profiles.credential_names(credentials) == ['dev', 'private']


def test_headers_that_differ_only_in_spaces_stop_the_extraction():
    # configparser は `[dev]` と `[ dev ]` を別の節と読む。同じ dev へ連結すると、
    # dev だけを選んでも別の節の秘密鍵が送られるため、中身を作らない
    credentials = ('[dev]\naws_secret_access_key = d\n'
                   '[ dev ]\naws_secret_access_key = p\n')
    payload = aws_profiles.build(None, credentials, ['dev'])

    assert payload.conflicts == ['dev']
    assert payload.credentials is None
    assert payload.encode() is None
    assert payload.digest() is None


def test_a_conflict_outside_the_selection_does_not_stop_the_extraction():
    credentials = ('[dev]\naws_secret_access_key = d\n'
                   '[other]\nk = a\n[ other ]\nk = b\n')
    payload = aws_profiles.build(None, credentials, ['dev'])

    assert payload.conflicts == []
    assert payload.credentials.decode() == '[dev]\naws_secret_access_key = d\n'


def test_an_indented_bracket_line_in_a_value_is_not_a_header():
    # 値の続きの行は、見出しに見えても configparser と同じく値の一部とする
    text = '[dev]\nk = a\n  [x]\n\n  b\n[w]\nk = c\n'

    assert aws_profiles.split_sections(text) == {
        'dev': '[dev]\nk = a\n  [x]\n\n  b\n', 'w': '[w]\nk = c\n'}


def test_the_encoded_value_is_stable_and_readable():
    payload = aws_profiles.build(CONFIG, '[x]\nk = v\n', ['x'])

    assert payload.encode() == aws_profiles.build(CONFIG, '[x]\nk = v\n', ['x']).encode()
    assert aws_profiles.profiles_in_value(payload.encode()) == ['x', 'y']


def test_candidates_fall_back_to_credentials_sections():
    assert aws_profiles.candidate_names(CONFIG, None) == ['default', 'x', 'y']
    assert aws_profiles.candidate_names(None, '[a]\n[b]\n') == ['a', 'b']


def test_an_unreadable_value_has_no_profiles():
    assert aws_profiles.profiles_in_value('old') is None
    assert aws_profiles.profiles_in_value(None) is None
