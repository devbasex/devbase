## 結論

devbase の標準バックエンドには、**SOPS + age** を推奨します。

SOPS は `.env`、YAML、JSON、INIなどを暗号化したまま編集・Git管理でき、暗号鍵には age、AWS KMS、GCP KMS、Azure Key Vaultなどを選べます。`sops exec-env` で、平文ファイルを作らず子プロセスだけに復号済み環境変数を渡すこともできます。複数利用者への暗号化、鍵追加・削除、データキーのローテーションにも対応しています。CNCF Sandboxプロジェクトで、2026年7月23日にも v3.13.3 がリリースされており、現在も活発に保守されています。 ([GitHub][1])

さらに devbase は、すでに `devbase env export/import` のバンドル暗号化で age を採用しています。したがって暗号方式を増やすのではなく、**既存の age をSOPSの鍵バックエンドとして日常運用にも広げる**形になります。

## 候補比較

| 候補             | 特徴                                                |            devbaseへの適合 |
| -------------- | ------------------------------------------------- | ---------------------: |
| **SOPS + age** | 汎用的な暗号化設定ファイル管理。ENV対応、複数recipient、KMS移行、ローテーション対応 |             **◎ 第一推奨** |
| **dotenvx**    | `.env`専用。導入・操作が非常に簡単                              | ○ 簡単だがdevbaseでは追加対応が多い |
| **Infisical**  | サーバー型Secrets Manager。権限、監査、履歴、ローテーション             |           ○ チーム・企業利用向け |
| **age単体**      | 単純で堅牢なファイル暗号化                                     |         △ CRUDや差分管理が弱い |
| **git-crypt**  | Git filterによる透過的暗号化                               |           × 今回の目的には不向き |

### SOPS + age

最も「一般的」「ツール非依存」「将来拡張可能」に寄っています。

SOPSは値だけを暗号化し、環境変数名は読める状態で残すため、どのキーが変更されたかGit上で確認できます。開発者ごとにage公開鍵を登録でき、`.sops.yaml` と `sops updatekeys` でメンバー追加・削除、`sops rotate` でデータキー更新ができます。ローカルではage、CIではAWS KMSやGCP KMS、といった切り替えも同じファイル形式のまま可能です。 ([GitHub][1])

### dotenvx

`.env`に特化するなら、操作性は最も分かりやすいです。

```bash
dotenvx encrypt
dotenvx set OPENAI_API_KEY ...
dotenvx run -- command
```

暗号化後もキー名を残して値だけを暗号化し、秘密鍵は `.env.keys` や外部Secrets Managerで管理します。公式にもDocker Compose向けの手順があります。 ([GitHub][2])

ただし、公式のDocker連携は基本的に「コンテナ内にdotenvxを入れ、アプリケーションコマンドを `dotenvx run --` で包む」方式です。devbaseはアプリを直接起動するというより、`tail -f /dev/null` で開発コンテナを維持し、その後 `devbase login` やVS Code Attachで新しいプロセスを起動します。そのため、PID 1だけをdotenvxで包んでも、後から `docker exec` したシェルにはその環境変数が自動的には引き継がれません。devbaseでは結局、ログイン・エディタ接続・deployなどを個別に包む必要があります。  ([Dotenvx][3])

dotenvxは悪くありませんが、**普通のWebアプリには簡単、開発コンテナマネージャーであるdevbaseにはやや相性が悪い**という評価です。

### Infisical

チーム利用で次が必要なら有力です。

* 誰がどの秘密にアクセスできるか
* 監査ログ
* シークレットの履歴・復元
* 定期ローテーション
* 開発、ステージング、本番の一元管理
* Gitに暗号文すら置かない

CLIから `infisical run -- command` として子プロセスへ注入でき、Linux/macOSに対応しています。Cloud版とセルフホスト版があります。 ([GitHub][4])

一方、サーバー、アカウント、認証トークン、ネットワーク接続などが必要になります。devbaseのデフォルト機能にすると重いため、将来的な**オプションSecrets Provider**として用意するのがよいでしょう。

### age単体・git-crypt

age単体はシンプルで堅牢ですが、ファイル全体を一つの暗号データとして扱うので、環境変数単位の編集、マージ、Git差分、recipient管理をdevbase側で再実装することになります。ageはSOPSの暗号バックエンドとして使う方が自然です。 ([GitHub][5])

