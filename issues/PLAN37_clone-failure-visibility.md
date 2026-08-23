# PLAN37: clone に失敗したリポジトリを `devbase up` と workspace に正しく反映する

## 関連リンク

- 前提となる設計: `issues/PLAN32_multi-repo-project.md`（複数リポジトリ clone と multi-root workspace）
- 実装: `containers/base/entrypoint.sh`、`lib/devbase/project/runtime.py`、`lib/devbase/commands/container.py`
- 発端: multi-repo 構成で「primary には権限があるがサブリポジトリには権限がない」ケースの挙動調査

## モード

`standard` — 既存の振る舞い（clone 失敗はコンテナ起動を止めない）は変えず、その結果の**見せ方**を足す。
公開コマンドは増えず、`project.yml` スキーマも変わらない。コンテナへ渡す内部 wire format を 1 つ追加する。

## 目的と非目的

達成したい状態:

- `devbase up` の出力だけで、**どのリポジトリが `/work` に無いか**が分かる。`docker logs` を掘らなくてよい。
- 生成される `*.code-workspace` に、**存在しないフォルダが並ばない**。

やらないこと:

- clone 失敗で `devbase up` を失敗させること。1 本落ちただけで開発環境ごと止めない方針（PLAN32）は維持する。
- 権限エラーと typo の区別。GitHub は権限のない private リポジトリにも `Repository not found` (404) を返すため、
  クライアント側では判別できない。表示は「`/work` に無い」という事実に留める。
- clone のリトライ・認証まわりの改善。entrypoint は起動のたびに clone を試すので、権限付与後は次回 `up` で解決する。

## 前提

調査で確認した現状（2026-08-24、実機 `nyle-dx-dev-1` と実イメージの `/entrypoint.sh` で確認）:

- 前提 1: `devbase_clone_repos` は個々の clone / checkout / init.sh の失敗を warning に留めて次の repo へ進む。
  回帰テストは `tests/containers/test_entrypoint_repos.py:131` にある。primary が落ちても起動は続く（同 `:271`）。
- 前提 2: 失敗はハングしない。コンテナは `Tty=false` で、GitHub は権限のない private リポジトリにも 404 を返すため、
  git は認証プロンプトへ落ちずに 0.4 秒で `exit 128` する。`~/.git-credentials` も消えない（401 ではないので reject が走らない）。
- 前提 3: `cmd_up` は `docker compose up -d` → ready 待ち → editor 起動の順で進み、entrypoint の標準出力を一切見ない。
  そのため clone 失敗があっても `=== Deploy completed successfully ===` で終わる。
- 前提 4: workspace の JSON はホスト側 (`build_workspace_document`) が `project.yml` から静的に組み立て、
  `DEVBASE_WORKSPACE_B64` で渡している。clone の成否は反映されない。
- 前提 5: entrypoint の変更は base イメージの再ビルドが要る（`devbase container build`）。ホストだけ更新した状態でも
  **今までどおり動く**必要がある。

## 受け入れ条件

実機検証は `nyle-dx` プロジェクトに権限のないリポジトリ (`volareinc/no-such-repo-xyz123`) を
一時的に足し、`devbase base` / プロジェクトイメージを再ビルドしたうえで `devbase up` を実行した。

- [x] AC1: `project.yml` に 2 件書き、片方が clone できない構成で `devbase up` すると、標準出力に
      「`/work` に無いリポジトリ」の一覧（dir と clone URL）と、詳細の確認先が出る。

      ```
      [5/6] Waiting for containers to be ready...
      All containers ready
      Warning: Repositories missing in /work of dev-1 (clone may have failed):
      Warning:   - no-such-repo-xyz123 (https://github.com/volareinc/no-such-repo-xyz123.git)
      Warning:   Details: devbase project logs nyle-dx | grep Warning
      === Deploy completed successfully ===
      ```
- [x] AC2: AC1 の状況でも `devbase up` の終了コードは 0 で、成功したリポジトリでは通常どおり作業できる。
      検証: 上記の実行で `exit=0`。`/work/nyle-dx` と `/work/ideabase` は通常どおり存在する。
- [x] AC3: 全リポジトリが揃っているときは、`up` の出力は従来と変わらない。
      検証: 一時エントリを外して再実行 → `Repositories missing` の出力は 0 件、`exit=0`。
