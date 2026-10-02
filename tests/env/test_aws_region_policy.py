"""AWS の方法 2・方法 3 の region を取り込みの方針に従わせる (#334)

偽の HOME の ``~/.aws/config`` に ``[default]`` と ``[profile dev]`` の ``region = ap-southeast-2`` を置き、
既定の値 ``ap-northeast-1`` と区別する。利用者の実際の ``~/.aws`` は読まない。
"""

from __future__ import annotations

import builtins
import logging

import pytest

from devbase.env import host_import, keys
from devbase.env.collectors import aws
from devbase.env.host_import import HostImport, ImportPolicy
from devbase.env.store import EnvFile

HOST_REGION = 'ap-southeast-2'
CONFIG = f"""\
[default]
region = {HOST_REGION}

[profile dev]
region = {HOST_REGION}
"""
CREDENTIALS = """\
[default]
aws_access_key_id = AKIADEFAULT
aws_secret_access_key = SECRET-DEFAULT
"""
SKIPPED = 'AWS認証: 取り込まない設定のため飛ばしました (globex)'
DECLINED = 'AWS認証: 取り込みません'
AUTO = 'AWS_DEFAULT_REGION: 自動取得完了'
REGION_PROMPT = 'AWS_DEFAULT_REGION'


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / 'home'
    (path / '.aws').mkdir(parents=True)
    (path / '.aws' / 'config').write_text(CONFIG)
    monkeypatch.setenv('HOME', str(path))
    return path


@pytest.fixture
def with_keys(home):
    (home / '.aws' / 'credentials').write_text(CREDENTIALS)
    return home


@pytest.fixture
def env_file(tmp_path):
    return EnvFile(tmp_path / '.env')


class Answers:
    """``input`` の差し替え。質問の文に含まれる語 → 答え。当たらなければ空 (Enter)"""

    def __init__(self, monkeypatch, answers=None):
        self.answers = dict(answers or {})
        self.prompts: list = []
        monkeypatch.setattr(builtins, 'input', self)

    def __call__(self, prompt=''):
        self.prompts.append(prompt)
        for needle, value in self.answers.items():
            if needle in prompt:
                return value
        return ''

    def region_prompts(self) -> list:
        return [p for p in self.prompts if p.startswith(REGION_PROMPT)]


def place(policy: ImportPolicy) -> HostImport:
    return HostImport(policy, 'globex')


def messages(caplog) -> list:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]


@pytest.fixture
def no_host_region(monkeypatch):
    """ホストの region を読んだら落ちる (I1)"""
    def fail(self, profile):
        raise AssertionError(f'ホストの region を読んだ: {profile}')

    monkeypatch.setattr(aws.AWSConfigParser, 'find_profile_region', fail)
    monkeypatch.setattr(aws.AWSConfigParser, 'get_profile_region', fail)


# ---------------------------------------------------------------------------
# 方法 3 (Access Key)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('credentials', [False, True])
def test_skip_does_not_write_the_host_region(home, env_file, monkeypatch, caplog, no_host_region,
                                             credentials):
    """AC1・AC2・I1・I5: skip は ap-northeast-1 を書き、自動取得の行も出さない。知らせは 1 行"""
    if credentials:
        (home / '.aws' / 'credentials').write_text(CREDENTIALS)
    answers = Answers(monkeypatch)
    caplog.set_level(logging.INFO)

    aws._collect_access_keys(env_file, place(ImportPolicy.SKIP))

    assert env_file.get(keys.AWS_DEFAULT_REGION) == 'ap-northeast-1'
    assert answers.region_prompts() == ['AWS_DEFAULT_REGION (デフォルト: ap-northeast-1): ']
    assert not any(m.startswith(AUTO) for m in messages(caplog))
    assert messages(caplog).count(SKIPPED) == 1
    assert env_file.get(keys.AWS_ACCESS_KEY_ID) is None


def test_non_terminal_ask_does_not_write_the_host_region(with_keys, env_file, monkeypatch, caplog,
                                                         no_host_region):
    """AC3・I1: 端末でない ask から落ちた skip でも、ホストの region を書かない"""
    host = host_import.resolve({}, 'globex', interactive=False)
    Answers(monkeypatch, {'取り込みますか': 'y'})
    caplog.set_level(logging.INFO)

    aws._collect_access_keys(env_file, host)

    assert env_file.get(keys.AWS_DEFAULT_REGION) == 'ap-northeast-1'
    assert env_file.get(keys.AWS_ACCESS_KEY_ID) is None
    assert messages(caplog).count('AWS認証: 標準入力が端末でないため飛ばしました (globex)') == 1


