"""compose_migrate.py: 構成ファイルの機密参照を外す / 戻す"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

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
    after, touched = cm.disable(text)

    assert '      # 共通設定\n' in after
    assert '      - env   # プロジェクト設定\n' in after
    # コメント行で走査が止まると、その後ろの機密参照が無効化されないまま残る。
    # 「往復で元に戻る」だけでは何も書き換えられなかった場合と区別できない。
    assert touched == ['${DEVBASE_ROOT}/.env']
    assert f'{cm.DISABLED_MARK}- ${{DEVBASE_ROOT}}/.env' in after
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
# コメント行を含むリスト
# ---------------------------------------------------------------------------

COMMENT_IN_LIST = """services:
  dev:
    env_file:
      - env
      # 機密はここから
      - ${DEVBASE_ROOT}/.env
      - .env
    image: x
"""


def test_comment_lines_inside_the_list_do_not_stop_the_scan():
    """コメント行で打ち切ると、その後ろの機密参照が有効なまま残ってしまう"""
    after, touched = cm.disable(COMMENT_IN_LIST)

    assert touched == ['${DEVBASE_ROOT}/.env', '.env']
    assert f'{cm.DISABLED_MARK}- ${{DEVBASE_ROOT}}/.env' in after
    assert f'{cm.DISABLED_MARK}- .env' in after
    assert '      # 機密はここから\n' in after
    assert '      - env\n' in after


def test_comment_lines_round_trip():
    disabled, _ = cm.disable(COMMENT_IN_LIST)
    restored, touched = cm.enable(disabled)

    assert restored == COMMENT_IN_LIST
    assert len(touched) == 2


def test_comment_lines_do_not_make_the_key_look_used():
    """コメント行の後ろに有効なエントリが残るなら `env_file:` は落とせない"""
    text = """services:
  dev:
    env_file:
      - ${DEVBASE_ROOT}/.env
      # プロジェクト設定
      - env
    image: x
"""
    after, _ = cm.disable(text)

    assert '    env_file:\n' in after
    assert f'{cm.DISABLED_MARK}env_file:' not in after


def test_comments_and_blank_lines_mixed_do_not_stop_the_scan():
    text = """services:
  dev:
    env_file:

      # 共通設定
      - ${DEVBASE_ROOT}/.env

      # プロジェクト設定
      - .env
    image: x
"""
    after, touched = cm.disable(text)

    assert touched == ['${DEVBASE_ROOT}/.env', '.env']
    # 有効なエントリが 1 つも残らないのでキー行も無効化される
    assert f'{cm.DISABLED_MARK}env_file:' in after
    assert cm.enable(after)[0] == text


def test_comment_does_not_leak_into_the_next_block():
    """ブロックの外のコメントを読み飛ばしても、次のサービスは壊さない"""
    text = """services:
  dev:
    env_file:
      - .env

  # ここから worker
  worker:
    env_file:
      - ${DEVBASE_ROOT}/.env
"""
    after, touched = cm.disable(text)

    assert touched == ['.env', '${DEVBASE_ROOT}/.env']
    assert '  # ここから worker\n' in after
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


def test_secret_inline_lines_are_separated_from_harmless_ones():
    """機密を指すインライン記法だけが「移行を止める理由」になる"""
    text = """services:
  dev:
    env_file: config/app.env
  worker:
    env_file: [ "${DEVBASE_ROOT}/.env", config/app.env ]
  batch:
    env_file: .env   # プロジェクト設定
"""
    # 対応していない記法としては 3 行すべてが挙がる
    assert [n for n, _ in cm.unsupported_env_file_lines(text)] == [3, 5, 7]
    # そのうち機密を指しているのは worker と batch だけ
    assert [n for n, _ in cm.secret_unsupported_env_file_lines(text)] == [5, 7]


def test_secret_inline_lines_respect_the_requested_targets():
    """プロジェクトだけを暗号化するなら、共通設定のインライン記法は止めない"""
    text = """services:
  dev:
    env_file: [ "${DEVBASE_ROOT}/.env" ]
"""
    assert cm.secret_unsupported_env_file_lines(text, {cm.TARGET_PROJECT}) == []
    assert len(cm.secret_unsupported_env_file_lines(text, {cm.TARGET_GLOBAL})) == 1


def test_disabled_inline_lines_are_not_reported_again():
    """コメントアウト済みの行を再び「止める理由」に数えない"""
    text = f"""services:
  dev:
    {cm.DISABLED_MARK}env_file: .env
