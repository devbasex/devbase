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
| duplication | consolidate_duplication | major | codex | 取り消し | 1 |

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
| magic_value | introduce_named_constant | minor | codex | 取り消し | 1 |

**なぜ**: The archive identity rule is embedded as literal filename checks for 'full.tar.zst', 'incr-', and '.tar.zst' inside the scan loop. The same archive naming convention is also used by create/restore helpers, so changing the convention would require coordinated edits in several places.

**手順**: 1. Introduce named constants or a private predicate for snapshot archive names near the existing snapshot constants
2. Replace the inline filename condition in last_snapshot_time with that predicate
3. Reuse the same constants in create/restore command construction only where it does not obscure the shell command
4. Run tests/snapshot/test_auto_snapshot.py and then the full test command

## ラウンド 2（実装 kiro / レビュー codex / gemini）

### R2-001 — `lib/devbase/volume/compose.py#generate_scaled_compose`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_parameter_list | introduce_parameter_object | major | codex / gemini | 採用 | 2 |

**なぜ**: scale 以外に compose_file, dev_service_name, secret_env_names, global_env_names, project_env_names, dev_environment が並び、機密名の全体/由来別セットと dev 環境の組が呼び出しから内部ヘルパーまで渡り回っている

**手順**: 1. 既存の公開シグネチャは残したまま、内部用の dataclass で compose path, dev service name, secret names, dev environment をまとめる
2. generate_scaled_compose の冒頭で引数から parameter object を組み立てる
3. _SecretNames 生成、_services_receiving_secrets 呼び出し、_build_scaled_services 呼び出しを parameter object 経由に置き換える
4. tests/volume の generate_scaled_compose 経路を実行して公開インタフェースの互換性を確認する

### R2-002 — `lib/devbase/volume/manager.py#get_volume_for_index`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| duplication | consolidate_duplication | minor | codex / gemini | 採用 | 2 |

**なぜ**: VolumeManager.get_volume_for_index と module-level get_volume_for_index が同じ SHARED_VOLUME_PREFIX 連結規則を別々に持ち、work volume 側だけは module-level helper が VolumeManager へ委譲する形になっていて同じ概念の表現が揺れている

**手順**: 1. module-level get_volume_for_index を VolumeManager().get_volume_for_index(index) へ委譲し、work volume helper と同じ構造に揃える
2. project_name 引数は後方互換のため残し、挙動を変えない
3. tests/volume/test_manager.py に shared volume helper と class method が同じ名前規則を返す現状固定テストを追加する
4. tests/volume/test_manager.py を実行し、必要なら volume/compose 経路も実行する

### R2-003 — `lib/devbase/snapshot/manager.py#SnapshotManager._run_docker_tar`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| primitive_obsession | introduce_value_object | major | codex | 採用 | 2 |

**なぜ**: mode は 'backup' / 'restore' の文字列で渡され、mount の読み書き方向を分岐させる制約が _run_docker_tar 内の三項演算子に閉じ込められているため、未検証の文字列でも restore 扱いになる

**手順**: 1. backup/restore の mount 仕様を表す小さな値オブジェクトを追加し、mode 文字列から生成する境界を 1 箇所に寄せる
2. _run_docker_tar は値オブジェクトから volume_mount と backup_mount を受け取る形にする
3. _create_full, _create_incremental, _restore_full_archive, _restore_incremental_archive の呼び出しは既存の公開挙動を保つ最小変更に留める
4. tests/snapshot の restore 経路に加え、backup 側のコマンド組み立てを固定するテストを先に追加してから実装する

### R2-004 — `lib/devbase/volume/manager.py#VolumeManager.ensure_volumes`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| duplication | consolidate_duplication | minor | gemini | 取り消し | 2 |

**なぜ**: 共有ホームボリュームとワークボリュームの存在確認、ログ出力、作成エラーハンドリングの処理が重複している

**手順**: 1. ボリュームの存在確認・ログ出力・作成を行う _ensure_volume メソッドを抽出する
2. ensure_volumes 内の各ボリューム確保処理を、抽出したメソッドの呼び出しに置き換える

### R2-005 — `lib/devbase/snapshot/manager.py#SnapshotManager._ensure_snapshot_image`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | minor | gemini | 取り消し | 2 |

**なぜ**: イメージの存在確認と、無かった場合のビルド処理（複数コマンドのフォールバック・エラー解析）が混在して見通しが悪い

