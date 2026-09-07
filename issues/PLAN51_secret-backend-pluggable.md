# PLAN51: 機密ストアの保存先を差し替え可能にする（第一 adapter: Infisical）

- 発端: [issues/security-key.md](security-key.md)（2026-09-08、利用者からの依頼）
- ワークフローモード: `standard`
  - 根拠: 新しい公開コマンド `devbase env backend` を追加し、**認証情報の取得経路そのもの**を
    変える。`lib/devbase/env/` と `lib/devbase/commands/` の複数モジュールにまたがり、
    対象には既存テストが十分にある。

## 依頼（原文）

`issues/security-key.md` の「目的」「ミッション」より引用する。

> ## 目的
> * 現在、devbaseでの機密情報の管理は独自実装のsecrets管理を利用している
> * しかし、WebUIもなく、管理が複雑
> * OSSサーバが導入されている場合、その設定のみをsecretsで管理することで、WebUIでの管理ができるようにしたい
>
> ## ミッション
> * devbaseの機密情報管理を代替できるソリューションを選択してください
>     * この文章下部にchatgptの提案があるので参考にする
>     * 自身でも調査すること
> * 現在の機密情報管理機構と入れ替えられるようにしてください
>     * コマンドで設定可能
>     * いつでも入れ替え可能とする

同ファイル下部には ChatGPT による製品比較が付いている。参考資料として扱い、そこでの主張は
「調査で確定した事実」で裏を取り直した。

会話中の追加指示（2026-09-08）:

> なお、Infisical立ち上げについて、devbaseに合わせた要件を整理して
> （社内インフラ構築リポジトリ）にissueを投稿してください

（このリポジトリは公開のため、引用中の非公開リポジトリの URL を伏せた。実際の宛先は
`carmo-cdk` で、投稿した issue は `carmo-cdk#312`。）

サーバの構築要件は `carmo-cdk#312` が持つ。この計画は**devbase 側**だけを扱う。

## 利用者が決めたこと

`AskUserQuestion` で確認した（2026-09-08）。

| 決めたこと | 選択 |
| --- | --- |
| 最初に実装するサーバ backend | **Infisical** |
| サーバ不達時の挙動 | **ローカルに暗号化キャッシュを持つ** |

## 製品選定の結論

| 候補 | ライセンス | WebUI | `.env` 適合 | 採否 |
| --- | --- | --- | --- | --- |
| **Infisical** | コア MIT | ◎ | ◎ project / environment / path が devbase の共通 / プロジェクトへ素直に対応する | **採用** |
| OpenBao | MPL 2.0（全体） | ○ | △ KV v2 への載せ替えが要る | 見送り。動的クレデンシャルや PKI を必要としない用途には概念が重い |
| Vaultwarden | AGPL | ◎ | △ `bws` 非対応のため項目・メモへ押し込む形になる | 見送り。依頼元の ChatGPT も `.env` 用途では Infisical を挙げている |
| Bitwarden 公式 | 一部商用 | ◎ | △ 同上 | 見送り。完全な OSS ではない |

**この選定自体を設計から切り離せる形にする。** 差し替え可能であることが依頼の条件なので、
Infisical は「最初の 1 つ」であって前提ではない。

## 目的

機密の保存先を**明示的に選べる**ようにし、WebUI を持つ OSS サーバへ載せ替えられる状態を作る。

現状の `devbase` は保存先を 2 つ持つが（平文 `.env` / age 暗号化）、**どちらを使うかは
ファイルの存在から推測している**。保存先がファイルでない backend はこの判定に載らないため、
サーバを足す前に「選ぶ」という概念そのものを作る必要がある。

依頼の「その設定のみをsecretsで管理する」は、**既存の age 暗号化ストアを、サーバへの接続設定を
置く場所として残す**という意味に解釈した。サーバ backend を有効にしても age ストアは消えない。

## 調査で確定した事実