"""
    assert cm.secret_unsupported_env_file_lines(text) == []


# ---------------------------------------------------------------------------
# 機密参照を持つサービスの列挙 (生成側が機密を渡す先を決めるのに使う)
# ---------------------------------------------------------------------------

MULTI_SERVICE = """services:

  dev:
    image: alpine
    env_file:
      - ${DEVBASE_ROOT}/.env
      - env
    volumes:
      - x:/work
  db:
    image: mysql
    env_file:
      - .env
  cache:
    image: redis
    env_file:
      - config/app.env
  worker:
    env_file: [ ".env" ]
volumes:
  x: {}
networks:
  net:
    driver: bridge
"""


def test_services_with_secret_env_file_lists_only_the_referencing_ones():
    """参照していたサービスと、その参照種別 (共通 / プロジェクト) を返す"""
    assert cm.services_with_secret_env_file(MULTI_SERVICE) == {
        'dev': {cm.TARGET_GLOBAL},
        'db': {cm.TARGET_PROJECT},
        'worker': {cm.TARGET_PROJECT},
    }


def test_services_with_secret_env_file_reports_both_targets():
    """両方を参照するサービスは両方の種別を持つ"""
    text = """services:
  dev:
    env_file:
      - ${DEVBASE_ROOT}/.env
      - env
      - .env
"""
    assert cm.services_with_secret_env_file(text) == {
        'dev': {cm.TARGET_GLOBAL, cm.TARGET_PROJECT}}


def test_services_with_secret_env_file_sees_disabled_entries():
    """移行後は参照がコメントアウトされる。種別まで含めて同じ結果を返す必要がある"""
    disabled, _ = cm.disable(MULTI_SERVICE)

    assert cm.services_with_secret_env_file(disabled) == {
        'dev': {cm.TARGET_GLOBAL},
        'db': {cm.TARGET_PROJECT},
        'worker': {cm.TARGET_PROJECT},
    }


def test_services_with_secret_env_file_ignores_other_sections():
    """`volumes:` などの `- .env` らしき行をサービス扱いしない"""
    text = """services:
  dev:
    volumes:
      - ./.env:/etc/x
volumes:
  data: {}
"""
    assert cm.services_with_secret_env_file(text) == {}


def test_services_with_secret_env_file_sees_past_comment_lines():
    """コメント行で走査が止まると、その後ろの参照を持つサービスを取りこぼす"""
    text = """services:
  dev:
    env_file:
      - env
      # 機密はここから
      - ${DEVBASE_ROOT}/.env

      # プロジェクト設定
      - .env
  # ここから db
  db:
    env_file:
      # プロジェクト設定
      - .env
"""
    assert cm.services_with_secret_env_file(text) == {
        'dev': {cm.TARGET_GLOBAL, cm.TARGET_PROJECT},
        'db': {cm.TARGET_PROJECT},
    }


def test_services_with_secret_env_file_sees_past_comments_after_disable():
    """移行後も同じ結果でなければ、機密が渡らないまま起動して失敗する"""
    text = """services:
  db:
    env_file:
      # プロジェクト設定
      - .env
      - env
"""
    disabled, touched = cm.disable(text)

    assert touched == ['.env']
    assert cm.services_with_secret_env_file(disabled) == {
        'db': {cm.TARGET_PROJECT}}


def test_services_with_secret_env_file_respects_targets():
    assert cm.services_with_secret_env_file(
        MULTI_SERVICE, {cm.TARGET_GLOBAL}) == {'dev': {cm.TARGET_GLOBAL}}
    assert cm.services_with_secret_env_file(
        MULTI_SERVICE, {cm.TARGET_PROJECT}) == {
            'db': {cm.TARGET_PROJECT}, 'worker': {cm.TARGET_PROJECT}}


# ---------------------------------------------------------------------------
# long syntax (`- path: .env`)
# ---------------------------------------------------------------------------

LONG_SYNTAX = """services:
  dev:
    env_file:
      - path: ${DEVBASE_ROOT}/.env
      - path: env
      - .env
