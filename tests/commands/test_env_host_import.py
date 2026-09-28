"""`env init` / `env sync` とホストの資格情報の取り込み (#314)

ホストの資格情報ファイルの中身は、利用者が選ばない限り、どのグループの参照にも入らない。
取り込みを許すグループ (`secrets/host-import.yml` の `import`) だけが今までどおり取り込む。
HOME と DEVBASE_ROOT は一時ディレクトリへ隔離し、「ホストに候補がある状態」を作る。
"""

from __future__ import annotations

import base64
import builtins
import io
import json
import logging
import tarfile
from pathlib import Path

import pytest

from devbase.commands import env as env_cmd
from devbase.env import host_import, keys
from devbase.env.sources import SourcesManager

KKG = 'team/kkg/global'
KKG_USER = 'users/member01/kkg/global'
NYLE = 'team/nyle/global'

#: AC2: 全部断ったときに、チーム共通に 1 つも無いはずのキー
IMPORTED_KEYS = (
    keys.GCP_ACTIVE_PROFILE, keys.GOOGLE_APPLICATION_CREDENTIALS, keys.BIGQUERY_KEY_FILE,
    keys.GOOGLE_CLOUD_PROJECT, keys.BIGQUERY_PROJECT, keys.GOOGLE_CLOUD_LOCATION,
    keys.BIGQUERY_LOCATION, keys.BIGQUERY_DATASETS, keys.AWS_CONFIG_BASE64, keys.AWS_PROFILE,
    keys.AWS_DEFAULT_REGION, keys.AWS_ACCESS_KEY_ID, keys.AWS_SECRET_ACCESS_KEY, keys.AWS_SSO_URL,
    keys.GIT_USER_NAME, keys.GIT_USER_EMAIL, keys.GIT_CREDENTIAL_HELPER,
    keys.GIT_CREDENTIALS_BASE64, keys.GITHUB_PERSONAL_ACCESS_TOKEN, keys.GH_TOKEN,
)

AWS_CONFIG = """\
# host config
[default]
region = ap-northeast-1

[profile carmo-dev]
region = us-east-1

[profile kkg]
sso_session = kkg-sso
sso_account_id = 111111111111
region = ap-northeast-1

[sso-session kkg-sso]
sso_start_url = https://kkg.awsapps.com/start
sso_region = ap-northeast-1

[profile lixil]
role_arn = arn:aws:iam::222222222222:role/x
source_profile = lixil-base

[profile lixil-base]
role_arn = arn:aws:iam::333333333333:role/y
source_profile = lixil-root

[profile lixil-root]
region = us-west-2

[services s3]
s3 =
  max_concurrent_requests = 5
"""

AWS_CREDENTIALS = """\
[default]
aws_access_key_id = AKIADEFAULT
aws_secret_access_key = SECRET-DEFAULT

[carmo-dev]
aws_access_key_id = AKIACARMO
aws_secret_access_key = SECRET-CARMO

[kkg]
aws_access_key_id = AKIAKKG
aws_secret_access_key = SECRET-KKG

[lixil-root]
aws_access_key_id = AKIALIXIL
aws_secret_access_key = SECRET-LIXIL
"""

SECRETS = ('SECRET-DEFAULT', 'SECRET-CARMO', 'SECRET-KKG', 'SECRET-LIXIL',
           'PRIVATE-KEY-BQ', 'PRIVATE-KEY-AN', 'ghp_TOKEN123')


# ---------------------------------------------------------------------------
# 足場
# ---------------------------------------------------------------------------