- [x] AC4: clone に失敗したリポジトリは `*.code-workspace` の `folders` に含まれない。
      検証: 実機の `/work/nyle-dx.code-workspace` は `nyle-dx` と `ideabase` の 2 件のみ。
- [x] AC5: 落としたフォルダは entrypoint のログに warning として残る。
      検証: `docker logs nyle-dx-dev-1` に
      `Warning: Skipping workspace folder (not cloned): no-such-repo-xyz123`。
- [x] AC6: 旧イメージ（新 wire format を知らない entrypoint）に新しいホストから `up` しても、workspace は
      従来どおり全フォルダ入りで書き出される。
      検証: 再ビルド前のイメージに焼かれていた**実物の** `/entrypoint.sh` へ新旧両方の環境変数を渡し、
      `DEVBASE_WORKSPACE_B64` 経由で 3 フォルダすべてが書き出されることを確認。
- [x] AC7: `pytest` が通る（1445 passed）。

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
|---|---|---|---|
| clone 結果をコンテナのログから grep してホストで表示 | `docker logs` を `Warning: Failed to clone` で検索 | 不採用 | ログはコンテナ再起動をまたいで積み上がり、いつの失敗か特定できない。「今 `/work` に有るか」を直接見る方が真実に近い |
| **`/work` の実体を見て不足を報告する** | ready 待ちの後に `ls -A1 /work` を 1 回実行し、`project.yml` の dir 集合と突き合わせる | **採用** | 冪等で、既存 clone を引き継いだ場合も正しい。exec は instance あたり 1 回で済む |
| dir ごとに `test -d` を exec | repo 件数 × instance 回の exec | 不採用 | 遅く、dir 名をシェルへ渡すためのクォートが増える。`ls` の 1 回で足りる |
| workspace を entrypoint 側で JSON パースして絞る | `jq` / `python3` で folders をフィルタ | 不採用 | base 非継承イメージ (lfm 等) に依存を増やす。PLAN32 が避けた方針をそのまま踏襲する |
| **folder ごとに直列化した JSON をホストから渡し、entrypoint は存在するものだけ連結する** | `DEVBASE_WORKSPACE_FOLDERS` = `<dir><US><folder の JSON>` の行 | **採用** | `dir` に `"` や `\` が入ってもホスト側の `json.dumps` が処理済み。entrypoint は連結するだけで JSON パーサが要らない |
| `DEVBASE_WORKSPACE_B64` を置き換える | 旧変数を削除する | 不採用 | ホストだけ更新してイメージが古い間、workspace が黙って書かれなくなる。旧変数は fallback として残す |

## ドメイン用語

| 用語 | 意味 |
|---|---|
| clone プラン | `project.yml` を正規化した内部表現。`DEVBASE_REPOS` でコンテナへ渡る（PLAN32） |
| workspace フォルダレコード | 本 PLAN で追加する wire format。`<dir><US><folder オブジェクトの JSON>` を 1 行とする LF 区切り、全体を base64 |
| 欠落リポジトリ | `project.yml` に書かれているのに `/work/<dir>` が存在しないリポジトリ |

## 不変条件

- clone の失敗はコンテナ起動を止めない（PLAN32 から継続）。
- ホスト側だけが `project.yml` と wire format の変換を行う。entrypoint は JSON を組み立てない。
- 新しい警告は**異常時のみ**出す。全リポジトリが揃っているときの出力は変えない。

## wire format

追加する環境変数（`DEVBASE_WORKSPACE` / `DEVBASE_WORKSPACE_B64` は現状のまま残す）:

```
DEVBASE_WORKSPACE_FOLDERS : base64。復号すると 1 行 1 フォルダの LF 区切り。
                            1 行 = <dir> <US(0x1f)> <folder オブジェクトの JSON>
                            例: nyle-dx\x1f{"name": "nyle-dx", "path": "/work/nyle-dx"}
