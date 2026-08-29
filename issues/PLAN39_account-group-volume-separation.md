# PLAN39: 永続化ボリュームをアカウントグループ単位に分離する

## 関連リンク

- issue: [#116](https://github.com/devbasex/devbase/issues/116)（背景・実機調査の全文）
- 参考: `docs/user/container-operations.md`（ボリューム構造・AI 設定の永続化）、`containers/base/entrypoint.sh`（symlink 機構）、`docs/plugin-dev/compose-yml-guidelines.md`（プロジェクト compose の書き方）

## モード

`architecture` — 永続化レイヤを二層化し、公開設定キー (`DEVBASE_ACCOUNT_GROUP`) を増やす。
データの置き場所が変わるため後戻りが安くなく、`volume` / `snapshot` / `entrypoint` を横断する。
issue #116 が `standard` 相当の Phase 分割で書かれていても、判断の粒度はこちらに合わせる。

## 目的と非目的

達成したい状態:

- `gcloud auth login` / `gws auth login` のユーザー OAuth が、**コンテナを作り直しても保たれる**（問題1）。
- その認証が**アカウントグループをまたいで共有されない**。nyle.co.jp で認証した gcloud を
  kk-generation.com のプロジェクトが引き継がない（問題2）。
- Claude Code の MCP OAuth トークンと Gemini の `vertex-ai` 設定が**グループ単位に分かれる**（問題3。すでに混線している）。
- 一方で `.claude/plugins`（238MB）等の**共通資産はグループ数だけ重複しない**。

やらないこと:

- `gcloud auth list` の active account と期待値の突き合わせによる**警告**。期待値をどこに宣言するか
  （新しい env キーか、`GCP_ACTIVE_PROFILE` からの導出か）の設計が別途必要なため、今回は
  「解決されたグループと実アカウントを起動時に 1 行ログ出力する」までとする。
- `~/.local/bin`(1.2GB) 等、再取得可能で容量の大きいディレクトリの永続化（issue #116 の「参考」節。別課題）。
- `~/.vscode-server` の永続化（`issues/PLAN36_vscode-server-persistence.md` が扱う）。
- AWS の分離。`AWS_PROFILE` + `AWS_CONFIG_BASE64` で既に達成されている。

## 前提

以下はすべて現行 `main` (`3f36a73`) 上で確認済み。

- 前提 1: 永続化されているのは `AI_SETTINGS`（`.claude.json` / `.claude` / `.codex` / `.gemini` /
  `.serena` / `.ssh` / `.kiro` / `share`）と、その置き場である `devbase_home_ubuntu` (`/persistent/ai`) だけ
  (`containers/base/entrypoint.sh:388-397`)。`~/.config/` 配下は対象外。
- 前提 2: `/persistent/ai` は index に関係なく**全コンテナで同一**
  (`volume/manager.py:82-95` の `get_ai_volume_for_index` が引数 `index` を捨てている)。
- 前提 3: `bin/devbase` はグローバル `env` とプロジェクト `./env` を `set -a` で source する
  (`bin/devbase:50,61,338`)。`.env`（機密）は `_inject_secrets` が起動前に `os.environ` へ載せる
  (`commands/container.py:46-88`)。したがって Python 側は `os.environ` から
  `DEVBASE_ACCOUNT_GROUP` を読めば 3 レベルの解決結果を得られる。
- 前提 4: 生成 compose は宣言されていないマウントを**自動で足す**
  (`volume/compose.py:100-108` の "Add missing mounts")。プロジェクト側 `compose.yml` の変更は不要。
- 前提 5: **entrypoint の symlink ループは入れ子パスを扱えない**。実測で確認した 2 つの不具合:

  | エントリ | 現行ロジックの分岐 | 起きること |
  |---|---|---|
  | `.claude/.credentials.json` | `*.json` → `sudo touch` | 親 `/persistent/group/.claude` が無く `touch: No such file or directory`。壊れた symlink が残る |
  | `.claude/history.jsonl` | `*.json` に**マッチしない** → `sudo mkdir -p` | `history.jsonl` が**ディレクトリとして**作られ、Claude Code が追記できない |

  ホーム側 (`ln -s` の直前) にも親ディレクトリ作成が無い。issue #116 は「追記が必要なのはホーム側だけ」と
  書いているが、**永続領域側にも `mkdir -p` と拡張子判定の修正が要る**。
- 前提 6: `SHARED_VOLUME_PREFIX = "devbase_home_"` は `devbase_home_<index>` にも使われる命名
  (`volume/manager.py:55-68`)。ただし `get_volume_for_index` は lib / tests のどこからも呼ばれていない死んだ API。
  `AI_VOLUME_PREFIX = "devbase_ai_"` も同様に未使用。
- 前提 7: スナップショットの対象は `VOLUME_NAME = 'devbase_home_ubuntu'` 固定
  (`snapshot/manager.py:17,335,369`)。
- 前提 8: entrypoint の実行順序は「GCP credentials の生成 (`containers/base/entrypoint.sh:189-222`)」→
  「AI Settings の symlink 生成 (`同 383-440`)」である。symlink ループはホーム側の既存実体を
  `rm -rf "$HOME_PATH"` (`同 420`) で消してから `ln -s` するため、**先に書かれた
  `~/.config/gcloud/credentials.json` は実ディレクトリごと消える**。
- 前提 9: `GOOGLE_APPLICATION_CREDENTIALS` と `BIGQUERY_KEY_FILE` は、**鍵の有無に関係なく**
  `/home/ubuntu/.config/gcloud/credentials.json` 固定で env に書かれる
  (`lib/devbase/env/collectors/google.py:139-141` の `_collect_common_settings` は、
  プロファイルが 1 件も見つからない経路 (`同 87`) からも無条件に呼ばれる)。
  一方 entrypoint の生成ブロックは全体が `if [ -n "$_GCP_CREDS_B64" ]`
  (`containers/base/entrypoint.sh:196`) の内側にあり、**鍵が無いときの削除経路が無い**。
  他の認証は「復元の直前に古い実体を消す」形になっている
  (`同 238` の `rm -f ~/.git-credentials`、`同 279` の `rm -f ~/.aws/config ~/.aws/credentials`) が、
  いずれも復元する側の分岐の中なので、GCP と同じく「未設定へ切り替えたとき」は消えない。
- 前提 10: この 2 変数は**プロジェクト側の `env` から任意のパスへ上書きできる**。wrapper は
  `projects/<name>/env` を `set -a && source ./env` で読み (`bin/devbase:61,338`)、wrapper を
  経ない経路でも `_load_project_env` (`lib/devbase/commands/container.py:295-418`) と
  `_project_env_overrides` (`lib/devbase/env/runtime.py:112-134`) が同じ値をコンテナへ渡す。
  entrypoint 側も変数があればそちらを優先する
  (`同 204` の `GAC_PATH="${GOOGLE_APPLICATION_CREDENTIALS:-$DEFAULT_CREDS_PATH}"`、
  `同 213` の `BQ_PATH="${BIGQUERY_KEY_FILE:-$DEFAULT_CREDS_PATH}"`)。
  したがって**「変数が指す先を消す」仕様にすると devbase 管理外の鍵まで消しうる**。削除対象は
  変数の値ではなく、entrypoint が生成時と同じ式で組み立てる固定パス `DEFAULT_CREDS_PATH`
  (`/home/${USERNAME}/.config/gcloud/credentials.json`、`同 198`。`USERNAME` は `同 186` の
  `${USERNAME:-ubuntu}` で解決される) に限定する必要がある。

## 受け入れ条件

- [ ] AC1: 同じグループのコンテナで `devbase down` → `devbase up` の後、`gcloud auth list` が
      **再認証なしで**同じ active account を返す。
- [ ] AC2: 同条件で `gws` の認証済みコマンドが再認証なしで通る（`~/.config/gws/credentials.enc` と
      `.encryption_key` が保たれる）。
- [ ] AC3: 異なるグループのコンテナが互いの認証を参照しない。検証: `kkg` グループのコンテナで
      `gcloud auth list` / `claude mcp list` を実行し、`default` グループの認証が見えないこと。
- [ ] AC4: 共通資産が重複しない。検証: 2 グループのコンテナで `readlink -f ~/.claude/plugins` が
      **同一の `/persistent/ai/.claude/plugins`** を指すこと。
- [ ] AC5: `DEVBASE_ACCOUNT_GROUP` 未設定のプロジェクトが `default` にフォールバックし、
      これまでどおり起動する。検証: 既存プロジェクトを `up` して entrypoint がエラーを出さないこと。
- [ ] AC6: 入れ子パスの symlink が正しく張られる。検証: `~/.claude/.credentials.json` が
      **壊れていない** symlink であること、`~/.claude/history.jsonl` が**ディレクトリでない**こと
      （前提 5 の退行を防ぐ）。
- [ ] AC7: Docker のボリューム名にできないグループ名、予約語 `ubuntu`（`devbase_home_ubuntu` と衝突する）、
      および**数字のみの名前**（`devbase_home_<index>` と衝突する。前提 6）を**起動前に拒否**し、
      理由の分かるエラーを出す。
- [ ] AC8: `default` グループでは、**現行 `/persistent/ai` に実体がある**分類 B のデータ
      （`.claude.json` / `.claude/.credentials.json` / 履歴 / `.gemini`）が**初回シードにより維持**され、
      Claude Code の再ログインが発生しない。検証: 現行環境で `up` 後に `claude` が未ログイン状態にならないこと。
      `.config/gcloud` / `.config/gws` は前提 1 のとおり現在どのボリュームにも無く**シード元が存在しない**ため、
      `default` を含む**全グループで初回 1 回だけ `gcloud auth login` / `gws auth login` が必要**である。
      これは AC8 の違反としない（AC1 / AC2 はその初回ログイン**以降**の維持を見る条件である）。
- [ ] AC9: スナップショットが共通・グループ両方のボリュームを対象にし、復元できる。
- [ ] AC10: `devbase status` に解決されたアカウントグループが表示される。
- [ ] AC11: 初回起動（グループボリュームが空）でも、env 由来のサービスアカウント鍵が消えない。
      検証: `GCP_CREDENTIALS_BASE64__<profile>` を設定した状態で `devbase up` した直後に
      `~/.config/gcloud/credentials.json` が存在し中身が空でないこと（前提 8 の退行を防ぐ）。
- [ ] AC12: 鍵が設定されていないプロファイルへ切り替えたら、既定パスの `credentials.json` が残らない。
      かつ、カスタムパスのファイルは**削除されない**。判定は 2 変数を個別に行い、片方だけが
      カスタムパスの混在ケースでも既定パスは消える（Task 5 補足 2 の判定表）。
      検証: (1) `GCP_CREDENTIALS_BASE64__a` を設定して `devbase up` し
      `~/.config/gcloud/credentials.json` が profile `a` の鍵であることを確認する。
      (2) `devbase down` 後、`GCP_ACTIVE_PROFILE=b`（`GCP_CREDENTIALS_BASE64__b` も
      `GOOGLE_APPLICATION_CREDENTIALS_BASE64` も未設定）にして `up` する。
      (3) 同じグループボリュームであっても `~/.config/gcloud/credentials.json` と
      `$BIGQUERY_KEY_FILE` が**存在しない**こと、`gcloud auth list` / `bq` が profile `a` の
      サービスアカウントで動かないことを確認する。
      (4) `gcloud auth login` によるユーザー OAuth（`credentials.db` / `access_tokens.db` /
      `application_default_credentials.json`）は削除されず、AC1 が引き続き成立すること。
      (5) **両方カスタムパスなら削除されないこと。** 両変数を既定パス以外
      （例 `/home/ubuntu/keys/custom.json`）に設定した env で (1)〜(2) を繰り返し、鍵未設定へ
      切り替えた後もそのファイルが**残っている**こと、および「devbase 管理外のパスのため削除しない」
      旨の WARN が両変数ぶん出ることを確認する。
      (6) **混在ケース。** `GOOGLE_APPLICATION_CREDENTIALS` をカスタムパス、`BIGQUERY_KEY_FILE` を
      未設定（および既定パス）にした env で (1)〜(2) を繰り返し、カスタムパスのファイルは**残り**、
      既定パスの `credentials.json` は**消えている**ことを確認する（逆の組み合わせも同様）。
      あわせて `tests/containers/` の単体テストで Task 5 補足 2 の判定表 9 通りを固定する。

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
|---|---|---|---|
| **A. 共通ボリューム + グループボリュームの二層** | `/persistent/ai` は現行のまま、`/persistent/group` に `devbase_home_<group>` を追加マウント | **採用** | 共通資産（plugins 238MB 等）を重複させずに認証だけ分離できる。既存 `devbase_home_ubuntu` を触らないので分類 A のデータ移行が不要 |
| B. ディレクトリを丸ごとグループ別ボリュームへ | `~/.claude` ごと `devbase_home_<group>` に置く | 不採用 | `plugins` / `skills` / `commands` / グローバル `CLAUDE.md` までグループ数だけ複製され二重管理になる。粒度が粗すぎる |
| C. 環境変数から毎回復元（AWS 方式） | `GCLOUD_CREDENTIALS_BASE64` のようなキーを増やす | 不採用 | gcloud のユーザー OAuth は `credentials.db` / `access_tokens.db` を含む可変の状態で、リフレッシュのたびに更新される。env へ書き戻す経路が無い |
| D. グループ別ボリューム 1 本だけにする（共通ボリュームを廃止） | 全部を `devbase_home_<group>` へ | 不採用 | B と同じ重複問題に加え、既存 `devbase_home_ubuntu` からの全データ移行が必要になる |
| **A'. `default` グループの初回シード** | グループボリュームが空なら `/persistent/ai` の分類 B 相当を**コピー**して初期化（`default` のみ） | **採用** | 現行 14 コンテナの大半を占める `default` で再ログインを避けられる。move ではなく copy なので切り戻し時に元データが残る。ただしシード元は現行 `/persistent/ai` にあるものに限られ、`.config/gcloud` / `.config/gws` は対象外（AC8） |
| A''. シードせず全グループで再認証 | issue #116 の当初案 | 不採用 | `default` まで再ログインさせる必要がない。分離の目的は「グループ間で混ぜない」ことであって「捨てる」ことではない |

## ドメイン用語

| 用語 | 意味 |
|---|---|
| アカウントグループ | 使用する Google / AWS アカウントの単位。`DEVBASE_ACCOUNT_GROUP` で宣言する（`default` / `kkg` / `with`） |
| 共通ボリューム | `devbase_home_ubuntu` → `/persistent/ai`。全グループ共有（分類 A） |
| グループボリューム | `devbase_home_<group>` → `/persistent/group`。グループ単位（分類 B） |
| 分類 A / B / C | A=全グループ共通、B=グループ別、C=永続化せず env から毎回復元 |

## 永続化対象の分類

issue #116 の「検討が必要な点」3 件は次のとおり決定した。

| 対象 | 分類 | 決定の理由 |
|---|---|---|
| `.claude.json` | **B** | `oauthAccount` を持ち `.credentials.json` と対になる。片方だけ分けるとログイン状態の表示と実体がずれる |
| `.claude/.credentials.json` | **B** | `mcpOAuth`（Google Drive / Slack / Notion×3 / Atlassian）が各 SaaS の企業テナントに紐づく。本体 OAuth も重複するが、実害はグループごとの初回 1 回のログインのみ |
| `.claude/history.jsonl`, `.claude/file-history` | **B** | 会話履歴に顧客情報が入りうる |
| `.gemini` | **B** | `security.auth.selectedType = vertex-ai` で GCP プロジェクトに紐づく |
| `.config/gcloud`, `.config/gws` | **B**（新規追加） | 問題1の本体 |
| `.claude/plugins`, `.claude/skills`, `.claude/commands`, `.claude/CLAUDE.md`, `.claude/settings.json` | **A** | 契約やテナントに紐づかない共通資産。238MB を重複させない |
| `.codex`, `.kiro`, `.serena`, `share` | **A** | Codex は ChatGPT アカウント、Kiro は AWS 側（env 由来）で分離済み |
| `.ssh` | **A**（現状維持） | entrypoint は `.ssh` を参照しておらず、git 認証は `GIT_CREDENTIALS_BASE64` / `GH_TOKEN` で完結している。企業テナントの境界になっていない。必要になれば配列間の 1 行移動で B へ移せる |
| `.aws`, `.git-credentials`, `.gitconfig` | **C** | env から毎回復元（現行どおり） |

## 不変条件

- 分類 A のエントリは、どのグループのコンテナから見ても `/persistent/ai` 配下の**同一実体**を指す。
- 分類 B のエントリは、異なるグループのコンテナから**互いに到達できない**。
- グループ名が未指定でも起動できる（`default` へフォールバック）。
- `~/.claude` はシンボリックリンクではなく**実ディレクトリ**であり、その配下に A / B 双方への
  シンボリックリンクが並ぶ。

## 互換性

| 対象 | 変更 | 互換性の扱い |
|---|---|---|
| `DEVBASE_ACCOUNT_GROUP` | 新規キー | 追加のみ。未設定は `default` |
| 生成 compose | dev サービスへ `/persistent/group` のマウントが増える | 追加のみ。`devbase up` で再生成される |
| プロジェクトの `compose.yml` | 変更不要 | 前提 4 の自動補完に載る |
| `devbase_home_ubuntu` | **変更しない** | 分類 A のデータはパスも含めてそのまま (`/persistent/ai/.claude/plugins` は移動しない) |
| 分類 B のデータ | 共通 → グループボリュームへ | 現行 `/persistent/ai` に実体があるもの（`.claude.json` / 認証 / 履歴 / `.gemini`）は `default` のみ初回シードで維持（AC8）。`.config/gcloud` / `.config/gws` はシード元が無く、`default` を含む全グループで初回 1 回の再認証が要る |
| スナップショット | 対象ボリュームが 2 系統になる | 既存スナップショットは共通ボリューム分として復元可能。メタデータに対象ボリュームを記録する |

## 修正対象

- `lib/devbase/env/keys.py` — `DEVBASE_ACCOUNT_GROUP` の定義
- `lib/devbase/volume/manager.py` — グループ名の解決・検証、グループボリュームの作成
- `lib/devbase/volume/compose.py` — `/persistent/group` のマウントとボリューム宣言、dev サービスへの env 受け渡し
- `lib/devbase/snapshot/manager.py` — 対象ボリュームの複数化
- `lib/devbase/commands/container.py` — `status` へのグループ表示、`up` 時のグループ解決
- `containers/base/entrypoint.sh` — `AI_SETTINGS` の 2 系統化、入れ子パス対応、初回シード、起動ログ
- `docs/user/container-operations.md` / `docs/user/environment-variables.md` /
  `docs/user/snapshot-guide.md` / `docs/plugin-dev/compose-yml-guidelines.md` /
  `docs/plugin-dev/quickstart.md` / `README.md` / `CHANGELOG.md`
- `tests/volume/`, `tests/snapshot/`, `tests/containers/`

## PR 分割計画

```
release branch: release/PLAN39
base branch:    main
```

| PR # | branch 名 | 概要 | 依存 | 並行可否 |
|---|---|---|---|---|
| 1 | `feature/PLAN39-volume` | `DEVBASE_ACCOUNT_GROUP` の解決・検証とグループボリュームの作成・マウント（Python 側） | なし | ○ |
| 2 | `feature/PLAN39-entrypoint` | `AI_SETTINGS` の 2 系統化、入れ子パス対応、`default` の初回シード | PR1 | × (PR1 merge 後) |
| 3 | `feature/PLAN39-gcloud` | `.config/gcloud` / `.config/gws` を分類 B へ追加（問題1の解消） | PR2 | × (PR2 merge 後) |
| 4 | `feature/PLAN39-observability` | snapshot のグループ対応、`devbase status` 表示、起動ログ、ドキュメント整備 | PR1 | ○ (PR2/PR3 と並行可) |

issue #116 は「Phase 1・2 を入れずに Phase 3 だけを適用すると問題2が顕在化する」として順序固定を求めているが、
**個別 PR の merge 先は `release/PLAN39` であり `main` ではない**ため、この制約は release PR が
まとまって merge されることで自動的に満たされる。PR 内の依存順は上表のとおり守る。

## タスク分解

### Task 1: グループ名の解決と検証（PR1）

- **対象ファイル:** `lib/devbase/env/keys.py`, `lib/devbase/volume/manager.py`, `tests/volume/test_manager_group.py`
- **変更内容:** `resolve_account_group()` と `get_group_volume(group)` を追加する。解決順は
  引数 → `os.environ["DEVBASE_ACCOUNT_GROUP"]`（前提 3 により 3 レベルの解決結果が入っている）→ `default`。
  ボリューム名は `devbase_home_<group>`。次の 3 つを `DevbaseError` で弾く（AC7）。
  (a) `^[a-zA-Z0-9][a-zA-Z0-9._-]*$` に合わないもの（Docker のボリューム名にできない）。
  (b) 予約語 `ubuntu`（`devbase_home_ubuntu` が共通ボリュームと衝突する）。
  (c) `^[0-9]+$` に合う**数字のみの名前**（`devbase_home_<index>` と衝突する。
  `volume/manager.py:58-68,146-157` の `get_volume_for_index` が同じ名前空間を使う。前提 6）。
  (b)(c) は (a) を通過するため、正規表現とは別のチェックとして明示的に持つ。
- **満たす受け入れ条件:** AC5, AC7
- **進め方:** テスト駆動。フォールバック・正常系・拒否ケースを先に固定する。
- **補足:** 未使用の `AI_VOLUME_PREFIX`（前提 6）は本 PR で削除する。用途を与えると
  `devbase_ai_` / `devbase_home_` の 2 系統が並び、命名が説明できなくなるため。

### Task 2: グループボリュームの作成とマウント（PR1）

- **対象ファイル:** `lib/devbase/volume/manager.py`, `lib/devbase/volume/compose.py`,
  `lib/devbase/commands/container.py`, `tests/volume/test_compose_group.py`
- **変更内容:** `ensure_volumes()` でグループボリュームも作成する。`_replace_volumes_for_instance` の
  `replacements` に `/persistent/group` を足し、`_build_volumes_section` で `external: true` として宣言する。
  entrypoint がシード判定に使うため、dev サービスの environment に `DEVBASE_ACCOUNT_GROUP` を載せる。
- **満たす受け入れ条件:** AC3, AC5
- **進め方:** テスト駆動。既存の `/persistent/ai` `/work` 差し替えテストと同じ形で、
  マウント・ボリューム宣言・env の 3 点を検証する。

### Task 3: symlink ループの入れ子パス対応（PR2）

- **対象ファイル:** `containers/base/entrypoint.sh`, `tests/containers/`
- **変更内容:** 前提 5 の 2 つの不具合を先に直す。(a) ホーム側・永続領域側の**双方**で
  `mkdir -p "$(dirname ...)"` を行う。(b) ファイルかディレクトリかの判定を拡張子リスト
  (`*.json` のみ) から改め、`.jsonl` を含む「ファイルとして作るエントリ」を明示的に列挙する。
- **満たす受け入れ条件:** AC6
- **進め方:** テスト駆動。`DEVBASE_ENTRYPOINT_LIB_ONLY=1`（`entrypoint.sh:182`）で関数を source し、
  一時ディレクトリを persistent 相当に見立てて検証する。**base イメージの再ビルドが必要**
  ([[entrypoint-change-needs-rebuild]])。

### Task 4: AI_SETTINGS の 2 系統化と初回シード（PR2）

- **対象ファイル:** `containers/base/entrypoint.sh`, `tests/containers/`
- **変更内容:** `AI_SETTINGS` を `AI_SETTINGS_SHARED`（→ `/persistent/ai`）と
  `AI_SETTINGS_GROUP`（→ `/persistent/group`）に分ける。分類は上表のとおり。
  `~/.claude` を実ディレクトリとして作り（既存の symlink が残っていれば外す）、その配下に
  両系統の symlink を張る。symlink 生成の**前に**、`DEVBASE_ACCOUNT_GROUP` が `default` で
  かつグループ側に実体が無いエントリだけ、`/persistent/ai` から**コピー**して初期化する。
  あわせて、**AI Settings の symlink 生成ブロック全体を GCP credentials の生成
  (`entrypoint.sh:189-222`) より前へ移動する**。前提 8 のとおり現状は credentials 生成が先・symlink が
  後であり、この順序のまま Task 5 で `.config/gcloud` を分類 B に足すと、直前に書かれた
  `~/.config/gcloud/credentials.json` が実ディレクトリごと `rm -rf "$HOME_PATH"` で消され、
  初回起動時にサービスアカウント鍵が欠落する。移動後は symlink 済みの
  `~/.config/gcloud` に対して credentials が書かれ、グループボリュームへ永続化される。
- **満たす受け入れ条件:** AC3, AC4, AC8, AC11
- **進め方:** テスト駆動。シードの冪等性（2 回目は何もしない）と、非 `default` グループで
  シードが走らないこと、および **symlink 生成が GCP credentials 生成より前に実行されること**を
  テストで固定する。

### Task 5: `.config/gcloud` / `.config/gws` の追加（PR3）

- **対象ファイル:** `containers/base/entrypoint.sh`, `docs/user/container-operations.md`
- **変更内容:** `AI_SETTINGS_GROUP` に `.config/gcloud` と `.config/gws` を足す。
  `~/.config` 配下は他のツールも使うため、`~/.config` 自体は実ディレクトリのまま
  個別エントリだけを symlink にする（Task 3 の親ディレクトリ作成と、Task 4 の実行順序入れ替えが前提）。
  順序が入れ替わっていない状態でこの変更だけを入れると前提 8 の事故が起きるため、**PR3 は PR2 の
  merge 後にのみ着手する**（PR 分割計画の依存どおり）。
- **満たす受け入れ条件:** AC1, AC2, AC11, AC12
- **進め方:** 実機検証。`gcloud auth login` → `devbase down` → `up` → `gcloud auth list` で
  再認証が要らないことを確認する。あわせて AC11（初回起動でサービスアカウント鍵が消えないこと）と
  AC12（鍵が未設定のプロファイルへ切り替えたら鍵が残らないこと）も見る。
- **補足 1（出力先）:** env 由来の `credentials.json`（サービスアカウント鍵）が永続領域へ書かれるようになる。
  出力先は `GOOGLE_APPLICATION_CREDENTIALS` 未設定なら `~/.config/gcloud/credentials.json` 固定で
  プロファイル名を含まない (`entrypoint.sh:198-204`) ため、`GCP_ACTIVE_PROFILE` を切り替えても
  同じパスが上書きされ、プロファイルごとのファイルが並ぶことはない。
- **補足 2（未設定プロファイルへ切り替えたときの削除 / 必須）:** 鍵の永続化を入れる以上、
  **未設定は「何もしない」ではなく「消す」** に変える。前提 9 のとおり生成ブロックには鍵が無いときの
  削除経路が無いため、`.config/gcloud` を永続化すると旧プロファイルの鍵がコンテナ再作成後も残り、
  **別グループ・別顧客のサービスアカウントを ADC 経由で使えてしまう**。そこで entrypoint に
  else 経路を足し、次の仕様にする。

  **鍵あり**は現行どおり `GAC_PATH` / `BQ_PATH` へ書き、`chmod 600` して export する（変更なし）。
  **鍵なし**は `GOOGLE_APPLICATION_CREDENTIALS`（以下 GAC）と `BIGQUERY_KEY_FILE`（以下 BQ）を
  **まとめてではなく変数ごとに** `未設定` / `既定`（`$DEFAULT_CREDS_PATH` と一致）/ `カスタム`（それ以外）へ
  分類し、(a) **いずれか一方でも** `未設定` か `既定` なら `rm -f "$DEFAULT_CREDS_PATH"`（消すのは常にこの 1 パスだけ）、
  (b) `既定` だった変数は unset、(c) `カスタム` の変数は unset せずその指す先も削除しない、
  (d) `カスタム` の変数があれば変数ごとに WARN、とする。2 変数 × 3 状態の全 9 通りは次表のとおり。

  | GAC | BQ | `$DEFAULT_CREDS_PATH` | unset する変数 | WARN 対象 |
  |---|---|---|---|---|
  | 未設定 | 未設定 | `rm -f` | — | — |
  | 未設定 | 既定 | `rm -f` | BQ | — |
  | 未設定 | カスタム | `rm -f` | — | BQ |
  | 既定 | 未設定 | `rm -f` | GAC | — |
  | 既定 | 既定 | `rm -f` | GAC, BQ | — |
  | 既定 | カスタム | `rm -f` | GAC | BQ |
  | カスタム | 未設定 | `rm -f` | — | GAC |
  | カスタム | 既定 | `rm -f` | BQ | GAC |
  | カスタム | カスタム | 削除しない | — | GAC, BQ |

  削除したときはその旨を、WARN 対象には「`<変数名>` が devbase 管理外の `<パス>` を指しているため削除しない。
  プロファイル切替時に旧い鍵が残る可能性があるため、必要なら手動で削除すること」を起動ログへ出し、
  いずれの場合も起動は続行する。

  **削除先を env の変数値ではなく固定パス `DEFAULT_CREDS_PATH` (`entrypoint.sh:198`) に限る**理由は
  前提 10 のとおり。カスタムパスは利用者の管理下でありうるので devbase が所有を主張できず、
  安全側（消さない）に倒して WARN で知らせる。片方がカスタムでももう片方が `未設定` / `既定` なら
  既定パスの旧い鍵は規則 (a) で確実に消えるため、混在ケースでも AC12 の分離要件は保たれる。両方カスタムの
  ときだけ削除対象が無く混線の危険が WARN のみで残るので、確実に断ちたい場合は両変数を `env` から
  外して既定パスへ戻せば削除対象になる旨を `docs/user/container-operations.md` に明記する。

  検討したが採らなかった案: 所有マーカー（例 `.devbase-managed`）に記録したパスならカスタムでも削除する。
  マーカーが永続ボリュームに残る追加状態となり、削除の安全性がその健全性に依存するため不採用。

  ディレクトリごとの `rm -rf` は行わない。`gcloud auth login` のユーザー OAuth は
  `credentials.db` / `access_tokens.db` / `application_default_credentials.json` という別ファイルなので
  影響を受けず、AC1 / AC2 は成立し続ける（既定パスに限った削除は、前提 9 に挙げた他の認証と同じ
  「古い実体を残さない」方針の踏襲である）。切替テストは AC12 として実機で確認し、`tests/containers/` に
  `DEVBASE_ENTRYPOINT_LIB_ONLY` (`entrypoint.sh:182-184`) を使った単体テストを足す（上表の 9 通りを網羅する）。

### Task 6: スナップショットのグループ対応（PR4）

- **対象ファイル:** `lib/devbase/snapshot/manager.py`, `tests/snapshot/`
- **変更内容:** `VOLUME_NAME` 固定（前提 7）を改め、共通ボリュームと解決されたグループボリュームの
  両方を対象にする。メタデータ (`snapshot.yml`) に対象ボリューム名を記録し、既存スナップショット
  （`volume: devbase_home_ubuntu` のみ）も復元できるようにする。
- **満たす受け入れ条件:** AC9
- **進め方:** テスト駆動。旧メタデータの読み込み互換を先にテストで固定する。

### Task 7: 可視化とドキュメント（PR4）

- **対象ファイル:** `lib/devbase/commands/container.py`, `containers/base/entrypoint.sh`,
  `docs/`, `README.md`, `CHANGELOG.md`
- **変更内容:** `devbase status` に解決されたグループを表示する。entrypoint の起動時に
  グループと `gcloud config get account` の結果を 1 行ログ出力する。`entrypoint.sh` は
  `set -e`（`containers/base/entrypoint.sh:3`）で動くため、未ログイン時に `gcloud` が非 0 を返しても
  起動が落ちないよう `$(gcloud config get account 2>/dev/null || echo "unset")` でフォールバックする。ボリューム構造の表
  （`container-operations.md` / `compose-yml-guidelines.md` / `quickstart.md`）と
  `environment-variables.md` の `DEVBASE_ACCOUNT_GROUP`、`snapshot-guide.md` の対象ボリュームを更新する。
- **満たす受け入れ条件:** AC10
- **進め方:** 表示とログは実機確認。ドキュメントは文書のみ。

## 影響範囲

- 全プロジェクトの生成 compose（`devbase up` のたびに再生成されるため移行作業は不要）。
- ディスク使用量: グループ数 × 分類 B のサイズ。実測の分類 B は `.config/gcloud` 3.9MB +
  `.config/gws` 2.9MB + `.claude.json` / 認証 / 履歴で数十 MB 程度。238MB の `plugins` は
  共通側に残るため増えない。
- entrypoint 変更のため base イメージの再ビルドが必要（Task 3・4・5・7）。
- スナップショットの世代管理の粒度が変わる（対象が 2 ボリュームになる）。

## リスクと対処

| リスク | 対処 |
|---|---|
| 入れ子パス対応の不備で `~/.claude` 配下が壊れ、Claude Code が起動しなくなる | Task 3 を Task 4 より先に、単独で検証する。AC6 で `history.jsonl` と `.credentials.json` を名指しで確認 |
| 初回シードが非 `default` グループでも走り、分離の意味が失われる | Task 4 でグループ名のガードをテストに固定。AC3 で実機確認 |
| グループ名が既存ボリューム名と衝突する（`ubuntu` は `devbase_home_ubuntu`、数字のみは `devbase_home_<index>`） | Task 1 で正規表現とは別の明示チェックとして両方を拒否。AC7 |
| `~/.config` 全体を symlink にしてしまい、他ツールの設定を巻き込む | Task 5 で `~/.config` は実ディレクトリのまま個別エントリのみ symlink |
| entrypoint 変更が `up` だけでは反映されない | [[entrypoint-change-needs-rebuild]]。検証手順に `devbase build --no-cache` を明記 |
| 既存スナップショットが復元できなくなる | Task 6 で旧メタデータ互換をテストで固定 |
| symlink 生成より前に書かれた `~/.config/gcloud/credentials.json` が `rm -rf` で消え、サービスアカウント鍵が欠落する（前提 8） | Task 4 で symlink ブロックを credentials 生成より前へ移動。AC11 で初回起動時の鍵の存在を確認 |
| 鍵が未設定のプロファイルへ `GCP_ACTIVE_PROFILE` を切り替えると、生成がスキップされ (`entrypoint.sh:196`) 旧プロファイルの `credentials.json` がグループボリュームに残り、固定パスを指したままの `GOOGLE_APPLICATION_CREDENTIALS`（前提 9）から別顧客の鍵が使われる | Task 5 補足 2 の判定表で、鍵なし時は 2 変数を個別に判定し、いずれかが未設定または既定パスなら固定パス `DEFAULT_CREDS_PATH` (`entrypoint.sh:198`) のみを `rm -f` して既定パス側の変数を unset する。AC12 で切替テストを実機確認し、`tests/containers/` にも固定する |
| 削除仕様を「変数が指す先を消す」と実装すると、`GOOGLE_APPLICATION_CREDENTIALS` / `BIGQUERY_KEY_FILE` はプロジェクトの `env` から任意パスへ上書きできる（前提 10）ため、ホストからマウントした鍵など devbase 管理外のファイルを消しうる | 削除先を env の値ではなく固定パス `DEFAULT_CREDS_PATH` に限定する。カスタムパスを指す変数だけは削除も unset もせず WARN を出し、もう片方が未設定 / 既定なら既定パスは消す。AC12 (5)(6) で「カスタムパスが残ること」「混在時も既定パスは消えること」をテストで固定する |
| 切り戻し時に、シード後にグループ側だけへ書かれた認証・履歴が失われる | 切り戻し手順の同期ステップを必須とし、正とするグループを 1 つに決めてから実行する |

## 切り戻し手順

初回シードは**その時点のコピー**であり、稼働開始後の認証更新（トークンのリフレッシュ、MCP の
再認可）と会話履歴は**グループボリューム側にしか書かれない**。したがって revert だけでは
`/persistent/ai` は**シード時点の状態**に戻る。次の順で行う。

1. **同期（revert より前に必ず行う）** — 正とするグループ（通常は `default`）のコンテナを
   `devbase down` で止めたうえで、グループボリュームの分類 B を共通ボリュームへ書き戻す。
   `devbase down` でコンテナは削除されるため、以降はコンテナ経由（`docker cp`）ではなく
   **ボリュームを一時コンテナへ直接マウントして**操作する。

   ```bash
   GROUP=default   # 正とするグループ名

   docker run --rm -v "devbase_home_${GROUP}:/from" -v devbase_home_ubuntu:/to alpine \
     sh -c 'for p in .claude.json .claude .gemini; do
              if [ ! -e "/from/$p" ]; then echo "skip (未作成): $p"; continue; fi
              if [ -d "/from/$p" ]; then
                mkdir -p "/to/$p" && cp -a "/from/$p/." "/to/$p/"
              else
                cp -a "/from/$p" "/to/$p"
              fi
              echo "copied: $p"
            done'
   ```

   グループ内で一度も使っていないツールのエントリは存在しないことがあるため、各パスの存在を
   確認してから `cp` し、無いものは `skip` として飛ばす（`&&` で連結すると 1 件目の欠落で
   以降の同期が止まる）。

   対象は分類 B のうち共通側に対応物があるものに限る。`.config/gcloud` / `.config/gws` は
   共通ボリュームに置き場が無く、revert 後は永続化対象外（現行 main と同じ）へ戻るため書き戻さない。
   保全したい場合は、同じくボリュームを直接マウントしてカレントディレクトリへ tar で退避する。

   ```bash
   GROUP=default

   docker run --rm -e GROUP="$GROUP" \
     -v "devbase_home_${GROUP}:/from" -v "$PWD:/backup" alpine \
     sh -c 'cd /from || exit 1
            set --
            for p in .config/gcloud .config/gws; do
              if [ -e "$p" ]; then set -- "$@" "$p"; else echo "skip (未作成): $p"; fi
            done
            [ "$#" -gt 0 ] || { echo "退避対象なし"; exit 0; }
            tar cf "/backup/devbase-${GROUP}-config.tar" "$@" && echo "saved: devbase-${GROUP}-config.tar"'
   ```

   一時コンテナは root で動くため、Linux ホストでは生成された tar が root 所有になる。
   必要なら `sudo chown "$(id -u):$(id -g)" devbase-<group>-config.tar` で引き取る。
2. **検証** — `docker run --rm -v devbase_home_ubuntu:/v alpine ls -l /v/.claude /v/.claude.json` で、
   `.credentials.json` と `history.jsonl` が**ファイルとして**存在しサイズが 0 でないこと、
   `.claude/plugins` が壊れていないことを確認する。
3. **競合時の扱い** — 共通ボリュームへ書き戻せるのは**1 グループ分だけ**で、後に書いた方が勝つ。
   複数グループを運用していた場合は、**どのグループを正とするかを先に決めて手順 1 を 1 回だけ実行する**。
   他グループのデータは `devbase_home_<group>` に残るので、後から必要になれば対象を変えて再実行できる。
4. **revert** — コード変更を revert し、`devbase build --no-cache` と `devbase up` で再生成する。
   `AI_SETTINGS` は元の 1 系統に戻り `/persistent/ai` 配下を参照する。
5. **後片付け** — 不要になったグループボリュームは `docker volume rm devbase_home_<group>` で削除する。
   そのボリュームをマウントしたコンテナが残っていると `volume is in use` で失敗するため、
   対象グループのコンテナを先に `devbase down` で削除しておく。

手順 1 を省いて revert だけを行った場合も**起動はする**が、`default` はシード時点の認証・履歴で
立ち上がり、それ以降のログイン更新と会話履歴は失われる。急ぎで戻すときの許容ラインとして、
この差を承知したうえで選ぶこと。

## 完了の定義

- [ ] AC1〜AC12 を満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run pytest` が green
- [ ] 個別 PR がすべて `/ndf:cross-review` で APPROVE 収束済み
- [ ] `devbase build --no-cache` 後の実機で、`default` と非 `default` の 2 グループを起動して
      AC1〜AC4 / AC8 / AC11 / AC12 を確認している
- [ ] `docs/` と `CHANGELOG.md` が新しいボリューム構造と `DEVBASE_ACCOUNT_GROUP` を説明している