@pytest.fixture
def home(openbao_root) -> Path:
    path = openbao_root / 'home'
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def host(home, monkeypatch):
    """ホストに候補がある状態 (鍵ファイル 2 つ・AWS のプロファイル 6 つ・Git の設定)"""
    from devbase.env.collectors import google

    creds = home / 'gcp-credentials'
    creds.mkdir()
    (creds / 'bigquery_full.json').write_text(json.dumps(
        {'project_id': 'nyle-carmo-analysis', 'private_key': 'PRIVATE-KEY-BQ'}))
    (creds / 'analytics.json').write_text(json.dumps({'private_key': 'PRIVATE-KEY-AN'}))
    monkeypatch.setattr(google, 'GCP_CREDENTIALS_DIR', creds)
    monkeypatch.setattr(google, 'LEGACY_CREDENTIALS_FILE', home / 'none.json')

    (home / '.aws').mkdir()
    (home / '.aws' / 'config').write_text(AWS_CONFIG)
    (home / '.aws' / 'credentials').write_text(AWS_CREDENTIALS)

    (home / '.git-credentials').write_text('https://member:ghp_TOKEN123@github.com\n')
    gitconfig = home / '.gitconfig'
    gitconfig.write_text('[user]\n\tname = Member\n\temail = member@example.com\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(gitconfig))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    return home


@pytest.fixture
def grouped(openbao_root, openbao):
    from tests.conftest import configure_openbao

    configure_openbao(openbao_root, openbao, layout='group')
    return openbao_root


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(host_import, '_stdin_is_tty', lambda: True)


class Answers:
    """``input`` の差し替え。質問の文に含まれる語 → 答え。当たらなければ空 (Enter)"""

    def __init__(self, monkeypatch, answers=None):
        self.answers = {k: list(v) if isinstance(v, list) else [v]
                        for k, v in (answers or {}).items()}
        self.prompts: list = []
        monkeypatch.setattr(builtins, 'input', self)

    def __call__(self, prompt=''):
        self.prompts.append(prompt)
        for needle, values in self.answers.items():
            if needle in prompt and values:
                return values.pop(0) if len(values) > 1 else values[0]
        return ''


def write_policy(root: Path, text: str) -> None:
    path = root / 'secrets' / 'host-import.yml'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def infos(caplog) -> list:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]


def aws_files(value: str) -> dict:
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(value)), mode='r:*') as tar:
        return {m.name: tar.extractfile(m).read().decode() for m in tar.getmembers()}


def headers(text: str) -> list:
    return [line.strip() for line in text.splitlines() if line.strip().startswith('[')]


def init(root, group='kkg'):
    return env_cmd.cmd_env_init(root, reset=True, group=group)


# ---------------------------------------------------------------------------
# init: 尋ねる (AC1・AC2・AC3)
# ---------------------------------------------------------------------------

def test_asking_shows_the_candidates_before_writing(grouped, openbao, host, tty, monkeypatch,
                                                    capsys):
    """AC1: 各ステップは候補の一覧を出してから尋ねる。尋ねる前に参照へ何も書かない"""
    from devbase.env.store import EnvFile

    written_before_question = []
    answers = Answers(monkeypatch, {'選択 [1/2/3/4]': '1'})
    original_set = EnvFile.set

    def spy_set(self, key, value):
        if key in IMPORTED_KEYS or key.startswith(keys.GCP_CREDENTIALS_BASE64_PREFIX):
            written_before_question.append(key)
        return original_set(self, key, value)

    monkeypatch.setattr(EnvFile, 'set', spy_set)

    assert init(grouped) == 0

    out = capsys.readouterr().out
    assert 'ホストで見つけた GCP の鍵 (2件):' in out
    assert '1) analytics (project: N/A)' in out
    assert '2) bigquery_full (project: nyle-carmo-analysis)' in out
    assert '~/.aws/config のプロファイル (6件):' in out
    assert '3) kkg' in out
    assert ('ホストの Git の設定から取り込めるキー: GIT_USER_NAME, GIT_USER_EMAIL, '
            'GIT_CREDENTIALS_BASE64, GITHUB_PERSONAL_ACCESS_TOKEN, GH_TOKEN') in out
    assert sum('取り込む番号' in p for p in answers.prompts) == 2
    assert any(p.startswith('取り込みますか?') for p in answers.prompts)
    assert written_before_question == []
    # I10: 一覧に機密の値を出さない
    assert not any(secret in out for secret in SECRETS)


def test_enter_everywhere_writes_no_host_credentials(grouped, openbao, host, tty, monkeypatch):
    """AC2: 全部 Enter で答えると、kkg のチーム共通に取り込みのキーが 1 つも無い"""
    Answers(monkeypatch)

    assert init(grouped) == 0

    stored = openbao.get(KKG) or {}
    assert not [k for k in stored if k in IMPORTED_KEYS
                or k.startswith(keys.GCP_CREDENTIALS_BASE64_PREFIX)]


