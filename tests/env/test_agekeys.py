"""agekeys.py: devbase 専用 age 鍵と受信者リストの管理"""

from __future__ import annotations

import os
import stat

import pyrage
import pytest

from devbase.env import agekeys


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """鍵の既定パスを tmp_path 配下へ閉じ込める"""
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.delenv(agekeys.KEY_FILE_ENV, raising=False)
    monkeypatch.setattr(agekeys.Path, 'home', staticmethod(lambda: tmp_path / 'home'))
    return tmp_path


# ---------------------------------------------------------------------------
# パス解決
# ---------------------------------------------------------------------------

def test_key_file_path_uses_xdg_config_home(isolated_home):
    assert agekeys.key_file_path() == isolated_home / 'config' / 'devbase' / 'age' / 'keys.txt'


def test_key_file_path_env_override_wins(isolated_home, monkeypatch):
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(isolated_home / 'custom' / 'k.txt'))
    assert agekeys.key_file_path() == isolated_home / 'custom' / 'k.txt'


def test_key_file_path_falls_back_to_home_config(isolated_home, monkeypatch):
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    assert agekeys.key_file_path() == isolated_home / 'home' / '.config' / 'devbase' / 'age' / 'keys.txt'


# ---------------------------------------------------------------------------
# 鍵の生成
# ---------------------------------------------------------------------------

def test_generate_key_file_writes_private_key_with_0600(isolated_home):
    path, public = agekeys.generate_key_file()

    assert path.exists()
    assert public.startswith('age1')
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert 'AGE-SECRET-KEY-1' in path.read_text()


def test_generate_key_file_creates_dir_with_0700(isolated_home):
    path, _ = agekeys.generate_key_file()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_generate_key_file_creates_every_missing_level_with_0700(isolated_home,
                                                                 monkeypatch):
    """親を複数階層まとめて作る場合、作った階層はすべて 0700 になる"""
    key_path = isolated_home / 'a' / 'b' / 'c' / 'keys.txt'
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(key_path))

    agekeys.generate_key_file()

    for level in (key_path.parent, key_path.parent.parent,
                  key_path.parent.parent.parent):
        assert stat.S_IMODE(level.stat().st_mode) == 0o700


def test_created_dirs_are_0700_from_the_moment_of_creation(isolated_home,
                                                           monkeypatch):
    """umask 0 でも各階層は「作成した瞬間から」0700。

    一括作成してから chmod する実装だと、作成〜chmod の間だけ umask 依存の
    緩い権限が露出する。作成直後の mode を記録して、その隙が無いことを見る。
    """
    key_path = isolated_home / 'u1' / 'u2' / 'keys.txt'
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(key_path))

    real_mkdir = agekeys.Path.mkdir
    modes_at_creation = {}

    def recording_mkdir(self, *args, **kwargs):
        result = real_mkdir(self, *args, **kwargs)
        modes_at_creation[self] = stat.S_IMODE(self.stat().st_mode)
        return result

    monkeypatch.setattr(agekeys.Path, 'mkdir', recording_mkdir)

    old = os.umask(0)
    try:
        agekeys.generate_key_file()
    finally:
        os.umask(old)

    levels = (key_path.parent, key_path.parent.parent)
    for level in levels:
        assert modes_at_creation[level] == 0o700, f'{level} が作成時点で緩い'
        assert stat.S_IMODE(level.stat().st_mode) == 0o700


def test_generate_key_file_survives_a_concurrently_created_level(isolated_home,
                                                                 monkeypatch):
    """途中の階層を別プロセスが先に作っていても失敗しない。

    先に作られた階層は「既存ディレクトリ」なので、権限は触らず素通りする
    (既存ディレクトリを chmod しないという方針と一貫させる)。
    """
    key_path = isolated_home / 'x' / 'y' / 'keys.txt'
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(key_path))

    racy = isolated_home / 'x'
    real_mkdir = agekeys.Path.mkdir

    def racing_mkdir(self, *args, **kwargs):
        if self == racy and not self.exists():
            # 別プロセスが一足先に作った状況を再現する
            real_mkdir(self)
            os.chmod(self, 0o755)
            raise FileExistsError(17, 'File exists', str(self))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(agekeys.Path, 'mkdir', racing_mkdir)

    path, _ = agekeys.generate_key_file()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # 他プロセスが作った階層の権限は変えない
    assert stat.S_IMODE(racy.stat().st_mode) == 0o755
    # 自分で作った階層は 0700
    assert stat.S_IMODE(key_path.parent.stat().st_mode) == 0o700