| 確認事項 | 結果 | 根拠 |
| --- | --- | --- |
| 保存先の抽象化はすでにあるか | **ある。`SecretBackend` Protocol と 2 実装** | `lib/devbase/env/secret_store.py:96` の `SecretBackend`、`PlaintextBackend`、`AgeBackend` |
| 保存先の選び方 | **ファイルの存在で自動判定**。明示的に選ぶ手段は無い | `SecretStore.backend_for()` が `age.exists()` / `plaintext.exists()` だけを見る |
| 差し替えの継ぎ目の数 | **`SecretStore(...)` の生成箇所は 11 か所** | `grep -rn "SecretStore(" lib/ bin/` → `bundle.py:241` `runtime.py:152` `io_import.py:143` `env_migrate.py:121,342` `env.py:30,646` `env_ops.py:70,190,553` `container.py:1145` |
| コンテナへの受け渡し経路 | **保存先に依存しない**。`runtime.resolve()` が辞書へ合成し、compose には変数名だけを渡す | `lib/devbase/env/runtime.py:137-172`、`SecretEnv.names` |
| 現在の依存パッケージ | `pyyaml` / `pyrage` / `boto3` / `questionary` の 4 つ。**HTTP クライアントは入っていない** | `pyproject.toml` の `dependencies` |
| 既存の HTTP 利用 | 無し（`urllib.request` は `url2pathname` の用途のみ） | `grep -rn "import requests\|import httpx\|urllib.request" lib/devbase/` → `storage.py:39` の 1 件のみ |
| 対象領域のテスト | **十分にある** | `tests/env/test_secret_store.py` `test_runtime.py` `test_store_roundtrip.py`、`tests/commands/test_env_migrate.py` `test_env_ops.py` `test_env_store_switch.py` ほか |
| Vaultwarden で `.env` を扱えるか | **扱いにくい。Secrets Manager（`bws`）は非対応** | [vaultwarden discussion #5702](https://github.com/dani-garcia/vaultwarden/discussions/5702)。Password Manager API のみ実装され、Secrets Manager は Bitwarden 独自ライセンスのため未実装 |
| Infisical の CLI が `.env` を扱えるか | **扱える**。`secrets set/get/delete`、`--file` で `.env` を一括投入 | [Infisical CLI: secrets](https://infisical.com/docs/cli/commands/secrets) |
| Infisical の認証（非対話） | **できる**。machine identity の client secret を access token へ交換して渡す | 同上 |
| Infisical のライセンス | **コアは MIT**。企業向け機能は商用ライセンス | [Infisical deployment models](https://infisical.com/docs/documentation/getting-started/concepts/deployment-models) |
| Infisical 自己ホストの構成 | **Postgres + Redis が必須**。目安 2 コア / 4 GB | [Infisical Docker Compose](https://infisical.com/docs/self-hosting/deployment-options/docker-compose) |

## 用語

| 用語 | 意味 |
| --- | --- |
| 参照（`SecretRef`） | 機密の宛先。共通（`global`）またはプロジェクト（`project:<name>`）の 2 種 |
| backend | 参照に対して機密を読み書きする実装。`plaintext` / `age` / `infisical` |
| ブートストラップ設定 | サーバ backend が接続に使う設定（URL・client secret・プロジェクト ID など）。**機密なので既存の age ストアに置く** |
| キャッシュ | サーバから取得した機密を、不達時に備えて手元へ age 暗号化して控えたもの |
| 既定 backend | 何も設定していないときに使われる backend。現状どおり「ファイルの存在で自動判定」 |
| client secret | machine identity に払い出される長期の資格情報。**手元に保存する**（ブートストラップ設定） |
| access token | client secret を交換して得る短期の資格情報。**プロセス内にだけ持ち、保存しない** |

## 前提

- 前提 1: **サーバの構築・運用は devbase の範囲外**。devbase は既存のサーバへ接続するだけで、
  サーバを起動する機能は持たない。（成否の判定: `devbase` のコマンドに Infisical サーバを
  起動・停止するものが 1 つも無いこと）
- 前提 2: **新しい常時依存を増やさない**。Infisical adapter は標準ライブラリの HTTP で
  REST を叩き、`infisical` CLI のインストールを必須にしない。（成否の判定:
  `pyproject.toml` の `dependencies` が 4 件のまま変わらないこと）
- 前提 3: **既定の挙動は変えない**。backend を明示的に設定するまで、現状と同じ判定・同じ
  出力になる。（成否の判定: 既存テストが 1 件も書き換えなしで通ること）
- 前提 4: キャッシュは**サーバから取得できたときだけ**更新する。ここでの「取得できた」は
  応答を読めたことを指し、**機密が 0 件の正常な応答も含む**。古いキャッシュを残すのは取得に
  失敗したとき — 通信できない、または応答が不正なとき — だけである。（成否の判定: 通信失敗と
  不正な応答の後はキャッシュファイルの中身が変わらず、機密 0 件の正常な応答の後はキャッシュが
  その結果で置き換わること）
- 前提 5: 1 台の端末が同時に使う backend は 1 つ。参照ごとに別々の backend を割り当てる用途は
  扱わない。（成否の判定: 設定に参照ごとの backend 指定が現れないこと）

## 対象範囲

含む:

- `SecretBackend` を**明示的に選べる登録簿**へ広げる（`SecretStore` の生成箇所は変えず、
  `SecretStore.backend_for()` の内側で設定を読んで解決先を決める）
- backend の設定を読み書きする場所を決める（非機密の設定ファイル + 機密はブートストラップ設定へ）
- `devbase env backend` コマンド群（`status` / `use` / `test` / `migrate`）
- Infisical adapter（REST、machine identity 認証）
- ローカル暗号化キャッシュと、サーバ不達時のフォールバック
- age の受信者更新（`devbase env rekey`）の対象へ、ブートストラップ機密とキャッシュを含める
- 既存の `env list/get/set/delete/edit/doctor` をサーバ backend でも同じ形で動かす
- 両方向の移行（age → Infisical、Infisical → age）
- 利用者向けドキュメント

含まない:

- Infisical サーバの構築（`carmo-cdk#312` が持つ）
- Vaultwarden / OpenBao / Bitwarden の adapter（登録簿には載せられる形にするが、実装はしない）
- 参照ごとに別々の backend を使う運用（前提 5）
- 機密の履歴・監査ログの devbase 側での保持（サーバの WebUI が持つものを使う）
- `devbase env export` / `import` のバンドル形式の変更
- 複数人での同時編集の競合解決（サーバ側の最終書き込み優先に従う）

## 受け入れ条件

### backend の選択と切り替え

- [ ] 何も設定していない状態で `devbase env backend status` を実行すると、現在の backend 名
      （`age` または `plaintext`）と保存先のパスが表示され、終了コードが 0 になる
- [ ] `devbase env backend use infisical --url <URL> --project-id <ID>` を実行すると、設定が
      保存され、`devbase env backend status` の backend 名が `infisical` に変わる
- [ ] `devbase env backend use age` を実行すると backend 名が `age` に戻り、切り替え前に
      age ストアへ入っていた値が `devbase env get` で同じ値として取れる
- [ ] 前提: backend が `infisical` に設定済み
      操作: `devbase env backend test` を実行する
      結果: サーバへ到達できれば接続先 URL と読めた参照の件数が表示されて 0 で終了し、
      到達できなければ理由が表示されて非ゼロで終了する
- [ ] 未知の backend 名（例 `--backend vaultwarden`）を指定すると、利用できる backend 名の
      一覧を添えたエラーになり、設定は書き換わらない

### 日々の操作が backend を問わず同じ形で動く

- [ ] backend が `infisical` のとき、`devbase env list` の出力の列構成が `age` のときと同じになる
- [ ] backend が `infisical` のとき、`devbase env set KEY=VALUE` の直後に `devbase env get KEY`
      が同じ値を返す
- [ ] backend が `infisical` のとき、`devbase env delete KEY` の後に `devbase env get KEY` が
      「未設定」を示して非ゼロで終了する
- [ ] backend が `infisical` のとき、`devbase env set KEY=VALUE -p` で書いた値が、そのプロジェクト
      でのみ取得でき、別プロジェクトからは取得できない
- [ ] backend が `infisical` のとき、`devbase up` で起動したコンテナの環境変数に、サーバ上の
      機密が `age` のときと同じ変数名で載る
- [ ] backend が `infisical` のとき、`${OTHER_KEY}` のような参照記法を含む値を
      `devbase env set` で保存して `devbase env get` で取ると、保存した文字列がそのまま返る
- [ ] backend が `infisical` のとき、`devbase env export` にサーバ上の機密が収録され、
      `devbase env import` の取り込み先がサーバになる
- [ ] backend を設定していない状態の `devbase env import` は、これまでどおり対象ファイルを
      `backups/` へ複製し、age の受信者鍵を用意していなくても成功する
- [ ] backend が `infisical` のとき、`devbase env import` の退避は age 暗号化され、`backups/` に
      平文の機密が新たに増えない。受信者鍵が無い場合は 1 件も取り込まずに非ゼロで終了する

### 移行

- [ ] `devbase env backend migrate --to infisical --dry-run` は、移す参照とキーの**名前だけ**を
      一覧表示し、値を表示せず、サーバ側を 1 件も書き換えない
- [ ] `devbase env backend migrate --to infisical` は、移行先に同じキーがある場合、1 件も
      書き込まずに衝突したキー名を表示して非ゼロで終了する
- [ ] `devbase env backend migrate --to infisical` は、サーバへ書き込んだ後に**読み戻して元と
      一致することを確認**し、一致しなければこの実行で作成したキーだけを消して非ゼロで終了する。
      移行の前からサーバにあった機密は変わらない
- [ ] 移行の途中で失敗した場合、移行前の backend 設定のまま残り、`devbase env get` が移行前と
      同じ値を返す
- [ ] 移行元の機密は自動削除されず、退避先のパスが表示される（既存の `env encrypt` と同じ性質）

### サーバ不達時

- [ ] 前提: backend が `infisical`、キャッシュあり、サーバへ到達できない
      操作: `devbase up` を実行する
      結果: キャッシュの値でコンテナが起動し、「キャッシュを使った」旨と最終取得時刻が警告として出る
- [ ] 前提: backend が `infisical`、キャッシュなし、サーバへ到達できない
      操作: `devbase up` を実行する
      結果: コンテナは起動せず、到達できない旨と接続先 URL が表示されて非ゼロで終了する
- [ ] サーバへ通信できない、または応答が不正なとき、既存のキャッシュファイルの内容が変化しない
- [ ] サーバが機密 0 件の正常な応答を返したとき、キャッシュはその結果で置き換わる。その後
      サーバへ到達できない状態で `devbase up` を実行しても、削除済みの機密がコンテナへ渡らない
- [ ] キャッシュファイルは age で暗号化されており、平文の機密を含むファイルが新たに増えない
- [ ] 接続先 URL・project ID・environment のいずれかを変えた後は、変更前に取得したキャッシュが
      使われず、サーバへ到達できなければ非ゼロで終了する
- [ ] キャッシュの控えと、その取得元を表す指紋は同じ 1 ファイルに収まっており、一方だけが
      更新された組み合わせが残らない
- [ ] キャッシュの更新中にプロセスが止まっても、前回のキャッシュがそのまま残り、途中の状態が
      読まれない
- [ ] 別の接続先・project・environment を使う 2 つの実行が同時にキャッシュを書いても、残るのは
      どちらか一方の完全な世代だけで、控えと指紋の対応が崩れない

### 起きてはいけないこと

- [ ] 機密の**値**が、ログ・エラーメッセージ・`--dry-run` の出力のいずれにも現れない
- [ ] Infisical の client secret が、プロセス一覧（`ps`）から読める形でコマンド引数に載らない
- [ ] backend を設定していない既存利用者の挙動が変わらない（既存テストが書き換えなしで通る）
- [ ] `devbase env doctor` が、backend 設定とキャッシュの権限（`0600`）の不整合を検出する
- [ ] 新しい設定ファイルとキャッシュが Git から除外される（`git check-ignore` で確認できる）
- [ ] age の識別鍵が無い端末では、ブートストラップ機密が平文で保存も読み込みもされず、
      鍵の用意を促して非ゼロで終了する
- [ ] backend が `infisical` のときも `devbase env rekey` が実行でき、受信者から外した鍵では
      ブートストラップ機密もキャッシュも復号できない

## 非機能の条件

| 種類 | 条件 |
| --- | --- |
| 性能 | `devbase up` 1 回あたりの HTTP は認証 1 回 + 参照ごとに 1 回（共通とプロジェクトで最大 3 回）に収める。キーごとに繰り返さない。到達しないときは 5 秒でタイムアウトし、キャッシュへ落ちる |
| 容量 | キャッシュは参照ごとに 1 ファイル。既存の `secrets/` 配下と同じ構成に揃える |
| 権限 | 設定ファイル・キャッシュ・client secret を含むファイルはすべて `0600`。置き場のディレクトリは `0700` |
| 記録 | 取得元・参照名・件数・最終取得時刻を残す。**値と client secret は残さない** |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | **増える**。`devbase env backend` 系のサブコマンドを追加する。既存のサブコマンドの引数・出力形式は変えない |
| データ | スキーマ変更は無い。新しい設定ファイルとキャッシュのディレクトリが増える。移行は明示的なコマンドでのみ行い、自動では走らない |
| 既存の振る舞い | backend を設定するまで変わらない。`SecretStore` の生成箇所（11 か所）は変えず、`backend_for()` の内側で解決先が決まる。既定の解決結果は現状と同じ |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `devbase up` / `devbase env backend status` |
| テスト | `uv run pytest`（`pyproject.toml` の `testpaths = ["tests"]`） |
| 静的解析・型検査 | リポジトリに設定が無いため導入しない（この変更で新設もしない） |
| 手動確認 | 実サーバに対する `devbase env backend test` と `devbase up`。サーバの用意は `carmo-cdk#312` の完了を待つ。**サーバが無い間は、`http.server` ベースの偽サーバに対する結合テストで代替する** |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | backend の実装は `lib/devbase/env/` 配下へ置く。CLI の入口は `lib/devbase/commands/` と `lib/devbase/cli.py`。`bin/devbase`（シェル側）には機密を扱う処理を追加しない |
| コーディング規約 | 既存コードに合わせる（日本語の docstring、`devbase.log.get_logger`、例外は `DevbaseError` 派生）。確認手段は `ndf:code-reviewer` によるレビュー |
| テスト戦略 | backend 実装は単体テスト（`tests/env/`）。CLI は既存の `tests/commands/` に倣う。サーバとの往復は偽サーバに対する結合テストで担保し、実サーバは手動確認に回す |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、`SecretStore.backend_for()` 内での backend 解決、値をログへ出さないことの確認 |
| 確認してから行う | `pyproject.toml` への依存追加、既存サブコマンドの引数変更、`secrets/` 配下のレイアウト変更 |
| 行わない | 依頼範囲外のリファクタリング、Infisical 以外の adapter の実装、サーバ側の構築 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| Infisical サーバの接続先 URL と認証方式（machine identity の払い出し方） | `carmo-cdk#312` の担当 | サーバ構築時 |
| チームで共有するとき、機密を Infisical の 1 プロジェクトに集約するか利用者ごとに分けるか | 利用者 | 実サーバでの手動確認まで |