def test_non_terminal_stdin_imports_nothing_even_with_piped_answers(grouped, openbao, host,
                                                                     monkeypatch, caplog):
    """AC3・I3: 端末でなければ尋ねずに取り込まない。パイプで番号を渡しても取り込まない"""
    monkeypatch.setattr(host_import, '_stdin_is_tty', lambda: False)
    answers = Answers(monkeypatch, {'取り込む番号': '1', '取り込みますか': 'y',
                                    '選択 [1/2/3/4]': '1'})
    caplog.set_level(logging.INFO)

    assert init(grouped) == 0

    stored = openbao.get(KKG) or {}
    assert not [k for k in stored if k in IMPORTED_KEYS
                or k.startswith(keys.GCP_CREDENTIALS_BASE64_PREFIX)]
    assert not any('取り込む番号' in p or '取り込みますか' in p for p in answers.prompts)
    assert ('標準入力が端末でないため、ホストの資格情報は取り込みません (kkg)。'
            '取り込むなら secrets/host-import.yml で import を指定します') in infos(caplog)


def test_dev_null_stdin_exits_zero_and_imports_nothing(grouped, openbao, host, monkeypatch):
    """AC3: 標準入力が /dev/null (EOF) でも 0 で終わり、何も取り込まない"""
    def eof(prompt=''):
        raise EOFError

    monkeypatch.setattr(host_import, '_stdin_is_tty', lambda: False)
    monkeypatch.setattr(builtins, 'input', eof)

    assert init(grouped) == 0

    stored = openbao.get(KKG) or {}
    assert not [k for k in stored if k in IMPORTED_KEYS
                or k.startswith(keys.GCP_CREDENTIALS_BASE64_PREFIX)]


def test_skip_policy_asks_nothing(grouped, openbao, host, tty, monkeypatch, caplog):
    """AC14: skip と名指ししたグループは取り込みの質問を出さず、AC2 と同じ結果"""
    write_policy(grouped, 'groups:\n  kkg: skip\n')
    answers = Answers(monkeypatch, {'選択 [1/2/3/4]': '1'})
    caplog.set_level(logging.INFO)

    assert init(grouped) == 0

    stored = openbao.get(KKG) or {}
    assert not [k for k in stored if k in IMPORTED_KEYS
                or k.startswith(keys.GCP_CREDENTIALS_BASE64_PREFIX)]
    assert not any('取り込む番号' in p or '取り込みますか' in p for p in answers.prompts)
    assert 'GCP認証: 取り込まない設定のため飛ばしました (kkg)' in infos(caplog)


# ---------------------------------------------------------------------------
# init: GCP (AC4・AC5・決定 5)
# ---------------------------------------------------------------------------

def test_choosing_one_gcp_key_writes_only_that_key(grouped, openbao, host, tty, monkeypatch):
    """AC4"""
    Answers(monkeypatch, {'取り込む番号 (例: 1,2': '2'})

    assert init(grouped) == 0

    stored = openbao.get(KKG)
    assert keys.gcp_credentials_key('bigquery_full') in stored
    assert keys.gcp_credentials_key('analytics') not in stored
    assert stored[keys.GCP_ACTIVE_PROFILE] == 'bigquery_full'
    assert stored[keys.GOOGLE_CLOUD_PROJECT] == 'nyle-carmo-analysis'


def test_none_as_the_active_profile_writes_no_gcp_key(grouped, openbao, host, tty, monkeypatch):
    """AC5: none を選ぶと GCP の鍵・鍵モードの変数・共通設定を書かない"""
    answers = Answers(monkeypatch, {'取り込む番号 (例: 1,2': 'all', 'アクティブプロファイル': 'none'})

    assert init(grouped) == 0

    assert any('none で設定しない' in p for p in answers.prompts)
    stored = openbao.get(KKG) or {}
    assert not [k for k in stored if k.startswith(keys.GCP_CREDENTIALS_BASE64_PREFIX)]
    for key in (keys.GCP_ACTIVE_PROFILE, keys.GOOGLE_APPLICATION_CREDENTIALS,
                keys.BIGQUERY_KEY_FILE, keys.GOOGLE_CLOUD_PROJECT, keys.BIGQUERY_PROJECT,
                keys.GOOGLE_CLOUD_LOCATION, keys.BIGQUERY_LOCATION):
        assert key not in stored


