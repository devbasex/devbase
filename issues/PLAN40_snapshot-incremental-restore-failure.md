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

- 前提 1（**Task 1 で訂正**）: **差分の適用が rename で落ちる。** 当初は
  「差分の 2 つ目以降」と書いたが、Task 1 の再現では `incr-001` から落ちる。
  条件は差分の個数ではなく、**ディレクトリが総入れ替えされたか**である。
  発生は非決定的で、同一手順 4 回中 3 回失敗した（inode の割り当て順に依存する）。

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

- 前提 6（**Task 1 で訂正**）: 当初挙げた `20260817-110811`（差分 10）と
  `20260823-114528`（差分 8）は `max_generations: 3` のローテーションで**削除済み**である
  （`find ~ -maxdepth 6 -name "*20260823*"` および `"*20260817-110811*"` で該当なし）。
  2026-08-29 時点で現存する世代:

  | 世代 | 差分数 | 対象ボリューム |
  |---|---|---|
  | `20260829-123605` | 1 | `ai: devbase_home_ubuntu`, `group: devbase_home_default` |
  | `20260829-150154` | 0 | `ai: devbase_home_ubuntu`, `group: devbase_home_kkg` |
  | `20260829-182126` | 1 | `ai: devbase_home_ubuntu`, `group: devbase_home_default` |

  差分が 1 個でも発生するため（前提 1）、現存世代も影響を受ける。実際に
  `20260829-182126/incr-001.tar.zst` は偽の rename レコードを保持している（Task 1 の結果を参照）。
  PLAN39 後の世代もローテーション上限 (`DEFAULT_MAX_INCREMENTALS = 10`) まで差分を
  積むので、放置すれば**新しい世代でも同じことが起きる**。

- 前提 7（**Task 1 で確定**）: エラーが出ているのは `~/.claude/plugins/cache/` 配下、すなわち
  **プラグインのキャッシュがディレクトリごと入れ替わる**場所である。これは状況証拠ではなく
  根本原因そのものだった。詳細は次節「Task 1 の結果」を参照。


## Task 1 の結果（根本原因）

**GNU tar 1.35 の incremental 作成時の rename 検出が、inode 番号の再利用で誤爆している。**

tar は `--listed-incremental` でディレクトリを **(dev, ino)** の組で追跡する。ディレクトリが
削除され新しいディレクトリが作られると、ファイルシステムが**同じ inode 番号を再利用**するため、
tar は「別名へ rename された」と誤判定し、親の dumpdir (`GNUTYPE_DUMPDIR`) に偽の
`R`（rename 元）/ `T`（rename 先）レコードを書き込む。復元側の
`tar --listed-incremental=/dev/null` はそれを忠実に `rename()` として実行し、
`No such file or directory` / `Directory not empty` で失敗して終了コード 2 を返す。
`_run_docker_tar` がそれを `SnapshotError` にするため、`restore()` がそこで中断する。

### エビデンス

**1. 偽の rename レコードが実在する。** 最小再現の `incr-001` の dumpdir をデコードした結果:

```
'R./.claude/plugins/cache/A1'                    ← 前の世代で削除済みのディレクトリ
'T./.claude/plugins/cache/B1/unknown/commands'   ← 新しく作ったディレクトリ
```

**2. inode が実際に再利用されている。** 同一ボリューム上で計測:

| パス | inode |
|---|---|
| `cache/C1` | 1159533 |
| `cache/D1`（`C1` を削除した後に新規作成） | **1159533** |
| `cache/C1/unknown/commands` | 1159541 |
| `cache/D1/unknown/commands` | **1159541** |

**3. 本番のスナップショットも汚染されている。**
`backups/20260829-182126/incr-001.tar.zst` を全走査（dumpdir 24,392 件）したところ 1 件が
rename レコードを保持していた。無関係なツリー間の rename であり、偽物であることが明白である:

```
'R./ai/.codex/.tmp/marketplaces/ai-plugins/plugins/playwright-kit'
'T./ai/.claude/plugins/cache/temp_git_1788002903410_i7hzgk/.git/objects'
```

**4. tar は rename 失敗後も展開を完遂している。** 失敗した実行でも、復元先の `find | sort` は
最後の差分時点のソースと**完全に一致**した（差分 0 行）。壊しているのは tar の展開ではなく、
**最初の失敗で `restore()` が中断すること**である。