```

`dir` は空白・制御文字を含まないことが `project.yml` のローダで保証済みなので、US / LF がフィールドを割ることはない。
JSON 側も `json.dumps` が制御文字をエスケープするため、1 行に収まる。

## 修正対象

- `lib/devbase/project/runtime.py` — `container_env` に `DEVBASE_WORKSPACE_FOLDERS` を追加
- `containers/base/entrypoint.sh` — `devbase_write_workspace` が存在するフォルダだけを書き出す
- `lib/devbase/commands/container.py` — ready 待ちの後に欠落リポジトリを報告する
- `tests/project/test_runtime.py` / `tests/containers/test_entrypoint_repos.py` / `tests/commands/` — 回帰テスト
- `docs/developer/architecture.md`、`docs/user/project-yml.md`、`CHANGELOG.md` — wire format と挙動の記述

## タスク分解

### Task 1: workspace フォルダレコードの生成（ホスト）

- `container_env` が repo 2 件以上のとき `DEVBASE_WORKSPACE_FOLDERS` を追加する。`build_workspace_document` の
  folders と同じ順序（primary 先頭）を使い、1 フォルダ = 1 レコードへ直列化する。
- 単体テスト: レコード数・順序・`dir` とのペア・repo 1 件のときは付かないこと。

### Task 2: 存在するフォルダだけを書き出す（entrypoint）

- `devbase_write_workspace <work_root>` にして、`DEVBASE_WORKSPACE_FOLDERS` があればレコードを読み、
  `<work_root>/<dir>` が存在する行だけを `{"folders": [...]}` へ連結する。
- 落とした行は `Warning: Skipping workspace folder (not cloned): <dir>` として出す。
- `DEVBASE_WORKSPACE_FOLDERS` が無ければ従来どおり `DEVBASE_WORKSPACE_B64` をそのまま書き出す（旧ホスト互換）。
- 単体テスト: 欠落フォルダの除外、全滅時 (`folders: []`)、fallback、warning の出力。

### Task 3: 欠落リポジトリの報告（ホスト）

- `cmd_up` の ready 待ちの直後に、instance ごとに `docker compose exec -T dev-<i> ls -A1 /work` を実行し、
  `project.yml` の dir 集合との差を求める。
- 欠落があれば `logger.warning` で dir と clone URL、`docker logs` の確認先を出す。欠落が無ければ何も出さない。
- exec 自体が失敗した場合は黙って諦める（`up` を倒さない）。
- 単体テスト: 欠落あり / 無し / exec 失敗の 3 経路。

### Task 4: ドキュメントと CHANGELOG

- `docs/developer/architecture.md` の `runtime.py` の説明に新 wire format を追記。
- `docs/user/project-yml.md` に「権限が無いリポジトリがあるとどうなるか」を追記。
- `CHANGELOG.md` に、entrypoint の変更を反映するには `devbase container build` が要る旨を明記。

## 影響範囲

| 対象 | 影響 |
|---|---|
| 既存プロジェクト（repo 1 件） | なし。`DEVBASE_WORKSPACE_FOLDERS` は 2 件以上でしか付かない |
| 既存プロジェクト（repo 2 件以上・全て clone 成功） | なし。workspace の内容も出力も変わらない |
| 旧イメージ + 新ホスト | workspace は fallback 経路で従来どおり。欠落リポジトリの報告はホスト側なので効く |
| 新イメージ + 旧ホスト | `DEVBASE_WORKSPACE_FOLDERS` が無く fallback へ落ちるだけ |

## リスクと対処

| リスク | 対処 |
|---|---|
| `/work` は複数プロジェクトで共有されるため、別プロジェクトが同名 dir を作っていると「有る」と判定される | dir 名の衝突は共有ボリュームの既存の性質。報告は「`/work` に無い」だけを述べ、所有権は主張しない |
| `ls -A1 /work` が巨大になる | 出力は名前だけで、共有ボリュームの実績でも数十件。1 instance につき 1 回に留める |
| entrypoint のシェルで JSON を組み立てる | 組み立てるのは `{"folders": [` と `]}` の外枠と `,` だけ。値はホストが直列化済み |

## 切り戻し手順

1. `lib/devbase/commands/container.py` の報告呼び出しを外す（警告が消えるだけ）。
2. workspace を元に戻す場合は `container_env` から `DEVBASE_WORKSPACE_FOLDERS` を落とす。entrypoint は
   fallback で `DEVBASE_WORKSPACE_B64` を使うため、イメージを戻さなくても旧挙動に戻る。

## 完了の定義

- 受け入れ条件 AC1〜AC7 を満たす。
- `pytest` が通り、新規テストが Task 1〜3 の各経路を押さえている。
- ドキュメントと CHANGELOG が更新されている。