def test_generate_key_file_does_not_chmod_an_existing_dir(isolated_home,
                                                          monkeypatch):
    """既存の共有ディレクトリを鍵の置き場に指定しても、その権限を変えない。

    ``DEVBASE_AGE_KEY_FILE=/tmp/devbase-key`` のように既に在る共有ディレクトリを
    指されたとき、そこを 0700 に落とすと他ユーザーやサービスのアクセスを壊す。
    devbase が作っていないディレクトリは devbase の管轄外として触らない。
    """
    shared = isolated_home / 'shared'
    shared.mkdir()
    os.chmod(shared, 0o755)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(shared / 'devbase-key'))

    path, _ = agekeys.generate_key_file()

    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    # ディレクトリを緩いままにする代わり、鍵ファイル自体は 0600 で守る
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_generate_key_file_warns_about_a_permissive_existing_dir(isolated_home,
                                                                 monkeypatch,
                                                                 caplog):
    """権限を変えない代わりに、緩い既存ディレクトリは警告で知らせる"""
    shared = isolated_home / 'shared'
    shared.mkdir()
    os.chmod(shared, 0o777)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(shared / 'devbase-key'))

    with caplog.at_level('WARNING'):
        agekeys.generate_key_file()

    assert any('shared' in r.getMessage() for r in caplog.records)


def test_generate_key_file_keeps_quiet_for_an_already_tight_existing_dir(
        isolated_home, monkeypatch, caplog):
    """既存でも 0700 なら警告しない (毎回鳴ると本当の警告が埋もれる)"""
    tight = isolated_home / 'tight'
    tight.mkdir()
    os.chmod(tight, 0o700)
    monkeypatch.setenv(agekeys.KEY_FILE_ENV, str(tight / 'devbase-key'))

    with caplog.at_level('WARNING'):
        agekeys.generate_key_file()

    assert caplog.records == []


def test_save_recipients_does_not_chmod_an_existing_secrets_dir(tmp_path):
    """受信者リスト側も既存ディレクトリの権限を変えない (鍵ファイルと一貫)"""
    secrets = tmp_path / 'secrets'
    secrets.mkdir(parents=True)
    os.chmod(secrets, 0o755)

    path = agekeys.save_recipients(
        tmp_path, [str(pyrage.x25519.Identity.generate().to_public())])

    assert stat.S_IMODE(secrets.stat().st_mode) == 0o755
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_recipients_creates_the_secrets_dir_with_0700(tmp_path):
    """自分で作った secrets/ は 0700 にする"""
    agekeys.save_recipients(
        tmp_path, [str(pyrage.x25519.Identity.generate().to_public())])
    assert stat.S_IMODE((tmp_path / 'secrets').stat().st_mode) == 0o700


def test_generate_key_file_refuses_overwrite_without_force(isolated_home):
    path, _ = agekeys.generate_key_file()
    before = path.read_bytes()

    with pytest.raises(agekeys.AgeKeyError, match='既に存在'):
        agekeys.generate_key_file()

    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# 新規生成の排他性 (TOCTOU)
#
# 「存在チェック → 生成」の隙間に他プロセスが鍵を作れると、後発が先発の鍵を
# 消し、先発鍵で暗号化した機密がその瞬間から復号不能になる。新規生成は
# O_CREAT|O_EXCL で不可分に作り、隙間そのものを無くす。
# ---------------------------------------------------------------------------

def test_generate_key_file_creates_a_new_key_exclusively(isolated_home, monkeypatch):
    """新規生成は O_EXCL 付きで open する (os.replace で置き換えない)"""
    seen = []
    real_open = os.open

    def spy_open(target, flags, *args, **kwargs):
        seen.append((str(target), flags))
        return real_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(agekeys.os, 'open', spy_open)

    path, _ = agekeys.generate_key_file()

    key_flags = [flags for target, flags in seen if target == str(path)]
    assert key_flags, "鍵ファイルが os.open 経由で作られていない"
    assert all(flags & os.O_EXCL for flags in key_flags), \
        "新規生成に O_EXCL が付いていない (判定と作成の隙間が残る)"


def test_generate_key_file_does_not_clobber_a_key_created_after_the_check(
        isolated_home, monkeypatch):
    """事前チェック通過後に他プロセスが鍵を作っても、その鍵を上書きしない。

    ``_ensure_private_dir`` の直後に鍵を差し込んで、判定と書き込みの隙間で
    並行プロセスが先に生成した状況を再現する。排他生成なら後発 (このテストの
    呼び出し) が負けて、先発の鍵が 1 バイトも変わらずに残る。
    """
    path = agekeys.key_file_path()
    real_ensure = agekeys._ensure_private_dir
    rival = b'AGE-SECRET-KEY-1RIVAL\n'

    def ensure_then_race(parent):
        real_ensure(parent)
        if not path.exists():
            path.write_bytes(rival)

    monkeypatch.setattr(agekeys, '_ensure_private_dir', ensure_then_race)

    with pytest.raises(agekeys.AgeKeyError, match='既に存在'):
        agekeys.generate_key_file()

    assert path.read_bytes() == rival