def test_an_unknown_active_profile_is_asked_again(grouped, openbao, host, tty, monkeypatch,
                                                  capsys):
    """決定 5: 選んだ鍵に無い名前は尋ね直し、続けて正しい名前を入れるとそれがアクティブ"""
    answers = Answers(monkeypatch, {'取り込む番号 (例: 1,2': 'all',
                                    'アクティブプロファイル': ['bigquery_ful', 'bigquery_full']})

    assert init(grouped) == 0

    assert sum('アクティブプロファイル' in p for p in answers.prompts) == 2
    assert "'bigquery_ful' は選んだ鍵にありません" in capsys.readouterr().out
    assert openbao.get(KKG)[keys.GCP_ACTIVE_PROFILE] == 'bigquery_full'


def test_an_unknown_active_profile_then_eof_uses_the_default():
    from devbase.env.collectors.google import _ask_active_profile

    calls = iter(['nope'])

    def fake(prompt=''):
        try:
            return next(calls)
        except StopIteration:
            raise EOFError from None

    original = builtins.input
    builtins.input = fake
    try:
        assert _ask_active_profile(['a', 'b'], 'a') == 'a'
    finally:
        builtins.input = original


# ---------------------------------------------------------------------------
# init: AWS (AC6・AC7・AC8)
# ---------------------------------------------------------------------------

def test_choosing_kkg_imports_only_its_sections(grouped, openbao, host, tty, monkeypatch, capsys):
    """AC6: [profile kkg] と sso_session の節、credentials は [kkg] だけ"""
    Answers(monkeypatch, {'選択 [1/2/3/4]': '1', '取り込む番号 (例: 3': '3'})

    assert init(grouped) == 0

    stored = openbao.get(KKG)
    files = aws_files(stored[keys.AWS_CONFIG_BASE64])
    assert headers(files['config']) == ['[profile kkg]', '[sso-session kkg-sso]']
    assert headers(files['credentials']) == ['[kkg]']
    assert 'SECRET-DEFAULT' not in files['credentials']
    assert stored[keys.AWS_PROFILE] == 'kkg'
    assert '含めます: [sso-session kkg-sso] (profile kkg の sso_session)' in capsys.readouterr().out
    source = SourcesManager(grouped, 'kkg').get_source('aws')
    assert source['type'] == 'aws_profiles'
    assert source['profiles'] == ['kkg']


def test_a_profile_without_credentials_imports_no_credentials_file(grouped, openbao, host, tty,
                                                                   monkeypatch):
    Answers(monkeypatch, {'選択 [1/2/3/4]': '1', '取り込む番号 (例: 3': '1'})
    (host / '.aws' / 'credentials').write_text('[kkg]\naws_access_key_id = A\n')

    assert init(grouped) == 0

    files = aws_files(openbao.get(KKG)[keys.AWS_CONFIG_BASE64])
    assert set(files) == {'config'}
    assert headers(files['config']) == ['[default]']


def test_source_profile_chains_are_included_with_a_reason(grouped, openbao, host, tty,
                                                         monkeypatch, capsys):
    """AC7: source_profile を 2 段たどり、config と credentials に入れ、1 行ずつ知らせる"""
    Answers(monkeypatch, {'選択 [1/2/3/4]': '1', '取り込む番号 (例: 3': '4'})

    assert init(grouped) == 0

    files = aws_files(openbao.get(KKG)[keys.AWS_CONFIG_BASE64])
    assert headers(files['config']) == ['[profile lixil]', '[profile lixil-base]',
                                        '[profile lixil-root]']
    assert headers(files['credentials']) == ['[lixil-root]']
    out = capsys.readouterr().out
    assert '含めます: [profile lixil-base] (profile lixil の source_profile)' in out
    assert '含めます: [profile lixil-root] (profile lixil-base の source_profile)' in out
    assert 'SECRET-LIXIL' not in out


