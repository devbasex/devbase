"""compose_migrate.py: 構成ファイルの機密参照を外す / 戻す"""

from __future__ import annotations

from pathlib import Path

from devbase.env import compose_migrate as cm


BASIC = """services:

  dev:
    image: carmo:latest
    env_file:
      - ${DEVBASE_ROOT}/.env
      - env
      - .env
    command: tail -f /dev/null
"""


def test_disable_comments_out_secret_entries_only():
    after, touched = cm.disable(BASIC)

    assert '- env\n' in after
    assert f'{cm.DISABLED_MARK}- ${{DEVBASE_ROOT}}/.env' in after
    assert f'{cm.DISABLED_MARK}- .env' in after
    assert touched == ['${DEVBASE_ROOT}/.env', '.env']


def test_disable_keeps_indentation():
    after, _ = cm.disable(BASIC)
    line = next(l for l in after.splitlines() if cm.DISABLED_MARK in l)
    assert line.startswith('      #')


def test_round_trip_restores_the_original_text():
    disabled, _ = cm.disable(BASIC)
    restored, touched = cm.enable(disabled)

    assert restored == BASIC
    assert len(touched) == 2


def test_disable_is_idempotent():
    once, _ = cm.disable(BASIC)
    twice, touched = cm.disable(once)

    assert twice == once
    assert touched == []


def test_only_the_requested_targets_are_disabled():
    after, touched = cm.disable(BASIC, {cm.TARGET_GLOBAL})

    assert touched == ['${DEVBASE_ROOT}/.env']
    assert '      - .env\n' in after


def test_project_only_leaves_the_global_entry():
    after, touched = cm.disable(BASIC, {cm.TARGET_PROJECT})

    assert touched == ['.env']
    assert '      - ${DEVBASE_ROOT}/.env\n' in after


def test_env_file_key_is_disabled_when_no_entry_remains():
    """全エントリを落とすと `env_file:` だけが残り Compose が失敗するため"""
    text = """services:
  dev:
    env_file:
      - ${DEVBASE_ROOT}/.env
    image: x
"""
    after, _ = cm.disable(text)

    assert f'{cm.DISABLED_MARK}env_file:' in after
    assert cm.enable(after)[0] == text


def test_other_env_files_keep_the_key_active():
    after, _ = cm.disable(BASIC)
    assert '    env_file:\n' in after


def test_user_comments_are_preserved():
    text = """services:
  dev:
    env_file:
      # 共通設定
      - ${DEVBASE_ROOT}/.env
      - env   # プロジェクト設定
    image: x
"""
    after, _ = cm.disable(text)

    assert '      # 共通設定\n' in after
    assert '      - env   # プロジェクト設定\n' in after
    assert cm.enable(after)[0] == text


def test_quoted_entries_are_recognised():
    text = """services:
  dev:
    env_file:
      - "${DEVBASE_ROOT}/.env"
      - env
"""
    after, touched = cm.disable(text)

    assert touched == ['${DEVBASE_ROOT}/.env']
    assert cm.enable(after)[0] == text


def test_bare_dollar_form_is_recognised():
    text = """services:
  dev:
    env_file:
      - $DEVBASE_ROOT/.env
      - env
"""
    _, touched = cm.disable(text)
    assert touched == ['$DEVBASE_ROOT/.env']


def test_unrelated_env_files_are_left_alone():
    text = """services:
  dev:
    env_file:
      - config/app.env
      - env
"""
    after, touched = cm.disable(text)

    assert touched == []
    assert after == text


def test_multiple_services_are_handled():
    text = """services:
  dev:
    env_file:
      - ${DEVBASE_ROOT}/.env
      - env
  worker:
    env_file:
      - ${DEVBASE_ROOT}/.env
      - env
"""
    after, touched = cm.disable(text)

    assert len(touched) == 2
    assert after.count(cm.DISABLED_MARK) == 2
    assert cm.enable(after)[0] == text


def test_find_secret_entries_does_not_modify():
    found = cm.find_secret_entries(BASIC)
    assert found == ['${DEVBASE_ROOT}/.env', '.env']


def test_diff_mentions_both_sides():
    after, _ = cm.disable(BASIC)
    patch = cm.diff(BASIC, after, Path('compose.yml'))

    assert '(現在)' in patch and '(変更後)' in patch
    assert '-      - ${DEVBASE_ROOT}/.env' in patch


def test_compose_files_lists_only_existing(tmp_path):
    (tmp_path / 'projects' / 'web').mkdir(parents=True)
    (tmp_path / 'projects' / 'web' / 'compose.yml').write_text(BASIC)
    (tmp_path / 'projects' / 'api').mkdir()

    found = cm.compose_files(tmp_path, ['web', 'api', 'missing'])

    assert found == [tmp_path / 'projects' / 'web' / 'compose.yml']
