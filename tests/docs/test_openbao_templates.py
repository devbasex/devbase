"""OpenBao のポリシーの雛形と試用の構成の形 (#295)

雛形 (``docs/user/openbao/policies/``) は利用者が自分のサーバへ書き込むもので、devbase は
実行時に読まない。ここでは、雛形が devbase の前提にした権限のほかを許さないこと
(マウント ``devbase`` の ``data/`` と ``metadata/`` の下だけ、管理の経路を含まない、読み取りの
雛形は読むだけ) と、試用の構成の版とポートを縛る。サーバが雛形を受け付けることと、2 人の
利用者での 403 / 通るの組は実機で確かめる (手動確認)。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
OPENBAO = ROOT / 'docs' / 'user' / 'openbao'
POLICIES = OPENBAO / 'policies'

PERSONAL = 'devbase-user.hcl'
V1 = ('v1/team-read.hcl', 'v1/team-write.hcl')
V2 = ('v2/team-read.hcl', 'v2/team-write.hcl')
ALL = (PERSONAL, *V1, *V2)

_PATH_BLOCK = re.compile(r'^path\s+"([^"]+)"\s*\{(.*?)^\}', re.MULTILINE | re.DOTALL)
_CAPABILITIES = re.compile(r'capabilities\s*=\s*\[([^\]]*)\]')


def _blocks(text: str) -> dict:
    """``path "<パス>" { capabilities = [...] }`` を パス → 許す操作の集合 にする"""
    blocks = {}
    for path, body in _PATH_BLOCK.findall(text):
        caps, = _CAPABILITIES.findall(body)
        blocks[path] = {c.strip().strip('"') for c in caps.split(',') if c.strip()}
    return blocks


def _policy(name: str, group: str | None = None) -> dict:
    text = (POLICIES / name).read_text(encoding='utf-8')
    if group is not None:
        text = text.replace('__GROUP__', group)
    return _blocks(text)


def _code(text: str) -> str:
    """コメント行を除いた HCL"""
    return '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))


@pytest.mark.parametrize('name', ALL)
def test_every_template_exists_and_has_paths(name):
    assert _policy(name), name


@pytest.mark.parametrize('name', ALL)
def test_templates_only_touch_the_devbase_kv_mount(name):
    for path in _policy(name, group='personal'):
        assert path.startswith(('devbase/data/', 'devbase/metadata/')), path
        assert not re.match(r'(sys|auth|identity)/', path), path


@pytest.mark.parametrize('name', ALL)
def test_templates_grant_no_admin_capabilities(name):
    for path, caps in _policy(name, group='personal').items():
        assert caps <= {'create', 'read', 'update', 'delete', 'list'}, (path, caps)
        assert 'sudo' not in caps and 'deny' not in caps


def test_personal_template_is_limited_to_the_own_entity():
    paths = _policy(PERSONAL)
    assert set(paths) == {
        'devbase/data/users/{{identity.entity.name}}/*',
        'devbase/metadata/users/{{identity.entity.name}}/*',
    }
    # 個人の雛形は version 1 / 2 共通。グループの置き換え語を持たない
    assert '__GROUP__' not in _code((POLICIES / PERSONAL).read_text())


@pytest.mark.parametrize('name', ('v1/team-read.hcl', 'v2/team-read.hcl'))
def test_read_templates_only_read(name):
    for path, caps in _policy(name, group='personal').items():
        assert caps == {'read'}, (path, caps)


@pytest.mark.parametrize('name', ('v1/team-write.hcl', 'v2/team-write.hcl'))
def test_write_templates_write_only_team_paths(name):
    for path, caps in _policy(name, group='personal').items():
        assert {'create', 'update'} <= caps, (path, caps)
        assert '/team/' in path and '/users/' not in path, path


def test_v1_templates_cover_the_flat_team_paths():
    for name in V1:
        assert set(_policy(name)) == {'devbase/data/team/global', 'devbase/data/team/projects/*'}
        assert '__GROUP__' not in (POLICIES / name).read_text()


@pytest.mark.parametrize('name', V2)
def test_v2_templates_become_one_group_by_replacing_the_placeholder(name):
    code = _code((POLICIES / name).read_text())
    # 置き換え語は全てのパスにあり、置き換えれば team/<group>/ の下だけを指す
    assert all('__GROUP__' in path for path in _blocks(code))
    assert set(_policy(name, group='personal')) == {
        'devbase/data/team/personal/global', 'devbase/data/team/personal/projects/*'}


def _compose() -> dict:
    return yaml.safe_load((OPENBAO / 'compose.yml').read_text(encoding='utf-8'))


def _major_minor(version: str) -> tuple:
    return tuple(version.split('.')[:2])


def test_trial_server_uses_the_same_minor_as_the_base_image():
    dockerfile = (ROOT / 'containers' / 'base' / 'Dockerfile').read_text()
    base, = re.findall(r'^ARG BAO_VERSION=(\S+)$', dockerfile, re.MULTILINE)
    services = _compose()['services']
    images = [s['image'] for s in services.values()]
    assert len(images) == 1
    repo, tag = images[0].split(':')
    assert repo == 'openbao/openbao'
    assert _major_minor(tag) == _major_minor(base)


def test_trial_server_publishes_only_on_loopback():
    for service in _compose()['services'].values():
        ports = service.get('ports', [])
        assert ports
        for port in ports:
            assert str(port).startswith('127.0.0.1:'), port


def test_trial_server_does_not_fix_a_root_token():
    """root token は決めない (開発モードのサーバがコンテナの中にだけ置く)"""
    text = _code((OPENBAO / 'compose.yml').read_text())
    assert 'DEV_ROOT_TOKEN' not in text
    assert '-dev-root-token-id' not in text
