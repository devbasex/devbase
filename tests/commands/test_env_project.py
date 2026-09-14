"""cmd_env_project: プロジェクト環境変数の設定（現状固定テスト）"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from devbase.commands import env as env_cmd


@pytest.fixture
def project_setup(tmp_path, monkeypatch):
    """一時ディレクトリの実 SecretStore を使い、projects/web 配下に移動する"""
    project_dir = tmp_path / 'projects' / 'web'
    project_dir.mkdir(parents=True)
    monkeypatch.setenv('PWD', str(project_dir))
    monkeypatch.chdir(project_dir)
    return tmp_path, project_dir


def test_env_project_outside_project_directory(tmp_path, monkeypatch, caplog):
    """projects/ 配下以外で実行された場合は 1 を返し、エラーログを出力する"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PWD', str(tmp_path))

    with caplog.at_level(logging.ERROR):
        rc = env_cmd.cmd_env_project(tmp_path)

    assert rc == 1
    assert "projects/ 配下で実行してください" in caplog.text


def test_env_project_with_env_yml_variables(project_setup, monkeypatch, capsys, caplog):
    """env.yml の variables 処理 (既存値スキップ・generate 既定長・generate 明示長・任意値の空入力)"""
    devbase_root, project_dir = project_setup

    # 既存値
    env_path = project_dir / '.env'
    env_path.write_text("EXISTING_KEY=old_value\n", encoding="utf-8")

    env_yml_content = {
        'variables': [
            {'name': 'EXISTING_KEY', 'prompt': 'Existing Key'},
            {'name': 'GEN_DEFAULT', 'generate': 'token'},
            {'name': 'GEN_CUSTOM', 'generate': 'token:32'},
            {'name': 'OPTIONAL_EMPTY', 'prompt': 'Optional', 'required': False},
            {'name': 'NORMAL_INPUT', 'prompt': 'Normal', 'default': 'my_default'},
        ]
    }
    (project_dir / 'env.yml').write_text(yaml.dump(env_yml_content), encoding='utf-8')

    # safe_input: OPTIONAL_EMPTY は空入力 (default も空 -> スキップ), NORMAL_INPUT は入力値
    inputs = iter(["", "custom_val"])
    monkeypatch.setattr('devbase.commands.env.safe_input',
                        lambda prompt, default="": (next(inputs) or default))

    # secrets.token_hex: 境界で固定 (length // 2 のバイト数を 16進数文字列化)
    monkeypatch.setattr('secrets.token_hex', lambda n: 'aa' * n)

    with caplog.at_level(logging.INFO):
        rc = env_cmd.cmd_env_project(devbase_root)

    assert rc == 0
    captured = capsys.readouterr()
    assert "=== web プロジェクト環境変数 ===" in captured.out
    assert "EXISTING_KEY: 設定済み" in captured.out
    assert "GEN_DEFAULT: (自動生成)" in captured.out
    assert "GEN_CUSTOM: (自動生成)" in captured.out

    saved_text = env_path.read_text(encoding="utf-8")
    assert "EXISTING_KEY=old_value" in saved_text
    assert f"GEN_DEFAULT={'aa' * 32}" in saved_text  # 64 hex chars
    assert f"GEN_CUSTOM={'aa' * 16}" in saved_text   # 32 hex chars
    assert "NORMAL_INPUT=custom_val" in saved_text
    assert "OPTIONAL_EMPTY" not in saved_text

    # 保存後の件数表示
    assert "保存完了:" in caplog.text
    assert "(4変数)" in caplog.text


def test_env_project_with_env_yml_required_empty_aborts(project_setup, monkeypatch, caplog):
    """必須変数で空入力の場合は中止して 1 を返し、保存しない"""
    devbase_root, project_dir = project_setup

    env_yml_content = {
        'variables': [
            {'name': 'REQUIRED_KEY', 'prompt': 'Required Key', 'required': True},
        ]
    }
    (project_dir / 'env.yml').write_text(yaml.dump(env_yml_content), encoding='utf-8')

    monkeypatch.setattr('devbase.commands.env.safe_input', lambda prompt, default="": "")

    with caplog.at_level(logging.ERROR):
        rc = env_cmd.cmd_env_project(devbase_root)

    assert rc == 1
    assert "必須変数 'REQUIRED_KEY' が設定されていません" in caplog.text
    assert not (project_dir / '.env').exists()


def test_env_project_without_env_yml_interactive_loop(project_setup, monkeypatch, capsys, caplog):
    """env.yml 不在時の手入力ループ (不正形式の再案内、KEY=VALUE の設定、空行での終了)"""
    devbase_root, project_dir = project_setup

    inputs = iter([
        "INVALID_NO_EQUALS",
        "FOO=bar",
        "BAZ = qux ",
        "",
    ])
    monkeypatch.setattr('devbase.commands.env.safe_input',
                        lambda prompt, default="": next(inputs))

    with caplog.at_level(logging.INFO):
        rc = env_cmd.cmd_env_project(devbase_root)

    assert rc == 0
    captured = capsys.readouterr()
    assert "env.yml が見つかりません。手動で変数を追加してください。" in captured.out
    assert "(Ctrl+Dで終了)" in captured.out
    assert "形式: KEY=VALUE" in captured.out

    saved_text = (project_dir / '.env').read_text(encoding="utf-8")
    assert "FOO=bar" in saved_text
    assert "BAZ=qux" in saved_text

    assert "保存完了:" in caplog.text
    assert "(2変数)" in caplog.text


def test_env_project_without_env_yml_eof_termination(project_setup, monkeypatch, caplog):
    """env.yml 不在時の手入力ループで EOFError が発生したときに正常終了して保存する"""
    devbase_root, project_dir = project_setup

    def raise_eof(prompt, default=""):
        raise EOFError

    monkeypatch.setattr('devbase.commands.env.safe_input', raise_eof)

    with caplog.at_level(logging.INFO):
        rc = env_cmd.cmd_env_project(devbase_root)

    assert rc == 0
    assert (project_dir / '.env').exists()
    assert "保存完了:" in caplog.text
    assert "(0変数)" in caplog.text