def test_ask_declined_does_not_offer_the_host_region(with_keys, env_file, monkeypatch, caplog,
                                                     no_host_region):
    """AC4・I1・I5: 鍵の確認で断ると ap-northeast-1 を既定の値にした入力へ進む"""
    answers = Answers(monkeypatch, {'取り込みますか': 'N'})
    caplog.set_level(logging.INFO)

    aws._collect_access_keys(env_file, place(ImportPolicy.ASK))

    assert env_file.get(keys.AWS_DEFAULT_REGION) == 'ap-northeast-1'
    assert answers.region_prompts() == ['AWS_DEFAULT_REGION (デフォルト: ap-northeast-1): ']
    assert messages(caplog).count(DECLINED) == 1
    assert not any('飛ばしました' in m for m in messages(caplog))
    assert env_file.get(keys.AWS_ACCESS_KEY_ID) is None


def test_ask_accepted_writes_the_host_region_without_asking(with_keys, env_file, monkeypatch):
    """AC5・I2"""
    answers = Answers(monkeypatch, {'取り込みますか': 'y'})

    aws._collect_access_keys(env_file, place(ImportPolicy.ASK))

    assert env_file.get(keys.AWS_DEFAULT_REGION) == HOST_REGION
    assert answers.region_prompts() == []
    assert env_file.get(keys.AWS_ACCESS_KEY_ID) == 'AKIADEFAULT'


@pytest.mark.parametrize('answer, expected', [('', HOST_REGION), ('us-east-1', 'us-east-1')])
def test_ask_without_keys_offers_the_host_region_as_the_default(home, env_file, monkeypatch,
                                                                answer, expected):
    """AC6・I3: 鍵が無く確認を出さないときは、ホストの region を入力の既定の値として見せる"""
    answers = Answers(monkeypatch, {REGION_PROMPT: answer})

    aws._collect_access_keys(env_file, place(ImportPolicy.ASK))

    assert env_file.get(keys.AWS_DEFAULT_REGION) == expected
    assert answers.region_prompts() == [
        f'AWS_DEFAULT_REGION (デフォルト: {HOST_REGION}、~/.aws/config の [default] から): ']


def test_ask_without_keys_or_host_region_uses_the_fixed_default(home, env_file, monkeypatch):
    """I3: ホストに region が無ければ既定の値は ap-northeast-1"""
    (home / '.aws' / 'config').write_text('[default]\noutput = json\n')
    answers = Answers(monkeypatch)

    aws._collect_access_keys(env_file, place(ImportPolicy.ASK))

    assert env_file.get(keys.AWS_DEFAULT_REGION) == 'ap-northeast-1'
    assert answers.region_prompts() == ['AWS_DEFAULT_REGION (デフォルト: ap-northeast-1): ']


@pytest.mark.parametrize('credentials', [False, True])
def test_import_writes_the_host_region_without_asking(home, env_file, monkeypatch, credentials):
    """AC7・I2・退行 1: import は鍵とホストの region を書く"""
    if credentials:
        (home / '.aws' / 'credentials').write_text(CREDENTIALS)
    answers = Answers(monkeypatch)

    aws._collect_access_keys(env_file, place(ImportPolicy.IMPORT))

    assert env_file.get(keys.AWS_DEFAULT_REGION) == HOST_REGION
    assert answers.region_prompts() == []
    assert not any(p.startswith('取り込みますか') for p in answers.prompts)
    assert env_file.get(keys.AWS_ACCESS_KEY_ID) == ('AKIADEFAULT' if credentials else None)


@pytest.mark.parametrize('policy', list(ImportPolicy))
def test_an_existing_region_is_kept(with_keys, env_file, monkeypatch, caplog, no_host_region,
                                    policy):
    """退行 2・I4: 方法 3 は参照の region を残し、ホストを読まず、入力も出さない"""
    env_file.set(keys.AWS_DEFAULT_REGION, 'us-west-2')
    answers = Answers(monkeypatch, {'取り込みますか': 'y'})
    caplog.set_level(logging.INFO)

    aws._collect_access_keys(env_file, place(policy))

    assert env_file.get(keys.AWS_DEFAULT_REGION) == 'us-west-2'
    assert answers.region_prompts() == []
    assert 'AWS_DEFAULT_REGION: 設定済み (us-west-2)' in messages(caplog)
    # skip の知らせは鍵の確認が出す 1 行だけ (region では足さない)
    assert messages(caplog).count(SKIPPED) == (1 if policy is ImportPolicy.SKIP else 0)