**5. 発生は非決定的。** 同一手順を 4 回実行して 3 回失敗した。inode の割り当て順に依存する。

### 案 A（当初の第一候補）の棄却

復元時に作成側の `snapshot.snar` を渡して実験したところ、`/dev/null` を渡した場合と
**結果が完全に同一**だった（`full` / `incr-001` とも rc=0、内容一致）。tar の展開側は
状態ファイルの中身を読まず書き出すだけなので、**復元専用の状態を持ち回っても何も変わらない**。

### 案 B / C に伴う data loss リスク

**rename を一律に無視・除去する案は、正当な rename のデータを失う。** 正当な `mv` を挟んで
差分を作ると、アーカイブには**ディレクトリのエントリしか入らない**:

```
drwxr-xr-x root/root   41  ./data/newdir/     ← f1〜f5 は入っていない
```

中身は rename レコードによる移動でしか復元されない（現行方式では f1〜f5 が正しく
`newdir` 配下へ出ることを確認済み）。したがって dumpdir から `R`/`T` を除去する実装や、
incremental 指定を外した素の `tar -xf` は採らない。素の `tar -xf` は削除セマンティクスも
失うため、最小再現で 160 行の取り残しが出ることも確認した。

## 受け入れ条件

- [x] AC1: 偽の rename レコードを含む世代が**最後の差分まで復元できる**。
      検証: 使い捨てボリュームに「フル → ディレクトリの総入れ替え → 差分」を 3 回以上
      繰り返した合成世代を作り、`incr-003` まで適用されて `restore()` が完走すること。
      （当初の検証対象 `backups/20260823-114528` はローテーションで消滅済み。前提 6 を参照）
- [x] AC2: 復元後の中身が**期待どおり**である。検証: 合成世代の復元先の `find | sort` が、
      最後の差分を取った時点のソースの `find | sort` と**完全に一致**すること
      （偽 rename を非致命扱いしても内容が欠けないことを固定する）。
- [x] AC3: **新しく作った**スナップショット（フル + 差分 3 つ以上、途中でディレクトリの
      入れ替えを含む）が復元できる。前提 7 の状況を人工的に作って再現テストにする。
- [x] AC4: 復元が失敗したとき、**何が起きたか・次に何をすればよいか**がエラーに出る。
      少なくとも「どの差分で失敗したか」と「`pre-restore-<name>` から戻せること」を示す。
- [x] AC5: 旧レイアウト（`volume: devbase_home_ubuntu` のみ）と新レイアウト
      （`volumes: {ai, group}`）の**両方**で AC1 が成り立つ。
- [x] AC6: `uv run pytest` が green で、再現ケースが**テストとして固定**されている。
      Docker を要するテストは、Docker が無い環境では skip する。

## 代替案と採否

Task 1 で原因が確定したため、採否を次のとおり確定した（採用は **F**）。

| 案 | 内容 | 見込み |
|---|---|---|
| A. 復元時に `--incremental` の状態を正しく渡す | `--listed-incremental=/dev/null` をやめ、復元専用の状態ファイルを世代ごとに持ち回る | 公式手順に近づく。ただし状態ファイルは**作成側**の記録なので、復元側で何を渡すべきかは要検証 |
| B. 差分適用前に対象ディレクトリを整える | tar が rename しようとする先を空にする / 事前に消す | 症状は消えるが、tar の削除セマンティクスを人手で再実装することになり脆い |
| C. 各差分を一時ディレクトリへ展開してから同期 | `rsync --delete` 相当を自前で行う | tar の incremental 依存を切れるが、削除の判定を自前で持つ必要があり形式変更に近い |
| D. 作成側を変える（差分の作り方を見直す） | `--level=N` など | 既存スナップショットが救えないため、単独では AC1 を満たせない |
| E. tar のバージョン / 実装を変える | busybox tar 等 | listed-incremental 非対応のものが多く、退行が大きい |
| **F. rename エラーだけを非致命として扱う** | 復元時、tar の stderr が `Cannot rename` 行だけなら警告を出して次の差分へ進む | **採用** |