git-cryptはチェックアウト後の作業ファイルが平文になり、ローカルディスク上の常置平文をなくす目的には合いません。また公式にも、Git filterは暗号化目的で設計されたものではないこと、アクセス取り消しや鍵ローテーションに対応していないことが制約として記載されています。 ([GitHub][6])

## devbaseの現状で必要になる変更

現在のdevbaseには、次の二つの平文機密ファイルがあります。

* `$DEVBASE_ROOT/.env`
* `$DEVBASE_ROOT/projects/<name>/.env`

`bin/devbase` はルート `.env` を直接 `source` し、プロジェクト側 `.env` はDocker Composeの `env_file` に読み込ませています。`EnvFile.save()` も値を平文で保存し、権限だけを `0600` にしています。

サンプルComposeも次の構造です。

```yaml
env_file:
  - ${DEVBASE_ROOT}/.env
  - env
  - .env
```

したがって、`.env`をSOPSで暗号化するだけでは動きません。Composeの `env_file` はSOPS暗号文を復号せず、そのまま環境変数値として扱ってしまいます。  ([Docker Documentation][7])

## 推奨するファイル構成

非機密設定と機密情報を明確に分けます。

```text
$DEVBASE_ROOT/
├── env                              # グローバル非機密設定
├── .sops.yaml                       # recipient公開鍵
├── secrets/
│   ├── global.env                   # SOPS暗号化済み
│   └── projects/
│       ├── project-a.env            # SOPS暗号化済み
│       └── project-b.env
└── projects/
    └── project-a/
        ├── env                      # 従来どおりGit管理する非機密設定
        └── compose.yml
```

SOPSは拡張子から形式を判定するため、暗号化されたファイルでも末尾を `.env` にしておくと扱いやすくなります。内容は次のように値だけが暗号文になります。

```dotenv
OPENAI_API_KEY=ENC[AES256_GCM,...]
AWS_SECRET_ACCESS_KEY=ENC[AES256_GCM,...]
sops_age__list_0__map_recipient=age1...
...
```

`.sops.yaml` は例えば次のようにします。

```yaml
creation_rules:
  - path_regex: '(^|/)secrets/.*\.env$'
    age: >-
      age1alice...,
      age1bob...
```

## devbaseへの組み込み方

### 1. `SopsSecretStore`を追加する

現在の `EnvFile` の上位に、暗号化ストレージのインターフェースを置きます。

```python
class SecretStore(Protocol):
    def load(self, scope: str) -> dict[str, str]: ...
    def set(self, scope: str, key: str, value: str) -> None: ...
    def delete(self, scope: str, key: str) -> None: ...
    def edit(self, scope: str) -> None: ...
```

最初の実装を `SopsSecretStore` とし、暗号処理は自前実装せず、SOPS CLIへ委譲します。

* `devbase env init`
* `devbase env sync`
* `devbase env set`
* `devbase env get`
* `devbase env delete`
* `devbase env edit`

既存のCLI UXは維持し、保存先だけをSOPSに変えます。

### 2. 起動時にメモリ上でマージする

優先順位は現在と同じでよいでしょう。

```text
グローバル暗号化secret
    ↓
プロジェクトの公開env
    ↓
プロジェクト暗号化secret
```

`devbase up` がSOPSで復号し、Pythonの辞書または子プロセス環境上でマージします。恒久的な平文 `.env` は作りません。

SOPSには、平文ファイルを作らず子プロセスだけに環境変数を渡す `exec-env` もあります。devbase内部では複数ファイルの優先順位処理が必要なので、`sops decrypt` の標準出力をメモリ上でパースして `subprocess.run(..., env=...)` に渡す実装の方が扱いやすそうです。 ([GitHub][1])

### 3. Composeにはキー名だけ渡す

プラグインの `compose.yml` から機密 `.env` の指定を外します。

```yaml
services:
  dev:
    env_file:
      - env
```

devbaseが実行時に次のようなoverride Composeを生成します。

```yaml
services:
  dev:
    environment:
      - OPENAI_API_KEY
      - ANTHROPIC_API_KEY
      - AWS_ACCESS_KEY_ID
      - AWS_SECRET_ACCESS_KEY
```

値なしの `environment` エントリは、Compose実行プロセスの環境変数から値を受け取ります。また `environment` は `env_file` より優先されます。これにより、暗号文や平文ファイルをComposeへ直接渡さずに済みます。 ([Docker Documentation][7])