def test_generate_key_file_leaves_no_temp_file_on_first_generation(isolated_home):
    """新規生成は一時ファイルを経由しない (鍵ディレクトリに残骸を残さない)"""
    path, _ = agekeys.generate_key_file()
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_generate_key_file_removes_a_half_written_key_on_failure(isolated_home,
                                                                 monkeypatch):
    """書き込み途中で落ちたら中途半端な鍵を残さない。

    半端な鍵が残ると、以後の生成が「既に存在します」で止まるうえ、その鍵では
    何も復号できないという最悪の状態になる。
    """
    path = agekeys.key_file_path()

    def boom(fd):
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(agekeys.os, 'fsync', boom)

    with pytest.raises(OSError):
        agekeys.generate_key_file()

    assert not path.exists()


def test_generate_key_file_force_replaces_key(isolated_home):
    path, first = agekeys.generate_key_file()
    _, second = agekeys.generate_key_file(force=True)
    assert first != second
    assert agekeys.read_public_key(path) == second


def test_generate_key_file_force_keeps_0600(isolated_home):
    """一時ファイル経由の差し替えでも権限が広がらない"""
    path, _ = agekeys.generate_key_file()
    agekeys.generate_key_file(force=True)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_generate_key_file_force_leaves_no_temp_file(isolated_home):
    """差し替え用の一時ファイルが鍵ディレクトリに残らない"""
    path, _ = agekeys.generate_key_file()
    agekeys.generate_key_file(force=True)
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_generate_key_file_force_keeps_old_key_when_replace_fails(isolated_home,
                                                                  monkeypatch):
    """差し替えに失敗しても旧鍵は無傷のまま残る (O_TRUNC 直書きなら失われる)"""
    path, first = agekeys.generate_key_file()
    before = path.read_bytes()

    def boom(src, dst):
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(agekeys._io_common.os, 'replace', boom)

    with pytest.raises(OSError):
        agekeys.generate_key_file(force=True)

    assert path.read_bytes() == before
    assert agekeys.read_public_key(path) == first
    # 書きかけの一時ファイルも掃除されている
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_save_recipients_keeps_old_list_when_replace_fails(tmp_path, monkeypatch):
    """受信者リストも差し替え失敗時に旧内容を保つ"""
    pubs = [str(pyrage.x25519.Identity.generate().to_public()) for _ in range(2)]
    agekeys.save_recipients(tmp_path, pubs)
    path = agekeys.recipients_file(tmp_path)
    before = path.read_bytes()

    def boom(src, dst):
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(agekeys._io_common.os, 'replace', boom)

    with pytest.raises(OSError):
        agekeys.save_recipients(tmp_path, pubs[:1])

    assert path.read_bytes() == before
    assert agekeys.load_recipients(tmp_path) == pubs


def test_generated_key_can_decrypt_what_its_public_key_encrypted(isolated_home):
    from devbase.env import cipher

    path, public = agekeys.generate_key_file()
    blob = cipher.encrypt(b'payload', recipients=[public])
    assert cipher.decrypt(blob, identities=[str(path)]) == b'payload'


# ---------------------------------------------------------------------------
# 公開鍵の読み取り
# ---------------------------------------------------------------------------

def test_read_public_key_derives_from_secret_not_comment(isolated_home):
    """コメント行が嘘でも、秘密鍵から導出した公開鍵を返す"""
    path, public = agekeys.generate_key_file()
    tampered = path.read_text().replace(f'# public key: {public}',
                                        '# public key: age1deadbeef')
    path.write_text(tampered)

    assert agekeys.read_public_key(path) == public


def test_read_public_key_missing_file(isolated_home):
    with pytest.raises(agekeys.AgeKeyError, match='見つかりません'):
        agekeys.read_public_key(isolated_home / 'nope.txt')


def test_read_public_key_rejects_non_age_key(isolated_home):
    path = isolated_home / 'ssh_like.txt'
    path.write_text('-----BEGIN OPENSSH PRIVATE KEY-----\nzzz\n')
    with pytest.raises(agekeys.AgeKeyError, match='AGE-SECRET-KEY-1'):
        agekeys.read_public_key(path)


# ---------------------------------------------------------------------------
# 受信者リスト
# ---------------------------------------------------------------------------

