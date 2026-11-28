# トップレベルコマンド

[CLI リファレンス目次に戻る](README.md)

## `devbase init`

devbase の初期セットアップを実行します。

```
devbase init
```

実行内容:
- `bin/devbase` を PATH に追加（`~/.bashrc` / `~/.zshrc`）
- シェル補完スクリプトの登録
- `plugins.yml` の作成（存在しない場合）
- ホストの userns の制限を外す（[`devbase userns apply`](#devbase-userns) と同じ。`DEVBASE_SKIP_USERNS` に空でない値があれば飛ばす）

userns の手順が失敗しても（権限が無い・sudo のパスワードの入力を中断したなど）、警告と打つコマンドを出して `init` は終了コード 0 で終わります。

## `devbase status`

現在の環境の状態をまとめて表示します。

```
devbase status
```

表示項目:
- コンテナの状態（起動中 / 停止中 / 未ビルド）
- インストール済みプラグイン一覧
- 環境変数の設定状況
- スナップショットの状態

## `devbase userns`

ホストの userns の制限（`kernel.apparmor_restrict_unprivileged_userns = 1`）を確かめる・外す・戻すコマンドです。
Ubuntu 23.10 以降のホストでは、この制限が dev コンテナの中の bubblewrap（codex のサンドボックスなど）による
user namespace の作成を止めます。ホストの設定だけでは codex のサンドボックスは動きません（Docker の既定の seccomp も止めます）。

```
devbase userns status
devbase userns apply
devbase userns revert
```

| サブコマンド | 何をするか | 終了コード |
| --- | --- | --- |
| `status` | ホストの値・devbase の sysctl の設定ファイル（パスと目印の有無）・コンテナが動くカーネルの値を出す。何も変えない | 0 |
| `apply` | 制限があれば、説明を出してから `/etc/sysctl.d/60-devbase-userns.conf` を置き、`sysctl -p` でその場で 0 にする | 0: 外した・制限が無い・変えないと決めた（キーが無い・コンテナの中・既に 0）。1: 権限が得られない・入力を中断した・同じパスに devbase のものでないファイルがある・`sysctl -p` が失敗した |
| `revert` | 1 行目に目印（`# devbase:`）のある devbase のファイルだけを消し、このキーだけを設定ファイルの上の値へ `sysctl -w` で戻す | 0: 消した・ファイルが無い。1: 目印が無い・権限が得られない・消せない・値を戻せない |

- 権限は root・パスワード無しの sudo・端末で尋ねる sudo（`sudo -v`）の順に選びます。どれも無ければ変えずに打つコマンドを出します
- 読む順で後のファイル（例 `99-hardening.conf`）が 1 を書くときは、そのパスを挙げて再起動の後に 1 へ戻ると警告し、そのファイルには触りません
- コンテナが動くカーネルの値は、手元にある `devbase-base:latest` を `--pull never` で起動したコンテナから読みます。イメージは取得しません。イメージが無ければ `docker info` の `SecurityOptions` で出し分けます
- macOS など、コンテナが VM の中で動く端末では VM の設定を変えず、VM の値が 1 なら変え方の案内だけを出します
- `revert` は、devbase のファイルを消した後に設定ファイルの上の値が無ければ、今の値を変えません（再起動の後はカーネルの既定になります）
- devbase が無くても戻せます: `sudo rm /etc/sysctl.d/60-devbase-userns.conf && sudo sysctl --system`

## `bin/rc`（いまのシェルで有効化）

`devbase init` 後に **いま開いているシェル**で devbase（PATH / 補完）を即時有効化するための source 用スクリプトです。`devbase` のサブコマンドではなく、`bin/rc` を直接 source して使います。

```bash
./bin/devbase init
. ./bin/rc        # = source ./bin/rc （bash / zsh 共通）
```

`bin/rc` は自身の場所から `DEVBASE_ROOT` を解決し、`DEVBASE_ROOT/bin` を PATH へ追加（冪等）したうえで、シェル補完を読み込みます（`init` が rc ファイルへ追記する有効化と同じ内容）。新しく開くシェルは init が rc に追記したブロックで自動有効化されるため、この手順は不要です。