初期実装を簡単にするなら、復号結果を権限 `0600` の一時ファイルに書き、生成したoverride Composeの `env_file` に指定し、`docker compose up` 完了後に削除する方法もあります。ただしmacOSでは短時間とはいえ平文がディスクに置かれるため、最終形としてはメモリ経由を推奨します。

## age鍵の管理方針

SSH鍵の流用も技術的には可能ですが、**devbase専用のage鍵を開発者ごとに作る**方がよいです。SSH鍵と暗号化鍵では、失効・保管・バックアップのライフサイクルが異なるためです。ageの公式ドキュメントも、SSH鍵は長期的な復号鍵として保護されていない可能性がある点に注意を促しています。 ([GitHub][5])

```bash
mkdir -p ~/.config/devbase/age
chmod 700 ~/.config/devbase/age

age-keygen -o ~/.config/devbase/age/keys.txt
chmod 600 ~/.config/devbase/age/keys.txt

age-keygen -y ~/.config/devbase/age/keys.txt
```

LinuxとmacOSではSOPSのデフォルト鍵配置先が異なるため、devbase側で明示的に統一すると運用が楽です。

```bash
export SOPS_AGE_KEY_FILE="$HOME/.config/devbase/age/keys.txt"
```

SOPSはこの環境変数による鍵パス指定を正式にサポートしています。 ([GitHub][1])

チームでは、秘密鍵を共有せず、各メンバーの公開鍵を `.sops.yaml` に登録します。退職・異動時はrecipientを削除して `sops updatekeys` と `sops rotate` を行い、必要に応じてAPIキーそのものもローテーションします。

## セキュリティ上の限界

SOPSによって守られるのは、主に次の範囲です。

* ローカルディスク上の保存ファイル
* Gitリポジトリ
* バックアップ
* ファイル転送中

実行時には、最終的に環境変数として平文になります。Dockerもパスワードなどの機密値については、環境変数よりCompose Secretsの利用を推奨しています。Compose Secretsはホスト環境変数やファイルを元に、コンテナ内の `/run/secrets/` にファイルとして提供できます。 ([Docker Documentation][8])

そのため将来的には、

* AWS：AWS SSOまたは `~/.aws` のread-only mount
* GCP：credential JSONをCompose Secretとしてmount
* Git：SSH Agentまたはcredential helper
* 一般APIキー：環境変数

というように、認証方式ごとに環境変数以外も選べるとさらに安全です。

## 最終推薦

devbaseの標準構成は次の方針が最もバランスがよいです。

> **標準：SOPS + 開発者ごとのage鍵**
> **将来オプション：SOPS + クラウドKMS、またはInfisical Provider**

dotenvxは単独アプリにはかなり魅力的ですが、devbaseのような対話型開発コンテナではSOPSをホスト側に一度組み込む方が、Linux/macOS、Docker Compose、VS Code Attach、複数プロジェクト、将来のKMS対応まで一貫して扱えます。

[1]: https://github.com/getsops/sops?utm_source=chatgpt.com "GitHub - getsops/sops: Simple and flexible tool for managing secrets · GitHub"
[2]: https://github.com/dotenvx/dotenvx?utm_source=chatgpt.com "GitHub - dotenvx/dotenvx: a secure dotenv–from the creator of `dotenv` · GitHub"
[3]: https://dotenvx.com/docs/secrets-in-docker-compose?utm_source=chatgpt.com "Encrypt a .env file for Docker Compose · Dotenvx"
[4]: https://github.com/infisical/infisical?utm_source=chatgpt.com "GitHub - Infisical/infisical: Infisical is the open-source platform for secrets, certificates, and privileged access management. · GitHub"
[5]: https://github.com/filosottile/age?utm_source=chatgpt.com "GitHub - FiloSottile/age: A simple, modern and secure encryption tool (and Go library) with small explicit keys, no config options, and UNIX-style composability. · GitHub"
[6]: https://github.com/AGWA/git-crypt?utm_source=chatgpt.com "GitHub - AGWA/git-crypt: Transparent file encryption in git · GitHub"
[7]: https://docs.docker.com/reference/compose-file/services/?utm_source=chatgpt.com "Define services in Docker Compose | Docker Docs"
[8]: https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/?utm_source=chatgpt.com "Set environment variables within your container's environment | Docker Docs"
