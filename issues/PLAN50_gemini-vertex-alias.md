# PLAN50: `gemini` の Vertex AI 強制をやめる

- 発端: with-ai-dev コンテナで gemini の Vertex AI 経由の利用ができなくなった（2026-09-03、利用者からの報告）
- ワークフローモード: `standard`
  - 根拠: 全コンテナの `gemini` コマンドの振る舞いが変わる。公開 API・スキーマ・認可の
    変更は無く、触るのは `containers/base` の 1 領域。

## 依頼（原文）

> with-aiのコンテナで、geminiのvertex ai経由の利用ができなくなっています。設定を復帰させてください

> OAuthで行きます。
> takemi.ohama@gmail.comの現在のプランを調べたい

利用者は Vertex AI ではなく OAuth（個人アカウント）で使う方針を選んだ。この計画が扱うのは、
その方針を邪魔している devbase 側の作りである。

## 目的

`containers/base/Dockerfile` が `.bashrc` へ書き込む alias が、**全コンテナで
`GOOGLE_GENAI_USE_VERTEXAI=true` を無条件に強制している**。Vertex AI を使わない
プロジェクトでも Vertex 経路へ倒れるため、これをやめる。

## 調査で確定した事実

| 確認事項 | 結果 | 根拠 |
| --- | --- | --- |
| `GOOGLE_GENAI_USE_VERTEXAI` の設定箇所 | **alias 1 か所のみ** | `grep -rn GOOGLE_GENAI_USE_VERTEXAI containers/ lib/ bin/` → `containers/base/Dockerfile:207` だけ。どの `projects/*/env` にも無く、`secrets/global.env.age` のキーにも無い |
| Vertex に必要な `GOOGLE_CLOUD_PROJECT` の出所 | `secrets/global.env.age`（値は nyle の GCP プロジェクト） | 機密ストアのキー一覧に `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` がある |
| プロジェクトを空にしているもの | `with-ai-dev` と `project-trygroup-prd` の 2 件 | 各 `projects/*/env` の走査 |

つまり alias は「Vertex を使う」と決め打つ一方、Vertex に不可欠なプロジェクトは
グローバルの機密が供給しており、**プロジェクトを空にした環境では前提が崩れる**。

現物での確認（with-ai-dev コンテナ）:

```
$ tail -n 3 ~/.bashrc
alias gemini='GOOGLE_GENAI_USE_VERTEXAI=true gemini --yolo "$@"'
$ echo "$GOOGLE_CLOUD_PROJECT"
nyle-carmo-analysis          # 空にしたはずの値。別の不具合（下記）
$ gcloud auth list
*  ohama.takemi@withjp.inc   # nyle のプロジェクトへの権限は無い
```

### alias の `"$@"` について

`alias x='... "$@"'` の `"$@"` は alias 自身の引数ではなく**シェルの位置パラメータ**に展開される。
対話シェルでは空で、非対話シェルでは alias 展開自体が既定で無効なため、現状は実害が出ていない。
意図した働きをしていないので、この機会に落とす。

## 前提

- 前提 1: 認証方式は環境変数 `GOOGLE_GENAI_USE_VERTEXAI` で**明示的に**選ぶ。起動定義は
  推論しない。（成否の判定: 起動定義に認証方式を決める分岐が 1 つも無いこと）
- 前提 2: `--yolo`（確認プロンプトの省略）は現状どおり付ける。他の AI CLI の alias と揃える方針を変えない。
- 前提 3: 稼働中のコンテナには反映されない。ベースイメージの再ビルドとコンテナ再作成が要る。

## 対象範囲

含む:

- `gemini` の起動時に `GOOGLE_GENAI_USE_VERTEXAI` を立てるのをやめ、環境で選ばせること
- 起動定義から、意図した働きをしていない `"$@"` を落とすこと（全 6 定義）
- alias 群を Dockerfile のインライン `echo` から独立したファイルへ出し、テストできるようにすること
- 上記に伴う `docs/` の追従と、既存利用者の振る舞いを保つ移行手順の明示

含まない:

- `GOOGLE_CLOUD_PROJECT` がプロジェクトの `env` の空上書きを無視してコンテナへ漏れる件。
  再現は確認したが、`devbase.env.runtime.resolve()` を現在の環境で実行すると空に解決されるため、
  稼働中のコンテナが古いだけの可能性がある。別途切り分ける
