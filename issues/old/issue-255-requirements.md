# #255: snapshot restore の復元前バックアップが、復元する世代ではなく実行した場所のグループのボリュームを控える

正は課題の本文（#255）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

課題の起票時の本文をそのまま引用する。

> ## 何を見つけたか
>
> **`devbase snapshot restore` の復元前の自動バックアップ（`pre-restore-<時刻>`）が、復元する世代のボリュームを控えない。**
>
> `restore` は復元先を**スナップショット自身のメタデータ**（`snapshot_volumes(snap_dir)`）から決める。一方、その直前の `self.create(name=pre_restore_name, full=True)` は `_create_full` → `self.volumes` を使い、これは実行時の `DEVBASE_ACCOUNT_GROUP` の解決結果で決まる。アカウントグループの宣言は必須で、`default` は予約語として弾かれる（#315）。`DEVBASE_ACCOUNT_GROUP` を置くのは `devbase up` / `scale` の経路だけで、`cmd_snapshot` は `create` 以外にグループを渡さない。
>
> 実行時の `DEVBASE_ACCOUNT_GROUP` によって、次の 2 つの形になる。
>
> | 実行時の `DEVBASE_ACCOUNT_GROUP` | 結果 |
> | --- | --- |
> | 未設定（CLI・TUI からの通常の実行） | `GroupDeclarationError` で控えに失敗し、「復元前バックアップに失敗しましたが続行します」の警告を 1 行出して、**控え無しで復元する**。`backups/` に中身の無い `pre-restore-*` のディレクトリが残る（`snapshot.yml` には載らない） |
> | 設定あり（例 `nyle`）で別のグループの世代を復元 | `pre-restore-*` は `devbase_home_ubuntu, devbase_home_nyle` を控え、書き戻される `devbase_home_with` を控えない。失敗しても `devbase_home_with` の復元前の状態はどこにも無いが、失敗時の案内は「`pre-restore-*` に退避してあります」と出る |
>
> 再現の形（docker の呼び出しを差し替えて確かめられる）:
>
> 1. `devbase_home_ubuntu, devbase_home_with` の世代 `X` がある
> 2. `DEVBASE_ACCOUNT_GROUP` を置かずに `SnapshotManager(root).restore('X')` を呼ぶ
> 3. 警告が 1 行出て、控えの `docker run` は行われず、`X` の復元だけが行われる。`backups/pre-restore-<時刻>/` は空のディレクトリで残る
> 4. `DEVBASE_ACCOUNT_GROUP=nyle` で同じことをすると、控えは `devbase_home_nyle` を対象に取られ、復元は `devbase_home_with` へ行われる
>
> `tests/snapshot/test_restore_incremental.py:35` と `tests/snapshot/test_manager_volumes.py:21` は `DEVBASE_ACCOUNT_GROUP` を置いてから `restore` を呼ぶため、未設定の形は試験に無い。
>
> ## どこで見つけたか
>
> `lib/devbase/snapshot/manager.py` の `restore`（復元前バックアップ `:340-348` と復元先の解決 `:350`）、`create`（`mkdir` の後に `_create_full` を呼ぶ `:243-249`）、`_create_full`（`:815-833`、`'volumes': dict(self.volumes)` が `:829`）。グループの解決は `lib/devbase/volume/manager.py` の `resolve_account_group`（`:96-117`）、グループを渡す分岐は `lib/devbase/commands/snapshot.py` の `cmd_snapshot`（`:59-63`）。
>
> ## なぜこの変更の範囲外なのか
>
> issue #248（閉じている）の範囲は世代の**保持の単位**（系列ごとの保持と差分の積み先）である。復元前バックアップが控える対象の取り違えは、系列の有無と関係なく起きる。
>
> ## 直さないと何が起きるか
>
> - CLI・TUI から復元すると、復元前の状態が控えられないまま対象のボリュームが書き戻される。警告は 1 行だけで、利用者は控えがあると思い込みうる
> - `DEVBASE_ACCOUNT_GROUP` を置いた状態で別グループの世代を復元して失敗すると、そのグループのボリュームを戻す手段が無い。案内の文言は戻せると読める
> - 控えに失敗するたびに、`backups/` に中身の無い `pre-restore-*` のディレクトリが増える
>
> ## 直し方
>
> 修正レイヤーは `create` / `_create_full` である。復元前バックアップでは、`self.volumes` ではなく、復元する世代のメタデータ（`snapshot_volumes(snap_dir)`）からボリュームの組を取る（控えるボリュームの組を引数で受けられるようにし、`restore` からその組を渡す）。控えに失敗したときは、空のディレクトリを残さない。
>
> 旧既定のボリューム（`devbase_home_default`）の世代を復元するときも、その組で `pre-restore-*` を作る。`docs/specifications/secret-backend.md:427-431` は旧既定のボリュームの系列に「新しい世代は作られない」と書いているため、復元前の控えはその例外であることを仕様に書き足す。
>
> 試験では、`DEVBASE_ACCOUNT_GROUP` を置かずに `restore` を呼ぶ形と、別グループの世代を復元する形で、控えの対象が復元する世代のボリュームの組になることを固定する。
>
> ## 依存
>
> #266 と同じ `restore` / `_create_full` に触る。同時に進めず、どちらかを先に入れてから他方を入れる。
>
> ## 由来
>
> issue #248 の設計（PLAN68）で `manager.py` を読んだときに見つけた。
>

