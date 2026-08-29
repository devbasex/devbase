# PLAN40: 差分スナップショットの復元が途中で失敗する不具合を直す

## 関連リンク

- 発見の経緯: `issues/PLAN39_account-group-volume-separation.md` の「検証中に見つかった別件」
- 参考: `lib/devbase/snapshot/manager.py`（`restore` / `_create_full` / `_create_incremental`）、
  `containers/snapshot/Dockerfile`、`docs/user/snapshot-guide.md`
- 一次情報:
  - [GNU tar: Incremental Dumps](https://www.gnu.org/software/tar/manual/html_node/Incremental-Dumps.html)
    — `--listed-incremental` の状態ファイルと、復元時にディレクトリの
    「そこに無いはずのもの」を扱う仕組み
  - [GNU tar: Levels of Backup / Restoring from Incremental](https://www.gnu.org/software/tar/manual/html_node/Restoring-from-Incremental.html)
    — レベル 0 から順に適用する前提と `--listed-incremental=/dev/null` の位置づけ

## モード

`standard` — 本番の振る舞い（復元）のバグ修正。公開インタフェースもデータ移行も伴わない。
ただし**失敗すると利用者のデータが中途半端な状態で残る**経路なので、現状固定テストを
先に置いてから触る。

## 目的と非目的

達成したい状態:

- 差分を 2 つ以上持つスナップショットが**最後まで復元できる**。
- 復元が失敗したとき、ボリュームが**中途半端な状態のまま放置されない**（利用者が
  次に何をすればよいか分かる）。
- 既存の（分離前を含む）スナップショットがそのまま復元できる。作り直しを強いない。

やらないこと:

- スナップショット形式そのものの作り替え（tar + zstd + listed-incremental の維持）。
- 世代管理・ローテーション・自動スナップショットの方針変更。
- PLAN39 で入れたアカウントグループ対応の設計変更（対象ボリュームの解決は現状のまま）。

## 前提

すべて現行 `main` (`e15ca84`) 上で確認済み。

- 前提 1: **差分の 2 つ目以降で復元が落ちる。** 実在するスナップショット
  `backups/20260823-114528`（差分 8 個）を使い捨てボリュームへ復元すると、
  `full` と `incr-001` は成功し、`incr-002` で失敗する。

  ```
  tar: Cannot rename './.claude/plugins/cache/claude-plugins-official/hookify/unknown/commands'
       to './.claude/plugins/cache/claude-plugins-official/skill-creator/unknown': Directory not empty
  tar: Exiting with failure status due to previous errors
  ```

- 前提 2: **PLAN39 の退行ではない。** PLAN39 は復元コマンドを
  `cd /target && ... tar -xf -` から `... tar -xf - -C /target` へ書き換えたが、
  **`main` と同じ旧コマンド形式でも同一エラーが再現する**。切り分けの手順:

  ```bash
  V=plan40_repro; SNAP=<backups/20260823-114528 のコピー>
  docker volume create $V
  # full (旧形式)
  docker run --rm -v $V:/target -v $SNAP:/backup:ro devbase-snapshot:latest bash -c \
    "cd /target && find . -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + 2>/dev/null; \
     zstd -d /backup/full.tar.zst -c | tar --listed-incremental=/dev/null -xf -"   # 成功
  # incr-001 → 成功 / incr-002 → 上記エラー
  ```

- 前提 3: 作成側と復元側のコマンドは次のとおり
  (`lib/devbase/snapshot/manager.py:211,229,452,498`)。

  | 側 | コマンド |
  |---|---|
  | フル作成 | `tar --listed-incremental=/backup/snapshot.snar -cf - -C /source . \| zstd -1 -T0 -o /backup/full.tar.zst` |
  | 差分作成 | `cp snapshot.snar snapshot.snar.bak && tar --listed-incremental=/backup/snapshot.snar -cf - -C /source . \| zstd ...` |
  | フル復元 | `<clear> zstd -d /backup/full.tar.zst -c \| tar --listed-incremental=/dev/null -xf - -C /target` |
  | 差分復元 | `zstd -d /backup/incr-NNN.tar.zst -c \| tar --listed-incremental=/dev/null -xf - -C /target` |

- 前提 4: **失敗するとボリュームが中途半端な状態で残る。**
  `restore()` は `full` を展開したあと差分を順に適用し、`_run_docker_tar` が
  `SnapshotError` を投げてそこで終わる (`manager.py:193-214`)。ロールバックは無い。
  直前に `pre-restore-<timestamp>` の自動バックアップは作られる (`同 172-178`)。

- 前提 5: tar は **GNU tar 1.35**（`containers/snapshot/Dockerfile` の
  `ubuntu:26.04` 同梱。`zstd` だけを追加インストールしている）。

- 前提 6: 影響を受けるのは**差分を 2 つ以上持つ世代**である。現存する世代:

  | 世代 | 差分数 | 対象ボリューム |
  |---|---|---|
  | `20260817-110811` | 10 | `devbase_home_ubuntu`（分離前） |
  | `20260823-114528` | 8 | `devbase_home_ubuntu`（分離前） |
  | `20260829-123605` | 1 | `devbase_home_ubuntu`, `devbase_home_default` |

  PLAN39 後の世代もローテーション上限 (`DEFAULT_MAX_INCREMENTALS = 10`) まで差分を
  積むので、放置すれば**新しい世代でも同じことが起きる**。

- 前提 7: エラーが出ているのは `~/.claude/plugins/cache/` 配下、すなわち
  **プラグインのキャッシュがディレクトリごと入れ替わる**場所である。世代間で
  ディレクトリの中身が総入れ替えになる箇所で再現しているという状況証拠がある。
  ただし**根本原因はまだ特定していない**（Task 1 で確定させる）。

## 受け入れ条件

- [ ] AC1: 差分を 2 つ以上持つ既存スナップショットが**最後の差分まで復元できる**。
      検証: `backups/20260823-114528`（差分 8）を使い捨てボリュームへ復元し、
      `incr-008` まで適用されて終了コード 0 になること。
- [ ] AC2: 復元後の中身が**期待どおり**である。検証: 復元先で
      `~/.claude/.credentials.json` と `history.jsonl` がファイルとして存在しサイズが 0 でないこと、
      `.claude/plugins` が壊れていないこと（PLAN39 の切り戻し手順の検証項目と同じ）。
- [ ] AC3: **新しく作った**スナップショット（フル + 差分 3 つ以上、途中でディレクトリの
      入れ替えを含む）が復元できる。前提 7 の状況を人工的に作って再現テストにする。
- [ ] AC4: 復元が失敗したとき、**何が起きたか・次に何をすればよいか**がエラーに出る。
      少なくとも「どの差分で失敗したか」と「`pre-restore-<name>` から戻せること」を示す。
- [ ] AC5: 旧レイアウト（`volume: devbase_home_ubuntu` のみ）と新レイアウト
      （`volumes: {ai, group}`）の**両方**で AC1 が成り立つ。
- [ ] AC6: `uv run pytest` が green で、再現ケースが**テストとして固定**されている。
      Docker を要するテストは、Docker が無い環境では skip する。

## 代替案と採否

現時点では**原因が未特定**のため、採否は Task 1 の結果で確定させる。候補は次のとおり。

| 案 | 内容 | 見込み |
|---|---|---|
| A. 復元時に `--incremental` の状態を正しく渡す | `--listed-incremental=/dev/null` をやめ、復元専用の状態ファイルを世代ごとに持ち回る | 公式手順に近づく。ただし状態ファイルは**作成側**の記録なので、復元側で何を渡すべきかは要検証 |
| B. 差分適用前に対象ディレクトリを整える | tar が rename しようとする先を空にする / 事前に消す | 症状は消えるが、tar の削除セマンティクスを人手で再実装することになり脆い |
| C. 各差分を一時ディレクトリへ展開してから同期 | `rsync --delete` 相当を自前で行う | tar の incremental 依存を切れるが、削除の判定を自前で持つ必要があり形式変更に近い |
| D. 作成側を変える（差分の作り方を見直す） | `--level=N` など | 既存スナップショットが救えないため、単独では AC1 を満たせない |
| E. tar のバージョン / 実装を変える | busybox tar 等 | listed-incremental 非対応のものが多く、退行が大きい |

**A を第一候補**とし、Task 1 で「なぜ rename が起きるのか」を確定してから決める。
どの案でも**既存スナップショットを復元できること**（AC1・AC5）を満たさない案は採らない。

## 不変条件

- 既存のスナップショットは**作り直さずに**復元できる。
- 復元は `full` → `incr-001` → … の順に適用する（順序を入れ替えない）。
- 復元前の自動バックアップ (`pre-restore-*`) は必ず作られる。
- 復元は対象ボリュームの**中身だけ**を操作し、マウントポイント自体は消さない。
- スナップショットのメタデータ由来のボリューム名は PLAN39 の検証を通す
  （devbase が作るボリュームだけを対象にする）。

## 修正対象

- `lib/devbase/snapshot/manager.py` — `restore()` と `_run_docker_tar()` の復元コマンド
- `containers/snapshot/Dockerfile` — （必要なら）tar の版や補助ツール
- `tests/snapshot/` — 再現テストと現状固定テスト
- `docs/user/snapshot-guide.md` — 復元の制約と、失敗したときの戻し方

## タスク分解

### Task 1: 根本原因の特定（調査）

- **対象:** 調査のみ。コードは変更しない
- **やること:**
  1. `incr-002` の中身を `tar -tvf` で開き、失敗している rename に対応する
     エントリ（`GNUTYPE_DUMPDIR` を含む）を確認する
  2. 世代作成時の `snapshot.snar` と、その世代の各差分の関係を確認する
  3. `--listed-incremental=/dev/null` で復元したときに tar が何を根拠に rename するのかを
     公式ドキュメントと突き合わせる
  4. **最小再現**を作る: 使い捨てボリュームで「フル → ディレクトリを総入れ替え → 差分 →
     さらに入れ替え → 差分」を作り、同じエラーが出ることを確認する
- **満たす受け入れ条件:** （調査。AC3 の再現手順の材料になる）
- **進め方:** 実機。`ndf:investigation-rules` に従い、**無いことの主張には検索結果を添える**

### Task 2: 再現テストを先に置く

- **対象ファイル:** `tests/snapshot/test_restore_incremental.py`（新規）
- **やること:** Task 1 の最小再現をテストにする。Docker を使うため
  `pytest.mark.skipif` で Docker 不在時は skip する。**この時点では失敗する**テストにする
- **満たす受け入れ条件:** AC6（の失敗側）
- **進め方:** テスト駆動

### Task 3: 復元コマンドの修正

- **対象ファイル:** `lib/devbase/snapshot/manager.py`（必要なら `containers/snapshot/Dockerfile`）
- **やること:** Task 1 で確定した原因に応じて代替案 A〜C から選び、Task 2 のテストを通す
- **満たす受け入れ条件:** AC1, AC3, AC5, AC6
- **進め方:** テスト駆動

### Task 4: 失敗時の扱いを改善する

- **対象ファイル:** `lib/devbase/snapshot/manager.py`, `docs/user/snapshot-guide.md`
- **やること:** 差分の適用に失敗したとき、**どの差分で落ちたか**と
  **`pre-restore-<name>` から戻せること**をエラーメッセージに含める。
  中途半端な状態で放置される旨も明示する
- **満たす受け入れ条件:** AC4
- **進め方:** テスト駆動（メッセージの内容を固定する）

### Task 5: 既存スナップショットでの実機確認

- **対象:** 検証のみ
- **やること:** `backups/20260823-114528`（差分 8・旧レイアウト）と、
  PLAN39 後の新レイアウト世代の両方を**使い捨てボリューム**へ復元し、AC1 / AC2 / AC5 を確認する。
  実データのボリュームは触らない（メタデータの複製に対して行う）
- **満たす受け入れ条件:** AC1, AC2, AC5
- **進め方:** 実機

## 影響範囲

- 復元経路のみ。作成・一覧・ローテーション・自動スナップショットは変えない見込み
  （Task 1 の結果で作成側も触る場合は、既存スナップショットの復元互換を AC1 で担保する）
- `containers/snapshot` イメージを変える場合は再ビルドが要る
  （`_ensure_snapshot_image` が自動ビルドするため利用者の手作業は不要）

## リスクと対処

| リスク | 対処 |
|---|---|
| 修正が既存スナップショットの復元を壊す | AC1 / AC5 を実在する世代に対して確認する。テストは使い捨てボリュームで行い実データを触らない |
| 復元の検証中に実データのボリュームを消す | 検証はメタデータを複製し**使い捨てボリューム名へ書き換えて**から行う（PLAN39 の検証で使った手順。アーカイブ本体はハードリンクで持ってくる） |
| tar の削除セマンティクスを自前で再実装して別のデータ欠損を生む | 代替案 B / C を採る場合は、削除・rename の各ケースをテストで固定してから入れる |
| 原因が tar のバグで手元では直せない | その場合は代替案 C（tar の incremental 依存を切る）へ倒す。形式変更になるため、既存スナップショットの読み出し互換を別途 AC に足す |

## 完了の定義

- [ ] AC1〜AC6 を満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run pytest` が green
- [ ] `/ndf:cross-review` で APPROVE 収束済み
- [ ] `docs/user/snapshot-guide.md` が復元の制約と失敗時の戻し方を説明している
- [ ] 実在する差分 8 個の世代を最後まで復元できることを実機で確認している
