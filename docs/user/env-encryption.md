# 環境変数の暗号化

devbase が扱う認証情報（クラウドのアクセスキー、コード管理サービスの個人アクセストークン、各種 AI サービスの API キーなど）を、保存時に暗号化して持つためのガイドです。

暗号化しない運用も引き続き可能です。移行は明示的なコマンドで行い、いつでも平文へ戻せます。

## 何が変わるのか

| | 暗号化しない場合（既定） | 暗号化した場合 |
|---|---|---|
| 共通の機密 | `$DEVBASE_ROOT/.env` | `$DEVBASE_ROOT/secrets/global.env.age` |
| プロジェクトの機密 | `projects/<name>/.env` | `secrets/projects/<name>.env.age` |
| コンテナへの渡り方 | 構成ファイルが平文を直接読む | devbase が復号し、変数名だけを列挙した構成で渡す |
| 日々の操作 | `devbase env set` / `get` / `edit` … | **変わらない** |

保存先はファイルの存在から自動で判定されます。暗号化ファイルがあればそれを、無ければ平文を使います。

## 使いはじめる

### 1. 鍵を作る

```bash
devbase env keygen
```

鍵は `~/.config/devbase/age/keys.txt` に `0600` で作られます。場所を変えたい場合は `DEVBASE_AGE_KEY_FILE` を設定してから実行してください。

> **鍵のバックアップは必須です。** この鍵を失うと、暗号化した機密は誰にも復号できません（devbase 側にも復旧手段はありません）。パスワード管理ツールなど、端末とは別の場所へ必ず複製してください。

### 2. 何が変わるか確認する

```bash
devbase env encrypt --dry-run
```

暗号化される設定の一覧と、各プロジェクトの `compose.yml` に加わる変更の差分が表示されます。

### 3. 暗号化する

```bash
devbase env encrypt
```

次の順で処理されます。

1. 平文の設定を暗号化して `secrets/` 配下へ保存する
2. **暗号化した内容を読み戻して元と一致することを確認**する
3. 元の平文を `backups/env-encrypt/<日時>/` へ退避する
4. 各プロジェクトの `compose.yml` から機密ファイルの参照をコメントアウトする

途中で失敗した場合は、それまでの変更をすべて巻き戻して中止します。「一部だけ暗号化され、構成は存在しないファイルを参照したまま」という状態にはなりません。

### 4. 退避された平文を消す

`encrypt` は元の平文を**自動では削除しません**。内容を確認してから、案内されたディレクトリを削除してください。

```bash
rm -rf $DEVBASE_ROOT/backups/env-encrypt/<日時>
```

消し忘れは `devbase env doctor` が指摘し続けます。

## 日々の操作

暗号化しても操作は変わりません。

```bash
devbase env list              # 暗号化された保存先には [暗号化] と表示される
devbase env set KEY=VALUE
devbase env get KEY
devbase env delete KEY --project
devbase env edit              # 復号 → 編集 → 再暗号化
```

`devbase env edit` は、復号結果を自分専用の一時ディレクトリへ `0600` で書き、編集後に暗号化し直してから必ず削除します。エディタへ値を渡す手段が他に無いため、**この操作の間だけ平文が一瞬ディスクに載ります**。

## 点検する

```bash
devbase env doctor
```

以下を確認し、問題があれば非ゼロで終了します。

- 鍵ファイルとその置き場の権限
- 暗号化ファイルと平文が同時に存在していないか
- 移行時・取り込み時に退避された平文が残っていないか
- 日時付きの控えファイル（`.env.bak-20260807172231` など）が残っていないか
- `.env` / `secrets/` 配下 / `.env.bak-<日時>` / `projects/<name>/.env` が実際に Git から除外されるか（`git check-ignore` で Git 自身に判定させます。`DEVBASE_ROOT` が Git リポジトリでなければ「確認できませんでした」と報告します）

## チームで共有する

各メンバーの公開鍵を受信者として登録すると、秘密鍵を渡さずに同じファイルを共同で使えます。

```bash
# 同僚の公開鍵を追加して、既存の機密をまとめて暗号化し直す
devbase env rekey --add-recipient age1xxxxxxxx...

# 抜けた人を外す
devbase env rekey --remove-recipient age1xxxxxxxx...

# 何が変わるか先に見る
devbase env rekey --add-recipient age1xxxxxxxx... --dry-run
```

受信者は `$DEVBASE_ROOT/secrets/recipients.txt` に記録されます。中身は公開鍵だけですが、第三者が自分の鍵を追記できると以後の暗号化がその相手にも復号可能になるため、`0600` で保護されます。

自分の公開鍵を受信者から外そうとすると警告が出ます。外したまま再暗号化すると、その端末では機密を復号できなくなります。

## 持ち運ぶ

`devbase env export` / `import` はそのまま使えます。書き出しは暗号化された機密を復号してバンドルへ入れ、バンドル自体を age で暗号化します。取り込み先が暗号化されていれば、**取り込み結果も暗号化されたまま**保存されます（平文の `.env` は作られません）。

```bash
devbase env export bundle.dbenv --recipient age1xxxxxxxx...
devbase env import bundle.dbenv --identity ~/.config/devbase/age/keys.txt
```

## 平文へ戻す

```bash
devbase env decrypt
```

`compose.yml` のコメントアウトも元に戻り、暗号化前の状態へそのまま復帰します。コメントや空行、`export KEY=value` 表記も失われません（`devbase env set` などで値を書き換えた場合は、平文だけで運用していたときと同じく整形されます）。

## 守れること / 守れないこと

**守れること**: 端末のディスク上に残る保存ファイル、バックアップ、クラウド同期フォルダ、ファイル転送中、リポジトリへの誤コミット、画面共有時の誤表示。

**守れないこと**:

- **実行時の平文化**: 最終的にコンテナへは環境変数として平文で渡ります
- **コンテナ環境の可視性**: コンテナの詳細情報を参照できれば、注入済みの環境変数は読めます。devbase は開発コンテナに Docker の制御ソケットを渡す構成を既定に含むため、コンテナ内から他コンテナの環境変数も参照できます
- **構成の展開結果**: `docker compose config` は変数名だけの列挙を実際の値へ解決して表示します
- **利用者権限を得た攻撃者**: 既定の鍵保管では鍵も同時に読めます

既定の保護は「鍵が錠前の隣にある」状態です。端末上で利用者権限を得た攻撃者は防げません。これは実運用上の妥協として明示しています。

## 関連

- [CLI リファレンス: env グループ](cli-reference/03-env.md)
- [環境変数の一覧](environment-variables.md)
- [バンドルの書き出しと取り込み](env-export-import.md)