- `~/.gemini/settings.json` の `selectedType` の管理（利用者が選ぶもの）
- 他の AI CLI（claude / claudb / codex / kiro / agy）の起動オプションの変更

## 用語

| 用語 | 意味 |
| --- | --- |
| Vertex 経路 | `GOOGLE_GENAI_USE_VERTEXAI=true` で GCP プロジェクト上の Vertex AI を使う経路 |
| OAuth 経路 | Google アカウントでログインして Code Assist を使う経路（`oauth-personal`） |
| alias 群 | `containers/base/Dockerfile` が `.bashrc` へ書き込む AI CLI の起動定義一式 |

## 受け入れ条件

- [ ] AC1: 起動定義のどこにも `GOOGLE_GENAI_USE_VERTEXAI` を設定する記述が無い。
      `gemini` の環境は呼び出し側から渡ったものがそのまま届く
- [ ] AC2: `GOOGLE_GENAI_USE_VERTEXAI=true` を環境に持つシェルで `gemini` を起動すると、
      その値が子プロセスへ渡る（環境で Vertex を選べる）
- [ ] AC3: `GOOGLE_GENAI_USE_VERTEXAI` を持たないシェルで `gemini` を起動すると、
      子プロセスにも設定されない（環境を素通しする）
- [ ] AC4: `gemini` へ渡した引数が、そのままの順序で実体へ届く。`--yolo` が必ず付く
- [ ] AC5: **すべての起動定義から `"$@"` が消えている。** `claude` / `claudb` / `gemini` /
      `codex` / `kiro` / `agy` のいずれの定義にも `$@` を含まない
- [ ] AC6: `claude` / `claudb` / `codex` / `kiro` / `agy` が起動する実体と、`"$@"` を除いた
      オプションが現行と変わらない
- [ ] AC7: 上記 5 つそれぞれに引数を渡すと、そのままの順序で実体へ届く
      （`"$@"` を落としても引数が欠けない）
- [ ] AC8: `complete -o default claudb kiro` が引き続き有効
- [ ] AC9: AC1〜AC7 を固定する自動テストが `tests/containers/` にあり、**Docker に依存せず**
      実行できる（既存の `tests/containers/test_entrypoint_*.py` と同じく shell を直接読む方式）