## 目的

- `devbase snapshot restore`（CLI・TUI）で、書き戻す直前の**復元する世代の対象ボリュームの組**の状態が、実行時の `DEVBASE_ACCOUNT_GROUP` の有無・値に関係なく `pre-restore-<時刻>` として控えられている状態にする
- 復元が途中で失敗したときの案内（「`pre-restore-*` に退避してあります」）が、実際に書き戻したボリュームを戻せる場合にだけ出る状態にする

## 前提

- 前提 1: 控える組は、復元する世代の `meta.yml` から `snapshot_volumes(snap_dir)` が返す組（検証済みの組）と同じものとする。`volumes` を持たない旧レイアウトの世代（共通ボリューム 1 本をルートへマウントする形）では、その 1 本の組で控える
- 前提 2: 控えに失敗したときは、今と同じく警告を出して**復元を続ける**。控えの失敗で復元を止める振る舞いへは変えない（止めるかどうかは未決に挙げる）
- 前提 3: 控えは常に新しいフルの世代として作る。`pre-restore-<時刻>` の名前（秒の単位）が既にある場合の扱いは今の `create` のまま変えない（同じ秒に 2 回復元することは想定しない）
- 前提 4: #266 とは同時に進めない。本件を先に入れ、#266 は本件のマージの後に `restore` / `_create_full` の差分を取り込み直してから進める
- 前提 5: 控えの世代の `meta.yml` と `snapshot.yml` のエントリの `volumes` は、控えた組を記録する。したがって控えは復元した世代と同じ系列に入り、系列ごとのローテーションの規則（docs/specifications/snapshot-series.md）で扱われる

## 対象範囲

含む:
- 復元前バックアップが控える対象ボリュームの組の決め方（`restore` から `create` / `_create_full` へ組を渡す）
- 控えに失敗したときに、その呼び出しで作ったディレクトリを残さないこと
- 旧既定のボリューム（`devbase_home_default`）の世代を復元するときの控え
- 控えの対象を示すログ
- 仕様の書き足し（`docs/specifications/secret-backend.md` の旧既定のボリュームの系列に「新しい世代は作られない」の例外として復元前バックアップを書く。`docs/specifications/snapshot-series.md` の「含まない」の #255 の行を直す）
- 利用者文書の文言（`docs/user/snapshot-guide.md`・`docs/user/cli-reference/05-snapshot.md` の「現在の対象ボリュームの状態」を、復元する世代の対象ボリュームの状態と読めるように直す）
- `DEVBASE_ACCOUNT_GROUP` 未設定の形と、別グループの世代を復元する形の試験