def test_recipients_roundtrip(tmp_path):
    pub = str(pyrage.x25519.Identity.generate().to_public())

    assert agekeys.load_recipients(tmp_path) == []
    assert agekeys.add_recipient(tmp_path, pub) is True
    assert agekeys.load_recipients(tmp_path) == [pub]

    # 重複登録は no-op
    assert agekeys.add_recipient(tmp_path, pub) is False
    assert agekeys.load_recipients(tmp_path) == [pub]

    assert agekeys.remove_recipient(tmp_path, pub) is True
    assert agekeys.load_recipients(tmp_path) == []
    assert agekeys.remove_recipient(tmp_path, pub) is False


def test_recipients_file_is_0600(tmp_path):
    pub = str(pyrage.x25519.Identity.generate().to_public())
    agekeys.add_recipient(tmp_path, pub)
    path = agekeys.recipients_file(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_add_recipient_rejects_malformed_key(tmp_path):
    from devbase.env.cipher import CipherError

    with pytest.raises(CipherError):
        agekeys.add_recipient(tmp_path, 'not-a-key')
    assert not agekeys.recipients_file(tmp_path).exists()


def test_load_recipients_skips_comments_and_blanks(tmp_path):
    pub = str(pyrage.x25519.Identity.generate().to_public())
    path = agekeys.recipients_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(f"# header\n\n{pub}\n   \n")
    assert agekeys.load_recipients(tmp_path) == [pub]


# ---------------------------------------------------------------------------
# 鍵の解決
# ---------------------------------------------------------------------------

def test_resolve_recipients_prefers_registered_list(isolated_home, tmp_path):
    _, own = agekeys.generate_key_file()
    other = str(pyrage.x25519.Identity.generate().to_public())
    agekeys.add_recipient(tmp_path, other)

    assert agekeys.resolve_recipients(tmp_path) == [other]
    assert own not in agekeys.resolve_recipients(tmp_path)


def test_resolve_recipients_falls_back_to_own_public_key(isolated_home, tmp_path):
    _, own = agekeys.generate_key_file()
    assert agekeys.resolve_recipients(tmp_path) == [own]


def test_resolve_recipients_without_any_key_raises(isolated_home, tmp_path):
    with pytest.raises(agekeys.AgeKeyError, match='公開鍵がありません'):
        agekeys.resolve_recipients(tmp_path)


def test_resolve_identities_puts_devbase_key_first(isolated_home, monkeypatch):
    ssh_key = isolated_home / 'id_ed25519'
    ssh_key.write_text('dummy')
    monkeypatch.setattr(agekeys._cipher, 'default_identity_paths',
                        lambda: [ssh_key])

    path, _ = agekeys.generate_key_file()
    assert agekeys.resolve_identities() == [str(path), str(ssh_key)]


def test_resolve_identities_empty_when_nothing_exists(isolated_home, monkeypatch):
    monkeypatch.setattr(agekeys._cipher, 'default_identity_paths', lambda: [])
    assert agekeys.resolve_identities() == []


def test_save_recipients_is_idempotent_for_content(tmp_path):
    pubs = [str(pyrage.x25519.Identity.generate().to_public()) for _ in range(2)]
    agekeys.save_recipients(tmp_path, pubs)
    first = agekeys.recipients_file(tmp_path).read_text()
    agekeys.save_recipients(tmp_path, pubs)
    assert agekeys.recipients_file(tmp_path).read_text() == first
    assert agekeys.load_recipients(tmp_path) == pubs


def test_umask_does_not_widen_key_permissions(isolated_home):
    """umask 0 でも鍵が 0600 で作られる (作成時点から権限を絞る)"""
    old = os.umask(0)
    try:
        path, _ = agekeys.generate_key_file()
    finally:
        os.umask(old)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_resolve_identities_still_decrypts_ssh_ciphertext_when_devbase_key_broken(
        isolated_home, monkeypatch):
    """専用鍵ファイルが壊れていても、旧来 ``~/.ssh`` の鍵で暗号化した暗号文を
    ``resolve_identities()`` 経由で復号できる (移行互換性そのものの検証)。

    ``resolve_identities`` は専用鍵を先頭に置くため、専用鍵の解決失敗でそこで
    止まってしまうと ``~/.ssh`` の鍵を試せない (PR #91 codex 指摘)。
    """
    from devbase.env import cipher

    ssh_identity = pyrage.x25519.Identity.generate()
    ssh_key = isolated_home / 'id_ed25519'
    ssh_key.write_text(str(ssh_identity))
    monkeypatch.setattr(agekeys._cipher, 'default_identity_paths',
                        lambda: [ssh_key])

    # 専用鍵ファイルは存在するが中身が壊れている状態を作る
    key_file = agekeys.key_file_path()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text('broken key material\n')

    identities = agekeys.resolve_identities()
    assert identities == [str(key_file), str(ssh_key)]

    blob = cipher.encrypt(b'legacy-secret',
                          recipients=[str(ssh_identity.to_public())])
    assert cipher.decrypt(blob, identities=identities) == b'legacy-secret'
