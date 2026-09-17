# .envのCLI、Web管理化

## 目的
* 現在、devbaseでの機密情報の管理は独自実装のsecrets管理を利用している
* しかし、WebUIもなく、管理が複雑
* OSSサーバが導入されている場合、その設定のみをsecretsで管理することで、WebUIでの管理ができるようにしたい

## ミッション
* devbaseの機密情報管理を代替できるソリューションを選択してください
    * この文章下部にchatgptの提案があるので参考にする
    * 自身でも調査すること
* 現在の機密情報管理機構と入れ替えられるようにしてください
    * コマンドで設定可能
    * いつでも入れ替え可能とする

## ChatGPTの提案

第一候補は **Vaultwarden** です。Bitwarden互換の軽量なOSSサーバーで、1Passwordにかなり近い使い方ができます。

| 製品               | Linux CLI | Web管理画面 |        ブラウザ拡張 | 主な用途               | OSS性        |
| ---------------- | --------: | ------: | ------------: | ------------------ | ----------- |
| **Vaultwarden**  |    ◎ `bw` |       ◎ | ◎ Bitwarden拡張 | 個人・小規模チーム          | AGPL        |
| **Bitwarden公式版** |    ◎ `bw` |       ◎ |             ◎ | 組織利用・公式サポート重視      | コアはOSS、一部商用 |
| **Passbolt CE**  |         ○ |       ◎ |             ◎ | チームでの認証情報共有        | OSS         |
| **Infisical**    |         ◎ |       ◎ |             △ | `.env`、APIキー、CI/CD | OSSコア＋商用機能  |
| **OpenBao**      |   ◎ `bao` |       ◎ |             ― | インフラ・動的シークレット      | OSS         |

## 最有力：Vaultwarden

[Vaultwarden](https://github.com/dani-garcia/vaultwarden)は、Rustで実装された非公式のBitwarden互換サーバーです。

* Dockerコンテナ1個で比較的軽量
* ブラウザ上のWeb Vaultからパスワードを登録・編集
* Chrome／FirefoxなどのBitwarden拡張が利用可能
* Linuxでは公式Bitwarden CLIの`bw`を利用
* スマートフォンアプリもBitwarden公式クライアントを利用可能
* 個人保管庫、組織、コレクション、TOTP、添付ファイルなどに対応
* 保存データはクライアント側で暗号化

CLIは次のように接続します。

```bash
bw config server https://vault.example.com
bw login
bw unlock
bw list items
bw get password example-service
```

Bitwarden公式CLIはLinux対応で、セルフホスト先も指定できます。[Bitwarden CLI公式ドキュメント](https://bitwarden.com/help/cli/)、[セルフホスト接続方法](https://bitwarden.com/help/change-client-environment/)

注意点は、Vaultwarden自体はBitwarden社の公式製品ではないことです。Bitwardenクライアントの更新後に、一時的な互換性問題が起きる可能性があります。また、Web VaultにはHTTPSが必要です。

## 公式性を重視するならBitwarden

[Bitwarden公式セルフホスト版](https://bitwarden.com/help/self-host-bitwarden/)も、Web Vault、ブラウザ拡張、Linux CLIをすべて備えています。

以前は構成がやや重めでしたが、現在はDocker Composeで動かせる[Bitwarden Lite](https://bitwarden.com/help/install-and-deploy-lite/)も用意されています。

ただし、厳密には製品全体が完全なOSSではありません。

* 個人向けクライアントと主要サーバー部分：GPL／AGPL
* SSOや一部企業向けモジュール：Bitwarden独自ライセンス
* 一部の組織・Premium機能：ライセンスファイルが必要

ライセンス構成は[公式FAQ](https://github.com/bitwarden/server/blob/main/LICENSE_FAQ.md)に記載されています。

会社で利用し、公式サポートや将来の互換性を優先するなら、Vaultwardenよりこちらが安全です。

## チーム共有中心ならPassbolt CE

[Passbolt Community Edition](https://github.com/passbolt/passbolt_api)は、チームでサーバーや外部サービスの認証情報を共有する用途に強いOSSです。

* Web画面で共有パスワードを管理
* ユーザーやグループ単位の細かい共有権限
* 操作履歴・監査を意識した設計
* ブラウザ拡張あり
* Docker／Linuxへのセルフホスト対応

Linux CLIには[go-passbolt-cli](https://github.com/passbolt/go-passbolt-cli)があります。ただし、公式Bitwarden CLIほど製品の中心的な位置付けではなく、README上もコミュニティ主導とされています。CLI利用を重視するならVaultwardenの方が無難です。

## `.env`やAPIキーも管理したい場合

以前検討されていた「`.env`の認証情報を暗号化してLinux CLIから展開する」用途なら、Vaultwardenより **Infisical** の方が向いています。

```bash
infisical run -- npm start
infisical run -- python app.py
```

`.env`ファイルを平文で配置せず、実行時にシークレットを環境変数へ注入できます。WebダッシュボードとCLIがあり、セルフホストにも対応しています。[ローカル開発での利用方法](https://infisical.com/docs/documentation/guides/local-development)、[セルフホスト方法](https://infisical.com/docs/self-hosting/overview)

よりインフラ寄りで、動的クレデンシャル、PKI、細かなポリシーまで必要なら[OpenBao](https://openbao.org/docs/what-is-openbao/)があります。HashiCorp Vaultの完全OSS系フォークですが、日常的なパスワード管理には少し大げさです。

結論としては、次の構成がおすすめです。

* 人間が使うID・パスワード、TOTP、SSH鍵：**Vaultwarden**
* `.env`、DBパスワード、APIキー、CI/CD：**Infisical**
* 会社導入で公式サポートが必要：**Bitwarden公式セルフホスト版**

個人または数人のチームであれば、まずVaultwardenをDocker Composeで構築するのが最も1Passwordに近く、CLI要件にもきれいに合います。