| 案 | 採否 | 理由 |
|---|---|---|
| A | **棄却** | 展開側は状態ファイルを読まないことを実験で確認した（「Task 1 の結果」参照）。何も変わらない |
| B | **棄却** | 偽 rename と正当な rename を事前に区別できず、tar の削除セマンティクスを人手で再実装することになる |
| C | **棄却** | 正当な rename のデータを失う（「Task 1 の結果」参照）。形式変更で `architecture` へ格上げにもなる |
| D | **棄却** | 既存スナップショットを救えず AC1 を満たさない |
| E | **棄却** | busybox tar 等は listed-incremental 非対応で退行が大きい |
| **F** | **採用** | tar が rename 失敗後も展開を完遂している実測（差分 0 行）に基づく。既存スナップショットを
そのまま救え、削除セマンティクスも維持し、イメージも形式も変えない |

**採用案 F の内容:** `_run_docker_tar` を「終了コードと stderr を呼び出し側へ返す」形にし、
`restore()` 側で判定する。stderr の全行が `tar: Cannot rename ... ` か
`tar: Exiting with failure status due to previous errors` のいずれかに一致する場合だけ、
偽の rename とみなして `logger.warning` を出し次の差分へ進む。それ以外のエラーは従来どおり
`SnapshotError` にする。

**案 F の残るリスク:** 正当な rename が失敗した場合も見逃す。ただし現行は同じ場面で
**復元ごと中断する**ため、退行ではない。見逃しを可視化するため、警告には失敗した rename の
パスをそのまま出す。

## 不変条件

- 既存のスナップショットは**作り直さずに**復元できる。
- 復元は `full` → `incr-001` → … の順に適用する（順序を入れ替えない）。
- 復元前の自動バックアップ (`pre-restore-*`) は必ず作られる。
- 復元は対象ボリュームの**中身だけ**を操作し、マウントポイント自体は消さない。
- スナップショットのメタデータ由来のボリューム名は PLAN39 の検証を通す
  （devbase が作るボリュームだけを対象にする）。

## 検証結果

| 受け入れ条件 | 検証手段 | 結果 |
|---|---|---|
| AC1 | `tests/snapshot/test_restore_incremental.py::test_a_generation_with_swapped_directories_restores_completely`（実 Docker・実 tar） | 総入れ替えを挟んだ差分 3 個の世代が `incr-003` まで適用され `restore()` が完走。3 回連続で `incr-001`〜`incr-003` すべてが偽 rename に当たり、すべて警告として飲み込まれた |
| AC2 | 同上（復元先の `find` の一覧と、最後の差分時点のソースの一覧を比較） | 完全一致 |
| AC3 | 同上（世代はテスト内で新規に作成する） | 満たす |
| AC4 | `test_the_failure_message_says_how_to_get_back` / `test_a_full_restore_failure_also_says_how_to_get_back` / `test_a_real_tar_error_still_stops_the_restore` / `docs/user/snapshot-guide.md` | どの差分で落ちたか・`pre-restore-<name>` から戻す手順を出す |
| AC5 | `test_both_layouts_survive_a_bogus_rename`（旧 `volume:` / 新 `volumes: {ai, group}` の 2 レイアウト） | 両方で後続の差分まで適用しきる |
| AC6 | `uv run pytest` | 1,676 passed。Docker が無い環境では実機テストだけ skip する |

### クロスレビューで追加した振る舞い

代替案 F は「偽 rename と正当な rename をエラー文だけでは区別できない」という弱点を持つ。
レビューでこの点を指摘され、**展開後の状態でなら区別できる**ことを実験で確かめて対処した。

| 失敗した rename の宛先 | 意味 | 実測 |
|---|---|---|
| 存在しない / 中身がある | 偽 rename。欠落なし | 総入れ替えの再現で確認 |
| **存在するが空のまま** | 正当な rename を取りこぼした疑い | 正当な `mv` の再現で確認 |

偽 rename の宛先はそのディレクトリ自身が新しく作られたものなので、アーカイブから中身が
展開されて空にならない。一方、正当な `mv` の差分には**ディレクトリのエントリしか入らない**
ため、取りこぼすと宛先が空のまま残る。`restore()` は飲み込んだ rename の宛先を集め、
**全アーカイブ適用後に 1 度だけ**検査する（後続の差分が中身を埋める場合があるため）。

