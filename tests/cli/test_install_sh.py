#!/usr/bin/env python3
"""install.sh (ワンライナー installer) の分岐ロジックのテスト (PLAN31_1)。

`curl | bash` 相当の非対話導入を検証する。実際の clone 元・init の挙動に依存
しないよう、以下をスタブする:

- clone 元 (`DEVBASE_INSTALL_REPO`): ローカルに作った雛形 git repo を指す。
  雛形の `bin/devbase` は init をスタブする薄いスクリプトで、呼び出し引数と
  CWD を `DEVBASE_TEST_INIT_LOG` に追記するだけ (uv / network を起動しない)。
- 配置先 (`DEVBASE_INSTALL_DIR`) と `HOME`: 一時ディレクトリに隔離する。

これにより install.sh 自身の「前提チェック / 配置先解決 / clone・pull /
既存判定 / init を 1 回正しく呼ぶ / サニタイズ」だけを検証する。init 本体の
挙動は既存 init テストの責務 (plan §8)。
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
BASH = shutil.which("bash") or "/bin/bash"

# 雛形 repo の bin/devbase。init をスタブし、呼び出しを記録するだけ。
_STUB_DEVBASE = """#!/usr/bin/env bash
{
  echo "called:$*"
  echo "cwd:$PWD"
} >> "${DEVBASE_TEST_INIT_LOG:-/dev/null}"
"""


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd),
         "-c", "user.email=test@example.com", "-c", "user.name=test",
         *args],
        check=True, capture_output=True, text=True,
    )


def _make_source_repo(root: Path) -> Path:
    """clone 元となる雛形 devbase repo (branch=main) を作る。"""
    src = root / "src-devbase"
    (src / "bin").mkdir(parents=True)
    stub = src / "bin" / "devbase"
    stub.write_text(_STUB_DEVBASE)
    stub.chmod(0o755)
    # clone でファイルが運ばれたことを確認するためのマーカ
    (src / "MARKER").write_text("devbase-source\n")
    _git(src, "init", "-q")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "init")
    _git(src, "branch", "-M", "main")
    return src


def _run_install(*, home: Path, install_dir: Path | None, repo: Path,
                 ref: str = "main", init_log: Path | None = None,
                 path: str | None = None) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HOME": str(home),
        "DEVBASE_INSTALL_REPO": str(repo),
        "DEVBASE_INSTALL_REF": ref,
    }
    if install_dir is not None:
        env["DEVBASE_INSTALL_DIR"] = str(install_dir)
    else:
        env.pop("DEVBASE_INSTALL_DIR", None)
    if init_log is not None:
        env["DEVBASE_TEST_INIT_LOG"] = str(init_log)
    if path is not None:
        env["PATH"] = path
    # bash 自体は絶対パスで起動し、PATH 制限が install.sh 内部にだけ効くようにする。
    return subprocess.run(
        [BASH, str(INSTALL_SH)],
        capture_output=True, text=True, env=env,
    )


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    return _make_source_repo(tmp_path)


class TestFreshInstall:
    def test_clones_into_install_dir(self, tmp_path, source_repo):
        home = tmp_path / "home"; home.mkdir()
        install_dir = tmp_path / "install"
        log = tmp_path / "init.log"

        result = _run_install(home=home, install_dir=install_dir,
                              repo=source_repo, init_log=log)

        assert result.returncode == 0, result.stderr
        devbase = install_dir / "bin" / "devbase"
        assert devbase.exists(), "clone 後に bin/devbase が存在すること"
        assert os.access(devbase, os.X_OK), "bin/devbase が実行可能であること"
        assert (install_dir / "MARKER").read_text() == "devbase-source\n"

    def test_invokes_init_once_in_install_dir(self, tmp_path, source_repo):
        home = tmp_path / "home"; home.mkdir()
        install_dir = tmp_path / "install"
        log = tmp_path / "init.log"

        result = _run_install(home=home, install_dir=install_dir,
                              repo=source_repo, init_log=log)

        assert result.returncode == 0, result.stderr
        logged = log.read_text()
        # init が 1 回だけ呼ばれる
        assert logged.count("called:init") == 1, logged
        # init の引数は厳密に `init` のみ (余計な引数を渡さない)
        assert "called:init\n" in logged, logged
        # CWD が install_dir 配下であること
        assert f"cwd:{install_dir}" in logged, logged

    def test_default_install_dir_is_home_devbase(self, tmp_path, source_repo):
        """DEVBASE_INSTALL_DIR 未指定なら $HOME/devbase に clone する。"""
        home = tmp_path / "home"; home.mkdir()
        log = tmp_path / "init.log"

        result = _run_install(home=home, install_dir=None,
                              repo=source_repo, init_log=log)

        assert result.returncode == 0, result.stderr
        assert (home / "devbase" / "bin" / "devbase").exists()


class TestIdempotentRerun:
    def test_second_run_uses_pull_and_succeeds(self, tmp_path, source_repo):
        home = tmp_path / "home"; home.mkdir()
        install_dir = tmp_path / "install"
        log = tmp_path / "init.log"

        first = _run_install(home=home, install_dir=install_dir,
                            repo=source_repo, init_log=log)
        assert first.returncode == 0, first.stderr

        second = _run_install(home=home, install_dir=install_dir,
                            repo=source_repo, init_log=log)
        assert second.returncode == 0, second.stderr
        # 2 回目は再 clone せず pull 経路に入る (出力にその旨)
        combined = (second.stdout + second.stderr).lower()
        assert "pull" in combined or "updat" in combined, combined
        # init は各回 1 度ずつ → 計 2 回
        assert log.read_text().count("called:init") == 2


class TestExistingNonDevbaseDir:
    def test_aborts_without_overwriting(self, tmp_path, source_repo):
        home = tmp_path / "home"; home.mkdir()
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        sentinel = install_dir / "important.txt"
        sentinel.write_text("DO_NOT_TOUCH")
        log = tmp_path / "init.log"

        result = _run_install(home=home, install_dir=install_dir,
                              repo=source_repo, init_log=log)

        assert result.returncode != 0, "非 devbase ディレクトリでは中止すること"
        # 既存ファイルを上書き/削除しない
        assert sentinel.read_text() == "DO_NOT_TOUCH"
        assert not (install_dir / "bin" / "devbase").exists()
        # 別ディレクトリ指定方法を案内する
        assert "DEVBASE_INSTALL_DIR" in (result.stdout + result.stderr)
        # init は呼ばれない
        assert not log.exists() or "called:init" not in log.read_text()


class TestPrerequisiteCheck:
    def test_missing_git_errors(self, tmp_path, source_repo):
        home = tmp_path / "home"; home.mkdir()
        install_dir = tmp_path / "install"

        # git を除いた PATH を構築する。
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        seen: set[str] = set()
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if not d or not os.path.isdir(d):
                continue
            for name in os.listdir(d):
                if name == "git" or name in seen:
                    continue
                try:
                    (fakebin / name).symlink_to(Path(d) / name)
                    seen.add(name)
                except OSError:
                    pass

        result = _run_install(home=home, install_dir=install_dir,
                              repo=source_repo, path=str(fakebin))

        assert result.returncode != 0, "git 不在ではエラー終了すること"
        assert "git" in (result.stdout + result.stderr).lower()
        assert not (install_dir / "bin" / "devbase").exists()


class TestRefSanitization:
    @pytest.mark.parametrize("bad_ref", [
        "--upload-pack=touch /tmp/pwn",   # オプション注入
        "-x",                              # 先頭ハイフン
        "main;rm -rf /",                   # シェルメタ文字
        "$(touch /tmp/pwn)",               # コマンド置換
    ])
    def test_rejects_unsafe_ref(self, tmp_path, source_repo, bad_ref):
        home = tmp_path / "home"; home.mkdir()
        install_dir = tmp_path / "install"

        result = _run_install(home=home, install_dir=install_dir,
                              repo=source_repo, ref=bad_ref)

        assert result.returncode != 0, f"不正な REF を拒否すること: {bad_ref!r}"
        assert not (install_dir / "bin" / "devbase").exists()
        # git の「branch not found」ではなく install.sh のサニタイズが拒否したこと
        # を確認する (clone に到達する前に弾く)。
        out = (result.stdout + result.stderr)
        assert "REF" in out and "invalid" in out.lower(), out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