def test_all_imports_the_whole_aws_directory(grouped, openbao, host, tty, monkeypatch):
    Answers(monkeypatch, {'選択 [1/2/3/4]': '1', '取り込む番号 (例: 3': 'all'})

    assert init(grouped) == 0

    files = aws_files(openbao.get(KKG)[keys.AWS_CONFIG_BASE64])
    assert files == {'config': AWS_CONFIG, 'credentials': AWS_CREDENTIALS}
    assert SourcesManager(grouped, 'kkg').get_source('aws')['type'] == 'tar_base64'


def test_aws_defaults_to_skip_and_access_keys_need_a_confirmation(grouped, openbao, host, tty,
                                                                  monkeypatch):
    """AC8: 既定は 4 (スキップ)。Access Key は [default] の鍵を書く前に確認し、既定は書かない"""
    answers = Answers(monkeypatch, {'選択 [1/2/3/4]': '3'})

    assert init(grouped) == 0

    assert any('(デフォルト: 4)' in p for p in answers.prompts if p.startswith('選択 [1/2/3/4]'))
    assert any(p.startswith('取り込みますか?') for p in answers.prompts)
    stored = openbao.get(KKG) or {}
    assert keys.AWS_ACCESS_KEY_ID not in stored
    assert keys.AWS_SECRET_ACCESS_KEY not in stored


def test_out_of_range_numbers_import_nothing(grouped, openbao, host, tty, monkeypatch, caplog):
    """I2・E5: 範囲外・数字でない語は 0 件として 1 行知らせる"""
    Answers(monkeypatch, {'選択 [1/2/3/4]': '1', '取り込む番号 (例: 3': '9',
                          '取り込む番号 (例: 1,2': 'kkg'})
    caplog.set_level(logging.INFO)

    assert init(grouped) == 0

    stored = openbao.get(KKG) or {}
    assert keys.AWS_CONFIG_BASE64 not in stored
    assert not [k for k in stored if k.startswith(keys.GCP_CREDENTIALS_BASE64_PREFIX)]
    assert '取り込む番号として読めないため、取り込みません: 9' in infos(caplog)
    assert '取り込む番号として読めないため、取り込みません: kkg' in infos(caplog)


# ---------------------------------------------------------------------------
# init: 取り込む (AC12)
# ---------------------------------------------------------------------------

#: この変更の前の実装で、同じホスト・Enter だけの入力で出た質問の並び (HOST_SSH_USER は端末の
#: 利用者名を含むため除く)。アクティブプロファイルの文だけ none の案内が加わる (決定 5)
PROMPTS_BEFORE = [
    "ANTHROPIC_API_KEY (空でスキップ): ",
    "OPENAI_API_KEY (空でスキップ): ",
    "GEMINI_API_KEY (空でスキップ): ",
    "CONTEXT7_API_KEY (空でスキップ): ",
    "PYPI_API_KEY (空でスキップ): ",
    "NPM_TOKEN (空でスキップ): ",
    "選択 [1/2/3/4] (デフォルト: 1): ",
    "\n使用するAWS_PROFILE (デフォルト: default): ",
    "\nDevin APIを設定しますか? [y/N]: ",
    "DEVBASE_OPEN_EDITOR: devbase up/list 後に VS Code を自動オープンしますか? [Y/n] (既定=1): ",
    "GIT_CREDENTIAL_HELPER (空でスキップ): ",
    "\nアクティブプロファイル (名前 / none で設定しない、デフォルト: analytics): ",
    "BIGQUERY_DATASETS (カンマ区切り、空でスキップ): ",
    "HOST_SSH_HOST [host.docker.internal]: ",
    "SLACK_BOT_TOKEN (空でスキップ): ",
    "SLACK_TEAM_ID (空でスキップ): ",
    "SLACK_CHANNEL_ID (空でスキップ): ",
    "SLACK_USER_MENTION (空でスキップ): ",
]

#: 同じ入力で前の実装が書いたキー
KEYS_BEFORE = {
    'AWS_CONFIG_BASE64', 'AWS_DEFAULT_REGION', 'AWS_PROFILE', 'BIGQUERY_KEY_FILE',
    'BIGQUERY_LOCATION', 'DEVBASE_OPEN_EDITOR', 'GCP_ACTIVE_PROFILE',
    'GCP_CREDENTIALS_BASE64__analytics', 'GCP_CREDENTIALS_BASE64__bigquery_full', 'GH_TOKEN',
    'GITHUB_PERSONAL_ACCESS_TOKEN', 'GIT_CREDENTIALS_BASE64', 'GIT_USER_EMAIL', 'GIT_USER_NAME',
    'GOOGLE_APPLICATION_CREDENTIALS', 'GOOGLE_CLOUD_LOCATION', 'HOST_SSH_HOST', 'HOST_SSH_USER',
}