含まない:
- `devbase snapshot create`（CLI・TUI）の対象の決め方。グループは今までどおり宣言か `--group` から決まる
- 控えの失敗で復元を止めるかどうかの変更（前提 2）
- 名前付きの世代・`pre-restore-*` をローテーションから守ること（#256）
- アーカイブ名と展開コマンドの組み立ての整理（#266）
- 復元の対象・順序・差分の適用・rename の警告の振る舞い
- 移し先のボリュームへ復元する経路（持たないことは secret-backend.md のとおり）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 復元する世代を確かめた（場所・`full.tar.zst` の有無） | `devbase snapshot restore <名前>`、TUI の復元 | `SnapshotError` で止まり、控えも復元も行わない | — |
| E2 | 復元する世代の対象ボリュームの組を読んだ | E1 | `meta.yml` が不正なら `SnapshotError` で止まり、控えも復元も行わない（今は控えの後に読むため、不正な世代でも控えが作られる。順序が変わる） | E1 |
| E3 | 復元前バックアップを作った（E2 の組を控え、`meta.yml` と `snapshot.yml` へ記録した） | E2 | 警告を 1 行出し、その呼び出しで作ったディレクトリを消し、控え無しとして E4 へ進む | E2 |
| E4 | 対象ボリュームを世代の中身で書き戻した（フル → 差分） | E3（成功・失敗のどちらでも） | 展開の失敗を `SnapshotError` で返す。案内は E3 が成功していれば `pre-restore-*` から戻す手順、失敗していれば別の世代から戻す手順 | E3 |

E2 を E3 の前へ移すことは、控える組を決めるために要る。E2 の失敗で止まるのは今も同じ（今は E3 の後で止まる）なので、不正な世代で控えが作られなくなる以外に振る舞いは変わらない。

## 用語

| 用語 | 意味 |
| --- | --- |
| 復元前バックアップ | `restore` が書き戻す前に自動で作るフルの世代 `pre-restore-<時刻>`。復元する世代の対象ボリュームの組を控える |

## 受け入れ条件

- [ ] 前提: `devbase_home_ubuntu, devbase_home_with` の世代 `X` がある。`DEVBASE_ACCOUNT_GROUP` が未設定
      操作: `SnapshotManager(root).restore('X')`
      結果: 控えの `docker run` が 1 回行われ、マウントされるのは `devbase_home_ubuntu` と `devbase_home_with` である。`GroupDeclarationError` の警告は出ない
- [ ] 上の操作の後、`backups/pre-restore-<時刻>/meta.yml` の `volumes` が `X` の `volumes` と一致し、`snapshot.yml` に同じ `volumes` の `pre-restore-<時刻>` のエントリがある
- [ ] 前提: 同じ世代 `X`。`DEVBASE_ACCOUNT_GROUP=nyle`
      操作: `restore('X')`
      結果: 控えのマウントに `devbase_home_with` が含まれ、`devbase_home_nyle` が含まれない
- [ ] 前提: `volumes` を持たない旧レイアウトの世代
      操作: `restore`
      結果: 控えは `devbase_home_ubuntu` 1 本をルート（`/source`）へマウントして作られ、控えの `meta.yml` の `volumes` は `{'': 'devbase_home_ubuntu'}` になる
- [ ] 前提: 旧既定のボリューム `devbase_home_default` を含む世代
      操作: `restore`
      結果: 控えは `devbase_home_ubuntu` と `devbase_home_default` を対象に作られる
- [ ] 前提: 控えの `docker run` が失敗する
      操作: `restore`
      結果: 警告が 1 行出て復元は続き、`backups/pre-restore-<時刻>/` のディレクトリは残らず、`snapshot.yml` にそのエントリは無い
- [ ] 前提: 控えの `docker run` が失敗し、続く復元の展開も失敗する
      結果: 失敗の案内は「復元前の自動バックアップは作成できていません」の側になり、`pre-restore-` の名前を含まない
- [ ] 前提: 控えは成功し、続く復元の展開が失敗する
      結果: 失敗の案内は控えの名前を含み、その控えの `volumes` は復元先の組と一致する
- [ ] 前提: 復元する世代の `meta.yml` の `volumes` が検証を通らない
      操作: `restore`
      結果: `SnapshotError` になり、控えの `docker run` も復元の `docker run` も行われず、`pre-restore-*` のディレクトリは作られない