"""


def test_single_line_long_syntax_is_disabled_and_restored():
    """1 行で閉じている long syntax は通常のエントリと同じように扱える"""
    after, touched = cm.disable(LONG_SYNTAX)

    assert touched == ['${DEVBASE_ROOT}/.env', '.env']
    assert f'{cm.DISABLED_MARK}- path: ${{DEVBASE_ROOT}}/.env' in after
    # 機密と無関係な long syntax は触らない
    assert '      - path: env\n' in after
    assert cm.enable(after)[0] == LONG_SYNTAX


def test_single_line_long_syntax_is_not_reported_as_unsupported():
    assert cm.unsupported_env_file_lines(LONG_SYNTAX) == []


def test_quoted_long_syntax_is_recognised():
    text = """services:
  dev:
    env_file:
      - "path": ".env"   # プロジェクト設定
      - env
"""
    after, touched = cm.disable(text)

    assert touched == ['.env']
    assert cm.enable(after)[0] == text


MULTI_LINE_LONG_SYNTAX = """services:
  dev:
    env_file:
      - path: .env
        required: false
      - env
"""


def test_multi_line_long_syntax_is_reported_and_blocks_the_migration():
    """`required: false` が続く形は行単位で無効化できない (契約 2.)"""
    assert cm.unsupported_env_file_lines(MULTI_LINE_LONG_SYNTAX) == [
        (4, '- path: .env')]
    assert [n for n, _ in
            cm.secret_unsupported_env_file_lines(MULTI_LINE_LONG_SYNTAX)] == [4]


def test_multi_line_long_syntax_is_left_untouched():
    """行だけ落とすと `required: false` が宙に浮いて YAML が壊れる"""
    after, touched = cm.disable(MULTI_LINE_LONG_SYNTAX)

    assert touched == []
    assert after == MULTI_LINE_LONG_SYNTAX


def test_entries_after_a_multi_line_entry_are_still_scanned():
    """続きの行で走査を打ち切ると、後ろの機密参照が有効なまま残る"""
    text = """services:
  dev:
    env_file:
      - path: config/app.env
        required: false
      - ${DEVBASE_ROOT}/.env
"""
    after, touched = cm.disable(text)

    assert touched == ['${DEVBASE_ROOT}/.env']
    assert cm.enable(after)[0] == text
    # 機密と無関係な long syntax は警告だけで、移行は止めない
    assert [n for n, _ in cm.unsupported_env_file_lines(text)] == [4]
    assert cm.secret_unsupported_env_file_lines(text) == []


def test_multi_line_entry_without_a_path_on_the_dash_line_is_still_seen():
    """`-` の行に参照が現れない書き方でも機密を見落とさない"""
    text = """services:
  dev:
    env_file:
      -
        path: .env
        required: false
"""
    assert len(cm.secret_unsupported_env_file_lines(text)) == 1
    assert cm.disable(text)[0] == text


def test_flow_mapping_entry_blocks_the_migration():
    """1 行に複数の指定が同居するフロー記法は書き換えの対象外 (契約 2.)"""
    text = """services:
  dev:
    env_file:
      - { path: .env, required: false }
"""
    assert cm.unsupported_env_file_lines(text) == [
        (4, '- { path: .env, required: false }')]
    assert len(cm.secret_unsupported_env_file_lines(text)) == 1
    assert cm.disable(text)[0] == text


def test_env_file_that_is_not_a_sequence_is_not_passed_silently():
    """シーケンスでない値 (Compose としては不正) も黙って通さない"""
    text = """services:
  dev:
    env_file:
      path: .env
      required: false
"""
    assert [n for n, _ in cm.unsupported_env_file_lines(text)] == [4]
    assert len(cm.secret_unsupported_env_file_lines(text)) == 1
    assert cm.disable(text)[0] == text


def test_inline_flow_mapping_is_seen_as_a_secret_reference():
    text = """services:
  dev:
    env_file: [ { path: .env } ]
"""
    assert len(cm.secret_unsupported_env_file_lines(text)) == 1


def test_services_with_secret_env_file_reads_long_syntax():
    text = """services:
  db:
    env_file:
      - path: .env
  cache:
    env_file:
      - path: ${DEVBASE_ROOT}/.env
        required: false
  none:
    env_file:
      - path: config/app.env