- [ ] AC10: 既存テスト一式（`tests/`）が退行しない
- [ ] AC11: `docs/` に、Vertex 経路と OAuth 経路をどこで選ぶかが書かれている
- [ ] AC12: 移行手順（下記「移行」）が計画に書かれ、実施の要否が完了報告に残る

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `gemini` は環境の `GOOGLE_GENAI_USE_VERTEXAI` に従うようになる。全 6 定義から `"$@"` が消えるが、引数の渡り方は変わらない |
| データ | なし |
| 既存の振る舞い | **移行を行わないと、いま Vertex を使っている環境が OAuth 側へ倒れる。** 共通機密へ `GOOGLE_GENAI_USE_VERTEXAI=true` を入れることで現行と同じになる（「移行」の節） |
| 反映の条件 | ベースイメージの再ビルド（`devbase build base --no-cache`）とコンテナの再作成が要る |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run pytest tests/containers -q`（限定）、`uv run pytest tests/ -q`（全体） |
| 静的解析 | `shellcheck --severity=error containers/base/ai-cli-aliases.sh`、`python -m compileall -q lib bin` |
| 手動確認 | ベースイメージを再ビルドしたコンテナで `type gemini` と、`GOOGLE_CLOUD_PROJECT` の有無による分岐。リリース後テストで行う |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | コンテナへ入れる shell 資産は `containers/base/` に置き、Dockerfile が `COPY` する（`tmux.conf` / `tmux-first` / `entrypoint.sh` と同じ）。テストは `tests/containers/` |
| コーディング規約 | shell は `shellcheck --severity=error` を通す。コメントは日本語で意図（なぜ）を書く |
| テスト戦略 | Docker を起動せず、shell ファイルを直接 source して振る舞いを固定する（既存の `tests/containers/` の方式） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テスト一式の実行、ShellCheck、変更範囲のコメント整備 |
| 確認してから行う | 他の AI CLI の alias の変更、`--yolo` の付け外し |
| 行わない | `GOOGLE_CLOUD_PROJECT` 漏れの調査、`settings.json` の管理、ベースイメージの他の変更 |

---

# 設計

## 構成要素

| 要素 | 責務 |
| --- | --- |
| `containers/base/ai-cli-aliases.sh`（新規） | AI CLI の起動定義を持つ唯一の場所。alias 群 |
| `containers/base/Dockerfile` | 上記を `COPY` し、`.bashrc` から読み込ませる。インラインの `echo` 群を落とす |
| `tests/containers/test_ai_cli_aliases.py`（新規） | 起動定義の振る舞いを Docker 抜きで固定する |

## 入出力の契約

### 起動定義

すべて `alias <名前>='<実体> <固定オプション>'` の形にする。環境変数の前置は
`claudb`（Bedrock を選ぶためのもの）だけが持ち、他は持たない。

| 名前 | 実体と固定オプション | 環境の前置 |
| --- | --- | --- |
| `claude` | `claude --dangerously-skip-permissions` | なし |
| `claudb` | `claude --dangerously-skip-permissions` | `CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-west-2` |
| `gemini` | `gemini --yolo` | **なし**（現行の `GOOGLE_GENAI_USE_VERTEXAI=true` を落とす） |
| `codex` | `codex --dangerously-bypass-approvals-and-sandbox` | なし |
| `kiro` | `kiro-cli chat --trust-all-tools` | なし |
| `agy` | `agy --dangerously-skip-permissions` | なし |

引数は alias の展開で末尾へ付くため、定義側に `"$@"` は要らない。

`gemini` の認証方式は環境が決める。

| 環境 | gemini の経路 |
| --- | --- |
| `GOOGLE_GENAI_USE_VERTEXAI=true` | Vertex AI |
| 未設定・空・`false` | `~/.gemini/settings.json` の `selectedType` に従う（OAuth など） |

## 移行

**この変更だけでは、いま Vertex を使っている環境が OAuth 側へ倒れる。** 起動定義が
補っていた `GOOGLE_GENAI_USE_VERTEXAI=true` を、環境の側へ移す必要がある。

| 対象 | 何をするか |
| --- | --- |
| 共通（Vertex を既定にする） | `devbase env set -g GOOGLE_GENAI_USE_VERTEXAI=true`。既に `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` が共通機密にあり、同じ層へ揃う |
| `with-ai-dev` / `project-trygroup-prd` | `projects/<name>/env` へ `GOOGLE_GENAI_USE_VERTEXAI=` を書き、共通の値を打ち消す。`GOOGLE_CLOUD_PROJECT=` と同じやり方 |

共通機密は利用者の環境にあり、リポジトリの差分では移せない。**手順として書き、実施したか
どうかを完了報告に残す。**

## 決定の記録

### 決定 1: 認証方式は起動定義で推論せず、環境変数で明示的に選ぶ

`GOOGLE_CLOUD_PROJECT` は gcloud・BigQuery・その他の GCP ツールが共有するプロジェクト指定で
あって、gemini の認証方式の opt-in ではない。これを判定に使うと、OAuth を選んだ利用者が
BigQuery などの目的で同じ変数を設定した瞬間、意図せず Vertex へ倒れる。認証方式を表す変数
（`GOOGLE_GENAI_USE_VERTEXAI`）だけで決める。

起動定義が値を補う案（`GOOGLE_CLOUD_PROJECT` が非空なら `true`）は、上記のとおり別の目的の
変数へ意味を重ねるため採らない。新しい専用の変数を足す案も採らない。gemini 自身が読む変数が
既にあり、設定する場所を増やすと食い違いの解決が要る。

**この決定は、いま Vertex を使っている環境へ移行を要求する。** 起動定義が補っていた値を
共通機密へ移す（「移行」の節）。移行を伴わない案（推論を残す）と比べ、認証方式が 1 か所で
読み取れる状態と引き換えに、1 度の手順を払う。

### 決定 2: alias のままにし、`"$@"` を落とす

条件分岐を持たないため、シェル関数にする必要はない。alias は展開時に引数が末尾へ付くので、
定義側に `"$@"` は要らない。

現行の `"$@"` は alias の引数ではなく**シェルの位置パラメータ**に展開される。対話シェルでは
空で、非対話シェルでは alias 展開自体が既定で無効なため実害は出ていないが、読み手には
「引数を渡すための記述」に見える。意図した働きをしていないので落とす。

### 決定 3: 起動定義を Dockerfile から独立したファイルへ出す

インラインの `echo ... >> ~/.bashrc` はテストできない。Dockerfile の文字列を `grep` する
テストは、書き方を変えるたびに壊れるうえ、振る舞いを固定しない。`tmux.conf` と同じく
`COPY` する資産にすれば、`tests/containers/` の既存の方式（shell を直接 source する）で
振る舞いそのものを固定できる。

この移動自体は振る舞いを変えない。`gemini` の変更とはコミットを分ける。

### 決定 4: 共通機密を既定の置き場にする

`GOOGLE_GENAI_USE_VERTEXAI` は `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` と同じ層
（`secrets/global.env.age`）へ置く。3 つは揃って初めて Vertex が成立するため、別々の層に
散らすと片方だけを変えたときに壊れる。プロジェクト単位の打ち消しは、既にある
`projects/<name>/env` の空上書き（`_project_env_overrides`）でそのまま効く。

## テスト設計

| 受け入れ条件 | 何で確かめるか |
| --- | --- |
| AC1 | `tests/containers/test_ai_cli_aliases.py`: `ai-cli-aliases.sh` の中身に `GOOGLE_GENAI_USE_VERTEXAI` が現れないこと |
| AC2 / AC3 | 同ファイル: PATH の先頭へ `gemini` のスタブ（受け取った環境と引数を出力する実行可能ファイル）を置き、`shopt -s expand_aliases` して source し `gemini` を呼ぶ。`GOOGLE_GENAI_USE_VERTEXAI` を設定した場合／しない場合で、スタブが見る値を突き合わせる |
| AC4 / AC7 | 同ファイル: スタブが出力した引数列が `<固定オプション> <渡した引数...>` と一致すること。6 つすべてを parametrize で回す |
| AC5 | 同ファイル: `alias` の定義文字列のいずれにも `$@` が含まれないこと |
| AC6 | 同ファイル: 実体と固定オプションが「起動定義」の表と一致すること |
| AC8 | 同ファイル: source 後に `complete -p claudb` / `complete -p kiro` が引けること |
| AC9 | 上記が Docker を起動しないこと（`bash` の起動のみ） |
| AC10 | `uv run pytest tests/ -q` |
| AC11 | `docs/` の差分をレビューで確認 |
| AC12 | 完了報告に移行の実施状況を書く |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 実コンテナでの動作 | この工程では shell を直接 source して検証する。ベースイメージを再ビルドしたコンテナで各 alias が期待どおり起動するかは、リリース後テストで確かめる |
| 移行後の Vertex 経路 | 共通機密へ `GOOGLE_GENAI_USE_VERTEXAI=true` を入れた後、nyle 系プロジェクトで Vertex が現行どおり動くこと。共通機密は利用者の環境にあるためリポジトリの検証では踏めない |
| `GOOGLE_CLOUD_PROJECT` の漏れ | 対象範囲外。稼働中のコンテナでは `nyle-carmo-analysis` が入っているが、現在の環境で `resolve()` を実行すると空になる。この計画では扱わない |

## 受け入れ条件の変更

- ~~AC1: `GOOGLE_CLOUD_PROJECT` が未設定または空のシェルで `gemini` を起動すると
  `GOOGLE_GENAI_USE_VERTEXAI` が子プロセスへ渡らない / AC2: 非空なら `true` が渡る~~
  → **起動定義は認証方式を推論しない。`GOOGLE_GENAI_USE_VERTEXAI` だけで決める**
  （2026-09-03、PR #148 のレビュー指摘による。`GOOGLE_CLOUD_PROJECT` は GCP 全般の
  プロジェクト指定であって gemini の認証方式の opt-in ではなく、OAuth 利用者が別の目的で
  設定した瞬間に Vertex へ倒れるため。移行の節を追加した）
- 追加: **AC5 / AC7 — すべての起動定義から `"$@"` を落とす**
  （2026-09-03、利用者の指示による。alias では意図した働きをしていないため）
