# PLAN39: 永続化ボリュームをアカウントグループ単位に分離する

## 関連リンク

- issue: [#116](https://github.com/devbasex/devbase/issues/116)（背景・実機調査の全文）
- 参考: `docs/user/container-operations.md`（ボリューム構造・AI 設定の永続化）、`containers/base/entrypoint.sh`（symlink 機構）、`docs/plugin-dev/compose-yml-guidelines.md`（プロジェクト compose の書き方）
- 一次情報（前提 8〜13 の根拠）:
  - [Managing gcloud CLI configurations](https://docs.cloud.google.com/sdk/docs/configurations) — `CLOUDSDK_CONFIG` で設定ディレクトリを差し替えられる
  - [Application Default Credentials](https://docs.cloud.google.com/docs/authentication/application-default-credentials) — ADC の探索順と、ローカル開発では SA 鍵ではなく `gcloud auth application-default login` を推奨する旨
  - [Scripting gcloud CLI commands](https://docs.cloud.google.com/sdk/docs/scripting-gcloud) — "Parallel execution of multiple gcloud CLI commands is not supported."
  - [googleworkspace/cli README](https://github.com/googleworkspace/cli/blob/main/README.md) — `GOOGLE_WORKSPACE_CLI_CONFIG_DIR`
  - [google-gemini/gemini-cli#1825](https://github.com/google-gemini/gemini-cli/issues/1825) — 設定ディレクトリを可変にする要望。2026-05-06 に「当面対応予定なし」としてクローズ

## モード

`architecture` — 永続化レイヤを二層化し、公開設定キー (`DEVBASE_ACCOUNT_GROUP` / `GCP_AUTH_MODE`) を増やす。
データの置き場所が変わるため後戻りが安くなく、`volume` / `snapshot` / `entrypoint` を横断する。
issue #116 が `standard` 相当の Phase 分割で書かれていても、判断の粒度はこちらに合わせる。

## 目的と非目的

達成したい状態:

- `gcloud auth login` / `gws auth login` のユーザー OAuth が、**コンテナを作り直しても保たれる**（問題1）。
- その認証が**アカウントグループをまたいで共有されない**。nyle.co.jp で認証した gcloud を
  kk-generation.com のプロジェクトが引き継がない（問題2）。
- Claude Code の MCP OAuth トークンと Gemini の `vertex-ai` 設定が**グループ単位に分かれる**（問題3。すでに混線している）。
- サービスアカウント鍵に依存せず、**ユーザー認証（ADC）を既定の経路にできる**。鍵が要る場面は
  `GCP_AUTH_MODE` で切り替えられる。
- 新しいアカウントグループを足す人が、**手順書だけを見て Google 認証を完了できる**。
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
- 前提 8: gcloud は設定ディレクトリの場所を **`CLOUDSDK_CONFIG` で差し替えられる**。
  `google/auth/_cloud_sdk.py:45-59` の `get_config_path()` が `os.environ[CLOUD_SDK_CONFIG_DIR]` を
  最優先で返し（`environment_vars.py:41` で `CLOUD_SDK_CONFIG_DIR = "CLOUDSDK_CONFIG"`）、
  同 `73-82` の `get_application_default_credentials_path()` は「その config path +
  `application_default_credentials.json`」なので、**ADC ファイルも一緒に移動する**。これは gcloud CLI
  専用の実装ではなく client library と同じ `google.auth` なので、BigQuery 等のクライアントも同じ場所を見る。
  実機確認: `CLOUDSDK_CONFIG=<dir> gcloud info --format='yaml(config.paths)'`（gcloud 569.0.0）で
  `global_config_dir` / `active_config_path` が指定先へ切り替わり、認証情報を持たない新しいディレクトリが作られる。
- 前提 9: **名前付き configuration では分離できない。** 設定ディレクトリ直下にあるのは
  `credentials.db` / `access_tokens.db` / `legacy_credentials/` / `application_default_credentials.json` の
  **1 セット**で、構成ごとに分かれるのは `configurations/` だけである。`--configuration` /
  `CLOUDSDK_ACTIVE_CONFIG_NAME` は「どれを使うか」を選ぶだけで認証情報自体は共有されるため、
  グループ分離には**設定ディレクトリを分ける**しかない。
- 前提 10: ADC の探索順は `GOOGLE_APPLICATION_CREDENTIALS` → `application_default_credentials.json`
  → メタデータサーバである。**変数が存在しないファイルを指していると ADC は例外で落ち、ユーザー認証へ
  フォールバックしない**。実機確認:
  `GOOGLE_APPLICATION_CREDENTIALS=/nonexistent/key.json python3 -c "import google.auth; google.auth.default()"`
  → `DefaultCredentialsError: File /nonexistent/key.json was not found.`
  したがって鍵を使わないモードでは、この変数を**未設定にする**必要がある（空文字でも
  `_default.py:349` の `explicit_file != ""` を通らないので可だが、unset で統一する）。
- 前提 11: `GOOGLE_APPLICATION_CREDENTIALS` と `BIGQUERY_KEY_FILE` は、**鍵の有無に関係なく**
  `/home/ubuntu/.config/gcloud/credentials.json` 固定で env に書かれる
  (`lib/devbase/env/collectors/google.py:139-141` の `_collect_common_settings` は、プロファイルが
  1 件も見つからない経路 (`同 87`) からも無条件に呼ばれる)。値はプロジェクトの `env` から任意パスへ
  上書きもできる (`bin/devbase:61,338` / `lib/devbase/commands/container.py:295-418` /
  `lib/devbase/env/runtime.py:112-134`)。entrypoint も変数があればそちらを優先する
  (`containers/base/entrypoint.sh:204,213`)。
- 前提 12: gws も設定ディレクトリを env で差し替えられる（公式 README の
  `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` — "Override config directory (default: `~/.config/gws`)"）。
  一方 **Gemini CLI には同等の env が無い**。設定ディレクトリを可変にする要望は
  google-gemini/gemini-cli#2815 → #1825 に集約され、#1825 は 2026-05-06 に「当面対応予定なし」として
  クローズされている。したがって `.gemini` と `.claude*` は symlink 方式のままとする。
- 前提 13: **gcloud は並行実行を想定していない。** 公式ドキュメント（Scripting gcloud CLI commands）に
  "Parallel execution of multiple gcloud CLI commands is not supported." とあり、`credentials.db` は
  SQLite なので、同一グループの複数コンテナが同時に gcloud を叩くと `database is locked` が出うる。
  これは設定ディレクトリの置き方によらず「グループボリュームを同グループの全コンテナで共有する」
  設計に内在するもので、symlink 方式でも同じである。
- 前提 14: entrypoint の実行順序は「GCP credentials の生成 (`containers/base/entrypoint.sh:189-222`)」→
  「AI Settings の symlink 生成 (`同 383-440`)」で、symlink ループはホーム側の既存実体を
  `rm -rf "$HOME_PATH"` (`同 420`) で消してから `ln -s` する。`~/.config/gcloud` を symlink 対象に
  **しない**本プランでは両者は衝突しないが、`~` 直下の生成物を将来 symlink 対象へ加えるときは
  この順序が効く（AC11 はその退行を見る）。
- 前提 15: **この環境では `gcloud auth login` は自動でブラウザ非起動フローになる**（実機検証済み。
  `carmo-ai-dev-1` / gcloud 582.0.0）。`check_browser.ShouldLaunchBrowser()` は Linux で
  `DISPLAY` / `WAYLAND_DISPLAY` / `MIR_SOCKET` がどれも無ければ False を返す
  (`googlecloudsdk/command_lib/util/check_browser.py:36-66`)。コンテナ内で実行すると
  `ShouldLaunchBrowser(True) = False`（`DISPLAY` は空。`xdg-open` は存在するが判定に使われない）。
  この場合 `api_lib/auth/util.py:355-365` の `elif not can_launch_browser:` へ入り、Google 所有の
  クライアント ID では `RemoteLoginWithAuthProxyFlowRunner`（= `--no-launch-browser` と同じ実装）が
  選ばれる。**フラグを付けなくても「URL を貼って認証コードを戻す」フローになる。**
  VS Code のポート転送の有無は関係しない（判定材料が `DISPLAY` であってポート到達性ではないため）。
  各経路の違いは次のとおり（`gcloud auth login --help`）。

  | 経路 | 手元に必要なもの | 受け渡すもの |
  |---|---|---|
  | 既定（この環境では下段と同じ挙動になる） | 別マシンのブラウザ | URL を渡し、**認証コード**を貼り戻す |
  | `--no-launch-browser` | 別マシンのブラウザのみ | 同上 |
  | `--no-browser` | 別マシンの**ブラウザ + gcloud 372.0 以上** | 生成コマンドを実行し、**長い URL** を貼り戻す |

- 前提 16: **`gcloud auth login --update-adc` は `gcloud auth application-default login` と等価ではない。**
  前者は既定で `add_quota_project=False` のまま `ADC(creds).DumpADCToFile()` を呼ぶ
  (`command_lib/auth/auth_util.py:197-220`) のに対し、後者は `DumpADCOptionalQuotaProject(creds)` を
  呼ぶ (`surface/auth/application_default/login.py:296`)。つまり **`--update-adc` では quota project が
  ADC に書かれない**。quota project を要する API を使うなら `gcloud auth application-default login` を
  別に実行する。また `WriteGcloudCredentialsToADC` は `PromptIfADCEnvVarIsSet()` を呼び、
  `GOOGLE_APPLICATION_CREDENTIALS` が設定されていると「Credentials will still be generated to the
  default location / To use these credentials, unset this environment variable before running your
  application」と警告する (`同 174-190`)。`adc` モードでこの変数を unset する本プランの判断と一致する。

- 前提 17: **gws はベースイメージに入っておらず、現在どのコンテナにも存在しない**（実機確認）。
  `containers/base/Dockerfile` に `gws` / `googleworkspace` の記述は無く、npm グローバル導入は
  `同 138` の `npm i -g yarn @playwright/test aws-cdk aws-cdk-lib typescript @google/gemini-cli @openai/codex`
  1 行のみで `@googleworkspace/cli` を含まない。稼働中の dev コンテナ 14 本すべてで `command -v gws` が
  空、`~/.config/gws` も存在しない。issue #116 が計測した 2.9MB は、調査対象コンテナ
  (`eef62d0d42cb`) ごと失われている（`docker ps -a` に無い）。
  したがって設定ディレクトリを永続化するだけでは gws は復旧しないため、
  **本プランで `@googleworkspace/cli` をベースイメージへ含める**（Task 5）。
  npm 上のパッケージは `@googleworkspace/cli`（確認時 0.22.5、`bin` は `gws`）。

- 前提 18: 空の named volume は **root 所有**で作られ、uid 1000 では書き込めない（実機確認:
  `docker run --rm -u 1000:1000 -v <空volume>:/v alpine touch /v/x` → `Permission denied`）。
  グループボリュームを `CLOUDSDK_CONFIG` の向き先にする以上、**export の前に `chown` が要る**。
  SQLite 自体は named volume 上で正常に動く（同じく実機で `create table` / `insert` を確認）ので
  `credentials.db` の置き場としては問題ない（並行実行は前提 13 の別件）。

- 前提 21: **entrypoint の `export` / `unset` は `docker exec` のシェルに届かない。**
  コンテナの環境変数はホスト側（生成 compose の `environment:` と `env_file`）が決めるもので、
  entrypoint が変更できるのは自分の子プロセス（`exec "$@"` で起動する PID 1 の子孫）だけである。
  実機確認: `docker exec carmo-ai-dev-1 sh -c 'echo $GOOGLE_APPLICATION_CREDENTIALS'` は
  entrypoint の外側の値をそのまま返す。したがって `CLOUDSDK_CONFIG` の設定も、
  `adc` モードでの 2 変数の除去も、**ホスト側で行う必要がある**（AC12 は `docker exec` で
  検証する条件なので、entrypoint だけでは満たせない）。

- 前提 20: **`~/.claude` の子要素は 30 件あり、プランが分類表で名指ししているのは 7 件だけ**
  （実機 `carmo-ai-dev-1` で確認。`.credentials.json` / `.last-cleanup` /
  `.last-update-result.json` / `.ndf-retention-checked` / `.ndf-retention.lock` /
  `.ndf-statusline-backup.json` / `.ndf-statusline.lock` / `CLAUDE.md` / `backups` /
  `cache` / `commands` / `daemon` / `daemon.log` / `debug` / `file-history` /
  `history.jsonl` / `ide` / `jobs` / `logs` / `mcp-needs-auth-cache.json` /
  `ndf-statusline.sh` / `paste-cache` / `plugins` / `projects` / `session-env` /
  `sessions` / `settings.json` / `shell-snapshots` / `skills` / `tasks`）。
  容量は `projects` 1.1GB・`plugins` 222MB・`file-history` 76MB・`session-env` 22MB・
  `jobs` 18MB で、`.claude` 全体は 1.5GB。`projects` は Claude Code の会話ログ実体であり、
  分類表が `history.jsonl` を B とした理由（顧客情報が入りうる）がそのまま当てはまる。
  Claude Code は版が上がるたびに新しい子ディレクトリを作るため、**永続化するエントリを
  列挙する方式では列挙漏れが黙って揮発する**。

- 前提 19: ADC の解決を実機（`carmo-ai-dev-1`、gcloud 同梱の `google.auth`）で確認した結果:
  (1) 鍵ありの現状は SA credentials が解決される（project `nyle-carmo-analysis`）、
  (2) `GOOGLE_APPLICATION_CREDENTIALS` が存在しないパスを指すと
  `DefaultCredentialsError: File ... was not found.`（フォールバックしない。前提 10 の再確認）、
  (3) 2 変数を unset し ADC ファイルも無いと `Your default credentials were not found.`。
  (3) が `adc` モードで**まだログインしていない**ときの正常な状態であり、手順書の出発点になる。

## 受け入れ条件

- [ ] AC1: 同じグループのコンテナで `devbase down` → `devbase up` の後、`gcloud auth list` が
      **再認証なしで**同じ active account を返す。
- [ ] AC2: `gws` がベースイメージに含まれ（`command -v gws` が通り）、同条件で認証済みコマンドが再認証なしで通る（`$GOOGLE_WORKSPACE_CLI_CONFIG_DIR` 配下の
      `credentials.enc` と `.encryption_key` が保たれる）。
- [ ] AC3: 異なるグループのコンテナが互いの認証を参照しない。検証: `kkg` グループのコンテナで
      `gcloud auth list` / `claude mcp list` を実行し、`default` グループの認証が見えないこと。
- [ ] AC4: 共通資産が重複しない。検証: 2 グループのコンテナで `readlink -f ~/.claude/plugins` が
      **同一の `/persistent/ai/.claude/plugins`** を指すこと。
- [ ] AC5: `DEVBASE_ACCOUNT_GROUP` 未設定のプロジェクトが `default` にフォールバックし、
      これまでどおり起動する。検証: 既存プロジェクトを `up` して entrypoint がエラーを出さないこと。
- [ ] AC6: 入れ子パスの symlink が正しく張られる。検証: `~/.claude/CLAUDE.md` と
      `~/.claude/settings.json` が**壊れていない**（実体に到達できる）symlink であり、かつ
      **ファイル**であること。`~/.claude/.credentials.json` に書き込めること、
      `~/.claude/history.jsonl` が**ディレクトリでない**こと（前提 5 の退行を防ぐ）。
      当初は `.credentials.json` / `history.jsonl` 自体を symlink にする想定だったが、
      不変条件の反転（既定をグループ側へ）により両者はグループボリューム上の実ファイルになる。
      入れ子 symlink として残るのは分類 A の 5 件で、うち `CLAUDE.md` / `settings.json` が
      「親ディレクトリが無い入れ子のファイルエントリ」という前提 5 と同じ条件を満たす。
      あわせて、Dockerfile が焼き込む `~/.claude/settings.json`（hooks 設定）は symlink 張り替えの
      `rm -rf` で消えるため、**張る前に共通側へ退避**する。退避しないと `/persistent/ai` に
      空ファイルだけが残り、hooks が初回起動で失われる（既存 main からの挙動を修正）。
- [ ] AC7: Docker のボリューム名にできないグループ名、予約語 `ubuntu`（`devbase_home_ubuntu` と衝突する）、
      および**数字のみの名前**（`devbase_home_<index>` と衝突する。前提 6）を**起動前に拒否**し、
      理由の分かるエラーを出す。
- [ ] AC8: `default` グループでは、**現行 `/persistent/ai` に実体がある**分類 B のデータ
      （`.claude.json` / `.claude/.credentials.json` / 履歴 / `.gemini`）が**初回シードにより維持**され、
      Claude Code の再ログインが発生しない。検証: 現行環境で `up` 後に `claude` が未ログイン状態にならないこと。
      gcloud / gws は前提 1 のとおり現在どのボリュームにも無く**シード元が存在しない**ため、
      `default` を含む**全グループで初回 1 回だけ `gcloud auth login` / `gws auth login` が必要**である。
      これは AC8 の違反としない（AC1 / AC2 はその初回ログイン**以降**の維持を見る条件である）。
- [ ] AC9: スナップショットが共通・グループ両方のボリュームを対象にし、復元できる。
- [ ] AC10: `devbase status` に解決されたアカウントグループが表示される。
- [ ] AC11: 鍵モードで起動したとき、従来どおりサービスアカウント鍵が使える。
      検証: `GCP_CREDENTIALS_BASE64__<profile>` を設定して `devbase up` した直後に
      `$GOOGLE_APPLICATION_CREDENTIALS` のファイルが存在し中身が空でないこと（前提 14 の退行を防ぐ）。
- [ ] AC12: **認証モードを任意に切り替えられる。** 検証:
      (1) `GCP_AUTH_MODE=adc` のプロジェクトで `up` し、コンテナ内で
      `GOOGLE_APPLICATION_CREDENTIALS` と `BIGQUERY_KEY_FILE` が**未設定**であること、
      `gcloud auth application-default login` 済みのユーザー認証で `google.auth.default()` が通ること。
      (2) 同じプロジェクトの `env` へ `GCP_AUTH_MODE=key` を書いて `up` し直すと鍵が書かれ、
      ADC より優先されること（前提 10 の探索順）。
      (3) `key` → `adc` へ戻すと 2 変数が未設定に戻り、前提 10 の `DefaultCredentialsError` が
      起きないこと。**この (3) が最も壊れやすい**（変数だけ残ると ADC がフォールバックせず落ちる）。
      あわせて `tests/containers/` で `GCP_AUTH_MODE` × 鍵 env の有無の組み合わせを固定する。
- [ ] AC13: サービスアカウント鍵が**永続化されない**。検証: 鍵モードで `up` したあと
      `devbase down` し、鍵の env を外して `up` し直すと `$DEFAULT_CREDS_PATH` にファイルが
      **存在しない**こと。グループボリューム (`/persistent/group`) 配下にも鍵が無いこと。
- [ ] AC14: **手順書だけを見て、第三者が新しいグループの Google 認証を完了できる。**
      検証: `docs/user/google-auth.md` の手順を、書いた本人以外（または記憶に頼らず手順書だけを見て）
      未認証のグループで最初から実行し、`gcloud auth list` / `google.auth.default()` / `gws` の
      いずれもが通ること。詰まった箇所は手順書へ反映してから完了とする。
      記載するコマンドと出力はすべて**実機で実行した結果を貼る**（想像で書かない）。

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
|---|---|---|---|
| **A. 共通ボリューム + グループボリュームの二層** | `/persistent/ai` は現行のまま、`/persistent/group` に `devbase_home_<group>` を追加マウント | **採用** | 共通資産（plugins 238MB 等）を重複させずに認証だけ分離できる。既存 `devbase_home_ubuntu` を触らないので分類 A のデータ移行が不要 |
| B. ディレクトリを丸ごとグループ別ボリュームへ | `~/.claude` ごと `devbase_home_<group>` に置く | 不採用 | `plugins` / `skills` / `commands` / グローバル `CLAUDE.md` までグループ数だけ複製され二重管理になる。粒度が粗すぎる |
| C. 環境変数から毎回復元（AWS 方式） | `GCLOUD_CREDENTIALS_BASE64` のようなキーを増やす | 不採用 | gcloud のユーザー OAuth は `credentials.db` / `access_tokens.db` を含む可変の状態で、リフレッシュのたびに更新される。env へ書き戻す経路が無い |
| D. グループ別ボリューム 1 本だけにする（共通ボリュームを廃止） | 全部を `devbase_home_<group>` へ | 不採用 | B と同じ重複問題に加え、既存 `devbase_home_ubuntu` からの全データ移行が必要になる |
| **A'. `default` グループの初回シード** | グループボリュームが空なら `/persistent/ai` の分類 B 相当を**コピー**して初期化（`default` のみ） | **採用** | 現行 14 コンテナの大半を占める `default` で再ログインを避けられる。move ではなく copy なので切り戻し時に元データが残る。ただしシード元は現行 `/persistent/ai` にあるものに限られ、gcloud / gws は対象外（AC8） |
| A''. シードせず全グループで再認証 | issue #116 の当初案 | 不採用 | `default` まで再ログインさせる必要がない。分離の目的は「グループ間で混ぜない」ことであって「捨てる」ことではない |
| **E. gcloud / gws の設定ディレクトリを env で差し替える** | `CLOUDSDK_CONFIG` / `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` をグループボリューム配下へ向ける | **採用** | 公式サポートの経路（前提 8 / 12）。入れ子 symlink が不要になり ADC ファイルも一緒に移る。SA 鍵の出力先 `~/.config/gcloud` は永続領域の外に残るため、鍵が持ち越されず削除仕様そのものが不要になる |
| E'. `.config/gcloud` / `.config/gws` を分類 B の symlink に足す | issue #116 の当初案 | 不採用 | 永続領域の中に SA 鍵の出力先が入るため、プロファイル切替時に旧い鍵が残る。塞ぐには「どのパスを消してよいか」の判定が要り、2 変数がプロジェクト `env` から上書き可能（前提 11）なぶん管理外のファイルを消す危険が残る。E ならこの問題自体が発生しない |
| **F. ADC を既定にし、SA 鍵は任意で切り替える** | `GCP_AUTH_MODE=adc\|key`（既定: 鍵の env があれば `key`、無ければ `adc`） | **採用** | Google は SA 鍵を非推奨とし、ローカル開発には `gcloud auth application-default login` を推奨している。本プランでユーザー認証をグループ単位に永続化するので ADC が現実的になる。権限の都合で鍵が要る場面は残るため切り替えを残す |
| G. Workload Identity Federation で鍵を全廃 | 外部 IdP のトークンを STS で交換し短命トークンを得る | 不採用（将来） | 鍵の全廃としては本命だが外部 IdP が要る。開発 Mac 上のコンテナには適用先が無い |
| H. サービスアカウントのインパーソネーション | `gcloud config set auth/impersonate_service_account` | 不採用（将来の選択肢） | 鍵ファイル無しで短命トークンを得られる中間解。ユーザー認証を基点にするため F の後なら追加しやすい。今回はスコープ外 |

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
| `.config/gcloud`, `.config/gws` | **B**（symlink ではなく env で差し替え） | 問題1の本体。`CLOUDSDK_CONFIG` / `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` をグループボリューム配下へ向ける（前提 8 / 12）。**symlink 対象にはしない** |
| `.claude/plugins`, `.claude/skills`, `.claude/commands`, `.claude/CLAUDE.md`, `.claude/settings.json` | **A** | 契約やテナントに紐づかない共通資産。238MB を重複させない。**`.claude` 配下で A なのはこの 5 件だけ**で、残りはすべて B（既定）になる |
| `.claude/projects`, `.claude/sessions`, `.claude/tasks`, `.claude/session-env` ほか `.claude` 配下の未列挙エントリ | **B**（既定） | 会話ログとセッション状態。顧客情報が入りうる点は `history.jsonl` と同じ。列挙せず既定で B にすることで、Claude Code が将来増やす子ディレクトリも取りこぼさない（前提 20） |
| `.codex`, `.kiro`, `.serena`, `share` | **A** | Codex は ChatGPT アカウント、Kiro は AWS 側（env 由来）で分離済み |
| `.ssh` | **A**（現状維持） | entrypoint は `.ssh` を参照しておらず、git 認証は `GIT_CREDENTIALS_BASE64` / `GH_TOKEN` で完結している。企業テナントの境界になっていない。必要になれば配列間の 1 行移動で B へ移せる |
| `.aws`, `.git-credentials`, `.gitconfig` | **C** | env から毎回復元（現行どおり） |

## 不変条件

- 分類 A のエントリは、どのグループのコンテナから見ても `/persistent/ai` 配下の**同一実体**を指す。
- 分類 B のエントリは、異なるグループのコンテナから**互いに到達できない**。
- グループ名が未指定でも起動できる（`default` へフォールバック）。
- `~/.claude` の**既定はグループ側**である。`~/.claude` は `/persistent/group/.claude` への
  シンボリックリンクで、その配下に分類 A のエントリだけが共通側 (`/persistent/ai/.claude/<x>`)
  への シンボリックリンクとして並ぶ。
  当初は「`~/.claude` を実ディレクトリにし、A / B 双方の symlink を並べる」としていたが、
  前提 20 のとおり `.claude` の子要素は 30 件あり、列挙方式では `projects`（1.1GB の会話ログ）
  のような**未列挙の子が黙って揮発する**。既定をグループ側へ倒し、共通にしたいものだけを
  名指しする向きに反転した。`~/.claude` が symlink であること自体は現行 `main` と同じで、
  変わるのは向き先だけである。
- サービスアカウント鍵は**永続領域に置かない**。毎起動 env から書き直され、コンテナ層とともに消える。

## 互換性

| 対象 | 変更 | 互換性の扱い |
|---|---|---|
| `DEVBASE_ACCOUNT_GROUP` | 新規キー | 追加のみ。未設定は `default` |
| 生成 compose | dev サービスへ `/persistent/group` のマウントが増える | 追加のみ。`devbase up` で再生成される |
| プロジェクトの `compose.yml` | 変更不要 | 前提 4 の自動補完に載る |
| `devbase_home_ubuntu` | **変更しない** | 分類 A のデータはパスも含めてそのまま (`/persistent/ai/.claude/plugins` は移動しない) |
| 分類 B のデータ | 共通 → グループボリュームへ | 現行 `/persistent/ai` に実体があるもの（`.claude.json` / 認証 / 履歴 / `.gemini`）は `default` のみ初回シードで維持（AC8）。gcloud / gws はシード元が無く、`default` を含む全グループで初回 1 回の再認証が要る |
| `GCP_AUTH_MODE` | 新規キー | 追加のみ。未設定なら鍵の env の有無で auto 判定するため、既存プロジェクトは現行どおり `key` 相当で動く |
| `GOOGLE_APPLICATION_CREDENTIALS` / `BIGQUERY_KEY_FILE` | `adc` モードでは unset される | 既定パスは変えないため、鍵モードの既存プロジェクトは影響を受けない。`adc` へ移すのは利用者の明示操作 |
| `~/.config/gcloud` の意味 | gcloud の設定ディレクトリ → 単なる鍵の置き場 | `CLOUDSDK_CONFIG` が実際の設定ディレクトリを指す。パスを直接参照している外部ツールがあれば `$CLOUDSDK_CONFIG` を見るよう直す必要がある |
| スナップショット | 対象ボリュームが 2 系統になる | 既存スナップショットは共通ボリューム分として復元可能。メタデータに対象ボリュームを記録する |

## 修正対象

- `lib/devbase/env/keys.py` — `DEVBASE_ACCOUNT_GROUP` / `GCP_AUTH_MODE` の定義
- `lib/devbase/env/gcp_auth.py`（新規） — 認証モードの解決と、鍵モード専用変数の除外（前提 21）
- `lib/devbase/env/collectors/google.py` — `GOOGLE_APPLICATION_CREDENTIALS` / `BIGQUERY_KEY_FILE` を無条件に書かないようにする（前提 11）
- `lib/devbase/volume/manager.py` — グループ名の解決・検証、グループボリュームの作成
- `lib/devbase/volume/compose.py` — `/persistent/group` のマウントとボリューム宣言、dev サービスへの env 受け渡し
- `lib/devbase/snapshot/manager.py` — 対象ボリュームの複数化
- `lib/devbase/commands/container.py` — `status` へのグループ表示、`up` 時のグループ解決
- `containers/base/Dockerfile` — npm グローバルへ `@googleworkspace/cli` を追加（前提 17）
- `containers/base/entrypoint.sh` — `AI_SETTINGS` の 2 系統化、入れ子パス対応、初回シード、`CLOUDSDK_CONFIG` / `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` の設定、認証モードの分岐、起動ログ
- `docs/user/google-auth.md`（新規。Google 認証の手順書）
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
| 3 | `feature/PLAN39-gcloud` | `CLOUDSDK_CONFIG` / `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` の差し替えと `GCP_AUTH_MODE`（問題1の解消） | PR2 | × (PR2 merge 後) |
| 4 | `feature/PLAN39-observability` | snapshot のグループ対応、`devbase status` 表示、起動ログ、ドキュメント整備 | PR1 | ○ (PR2/PR3 と並行可) |
| 5 | `feature/PLAN39-authdoc` | Google 認証の手順書 `docs/user/google-auth.md` の作成（実機で全手順を実行して書く） | PR3 | × (PR3 merge 後) |

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
- **変更内容:** `AI_SETTINGS` を 3 つの配列に分ける。
  `DEVBASE_SHARED_SETTINGS`（ホーム直下・分類 A → `/persistent/ai`）、
  `DEVBASE_GROUP_SETTINGS`（ホーム直下・分類 B → `/persistent/group`）、
  `DEVBASE_SHARED_CLAUDE_SETTINGS`（`.claude` 配下の分類 A。グループ側の `.claude` から
  共通側へ張る）。`~/.claude` は symlink のまま向き先を `/persistent/group/.claude` へ変え、
  その配下に共通資産 5 件の symlink を張る（不変条件の反転。理由は前提 20）。
  symlink 生成の**前に**、`DEVBASE_ACCOUNT_GROUP` が `default` で
  かつグループ側に実体が無いエントリだけ、`/persistent/ai` から**コピー**して初期化する。
  `.claude` のシードでは分類 A の 5 件を**除外**する（共通資産を重複させないため。
  除外しないと直後の symlink 生成が消すだけの無駄なコピーになる）。
  `~/.config/gcloud` を symlink 対象に**しない**ため（Task 5 は env で差し替える）、
  前提 14 の実行順序による事故は起きない。**symlink ブロックの移動は行わない**。
  ただし将来 `~` 直下の生成物を symlink 対象へ加えると同じ衝突が起きるので、
  「symlink ループは `rm -rf "$HOME_PATH"` してから `ln -s` する」ことをコメントで明示し、
  AC11 を退行検知として残す。
- **満たす受け入れ条件:** AC3, AC4, AC8, AC11
- **進め方:** テスト駆動。シードの冪等性（2 回目は何もしない）と、非 `default` グループで
  シードが走らないことをテストで固定する。

### Task 5: gcloud / gws の設定ディレクトリ差し替えと認証モード（PR3）

- **対象ファイル:** `containers/base/Dockerfile`, `containers/base/entrypoint.sh`,
  `lib/devbase/env/keys.py`, `lib/devbase/env/collectors/google.py`, `tests/containers/`,
  `docs/user/container-operations.md`, `docs/user/environment-variables.md`
- **変更内容:** `.config/gcloud` / `.config/gws` を symlink 対象にはせず、**設定ディレクトリごと
  グループボリュームへ向ける**（前提 8 / 12）。あわせて認証モードを切り替え可能にする。

  ```
  CLOUDSDK_CONFIG=/persistent/group/gcloud
  GOOGLE_WORKSPACE_CLI_CONFIG_DIR=/persistent/group/gws
  ```

  この 2 つだけで、`credentials.db` / `access_tokens.db` / `legacy_credentials/` /
  `configurations/` / `application_default_credentials.json`（= ADC ファイル）と gws の
  `credentials.enc` / `.encryption_key` がグループボリュームへ移る。

  **渡すのは entrypoint の `export` ではなくホスト側の生成 compose である**（前提 21）。
  あわせて **`containers/base/Dockerfile:138` の npm グローバル行へ `@googleworkspace/cli` を足す**。
  前提 17 のとおり gws はどのコンテナにも入っておらず、設定だけ永続化しても復旧しないため。
  `@google/gemini-cli` / `@openai/codex` と同じ扱いにする（`bin` は `gws`）。ディレクトリは entrypoint が
  `mkdir -p` + `chown` してから export する（空ボリュームは root 所有で作られ uid 1000 では書けない。前提 18）。

- **認証モード:** `GCP_AUTH_MODE` を新設する。

  | 値 | 挙動 |
  |---|---|
  | `adc`（既定の推奨） | 鍵を書かない。`GOOGLE_APPLICATION_CREDENTIALS` と `BIGQUERY_KEY_FILE` を **unset** し、ADC を `$CLOUDSDK_CONFIG/application_default_credentials.json`（= `gcloud auth application-default login` の結果）に委ねる |
  | `key` | 現行どおり `GCP_CREDENTIALS_BASE64__<profile>` を復号して書き、2 変数を export する |
  | 未設定（auto） | 鍵の env があれば `key`、無ければ `adc` |

  `adc` で 2 変数を **コンテナへ渡さない**のが要点である。値だけ残して実体が無いと ADC は
  ユーザー認証へフォールバックせず `DefaultCredentialsError` で落ちる（前提 10）。
  前提 21 のとおり entrypoint の `unset` は `docker exec` のシェルへ届かないため、
  **ホスト側で生成 compose の `environment:` の列挙から外す**。名前が載らなければ
  Compose はその変数をコンテナへ渡さない。entrypoint 側の `unset` は、古いホストから
  起動された場合とプロジェクト `env` 直書きに対する保険として残す。
  現状 `_collect_common_settings` は鍵の有無に関係なく 2 変数を書く（前提 11）ため、
  `devbase env init` 側も鍵を登録したときだけ書くよう直す。

- **鍵の出力先:** 現行のまま `/home/${USERNAME}/.config/gcloud/credentials.json` を既定とする
  (`entrypoint.sh:198`)。`CLOUDSDK_CONFIG` を向け直した後の `~/.config/gcloud` は
  **gcloud の設定ディレクトリではなく、単なる鍵の置き場**になり、コンテナ層（揮発）に残る。
  したがって鍵は毎起動 env から書き直され、`devbase up` が `down` を挟んでコンテナを作り直す以上
  **持ち越されない**。プロファイルを切り替えても旧い鍵は存在しないので、削除仕様は要らない。
  既定パスを変えないのは、既存プロジェクトの `env` が `BIGQUERY_KEY_FILE` にこのパスを
  書いているため（前提 11）。ドキュメントには「このディレクトリは gcloud の設定ではない」旨を明記する。

- **満たす受け入れ条件:** AC1, AC2, AC11, AC12, AC13
- **進め方:** テスト駆動 + 実機検証。`tests/containers/` で `DEVBASE_ENTRYPOINT_LIB_ONLY`
  (`entrypoint.sh:182-184`) を使い、`GCP_AUTH_MODE` × 鍵 env の有無で
  「2 変数が export されるか unset されるか」を固定する。実機では
  `gcloud auth login` / `gcloud auth application-default login` → `devbase down` → `up` →
  `gcloud auth list` と `google.auth.default()` が再認証なしで通ることを確認する。
- **注意:** 前提 13 のとおり gcloud は並行実行を想定していない。同一グループで複数コンテナを
  同時に動かすと `database is locked` が出うる。恒久対策は取らず、リスク表に記録して
  再実行で回避する方針とする。

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

### Task 8: Google 認証の手順書（PR5）

- **対象ファイル:** `docs/user/google-auth.md`（新規）、`docs/user/container-operations.md`（相互リンク）、
  `docs/user/environment-variables.md`（`GCP_AUTH_MODE` からの参照）、`README.md`（目次）
- **位置づけ:** Task 1〜7 は「仕組みを作る」タスクだが、この仕組みは**グループごとに人が 1 回
  対話的に認証する**ことを前提にしている。その 1 回をどう実施するかが書かれていないと、
  新しいグループを足すたびに手探りになる。手順書はこのプランの成果物の一部とする。
- **前提:** PR3 が merge され、`CLOUDSDK_CONFIG` と `GCP_AUTH_MODE` が実際に動く状態であること。
  **書きながら実機で全手順を実行する**（想像で書かない。`ndf:investigation-rules`）。
- **書く内容:**

  1. **前提の説明** — アカウントグループとは何か、どのボリュームに何が入るか、
     `~/.config/gcloud` は gcloud の設定ディレクトリ**ではない**こと（`$CLOUDSDK_CONFIG` を見る）。
  2. **新しいグループの初回セットアップ** — `projects/<name>/env` に
     `DEVBASE_ACCOUNT_GROUP` / `GCP_ACTIVE_PROFILE` / `AWS_PROFILE` を書く → `devbase up` →
     コンテナ内で認証する、までを 1 本の流れとして書く。
  3. **gcloud の認証** — 前提 15 / 16 で調査済みなので、手順としては次を書けばよい。
     `gcloud auth login`（フラグ不要。この環境では自動で URL + 認証コードのフローになる）と、
     ADC 用に `gcloud auth application-default login` の 2 回。`--update-adc` で 1 回に減らす案は
     quota project が書かれないため（前提 16）**既定の手順にはしない**。
     残る実地確認: 実際に 1 回通して、貼り戻しの UI（プロンプト文言）と所要時間を手順書に書き写す。
  4. **gws の認証** — バイナリは Task 5 でベースイメージに入るので、手順書は認証から書く。
     `gws auth setup`（Cloud プロジェクト設定。gcloud に依存する）と `gws auth login` を実地確認し、
     `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file` が必要かどうか（コンテナに OS キーリングが無い場合の挙動）を
     確かめて書く。
  5. **認証モードの切り替え** — `GCP_AUTH_MODE` の `adc` / `key` / 未設定の使い分けと、
     切り替え後に `devbase up` が必要なこと。鍵が要るのはどういう場面かを 1 段落で書く。
  6. **確認コマンド** — `gcloud auth list` / `gcloud config get account` /
     `python3 -c "import google.auth; print(google.auth.default()[1])"` / `gws` の疎通確認。
     **いま自分がどのグループにいるか**の確認方法（`devbase status` の表示、`echo $CLOUDSDK_CONFIG`）。
  7. **トラブルシュート** — 少なくとも次の 3 つ。いずれも本プランで実際に踏みうるもの:
     `DefaultCredentialsError`（前提 10。`adc` なのに変数が残っている）、
     `database is locked`（前提 13。同一グループの同時実行）、
     意図しないアカウントで操作していた場合の確認と切り替え。
- **満たす受け入れ条件:** AC14
- **進め方:** 文書のみ。ただし**未認証のグループを 1 つ用意して最初から通す**こと。
  詰まった箇所は手順書に反映してから完了とする。

## 影響範囲

- 全プロジェクトの生成 compose（`devbase up` のたびに再生成されるため移行作業は不要）。
- ディスク使用量: グループ数 × 分類 B のサイズ。実測では gcloud 3.9MB + gws 2.9MB +
  `.claude.json` / 認証 / 履歴で数十 MB 程度。238MB の `plugins` は共通側に残るため増えない。
- entrypoint と Dockerfile の変更のため base イメージの再ビルドが必要（Task 3・4・5・7）。
  `@googleworkspace/cli` の追加ぶんイメージが増えるが、既に再ビルドは必須なので追加の手間は無い。
- スナップショットの世代管理の粒度が変わる（対象が 2 ボリュームになる）。

## リスクと対処

| リスク | 対処 |
|---|---|
| 入れ子パス対応の不備で `~/.claude` 配下が壊れ、Claude Code が起動しなくなる | Task 3 を Task 4 より先に、単独で検証する。AC6 で `history.jsonl` と `.credentials.json` を名指しで確認 |
| 初回シードが非 `default` グループでも走り、分離の意味が失われる | Task 4 でグループ名のガードをテストに固定。AC3 で実機確認 |
| グループ名が既存ボリューム名と衝突する（`ubuntu` は `devbase_home_ubuntu`、数字のみは `devbase_home_<index>`） | Task 1 で正規表現とは別の明示チェックとして両方を拒否。AC7 |
| entrypoint 変更が `up` だけでは反映されない | [[entrypoint-change-needs-rebuild]]。検証手順に `devbase build --no-cache` を明記 |
| 既存スナップショットが復元できなくなる | Task 6 で旧メタデータ互換をテストで固定 |
| `GCP_AUTH_MODE=adc` で `GOOGLE_APPLICATION_CREDENTIALS` の unset を忘れると、値だけ残って実体が無く ADC が `DefaultCredentialsError` で落ちる（前提 10。フォールバックしない） | Task 5 で 2 変数を unset する。AC12 (3) で `key` → `adc` の戻り方向を実機とテストの両方で固定する |
| 同一グループの複数コンテナが同時に gcloud を叩き `database is locked` になる（前提 13。gcloud は並行実行非対応で `credentials.db` は SQLite） | 恒久対策は取らない。グループボリュームを共有する設計に内在するもので symlink 方式でも同じ。ドキュメントに再実行で回避する旨を書く |
| gws の設定を永続化しても、バイナリが無ければ復旧しない（前提 17） | Task 5 で `containers/base/Dockerfile:138` へ `@googleworkspace/cli` を足し、ベースイメージに含める。AC2 で `command -v gws` を確認する |
| `~/.config/gcloud` が gcloud の設定ディレクトリだと誤解され、そこを永続化しようとする揺り戻しが起きる | `CLOUDSDK_CONFIG` 導入後は単なる鍵の置き場である旨を Task 5 の記述と `docs/user/container-operations.md` に明記する |
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
                mkdir -p "/to/$p"
                # 分類 A への symlink (plugins / skills / commands / CLAUDE.md /
                # settings.json) は書き戻さない。共通側の実体を指すリンクなので、
                # 書き戻すと実体が自分自身を指す symlink に置き換わる
                for c in "/from/$p"/* "/from/$p"/.[!.]*; do
                  [ -e "$c" ] || [ -L "$c" ] || continue
                  [ -L "$c" ] && { echo "skip (共通側への link): ${c##*/}"; continue; }
                  cp -a "$c" "/to/$p/"
                done
              else
                cp -a "/from/$p" "/to/$p"
              fi
              echo "copied: $p"
            done'
   ```

   グループ内で一度も使っていないツールのエントリは存在しないことがあるため、各パスの存在を
   確認してから `cp` し、無いものは `skip` として飛ばす（`&&` で連結すると 1 件目の欠落で
   以降の同期が止まる）。`/persistent/group/.claude` 配下には分類 A の実体へ向いた symlink が
   並ぶので（不変条件）、**symlink は書き戻さない**。書き戻すと共通側の実体
   (`/persistent/ai/.claude/plugins` 等) が自分自身を指す symlink に置き換わってしまう。

   対象は分類 B のうち共通側に対応物があるものに限る。gcloud / gws は共通ボリュームに置き場が無く、
   revert 後は永続化対象外（現行 main と同じ）へ戻るため書き戻さない。グループボリューム直下の
   `gcloud/` `gws/`（`CLOUDSDK_CONFIG` / `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` の実体）を保全したい場合は、
   同じくボリュームを直接マウントしてカレントディレクトリへ tar で退避する。

   ```bash
   GROUP=default

   docker run --rm -e GROUP="$GROUP" \
     -v "devbase_home_${GROUP}:/from" -v "$PWD:/backup" alpine \
     sh -c 'cd /from || exit 1
            set --
            for p in gcloud gws; do
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

- [ ] AC1〜AC14 を満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run pytest` が green
- [ ] 個別 PR がすべて `/ndf:cross-review` で APPROVE 収束済み
- [ ] `devbase build --no-cache` 後の実機で、`default` と非 `default` の 2 グループを起動して
      AC1〜AC4 / AC8 / AC11〜AC13 を確認している
- [ ] `docs/` と `CHANGELOG.md` が新しいボリューム構造と `DEVBASE_ACCOUNT_GROUP` / `GCP_AUTH_MODE` を説明している
- [ ] `docs/user/google-auth.md` が実機で通した手順になっており、未認証のグループで通しの検証が済んでいる（AC14）
