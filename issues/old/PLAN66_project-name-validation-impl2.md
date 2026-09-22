# PLAN66 実装 2 本目: スナップショットの名前の検証を `utils/names` の述語へ寄せる

## 関連リンク

- 課題: devbasex/devbase#203（この Pull Request で閉じる）
- 要求と受け入れ条件: `issues/PLAN66_project-name-validation.md`
- 設計: `issues/PLAN66_project-name-validation-design.md`（設計 PR #230。決定 5・6）
- 実装 1 本目: #233（知らせ。F1・F2。`release/v3.7.0` へマージ済み）
- release PR: #212（base は `release/v3.7.0`）
- この計画が扱うのは設計の「実装の分け方」の **2 本目（スナップショットの寄せ。F3）だけ**である

## モード

`standard`（要求の文書の判定のまま。受け付ける名前が狭まるのは末尾の改行 1 ケースだけで、利用者が承認済み）。

## 目的と非目的

達成したい状態:

- スナップショット名の文字の規則を `utils/names.is_single_segment_name` の 1 か所に置き、`_VALID_NAME_RE` の二重持ちをやめる
- 共有した述語が将来広がったとき、スナップショット側のテストで気づける

やらないこと:

- `SnapshotError` の型と文言（「英数字・ハイフン・アンダースコア・ドットのみ使用可能、先頭は英数字」）の変更。
  `NAME_FORM_HINT` へ寄せる案は採らない（決定 5 の採らなかった案）
- 逆向きの寄せ（`utils/names` が `snapshot` の定数を使う）
- `_safe_snap_dir` の封じ込め（`resolve()` + `startswith`）の変更
- 末尾の改行以外で受け付ける名前を狭める変更
- 確定仕様「運用」の 1 つ目の箇条書きと、CHANGELOG の Added の行（1 本目の範囲。書き換えない）

## 受け入れ条件

要求の文書の番号をそのまま使う。この Pull Request が満たすのは 10〜12・13 の 2 つ目と 3 つ目・14 の Changed・17 である。

- [ ] 10: `_validate_name("abc\n")` が `SnapshotError`（`tests/snapshot/test_manager_name.py`）
- [ ] 11: 受理 4 件（`ok-name`・`a.b`・`A_b`・`0abc`）が例外にならず、拒否 8 件（`_foo`・`.x`・`-x`・空・`..`・`café`・`a/b`・`abc\n`）が
  `SnapshotError` で文言 `無効なスナップショット名` を含む（同上）
- [ ] 12: `grep -n "_VALID_NAME_RE" lib/devbase/snapshot/manager.py` が 0 件
- [ ] 13 の 2 つ目・3 つ目: `docs/specifications/cli-argument-resolution.md` の「運用」の 2 つ目の箇条書きが、寄せていない規則は
  `env/bundle.py` と `env/secret_store.py` の 2 つであること、スナップショットの名前が `utils/names` の述語を共有すること
  （共有してよい理由と、広がらないことを固定するテストの在り処）を書く
- [ ] 14 の Changed: `CHANGELOG.md` の `[Unreleased]` に Changed（スナップショットの名前が末尾の改行を受け付けなくなる）を足す
- [ ] 17: `uv run --locked pytest tests/ -q` が exit=0

## 修正対象

- `lib/devbase/snapshot/manager.py`
- `tests/snapshot/test_manager_name.py`（新設）
- `docs/specifications/cli-argument-resolution.md`（「運用」の 2 つ目の箇条書きだけ）
- `CHANGELOG.md`（`[Unreleased]` に Changed を足すだけ）

## タスク分解

### Task 1: 受理と拒否を固定し、述語へ寄せる（F3）

- **対象ファイル:** `tests/snapshot/test_manager_name.py`・`lib/devbase/snapshot/manager.py`
- **変更内容:** `pytest.mark.parametrize` で受理 4 件・拒否 8 件を固定するテストを先に書く（`abc\n` だけが今の実装で落ちる）。
  `_VALID_NAME_RE` を消し、`_validate_name` の判定を `not is_single_segment_name(name)` にする（空は述語が弾くため `not name` のガードは消す）。
  例外の型・文言・`_safe_snap_dir` は変えない。`re` は他の正規表現が使うため import を残す
- **満たす受け入れ条件:** 10・11・12
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 2: 確定仕様と CHANGELOG

- **対象ファイル:** `docs/specifications/cli-argument-resolution.md`・`CHANGELOG.md`
- **変更内容:** 「運用」の 2 つ目を設計の「確定仕様の書き換え」の表の 2 行目どおりに書き換える。CHANGELOG の `[Unreleased]` に `### Changed` を足す
- **満たす受け入れ条件:** 13 の 2 つ目・3 つ目・14 の Changed
- **進め方:** 文書のためテスト駆動を適用しない

## 影響範囲

- `SnapshotManager` の名前を受ける入口（`create`・`restore`・`rename`・`delete` など `_safe_snap_dir` を通る経路）。
  末尾に改行を持つ名前だけが新たに弾かれる

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 受け付ける名前が末尾の改行以外でも変わる | 受理 4 件・拒否 8 件のテストで固定し、文字集合が同じ（`[A-Za-z0-9][A-Za-z0-9._-]*`）ことを差分で確かめる |
| 触る範囲は 1 関数で、テストを新設する | 実装の後の構造改善で足りる |

## 切り戻し手順

データ・スキーマを持たない。この Pull Request の revert で完全に戻る。

## 完了の定義

- [ ] 受け入れ条件 10〜12 がテストと grep で確かめられ、17 が exit=0
- [ ] Draft の Pull Request の本文に Test plan と実行結果を載せる