**手順**: 1. 複数コマンドのフォールバックによるビルド処理を _build_snapshot_image メソッドとして抽出する
2. _ensure_snapshot_image ではイメージが存在しない場合に抽出したメソッドを呼び出すようにする

## ラウンド 3（実装 claude / レビュー codex / kiro）

### R3-001 — `lib/devbase/volume/compose.py#_replace_volumes_for_instance`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | codex | レビュー中 | 1 |

**なぜ**: 1つの関数でマウント対象の判定、deprecated mount の除外、文字列形式と dict 形式それぞれの置換、欠落 mount の補完まで扱っており、volume 仕様の追加時に分岐全体を読み直す必要がある

**手順**: 1. 1件の volume entry を置換する _replace_volume_entry_for_instance を抽出し、置換後 entry と replaced target を返す
2. _replace_volumes_for_instance は反復、deprecated 除外、欠落 mount の補完だけを担う形に縮小する
3. 既存の compose 生成テストで string/dict volume と欠落 mount 補完の振る舞いが変わらないことを確認する

### R3-002 — `lib/devbase/volume/compose.py#_drop_missing_env_files`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | codex | レビュー中 | 1 |

**なぜ**: env_file の正規化、参照先解決、欠落した機密参照かどうかの判定、service への反映が同じ関数に同居しており、欠落参照の扱いを読むために副作用部分まで追う必要がある

**手順**: 1. env_file を list へ正規化する _env_file_entries を抽出する
2. 欠落した機密 env_file 参照かを判定する _is_missing_secret_env_file を抽出する
3. _drop_missing_env_files は kept の組み立てと service への反映だけにする
4. tests/volume/test_compose_secret_env.py の既存ケースで欠落機密、欠落非機密、未解決変数、全削除時の env_file 削除を確認する

### R3-003 — `lib/devbase/volume/compose.py#_replace_volumes_for_instance`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| primitive_obsession | introduce_value_object | major | gemini | レビュー中 | 2 |

**なぜ**: Dockerのvolume定義が文字列(source:target)と辞書(source, target)の2種類で表現されており、各関数で型チェック(isinstance)を伴うパースが重複して散在している

**手順**: 1. `VolumeMount`クラス（source, targetなどの属性を持つ）を作成する
2. str/dict から `VolumeMount` を生成するパース処理を実装する
3. `VolumeMount` から元の型（str/dict）にシリアライズする処理を実装する
4. `_replace_volumes_for_instance` と `_volume_target` の処理を `VolumeMount` を経由するよう置き換える

### R3-004 — `lib/devbase/snapshot/manager.py#SnapshotManager._load_snap_meta`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| magic_value | introduce_named_constant | minor | codex | レビュー中 | 1 |

**なぜ**: 個別スナップショットメタデータのファイル名 'meta.yml' が _load_snap_meta、_save_snap_meta、last_snapshot_time の除外説明とテストデータに散在し、global metadata の METADATA_FILE と違って意味が名前で表現されていない

**手順**: 1. SNAPSHOT_META_FILE = 'meta.yml' を module 定数として追加する
2. _load_snap_meta と _save_snap_meta の Path 組み立てを定数参照へ置き換える
3. last_snapshot_time の除外コメントで定数名を使うか、コメントは挙動説明に留めて直接値の重複を避ける
4. 必要なら tests/snapshot/test_auto_snapshot.py のノイズファイル作成も定数 import に寄せ、既存テストを実行する

### R3-005 — `lib/devbase/volume/manager.py#VolumeManager._volume_exists`

| スメル | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| swallowed_exception | propagate_exception | minor | kiro | 取り消し | 2 |

**なぜ**: `except Exception` で docker CLI 呼び出し中の全ての例外 (FileNotFoundError: docker バイナリ不在、PermissionError 等) を捕まえ、警告ログのみで False にすり替えている。呼び出し元の ensure_volumes は False を『ボリュームが存在しない』として扱い _create_volume を呼ぶため、docker が使えない環境でも一旦 create を試みてから別のエラーとして失敗する遠回りな経路になり、元の例外の種類・原因が失われる。

**手順**: 1. subprocess.run の呼び出しで発生しうる例外を洗い出す (FileNotFoundError: docker 未インストール、OSError 系)
2. `except Exception` を `except (OSError, subprocess.SubprocessError)` など想定範囲へ絞る
3. 想定外の例外はそのまま伝播させる (catch しない)
4. 既存の returncode != 0 判定 (通常の『存在しない』経路) はそのまま残す
