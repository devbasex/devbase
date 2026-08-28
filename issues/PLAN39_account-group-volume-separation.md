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
- [ ] AC7: Docker のボリューム名にできないグループ名と、予約語 `ubuntu`（`devbase_home_ubuntu` と衝突する）
      を**起動前に拒否**し、理由の分かるエラーを出す。
- [ ] AC8: `default` グループでは、既存の Claude Code / gcloud の認証が**初回シードにより維持**され、
      再ログインが発生しない。検証: 現行環境で `up` 後に `claude` が未ログイン状態にならないこと。
- [ ] AC9: スナップショットが共通・グループ両方のボリュームを対象にし、復元できる。
- [ ] AC10: `devbase status` に解決されたアカウントグループが表示される。

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
|---|---|---|---|
| **A. 共通ボリューム + グループボリュームの二層** | `/persistent/ai` は現行のまま、`/persistent/group` に `devbase_home_<group>` を追加マウント | **採用** | 共通資産（plugins 238MB 等）を重複させずに認証だけ分離できる。既存 `devbase_home_ubuntu` を触らないので分類 A のデータ移行が不要 |
| B. ディレクトリを丸ごとグループ別ボリュームへ | `~/.claude` ごと `devbase_home_<group>` に置く | 不採用 | `plugins` / `skills` / `commands` / グローバル `CLAUDE.md` までグループ数だけ複製され二重管理になる。粒度が粗すぎる |
| C. 環境変数から毎回復元（AWS 方式） | `GCLOUD_CREDENTIALS_BASE64` のようなキーを増やす | 不採用 | gcloud のユーザー OAuth は `credentials.db` / `access_tokens.db` を含む可変の状態で、リフレッシュのたびに更新される。env へ書き戻す経路が無い |
| D. グループ別ボリューム 1 本だけにする（共通ボリュームを廃止） | 全部を `devbase_home_<group>` へ | 不採用 | B と同じ重複問題に加え、既存 `devbase_home_ubuntu` からの全データ移行が必要になる |
| **A'. `default` グループの初回シード** | グループボリュームが空なら `/persistent/ai` の分類 B 相当を**コピー**して初期化（`default` のみ） | **採用** | 現行 14 コンテナの大半を占める `default` で再ログインを避けられる。move ではなく copy なので切り戻し時に元データが残る |
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
| 分類 B のデータ | 共通 → グループボリュームへ | `default` は初回シードで維持（AC8）。新規グループは初回のみ再認証 |
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
  ボリューム名は `devbase_home_<group>`。`^[a-zA-Z0-9][a-zA-Z0-9._-]*$` に合わないもの、および
  予約語 `ubuntu` は `DevbaseError` で弾く（前提 6 の命名衝突。`devbase_home_ubuntu` は共通ボリューム）。
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
- **満たす受け入れ条件:** AC3, AC4, AC8
- **進め方:** テスト駆動。シードの冪等性（2 回目は何もしない）と、非 `default` グループで
  シードが走らないことをテストで固定する。

### Task 5: `.config/gcloud` / `.config/gws` の追加（PR3）

- **対象ファイル:** `containers/base/entrypoint.sh`, `docs/user/container-operations.md`
- **変更内容:** `AI_SETTINGS_GROUP` に `.config/gcloud` と `.config/gws` を足す。
  `~/.config` 配下は他のツールも使うため、`~/.config` 自体は実ディレクトリのまま
  個別エントリだけを symlink にする（Task 3 の親ディレクトリ作成が前提）。
- **満たす受け入れ条件:** AC1, AC2
- **進め方:** 実機検証。`gcloud auth login` → `devbase down` → `up` → `gcloud auth list` で
  再認証が要らないことを確認する。
- **補足:** env 由来の `credentials.json`（サービスアカウント鍵）が永続領域へ書かれるようになる。
  起動のたびに上書きされるが、`GCP_ACTIVE_PROFILE` を切り替えると旧プロファイルのファイルが
  残る。ドキュメントに明記する。

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
  グループと `gcloud config get account` の結果を 1 行ログ出力する。ボリューム構造の表
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
| グループ名が既存ボリューム名と衝突する（`ubuntu`、数字のみ） | Task 1 で拒否。AC7 |
| `~/.config` 全体を symlink にしてしまい、他ツールの設定を巻き込む | Task 5 で `~/.config` は実ディレクトリのまま個別エントリのみ symlink |
| entrypoint 変更が `up` だけでは反映されない | [[entrypoint-change-needs-rebuild]]。検証手順に `devbase build --no-cache` を明記 |
| 既存スナップショットが復元できなくなる | Task 6 で旧メタデータ互換をテストで固定 |
| `GCP_ACTIVE_PROFILE` 切替時に旧プロファイルの `credentials.json` が永続領域に残る | Task 5 の補足としてドキュメント化。削除は運用手順に委ねる |

## 切り戻し手順

- コード変更を revert し、`devbase build --no-cache` と `devbase up` で再生成すれば、
  `AI_SETTINGS` は元の 1 系統に戻り `/persistent/ai` 配下を参照する。
- 分類 B のデータは `default` グループへ**コピー**しているだけで `/persistent/ai` 側の元データを
  消さないため、`default` は revert 後もそのまま動く。
- 非 `default` グループで作られた認証は `devbase_home_<group>` に残る。不要なら
  `docker volume rm devbase_home_<group>` で削除する。

## 完了の定義

- [ ] AC1〜AC10 を満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run pytest` が green
- [ ] 個別 PR がすべて `/ndf:cross-review` で APPROVE 収束済み
- [ ] `devbase build --no-cache` 後の実機で、`default` と非 `default` の 2 グループを起動して
      AC1〜AC4 / AC8 を確認している
- [ ] `docs/` と `CHANGELOG.md` が新しいボリューム構造と `DEVBASE_ACCOUNT_GROUP` を説明している
