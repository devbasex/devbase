"""devbase env の各コマンドが秘密ストア経由で読み書きすることの検証

保存先が平文か暗号化かに関わらず、同じ操作で同じ結果になることを確かめる。
"""

from __future__ import annotations

import pyrage
import pytest

from devbase.commands import env as env_cmd
from devbase.env.secret_store import SecretRef, SecretStore


GLOBAL = SecretRef.for_global()


@pytest.fixture
def devbase_root(tmp_path, monkeypatch):
    """projects/ を持つ空の DEVBASE_ROOT。鍵は tmp 配下に閉じ込める。"""
    from devbase.env import agekeys

    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tmp_path / 'age' / 'keys.txt'))
    monkeypatch.setenv('PWD', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def with_key(devbase_root):
    """暗号化に使える devbase 専用鍵を用意する"""
    from devbase.env import agekeys

    _, public = agekeys.generate_key_file()
    return public


def encrypt_global(root, data):
    """グローバル設定を暗号化状態で作る"""
    SecretStore(root).age.save(GLOBAL, data)


def plain_global(root, data):
    SecretStore(root).plaintext.save(GLOBAL, data)


# ---------------------------------------------------------------------------
# プロジェクト名の解決
# ---------------------------------------------------------------------------

def test_project_name_resolves_from_the_project_dir(devbase_root):
    name = env_cmd._current_project_name(devbase_root, devbase_root / 'projects' / 'web')
    assert name == 'web'


def test_project_name_resolves_from_a_subdirectory(devbase_root):
    sub = devbase_root / 'projects' / 'web' / 'src' / 'deep'
    sub.mkdir(parents=True)
    assert env_cmd._current_project_name(devbase_root, sub) == 'web'


def test_project_name_is_none_outside_projects(devbase_root):
    assert env_cmd._current_project_name(devbase_root, devbase_root) is None


@pytest.fixture
def linked_project(devbase_root, tmp_path):
    """``projects/linked`` を tmp 配下の実体へのシンボリックリンクとして作る。

    プラグイン経由のプロジェクト (projects/<name> -> plugins/...) の再現。
    """
    target = tmp_path / 'link-target'
    (target / 'sub').mkdir(parents=True)
    (devbase_root / 'projects' / 'linked').symlink_to(target)
    return target


def test_project_name_resolves_inside_a_symlinked_project(devbase_root, linked_project):
    """リンク経由の論理パスで入ったらプロジェクト名が取れる"""
    link = devbase_root / 'projects' / 'linked'
    assert env_cmd._current_project_name(devbase_root, link) == 'linked'


def test_project_name_resolves_in_a_symlinked_project_subdirectory(devbase_root,
                                                                   linked_project):
    sub = devbase_root / 'projects' / 'linked' / 'sub'
    assert env_cmd._current_project_name(devbase_root, sub) == 'linked'


def test_project_name_is_none_from_the_symlink_target_path(devbase_root, linked_project):
    """リンク先の実体パスから実行した場合は ``None``。

    実体は ``projects/`` の外にあり、どのリンク名から辿られたのかを一意に
    決められない (複数のリンクが同じ実体を指しうる) ため、推測せず断る。
    """
    assert env_cmd._current_project_name(devbase_root, linked_project) is None
    assert env_cmd._current_project_name(devbase_root, linked_project / 'sub') is None


def test_project_name_resolves_through_a_dot_dot_path(devbase_root):
    """``..`` を含むパスは物理パス側のフォールバックで拾える"""
    sub = devbase_root / 'projects' / 'web' / 'src'
    sub.mkdir(parents=True)
    assert env_cmd._current_project_name(devbase_root, sub / '..' / 'src') == 'web'


# ---------------------------------------------------------------------------
# set / get / delete
# ---------------------------------------------------------------------------

def test_set_creates_plaintext_when_nothing_exists(devbase_root):
    assert env_cmd.cmd_env_set(devbase_root, 'FOO=bar') == 0
    assert (devbase_root / '.env').exists()
    assert SecretStore(devbase_root).load(GLOBAL) == {'FOO': 'bar'}


def test_set_writes_into_the_encrypted_store(devbase_root, with_key):
    encrypt_global(devbase_root, {'FOO': 'old'})

    assert env_cmd.cmd_env_set(devbase_root, 'FOO=new') == 0

    assert not (devbase_root / '.env').exists()      # 平文が生まれていない
    store = SecretStore(devbase_root)
    assert store.is_encrypted(GLOBAL)
    assert store.load(GLOBAL) == {'FOO': 'new'}


def test_set_does_not_lose_other_keys_in_the_encrypted_store(devbase_root, with_key):
    encrypt_global(devbase_root, {'KEEP': '1'})

    env_cmd.cmd_env_set(devbase_root, 'ADDED=2')

    assert SecretStore(devbase_root).load(GLOBAL) == {'KEEP': '1', 'ADDED': '2'}


def test_get_reads_from_the_encrypted_store(devbase_root, with_key, capsys):
    encrypt_global(devbase_root, {'TOKEN': 'secret-value'})

    assert env_cmd.cmd_env_get(devbase_root, 'TOKEN') == 0
    assert capsys.readouterr().out.strip() == 'secret-value'


def test_get_falls_back_to_the_project_secrets(devbase_root, with_key, monkeypatch, capsys):
    project_dir = devbase_root / 'projects' / 'web'
    monkeypatch.setenv('PWD', str(project_dir))
    SecretStore(devbase_root).age.save(SecretRef.for_project('web'), {'DB': 'pw'})

    assert env_cmd.cmd_env_get(devbase_root, 'DB') == 0
    assert capsys.readouterr().out.strip() == 'pw'


def test_get_reports_a_missing_key(devbase_root):
    assert env_cmd.cmd_env_get(devbase_root, 'NOPE') == 1


def test_delete_updates_the_encrypted_store(devbase_root, with_key):
    encrypt_global(devbase_root, {'A': '1', 'B': '2'})

    assert env_cmd.cmd_env_delete(devbase_root, 'A') == 0

    assert SecretStore(devbase_root).load(GLOBAL) == {'B': '2'}
    assert not (devbase_root / '.env').exists()


def test_delete_reports_a_missing_key(devbase_root, with_key):
    encrypt_global(devbase_root, {'A': '1'})
    assert env_cmd.cmd_env_delete(devbase_root, 'B') == 1


def test_delete_project_updates_the_encrypted_project_store(devbase_root, with_key,
                                                            monkeypatch):
    """暗号化されたプロジェクト設定からも CLI でキーを消せる"""
    monkeypatch.setenv('PWD', str(devbase_root / 'projects' / 'web'))
    store = SecretStore(devbase_root)
    store.age.save(SecretRef.for_project('web'), {'A': '1', 'B': '2'})
    encrypt_global(devbase_root, {'A': 'global'})

    assert env_cmd.cmd_env_delete(devbase_root, 'A', project=True) == 0

    assert store.load(SecretRef.for_project('web')) == {'B': '2'}
    # グローバル側の同名キーは巻き添えにしない
    assert store.load(GLOBAL) == {'A': 'global'}
    assert not (devbase_root / 'projects' / 'web' / '.env').exists()


def test_delete_project_requires_a_project_dir(devbase_root, with_key):
    encrypt_global(devbase_root, {'A': '1'})

    assert env_cmd.cmd_env_delete(devbase_root, 'A', project=True) == 1

    # グローバルへフォールバックしていない
    assert SecretStore(devbase_root).load(GLOBAL) == {'A': '1'}


def test_delete_project_reports_a_missing_key(devbase_root, with_key, monkeypatch):
    monkeypatch.setenv('PWD', str(devbase_root / 'projects' / 'web'))
    SecretStore(devbase_root).age.save(SecretRef.for_project('web'), {'A': '1'})

    assert env_cmd.cmd_env_delete(devbase_root, 'B', project=True) == 1


def test_set_project_requires_a_project_dir(devbase_root):
    assert env_cmd.cmd_env_set(devbase_root, 'FOO=bar', project=True) == 1
    assert not (devbase_root / '.env').exists()


def test_set_project_writes_under_the_project(devbase_root, monkeypatch):
    project_dir = devbase_root / 'projects' / 'web'
    monkeypatch.setenv('PWD', str(project_dir / 'src'))
    (project_dir / 'src').mkdir()

    assert env_cmd.cmd_env_set(devbase_root, 'FOO=bar', project=True) == 0

    # 下位ディレクトリで実行してもプロジェクト直下に書かれる
    assert (project_dir / '.env').exists()
    assert not (project_dir / 'src' / '.env').exists()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_list_marks_the_encrypted_store(devbase_root, with_key, capsys):
    encrypt_global(devbase_root, {'TOKEN': 'x'})

    env_cmd.cmd_env_list(devbase_root, global_only=True)

    out = capsys.readouterr().out
    assert '[暗号化]' in out
    assert 'global.env.age' in out


def test_list_does_not_mark_plaintext(devbase_root, capsys):
    plain_global(devbase_root, {'FOO': 'bar'})

    env_cmd.cmd_env_list(devbase_root, global_only=True)

    assert '[暗号化]' not in capsys.readouterr().out


def test_list_shows_project_secrets(devbase_root, with_key, monkeypatch, capsys):
    monkeypatch.setenv('PWD', str(devbase_root / 'projects' / 'web'))
    SecretStore(devbase_root).age.save(SecretRef.for_project('web'), {'DB': 'pw'})

    env_cmd.cmd_env_list(devbase_root, project_only=True, keys_only=True)

    out = capsys.readouterr().out
    assert 'web' in out and 'DB' in out


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def test_init_treats_an_encrypted_store_as_already_set_up(devbase_root, with_key, capsys):
    encrypt_global(devbase_root, {'FOO': 'bar'})

    assert env_cmd.cmd_env_init(devbase_root) == 0
    assert '既にセットアップ済み' in capsys.readouterr().out


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------

def fake_editor(monkeypatch, mutate):
    """EDITOR 起動を差し替えて、渡された一時ファイルを mutate させる"""
    calls = []

    def _call(argv):
        path = argv[-1]
        calls.append(path)
        return mutate(path)

    monkeypatch.setattr(env_cmd.subprocess, 'call', _call)
    return calls


def test_edit_opens_the_plaintext_file_directly(devbase_root, monkeypatch):
    plain_global(devbase_root, {'FOO': 'bar'})
    calls = fake_editor(monkeypatch, lambda path: 0)

    assert env_cmd.cmd_env_edit(devbase_root) == 0
    assert calls == [str(devbase_root / '.env')]


def test_edit_reencrypts_the_result(devbase_root, with_key, monkeypatch):
    from pathlib import Path

    encrypt_global(devbase_root, {'FOO': 'bar'})

    def mutate(path):
        Path(path).write_text('FOO=changed\nNEW=added\n')
        return 0

    fake_editor(monkeypatch, mutate)

    assert env_cmd.cmd_env_edit(devbase_root) == 0

    store = SecretStore(devbase_root)
    assert store.is_encrypted(GLOBAL)
    assert store.load(GLOBAL) == {'FOO': 'changed', 'NEW': 'added'}
    assert not (devbase_root / '.env').exists()


def test_edit_removes_the_temporary_plaintext(devbase_root, with_key, monkeypatch):
    from pathlib import Path

    encrypt_global(devbase_root, {'FOO': 'bar'})
    seen = {}

    def mutate(path):
        seen['path'] = Path(path)
        assert seen['path'].exists()
        assert 'bar' in seen['path'].read_text()   # 復号結果が渡っている
        return 0

    fake_editor(monkeypatch, mutate)
    env_cmd.cmd_env_edit(devbase_root)

    assert not seen['path'].exists()
    assert not seen['path'].parent.exists()


def test_edit_keeps_the_stored_value_when_the_editor_fails(devbase_root, with_key,
                                                          monkeypatch):
    from pathlib import Path

    encrypt_global(devbase_root, {'FOO': 'bar'})

    def mutate(path):
        Path(path).write_text('FOO=should-not-be-saved\n')
        return 1

    fake_editor(monkeypatch, mutate)

    assert env_cmd.cmd_env_edit(devbase_root) == 1
    assert SecretStore(devbase_root).load(GLOBAL) == {'FOO': 'bar'}


def test_edit_without_changes_leaves_the_ciphertext_alone(devbase_root, with_key,
                                                         monkeypatch, caplog):
    encrypt_global(devbase_root, {'FOO': 'bar'})
    before = SecretStore(devbase_root).age.path(GLOBAL).read_bytes()

    fake_editor(monkeypatch, lambda path: 0)

    with caplog.at_level('INFO', logger='devbase'):
        assert env_cmd.cmd_env_edit(devbase_root) == 0
    assert '変更はありません' in caplog.text
    # 同じ内容でも再暗号化すると nonce が変わり差分が出るため、書き直していない
    # ことをバイト列の同一性で確かめる
    assert SecretStore(devbase_root).age.path(GLOBAL).read_bytes() == before


def test_edit_rejects_a_non_utf8_result(devbase_root, with_key, monkeypatch):
    from pathlib import Path

    encrypt_global(devbase_root, {'FOO': 'bar'})

    def mutate(path):
        Path(path).write_bytes(b'\xff\xfe\x00broken')
        return 0

    fake_editor(monkeypatch, mutate)

    assert env_cmd.cmd_env_edit(devbase_root) == 1
    assert SecretStore(devbase_root).load(GLOBAL) == {'FOO': 'bar'}


def test_edit_project_reencrypts_the_project_store(devbase_root, with_key, monkeypatch):
    """暗号化されたプロジェクト設定も復号 → 編集 → 再暗号化できる"""
    from pathlib import Path

    monkeypatch.setenv('PWD', str(devbase_root / 'projects' / 'web'))
    store = SecretStore(devbase_root)
    project = SecretRef.for_project('web')
    store.age.save(project, {'FOO': 'bar'})
    encrypt_global(devbase_root, {'GLOBAL_KEY': 'kept'})

    def mutate(path):
        assert 'bar' in Path(path).read_text()   # 復号結果が渡っている
        Path(path).write_text('FOO=changed\n')
        return 0

    fake_editor(monkeypatch, mutate)

    assert env_cmd.cmd_env_edit(devbase_root, project=True) == 0

    assert store.is_encrypted(project)
    assert store.load(project) == {'FOO': 'changed'}
    assert not (devbase_root / 'projects' / 'web' / '.env').exists()
    # グローバル側は触っていない
    assert store.load(GLOBAL) == {'GLOBAL_KEY': 'kept'}


def test_edit_project_opens_the_plaintext_file_directly(devbase_root, monkeypatch):
    project_dir = devbase_root / 'projects' / 'web'
    monkeypatch.setenv('PWD', str(project_dir))
    SecretStore(devbase_root).plaintext.save(SecretRef.for_project('web'), {'FOO': 'bar'})
    calls = fake_editor(monkeypatch, lambda path: 0)

    assert env_cmd.cmd_env_edit(devbase_root, project=True) == 0
    assert calls == [str(project_dir / '.env')]


def test_edit_project_requires_a_project_dir(devbase_root, with_key, monkeypatch):
    encrypt_global(devbase_root, {'FOO': 'bar'})
    calls = fake_editor(monkeypatch, lambda path: 0)

    assert env_cmd.cmd_env_edit(devbase_root, project=True) == 1

    # エディタも起動していない (グローバルへフォールバックしていない)
    assert calls == []


# ---------------------------------------------------------------------------
# 両形式が同時に存在する場合
# ---------------------------------------------------------------------------

def test_commands_stop_when_both_formats_exist(devbase_root, with_key):
    from devbase.env.secret_store import SecretStoreError

    plain_global(devbase_root, {'FOO': 'plain'})
    encrypt_global(devbase_root, {'FOO': 'encrypted'})

    with pytest.raises(SecretStoreError, match='両方に存在'):
        env_cmd.cmd_env_get(devbase_root, 'FOO')


# ---------------------------------------------------------------------------
# 明示的な受信者での運用
# ---------------------------------------------------------------------------

def test_set_encrypts_for_every_registered_recipient(devbase_root, with_key):
    """受信者リストがあれば、set はそこに並ぶ全員宛に暗号化する"""
    from devbase.env import agekeys

    other = pyrage.x25519.Identity.generate()
    agekeys.add_recipient(devbase_root, with_key)              # 自分
    agekeys.add_recipient(devbase_root, str(other.to_public()))  # 同僚
    encrypt_global(devbase_root, {'FOO': 'bar'})

    assert env_cmd.cmd_env_set(devbase_root, 'FOO=updated') == 0

    # 自分の鍵でも
    assert SecretStore(devbase_root).load(GLOBAL) == {'FOO': 'updated'}
    # 同僚の鍵でも読める
    other_key = devbase_root / 'other.key'
    other_key.write_text(str(other))
    reader = SecretStore(devbase_root, identities=[str(other_key)])
    assert reader.load(GLOBAL) == {'FOO': 'updated'}