- [ ] 控えの前に、控える対象ボリュームを並べた info のログが 1 行出る
- [ ] 退行しない: `devbase snapshot create`（`--group` あり・宣言あり）の対象ボリュームと `meta.yml` の `volumes` は変わらない（既存の `tests/snapshot/` の試験がすべて通る）
- [ ] 退行しない: 自動スナップショット（`devbase up` / `down`）の積み先と世代の作り方は変わらない（`tests/snapshot/test_auto_snapshot*.py` が通る）
- [ ] `docs/specifications/secret-backend.md` の旧既定のボリュームの系列の段落に、復元前バックアップだけは旧既定のボリュームの組で新しい世代が作られることが書かれている
- [ ] `docs/specifications/snapshot-series.md` の「含まない」から「復元前の自動バックアップが控えるボリュームは #255」の記述が消え、控えが復元する世代の系列に入ることが読める
- [ ] `docs/user/snapshot-guide.md` と `docs/user/cli-reference/05-snapshot.md` の復元前バックアップの説明が、控えるのは復元する世代の対象ボリュームであると読める

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 可用性 | 控えの失敗で復元を止めない（前提 2）。控えの失敗が残すディスク上の痕跡は 0（空のディレクトリを残さない） |
| 運用・保守性 | 控えの対象ボリュームを info のログで 1 行示し、失敗時の案内が実際に戻せるかどうかと一致する |
| セキュリティ | 控えでマウントする組は `snapshot_volumes` の検証（devbase のボリュームだけを許す）を通ったものに限る。検証を通らない世代では控えの `docker run` を行わない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | CLI の引数・終了コードは変わらない。`SnapshotManager.create` / `_create_full` は控える組を受け取れるようになる（省いたときは今の `self.volumes`）。形は設計で決める |
| データ | `snapshot.yml`・`meta.yml` の形は変わらない。`pre-restore-*` の `volumes` の値が、復元する世代の組になる |
| 既存の振る舞い | `restore` で、世代のメタの検証が控えより前になる。`DEVBASE_ACCOUNT_GROUP` 未設定でも控えが作られる。控えの失敗で空のディレクトリが残らない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --locked pytest tests/snapshot -q`、全体は `uv run --locked pytest tests/ -q`（CI と同じ。`DEVBASE_ROOT` を継承するため、docker の呼び出しは差し替えた試験だけで確かめる） |
| 静的解析 | `python -m compileall -q lib bin` と `ruff check --select=E9,F63,F7,F82 lib`（`.github/workflows/ci.yml` と同じ） |
| 手動確認 | 実機（docker あり）で、グループ `A` の世代を `DEVBASE_ACCOUNT_GROUP` を置かずに `devbase snapshot restore` し、`backups/pre-restore-*/meta.yml` の `volumes` が `devbase_home_A` を含むことを見る。マージ前に確かめる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 変更は `lib/devbase/snapshot/manager.py` の中に留める。`commands/snapshot.py` と TUI にグループを渡す経路を足さない（復元の対象は世代のメタから決まるため） |
| コーディング規約 | `docs/developer/contributing.md` の規約。ログ・案内の文言は日本語で、既存の `restore` の文言の調子に合わせる |
| テスト戦略 | `tests/snapshot/` の単体試験で、`_run_docker_tar` を差し替えてマウントの組とメタを固定する（`test_manager_volumes.py` の `RecordingManager` の形）。`DEVBASE_ACCOUNT_GROUP` を置かない試験は、ファイルの autouse の fixture を上書きして未設定にする |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | `tests/snapshot` と全体の pytest の実行、仕様と利用者文書の同じ差分での更新 |
| 確認してから行う | 控えの失敗で復元を止める変更、`snapshot.yml` / `meta.yml` の形の変更 |
| 行わない | #266 の整理（アーカイブ名・展開コマンド）、#256 の世代の保護、`snapshot create` の対象の決め方の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 控えに失敗したときに復元を止める（または確認を求める）か。今回は続ける（前提 2）が、控え無しで書き戻すことの是非は別に判断が要る | 利用者（alice）。止める場合は別の課題にする | 本件の設計 PR のレビュー時 |
| `create` へ組を渡す形（`create` の引数にするか、復元前バックアップ専用の内部メソッドにするか） | 設計（`design`） | 設計 PR |