def test_import_policy_keeps_the_previous_questions_and_values(grouped, openbao, host, tty,
                                                               monkeypatch):
    """AC12: import のグループは取り込みの確認を足さず、前と同じキーと値を書く"""
    from devbase.commands.env import _aws_payload
    from devbase.env.collectors.aws import _encode_aws_config_files

    write_policy(grouped, 'groups:\n  nyle: import\n')
    answers = Answers(monkeypatch)

    assert init(grouped, group='nyle') == 0

    assert [p for p in answers.prompts if not p.startswith('HOST_SSH_USER')] == PROMPTS_BEFORE
    stored = openbao.get(NYLE)
    assert set(stored) == KEYS_BEFORE
    gcp = {keys.gcp_credentials_key(n) for n in ('analytics', 'bigquery_full')}
    assert gcp <= set(stored)
    assert stored[keys.GCP_ACTIVE_PROFILE] == 'analytics'
    assert _aws_payload(stored[keys.AWS_CONFIG_BASE64]) == _aws_payload(_encode_aws_config_files())
    assert stored[keys.AWS_PROFILE] == 'default'
    assert stored[keys.GIT_USER_NAME] == 'Member'
    assert stored[keys.GH_TOKEN] == 'ghp_TOKEN123'
    assert SourcesManager(grouped, 'nyle').get_source('aws')['type'] == 'tar_base64'


# ---------------------------------------------------------------------------
# 設定の誤り (AC13)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('text, needle', [
    ('groups: [nyle', 'YAML として読めません'),
    ('- nyle\n', '最上位は groups を持つマッピング'),
    ('groups: nyle\n', 'groups はグループ名から方針へのマッピング'),
    ('groups:\n  nyle: yes-please\n', "groups.nyle の方針が不正です: 'yes-please'"),
    ('groups:\n  default: import\n', 'groups.default は使えないグループ名です'),
    ('group:\n  nyle: import\n', 'group は使えないキーです'),
])
@pytest.mark.parametrize('command', ['init', 'sync'])
def test_a_broken_policy_file_stops_before_opening_the_refs(grouped, openbao, monkeypatch,
                                                            caplog, text, needle, command):
    write_policy(grouped, text)
    opened = []
    monkeypatch.setattr(env_cmd, '_global_env', lambda *a, **kw: opened.append(1))

    if command == 'init':
        rc = init(grouped)
    else:
        rc = env_cmd.cmd_env_sync(grouped, group='kkg')

    assert rc == 1
    assert opened == []
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(str(grouped / 'secrets' / 'host-import.yml') in e and needle in e
               and '受け付ける値: ask, skip, import' in e for e in errors)


# ---------------------------------------------------------------------------
# sync (AC9・AC10・AC11・I8・決定 10)
# ---------------------------------------------------------------------------

def test_sync_after_declining_everything_adds_nothing(grouped, openbao, host, tty, monkeypatch):
    """AC9"""
    Answers(monkeypatch)
    assert init(grouped) == 0
    (host / '.aws' / 'config').write_text(AWS_CONFIG + '\n[profile new]\n')
    (host / '.git-credentials').write_text('https://member:ghp_OTHER@github.com\n')

    assert env_cmd.cmd_env_sync(grouped, group='kkg') == 0

    for path in (KKG, KKG_USER):
        stored = openbao.get(path) or {}
        assert not [k for k in stored if k in IMPORTED_KEYS
                    or k.startswith(keys.GCP_CREDENTIALS_BASE64_PREFIX)]