# ---------------------------------------------------------------------------
# 方法 2 (SSO Profile)
# ---------------------------------------------------------------------------

def sso(env_file, policy, monkeypatch, region_answer=''):
    answers = Answers(monkeypatch, {'AWS_PROFILE': 'dev', REGION_PROMPT: region_answer})
    aws._collect_sso_profile(env_file, place(policy))
    assert env_file.get(keys.AWS_PROFILE) == 'dev'
    return answers


@pytest.mark.parametrize('existing', [None, 'us-west-2'])
def test_sso_skip_does_not_write_the_host_region(home, env_file, monkeypatch, caplog,
                                                 no_host_region, existing):
    """AC8・I1・I4・I5"""
    if existing:
        env_file.set(keys.AWS_DEFAULT_REGION, existing)
    caplog.set_level(logging.INFO)

    answers = sso(env_file, ImportPolicy.SKIP, monkeypatch)

    assert env_file.get(keys.AWS_DEFAULT_REGION) == 'ap-northeast-1'
    assert answers.region_prompts() == ['AWS_DEFAULT_REGION (デフォルト: ap-northeast-1): ']
    assert messages(caplog).count(SKIPPED) == 1
    assert not any('設定済み' in m for m in messages(caplog))


@pytest.mark.parametrize('existing', [None, 'us-west-2'])
def test_sso_ask_offers_the_host_region_as_the_default(home, env_file, monkeypatch, caplog,
                                                       existing):
    """AC9・I3・I4"""
    if existing:
        env_file.set(keys.AWS_DEFAULT_REGION, existing)
    caplog.set_level(logging.INFO)

    answers = sso(env_file, ImportPolicy.ASK, monkeypatch)

    assert env_file.get(keys.AWS_DEFAULT_REGION) == HOST_REGION
    assert answers.region_prompts() == [
        f'AWS_DEFAULT_REGION (デフォルト: {HOST_REGION}、~/.aws/config の [profile dev] から): ']
    assert not any('設定済み' in m or '飛ばしました' in m for m in messages(caplog))


def test_sso_ask_takes_a_typed_region(home, env_file, monkeypatch):
    """I3: 入れた値はその値を書く"""
    sso(env_file, ImportPolicy.ASK, monkeypatch, region_answer='us-east-1')

    assert env_file.get(keys.AWS_DEFAULT_REGION) == 'us-east-1'


@pytest.mark.parametrize('existing', [None, 'us-west-2'])
def test_sso_import_writes_the_host_region_without_asking(home, env_file, monkeypatch, caplog,
                                                          existing):
    """AC10・I2・I4"""
    if existing:
        env_file.set(keys.AWS_DEFAULT_REGION, existing)
    caplog.set_level(logging.INFO)

    answers = sso(env_file, ImportPolicy.IMPORT, monkeypatch)

    assert env_file.get(keys.AWS_DEFAULT_REGION) == HOST_REGION
    assert answers.region_prompts() == []
    assert f'AWS_DEFAULT_REGION: 自動取得完了 ({HOST_REGION})' in messages(caplog)
    assert not any('設定済み' in m for m in messages(caplog))


def test_sso_without_a_profile_name_decides_no_region(home, env_file, monkeypatch):
    """プロファイル名が空なら何も書かずに戻る"""
    answers = Answers(monkeypatch)

    aws._collect_sso_profile(env_file, place(ImportPolicy.IMPORT))

    assert env_file.get(keys.AWS_PROFILE) is None
    assert env_file.get(keys.AWS_DEFAULT_REGION) is None
    assert answers.region_prompts() == []


# ---------------------------------------------------------------------------
# 節の名前と region の読み取り
# ---------------------------------------------------------------------------

def test_find_profile_region_returns_the_section(home):
    (home / '.aws' / 'config').write_text(CONFIG + '\n[bare]\nregion = eu-west-1\n')
    parser = aws.AWSConfigParser()

    assert parser.find_profile_region('default') == ('default', HOST_REGION)
    assert parser.find_profile_region('dev') == ('profile dev', HOST_REGION)
    assert parser.find_profile_region('bare') == ('bare', 'eu-west-1')
    assert parser.find_profile_region('missing') is None
    assert parser.get_profile_region('dev') == HOST_REGION
    assert parser.get_profile_region('missing') is None
