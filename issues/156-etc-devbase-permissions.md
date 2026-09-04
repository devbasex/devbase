# `/etc/devbase` permission 修正

## モード

standard: baseイメージから起動する全コンテナのシェル初期化を修正する。

## 目的と非目的

達成したい状態:

- `/etc/devbase`を全ユーザーが探索できる`0755`で作成する。
- `ai-cli-aliases.sh`自体は書き換え不要な`0644`を保つ。
- ubuntuユーザーのbash起動時にalias定義を読み込める。

やらないこと:

- AI CLIのalias内容や起動オプションは変更しない。
- 実行中コンテナへの暫定`chmod`を恒久策として扱わない。

## 受け入れ条件

- [ ] Dockerfileが`/etc/devbase`を`0755`で明示作成してからaliasファイルを配置する。
- [ ] no-cacheビルドしたイメージで、`/etc/devbase`が`0755`、aliasファイルが`0644`になる。
- [ ] ubuntuユーザーが`/etc/devbase/ai-cli-aliases.sh`をsourceできる。
- [ ] 既存のAI CLI aliasテストと全体テストが成功する。

## 設計

異常が最初に生じるDockerfileで親ディレクトリを明示作成する。entrypointで毎回`chmod`する案は、
壊れたイメージを実行時に補正する下流対応になるため採用しない。ファイルとディレクトリでは必要な
permissionが異なるため、ubuntuユーザーへ切り替えた後でもroot所有で作成できる
`RUN sudo install -d -m 0755 /etc/devbase`と`COPY --chmod=0644`を分ける。

## 実装計画

1. Dockerfileの作成順を検査する失敗テストを追加する。
2. COPY前に`/etc/devbase`を`0755`で作成する。
3. 限定テスト、全体テスト、静的検査を実行する。
4. no-cacheビルドした実イメージでpermissionとsourceを確認する。

## 関連

- https://github.com/devbasex/devbase/issues/156
- https://github.com/devbasex/devbase/pull/154
