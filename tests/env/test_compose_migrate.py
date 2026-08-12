"""compose_migrate.py: 構成ファイルの機密参照を外す / 戻す"""

from __future__ import annotations

import logging
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


# ---------------------------------------------------------------------------
# enable: 種別を絞った復元 (部分復号)
# ---------------------------------------------------------------------------

def test_enable_restores_only_the_requested_targets():
    """共通設定が暗号化されたままなら、その参照は戻してはいけない"""
    disabled, _ = cm.disable(BASIC)

    after, restored = cm.enable(disabled, {cm.TARGET_PROJECT})

    assert '      - .env\n' in after
    assert restored == ['- .env']
    assert f'{cm.DISABLED_MARK}- ${{DEVBASE_ROOT}}/.env' in after


def test_enable_is_the_inverse_of_disable_per_target():
    disabled, _ = cm.disable(BASIC)
    partial, _ = cm.enable(disabled, {cm.TARGET_PROJECT})
    full, _ = cm.enable(partial, {cm.TARGET_GLOBAL})

    assert full == BASIC


def test_enable_leaves_the_key_disabled_while_entries_stay_disabled():
    """エントリを戻さないのに `env_file:` だけ戻すと Compose が失敗する"""
    text = """services:
  dev:
    env_file:
      - ${DEVBASE_ROOT}/.env
    image: x
"""
    disabled, _ = cm.disable(text)

    after, restored = cm.enable(disabled, {cm.TARGET_PROJECT})

    assert after == disabled
    assert restored == []


def test_enable_restores_the_key_together_with_the_last_entry():
    text = """services:
  dev:
    env_file:
      - ${DEVBASE_ROOT}/.env
    image: x
"""
    disabled, _ = cm.disable(text)

    after, restored = cm.enable(disabled, {cm.TARGET_GLOBAL})

    assert after == text
    assert restored == ['- ${DEVBASE_ROOT}/.env', 'env_file:']


# ---------------------------------------------------------------------------
# 空行を含むリスト
# ---------------------------------------------------------------------------

BLANK_IN_LIST = """services:
  dev:
    env_file:
      - ${DEVBASE_ROOT}/.env

      - .env
    image: x
"""


def test_blank_lines_inside_the_list_do_not_stop_the_scan():
    after, touched = cm.disable(BLANK_IN_LIST)

    assert touched == ['${DEVBASE_ROOT}/.env', '.env']
    assert f'{cm.DISABLED_MARK}- .env' in after


def test_blank_lines_do_not_make_the_key_look_used():
    """空行で走査が止まると「有効なエントリ 0 件」と誤判定してキーを落とす"""
    text = """services:
  dev:
    env_file:
      - ${DEVBASE_ROOT}/.env

      - env
    image: x
"""
    after, _ = cm.disable(text)

    assert '    env_file:\n' in after
    assert f'{cm.DISABLED_MARK}env_file:' not in after


def test_blank_lines_round_trip():
    disabled, _ = cm.disable(BLANK_IN_LIST)
    assert cm.enable(disabled)[0] == BLANK_IN_LIST


def test_blank_line_does_not_leak_into_the_next_block():
    text = """services:
  dev:
    env_file:
      - .env

  worker:
    image: x
"""
    after, touched = cm.disable(text)

    assert touched == ['.env']
    assert '  worker:\n' in after
    assert cm.enable(after)[0] == text


# ---------------------------------------------------------------------------
# 対応していない記法
# ---------------------------------------------------------------------------

INLINE = """services:
  dev:
    env_file: [ "${DEVBASE_ROOT}/.env", .env ]
  worker:
    env_file: .env
  batch:
    env_file:
      - ${DEVBASE_ROOT}/.env
      - env
"""


def test_inline_notation_is_reported():
    found = cm.unsupported_env_file_lines(INLINE)

    assert [number for number, _ in found] == [3, 5]
    assert found[0][1] == 'env_file: [ "${DEVBASE_ROOT}/.env", .env ]'
    assert found[1][1] == 'env_file: .env'


def test_block_sequence_alone_reports_nothing():
    assert cm.unsupported_env_file_lines(BASIC) == []


def test_env_file_key_with_a_trailing_comment_is_not_reported():
    text = """services:
  dev:
    env_file:   # 共通設定
      - env
"""
    assert cm.unsupported_env_file_lines(text) == []


def test_warn_unsupported_env_file_names_the_file_and_line(caplog):
    with caplog.at_level(logging.WARNING, logger='devbase.env.compose_migrate'):
        cm.warn_unsupported_env_file(INLINE, Path('projects/web/compose.yml'))

    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2
    assert 'projects/web/compose.yml:3' in messages[0]
    assert 'env_file: [ "${DEVBASE_ROOT}/.env", .env ]' in messages[0]
    assert 'projects/web/compose.yml:5' in messages[1]


def test_inline_notation_does_not_break_the_block_sequence():
    """対象外の記法が混ざっていても、扱える書き方は従来どおり処理する"""
    after, touched = cm.disable(INLINE)

    assert touched == ['${DEVBASE_ROOT}/.env']
    assert '      - env\n' in after
    assert cm.enable(after)[0] == INLINE


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