空の宛先を検出しても**失敗にはせず警告に留める**。空の宛先で復元を止めると、正当に空だった
ディレクトリで再び途中停止が起き、この PLAN が直そうとしている症状を再発させる。空の宛先の
中身はそのスナップショットには入っていないため、停止しても復旧しない。

検証は復元の完了**後**に走るので、検証自体の失敗を復元の失敗にしない。パス数が多い場合は
`chunk_paths()` で `docker run` の引数長を抑えて複数回に分ける。

**AC5 の実機確認の範囲について。** 実 Docker の検証は `group` 側のボリューム
(`devbase_home_plan40test`) だけで行った。旧レイアウトと新レイアウトの `ai` 側は
`devbase_home_ubuntu` に固定されており (`_validate_volumes`)、これは**利用者の実データが
入っているボリューム**である。復元は対象ボリュームの中身を消してから展開するため、
実機テストの対象にしていない。レイアウトの違いは対象ボリュームの解決とマウントにだけ効き、
rename エラーの扱いには影響しないので、その差はテストダブルで固定している。

## 修正対象

- `lib/devbase/snapshot/manager.py` — `restore()` と `_run_docker_tar()` の復元コマンド
- `containers/snapshot/Dockerfile` — （必要なら）tar の版や補助ツール
- `tests/snapshot/` — 再現テストと現状固定テスト
- `docs/user/snapshot-guide.md` — 復元の制約と、失敗したときの戻し方

## タスク分解

### Task 1: 根本原因の特定（調査） — **完了**

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
- **結果:** 「Task 1 の結果」節に記載。原因は inode 再利用による偽 rename レコード。
  採用案は F（rename エラーだけを非致命扱い）

### Task 2: 再現テストを先に置く

- **対象ファイル:** `tests/snapshot/test_restore_incremental.py`（新規）
- **やること:** Task 1 の最小再現をテストにする。Docker を使うため
  `pytest.mark.skipif` で Docker 不在時は skip する。**この時点では失敗する**テストにする
- **満たす受け入れ条件:** AC6（の失敗側）
- **進め方:** テスト駆動

### Task 3: 復元コマンドの修正

- **対象ファイル:** `lib/devbase/snapshot/manager.py`（必要なら `containers/snapshot/Dockerfile`）
- **やること:** 採用案 F を実装する。`_run_docker_tar` が終了コードと stderr を返すようにし、
  `restore()` が「`Cannot rename` 行だけの失敗」を警告として飲み込んで次の差分へ進む
- **満たす受け入れ条件:** AC1, AC3, AC5, AC6
- **進め方:** テスト駆動

### Task 4: 失敗時の扱いを改善する

- **対象ファイル:** `lib/devbase/snapshot/manager.py`, `docs/user/snapshot-guide.md`
- **やること:** 差分の適用に失敗したとき、**どの差分で落ちたか**と
  **`pre-restore-<name>` から戻せること**をエラーメッセージに含める。
  中途半端な状態で放置される旨も明示する
- **満たす受け入れ条件:** AC4
- **進め方:** テスト駆動（メッセージの内容を固定する）

### Task 5: 合成世代での実機確認

- **対象:** 検証のみ
- **やること:** 合成世代（フル → 総入れ替え → 差分 を 3 回以上）を**旧レイアウト**
  （`volume: devbase_home_ubuntu` 相当 1 本）と**新レイアウト**（`volumes: {ai, group}`）の
  両方で作り、使い捨てボリュームへ復元して AC1 / AC2 / AC5 を確認する。
  実データのボリュームと実データの世代は触らない
  （当初の対象 `20260823-114528` は消滅済み。現存世代の復元確認は行わない — 利用者の判断）
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

- [x] AC1〜AC6 を満たし、条件ごとに検証手段と結果が対応している（「検証結果」節）
- [x] `uv run pytest` が green（1,663 passed）
- [x] `/ndf:cross-review` で APPROVE 収束済み（4 ラウンド。codex / gemini 両者 APPROVE）
- [x] `docs/user/snapshot-guide.md` が復元の制約と失敗時の戻し方を説明している
- [x] 総入れ替えを挟んだ差分 3 個の世代を最後まで復元できることを実機で確認している
      （当初の「実在する差分 8 個の世代」はローテーションで消滅済み。前提 6 を参照）
