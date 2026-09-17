# AI CLI alias の読み込み

## 概要

base イメージは、AI CLI の起動 alias を `/etc/devbase/ai-cli-aliases.sh` に配置し、一般ユーザーの
Bash 初期化時に読み込める状態を提供する。

## 仕様

`/etc/devbase` は root 所有、permission `0755` で明示的に作成する。その後、
`ai-cli-aliases.sh` を root 所有、permission `0644` で配置する。

Dockerfile では一般ユーザーへ切り替えた後にこの設定を行うため、ディレクトリ作成には
`sudo install -d -m 0755 /etc/devbase` を使用する。ファイル用の `COPY --chmod=0644` に親
ディレクトリの暗黙作成を任せると、親も `0644` になって一般ユーザーが配下を探索できないため、
ディレクトリ作成は必ず `COPY` より先に行う。

常に次の条件を保つ。

- `/etc/devbase` は全ユーザーが探索できる `0755` とする。
- `/etc/devbase/ai-cli-aliases.sh` は全ユーザーが読み取れる `0644` とする。
- 一般ユーザーは alias ファイルを source できる。
- alias の内容と起動オプションは、ディレクトリ permission の設定から独立させる。

## 運用

設定変更は base イメージの再ビルドとコンテナの再作成後に反映される。既存コンテナで
`/etc/devbase` が `0644` の場合は root で `chmod 0755 /etc/devbase` を実行すれば一時復旧できるが、
恒久対応には修正済みイメージを使用する。

## テスト観点

- Dockerfile が `/etc/devbase` を `0755` で作成してから alias ファイルを配置すること。
- ビルドしたイメージで `/etc/devbase` が `0755`、alias ファイルが `0644` になること。
- `ubuntu` ユーザーが `/etc/devbase/ai-cli-aliases.sh` を source できること。
- 既存の AI CLI alias 定義と起動オプションが変わらないこと。

## 関連リンク

- [Issue #156](https://github.com/devbasex/devbase/issues/156)