def test_sync_watches_only_the_chosen_profiles(grouped, openbao, host, tty, monkeypatch, caplog):
    """AC10・I7: 選ばなかった節の書き換えは変更なし。選んだ節の書き換えは選んだ節だけで入れ直す"""
    Answers(monkeypatch, {'選択 [1/2/3/4]': '1', '取り込む番号 (例: 3': '3'})
    assert init(grouped) == 0
    before = openbao.get(KKG)[keys.AWS_CONFIG_BASE64]
    config = host / '.aws' / 'config'
    config.write_text(AWS_CONFIG.replace('region = us-east-1', 'region = eu-west-1'))
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(grouped, group='kkg') == 0

    assert openbao.get(KKG)[keys.AWS_CONFIG_BASE64] == before
    assert 'AWS認証: 変更なし' in infos(caplog)

    caplog.clear()
    config.write_text(config.read_text().replace('sso_account_id = 111111111111',
                                                 'sso_account_id = 999999999999'))

    assert env_cmd.cmd_env_sync(grouped, group='kkg') == 0

    files = aws_files(openbao.get(KKG)[keys.AWS_CONFIG_BASE64])
    assert headers(files['config']) == ['[profile kkg]', '[sso-session kkg-sso]']
    assert '999999999999' in files['config']
    assert headers(files['credentials']) == ['[kkg]']
    assert 'AWS認証: 更新しました' in infos(caplog)
    assert SourcesManager(grouped, 'kkg').check_changed('aws') is False


def test_sync_reports_a_chosen_profile_that_disappeared(grouped, openbao, host, tty, monkeypatch,
                                                         caplog):
    Answers(monkeypatch, {'選択 [1/2/3/4]': '1', '取り込む番号 (例: 3': '3'})
    assert init(grouped) == 0
    before = openbao.get(KKG)[keys.AWS_CONFIG_BASE64]
    (host / '.aws' / 'config').write_text('[default]\n')
    (host / '.aws' / 'credentials').write_text('[default]\n')
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(grouped, group='kkg') == 0

    assert openbao.get(KKG)[keys.AWS_CONFIG_BASE64] == before
    lines = infos(caplog)
    assert 'AWS認証: 選んだプロファイル kkg が ~/.aws/config にありません' in lines
    assert 'AWS認証: 控えと比べられません（ハッシュか元のファイルがありません）' in lines


@pytest.mark.parametrize('source', ['aws', 'git_credentials', 'gcp'])
def test_sync_does_not_write_a_registered_source_missing_from_the_refs(
        grouped, openbao, host, tty, monkeypatch, caplog, source):
    """AC11・I5: 控えに登録済みでも、参照から消したキーは書かずに 1 行知らせる"""
    write_policy(grouped, 'groups:\n  kkg: import\n')
    Answers(monkeypatch)
    assert init(grouped) == 0
    key, label, touch = {
        'aws': (keys.AWS_CONFIG_BASE64, 'AWS認証',
                lambda: (host / '.aws' / 'config').write_text(AWS_CONFIG + '\n')),
        'git_credentials': (keys.GIT_CREDENTIALS_BASE64, 'Git認証',
                            lambda: (host / '.git-credentials').write_text('x\n')),
        'gcp': (keys.gcp_credentials_key('analytics'), 'GCP認証 (analytics)',
                lambda: (host / 'gcp-credentials' / 'analytics.json').write_text('{}')),
    }[source]
    stored = openbao.get(KKG)
    assert key in stored
    del stored[key]
    openbao.put(KKG, stored)
    touch()
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(grouped, group='kkg') == 0

    assert key not in (openbao.get(KKG) or {})
    assert key not in (openbao.get(KKG_USER) or {})
    assert (f'{label}: 参照にキーが無いため書きません。取り込むなら devbase env init --reset、'
            '手で入れるなら devbase env set') in infos(caplog)


def test_import_policy_sync_updates_the_whole_directory(grouped, openbao, host, tty, monkeypatch,
                                                         caplog):
    """AC12・I8: 丸ごとの取り込み (tar_base64) は今と同じに丸ごとで入れ直す"""
    from devbase.commands.env import _aws_payload
    from devbase.env.collectors.aws import _encode_aws_config_files

    write_policy(grouped, 'groups:\n  nyle: import\n')
    Answers(monkeypatch)
    assert init(grouped, group='nyle') == 0
    (host / '.aws' / 'config').write_text(AWS_CONFIG + '\n[profile new]\n')
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(grouped, group='nyle') == 0

    assert (_aws_payload(openbao.get(NYLE)[keys.AWS_CONFIG_BASE64])
            == _aws_payload(_encode_aws_config_files()))
    assert 'AWS認証: 更新しました' in infos(caplog)