"""
    assert cm.services_with_secret_env_file(text) == {
        'db': {cm.TARGET_PROJECT},
        'cache': {cm.TARGET_GLOBAL},
    }


# ---------------------------------------------------------------------------
# クォートされたサービス名
# ---------------------------------------------------------------------------

QUOTED_SERVICES = """services:
  "db":
    image: mysql
    env_file:
      - .env
  'cache':
    image: redis
    env_file:
      - ${DEVBASE_ROOT}/.env
"""


def test_quoted_service_names_match_the_parsed_ones():
    """PyYAML は `"db":` を `db` と読む。引用符込みで記録すると照合できない"""
    parsed = set(yaml.safe_load(QUOTED_SERVICES)['services'])
    found = cm.services_with_secret_env_file(QUOTED_SERVICES)

    assert set(found) <= parsed
    assert found == {
        'db': {cm.TARGET_PROJECT},
        'cache': {cm.TARGET_GLOBAL},
    }


def test_quoted_service_names_survive_the_migration():
    """移行でコメントアウトされたあとも同じサービス名で拾えること"""
    disabled, _ = cm.disable(QUOTED_SERVICES)

    assert cm.services_with_secret_env_file(disabled) == {
        'db': {cm.TARGET_PROJECT},
        'cache': {cm.TARGET_GLOBAL},
    }


# ---------------------------------------------------------------------------
# 改行コード (CRLF / 混在)
# ---------------------------------------------------------------------------

def test_crlf_round_trip_is_byte_identical():
    """行末を LF へ潰すと、往復しても元の compose.yml に戻らない"""
    text = BASIC.replace('\n', '\r\n')

    disabled, touched = cm.disable(text)

    assert touched == ['${DEVBASE_ROOT}/.env', '.env']
    assert f'{cm.DISABLED_MARK}- .env\r\n' in disabled
    # LF 単独の行が紛れ込んでいない
    assert '\n' not in disabled.replace('\r\n', '')
    assert cm.enable(disabled)[0] == text


def test_crlf_key_line_round_trip():
    """キー行ごと無効化する場合も行末を保つ"""
    text = ('services:\r\n  dev:\r\n    env_file:\r\n'
            '      - ${DEVBASE_ROOT}/.env\r\n    image: x\r\n')

    disabled, _ = cm.disable(text)

    assert f'{cm.DISABLED_MARK}env_file:\r\n' in disabled
    assert cm.enable(disabled)[0] == text


def test_mixed_line_endings_are_preserved():
    """混在していても、書き換えた行の行末だけをそのまま引き継ぐ"""
    text = ('services:\n  dev:\r\n    env_file:\n'
            '      - ${DEVBASE_ROOT}/.env\r\n      - .env\n      - env\r\n')

    disabled, touched = cm.disable(text)

    assert touched == ['${DEVBASE_ROOT}/.env', '.env']
    assert f'{cm.DISABLED_MARK}- ${{DEVBASE_ROOT}}/.env\r\n' in disabled
    assert f'{cm.DISABLED_MARK}- .env\n' in disabled
    assert cm.enable(disabled)[0] == text


def test_a_file_without_a_trailing_newline_round_trips():
    text = 'services:\n  dev:\n    env_file:\n      - .env'

    disabled, touched = cm.disable(text)

    assert touched == ['.env']
    assert not disabled.endswith('\n')
    assert cm.enable(disabled)[0] == text


# ---------------------------------------------------------------------------
# 末尾スペース / 行末コメントを伴うエントリ
# ---------------------------------------------------------------------------

def test_entries_with_trailing_comments_and_spaces_are_disabled():
    """`- ${DEVBASE_ROOT}/.env   # 共通設定` のような行も取りこぼさない"""
    text = """services:
  dev:
    env_file:
      - ${DEVBASE_ROOT}/.env   # 共通設定
      - ".env"    # プロジェクト設定
      - env
"""
    after, touched = cm.disable(text)

    assert touched == ['${DEVBASE_ROOT}/.env', '.env']
    assert f'{cm.DISABLED_MARK}- ${{DEVBASE_ROOT}}/.env   # 共通設定' in after
    # 行末コメントごと元の姿へ戻る
    assert cm.enable(after)[0] == text


def test_services_with_secret_env_file_handles_trailing_comments():
    text = """services:
  db:
    env_file:
      - .env   # プロジェクト設定
"""
    assert cm.services_with_secret_env_file(text) == {
        'db': {cm.TARGET_PROJECT}}


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
