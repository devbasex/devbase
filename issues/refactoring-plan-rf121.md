# 改修計画 — devbasex/devbase #121

`/ndf:cross-refactoring` が提案し、適用した改善項目の記録である。
理由と手順は提案の時点でしか残らないため、公開の直前に書き出している。

- 対象範囲: lib/devbase/volume, lib/devbase/snapshot, tests/volume, tests/snapshot
- 着手前のテスト: uv run --group dev python -m pytest -q

## ラウンド 1（実装 codex / レビュー gemini / kiro）

### R1-001 — `lib/devbase/snapshot/manager.py#SnapshotManager.restore`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | split_into_pipeline | major | codex / gemini | レビュー中 | 2 |

**なぜ**: restore currently performs point validation, snapshot path/archive validation, pre-restore backup, full restore command construction, incremental archive selection, incremental restore command construction, and completion logging in one method. These are sequential stages with clear inputs and outputs, and the restore path has little direct test coverage.

**手順**: 1. Add characterization tests that monkeypatch create and _run_docker_tar to capture the full restore command sequence, point-limited incremental selection, and invalid point/archive errors
2. Extract snapshot validation into a helper returning snap_dir and full_archive
3. Extract pre-restore backup into a helper that preserves the current warning-and-continue behavior
4. Extract incremental archive selection into a helper returning the files to apply for a given point
5. Keep restore as the pipeline coordinator and run the new snapshot tests, then the full test command

### R1-002 — `lib/devbase/volume/compose.py#_mask_secret_environment / _apply_dev_environment`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| duplication | consolidate_duplication | major | codex | レビュー中 | 1 |

**なぜ**: Both functions branch on the same Compose environment shapes (None, dict, list, unsupported), parse list entries with split('=', 1), preserve existing non-target keys, and append missing keys. The intended values differ, but the environment-shape traversal changes for the same reason whenever Compose environment handling is extended.

**手順**: 1. Add characterization cases for unsupported environment forms if existing tests do not assert the warning/fallback behavior directly
2. Extract a small helper that reads a service environment into ordered entries while preserving the original representation kind
3. Rebuild _mask_secret_environment through the helper with secret keys mapped to value-less references
4. Rebuild _apply_dev_environment through the helper with extra keys mapped to KEY=VALUE or dict entries
5. Run tests/volume/test_compose_secret_env.py and tests/volume/test_compose_dev_environment.py, then the full test command

### R1-003 — `lib/devbase/volume/manager.py#VolumeManager.get_ai_volume_for_index`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| dead_code | remove_dead_code | minor | kiro | レビュー中 | 1 |

**なぜ**: VolumeManager.get_ai_volume_for_index（メソッド）は grep で `.get_ai_volume_for_index(` に一致する呼び出しが存在せず、ensure_volumes 内部からも呼ばれていない。実際に使われているのは同名の module-level 関数（compose.py から import）であり、メソッド版は宣言されただけで到達しない。

**手順**: 1. grep で `.get_ai_volume_for_index(` の呼び出しが VolumeManager インスタンス経由で無いことを最終確認する
2. VolumeManager.get_ai_volume_for_index メソッドを削除する
3. `uv run --group dev python -m pytest -q` を実行し、全体テストが変化しないことを確認する

### R1-004 — `lib/devbase/volume/manager.py#get_work_volume_for_index`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| duplication | consolidate_duplication | minor | kiro | レビュー中 | 2 |

**なぜ**: module-level get_work_volume_for_index と VolumeManager.get_work_volume_for_index は同じ `f"{WORK_VOLUME_PREFIX}{index}"` を独立に持っている。同じ命名規則（WORK_VOLUME_PREFIX の組み立て）に由来し、変わるときは必ず一緒に変わる（プレフィックスの命名を変える場合、両方直す必要がある）ため、code-smells.md の『共通化してよい重複』に当たる。VolumeManager.ensure_volumes は self.get_work_volume_for_index を内部で使っているため、モジュール関数側を残しつつクラスメソッドへ委譲させる（module-level 関数が公開 API・呼び出し元は compose.py と外部プロジェクトの互換性のため維持する）。

**手順**: 1. module-level get_work_volume_for_index の本体を `return VolumeManager().get_work_volume_for_index(index)` に置き換える（docstring は現状維持）
2. compose.py 側の呼び出し (`from .manager import get_work_volume_for_index` の呼び出し 2 箇所) は署名が変わらないため書き換え不要であることを確認する
3. `uv run --group dev python -m pytest -q` を実行し、全体テストが変化しないことを確認する

### R1-005 — `lib/devbase/snapshot/manager.py#SnapshotManager.last_snapshot_time`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| magic_value | introduce_named_constant | minor | codex | レビュー中 | 1 |

**なぜ**: The archive identity rule is embedded as literal filename checks for 'full.tar.zst', 'incr-', and '.tar.zst' inside the scan loop. The same archive naming convention is also used by create/restore helpers, so changing the convention would require coordinated edits in several places.

**手順**: 1. Introduce named constants or a private predicate for snapshot archive names near the existing snapshot constants
2. Replace the inline filename condition in last_snapshot_time with that predicate
3. Reuse the same constants in create/restore command construction only where it does not obscure the shell command
4. Run tests/snapshot/test_auto_snapshot.py and then the full test command