def test_an_unregistered_partial_value_is_compared_within_its_profiles(grouped, openbao, host,
                                                                       caplog):
    """決定 10: 控えに項目が無く、参照の値が kkg だけなら、kkg の範囲だけで比べる"""
    from devbase.env import aws_profiles

    value = aws_profiles.build(AWS_CONFIG, AWS_CREDENTIALS, ['kkg']).encode()
    openbao.put(KKG, {keys.AWS_CONFIG_BASE64: value, keys.HOST_SSH_USER: 'u',
                      keys.HOST_SSH_HOST: 'h'})
    (host / '.aws' / 'config').write_text(AWS_CONFIG.replace('us-east-1', 'eu-west-1'))
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(grouped, group='kkg') == 0

    assert openbao.get(KKG)[keys.AWS_CONFIG_BASE64] == value
    source = SourcesManager(grouped, 'kkg').get_source('aws')
    assert source['type'] == 'aws_profiles'
    assert source['profiles'] == ['kkg']


def test_an_unregistered_value_is_not_whole_when_credentials_have_more(grouped, openbao, host,
                                                                       caplog):
    """決定 10: credentials にだけあるプロファイルが値に無ければ丸ごとにしない"""
    from devbase.env import aws_profiles

    config = '[profile dev]\nregion = us-east-1\n'
    dev_creds = '[dev]\naws_access_key_id = AKIADEV\naws_secret_access_key = SECRET-DEV\n'
    value = aws_profiles.build(config, dev_creds, ['dev']).encode()
    openbao.put(KKG, {keys.AWS_CONFIG_BASE64: value})
    (host / '.aws' / 'config').write_text(config)
    (host / '.aws' / 'credentials').write_text(
        dev_creds + '\n[private]\naws_access_key_id = AKIAPRIV\n'
        'aws_secret_access_key = SECRET-PRIVATE\n')
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(grouped, group='kkg') == 0

    stored = aws_files(openbao.get(KKG)[keys.AWS_CONFIG_BASE64])
    assert 'SECRET-PRIVATE' not in stored.get('credentials', '')
    source = SourcesManager(grouped, 'kkg').get_source('aws')
    assert source['type'] == 'aws_profiles'
    assert source['profiles'] == ['dev']


def test_a_skipped_unregistered_aws_value_is_not_registered(grouped, openbao, host, caplog):
    """決定 10: 値を読めず書かなかった AWS は、ほかの更新があっても控えに登録しない"""
    openbao.put(KKG, {keys.AWS_CONFIG_BASE64: 'not-a-tar'})
    caplog.set_level(logging.INFO)

    assert env_cmd.cmd_env_sync(grouped, group='kkg') == 0

    assert openbao.get(KKG)[keys.AWS_CONFIG_BASE64] == 'not-a-tar'
    assert (openbao.get(KKG_USER) or {}).get(keys.HOST_SSH_HOST)
    assert SourcesManager(grouped, 'kkg').get_source('aws') is None


# ---------------------------------------------------------------------------
# 契約 (AC15・I11)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('module', ['google', 'aws', 'git'])
def test_host_reading_collectors_require_the_policy(module):
    import importlib

    collector = importlib.import_module(f'devbase.env.collectors.{module}').COLLECTOR
    assert collector.host_import is True
    with pytest.raises(TypeError):
        collector.collect_fn(object())


def test_collectors_do_not_compare_group_names():
    import inspect

    from devbase.env.collectors import aws, git, google

    for module in (aws, git, google):
        source = inspect.getsource(module)
        assert 'host.group' not in source
        assert '.group ==' not in source


def test_a_collector_reading_host_files_must_declare_it():
    from devbase.env.collector import Collector

    with pytest.raises(ValueError):
        Collector(name='x', display_name='X', collect_fn=lambda env_file: None,
                  source_files=['~/.x'])
    assert Collector(name='x', display_name='X', collect_fn=lambda env_file, *, host: None,
                     source_files=['~/.x'], host_import=True).host_import
