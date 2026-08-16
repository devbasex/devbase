# Orca / コンテナ内 sshd 廃止に伴う移行ガイド（breaking change）

## 要旨

base image から **sshd を廃止**し、`devbase orca` 連携および `ENABLE_SSH` / `SSH_AUTHORIZED_KEYS`
などの SSH 公開まわりの CLI・compose・env を削除しました。
これにより、**コンテナへ直接 SSH で入る従来の Orca 接続は使えなくなります**（breaking change）。

## 影響

- 「**Windows Orca → Mac → コンテナ内 sshd**」でコンテナに接続していた利用者。
- 旧 project env に残る `ENABLE_SSH=true` / `SSH_AUTHORIZED_KEYS` 等は**無視されます**（エラーにはなりません）。

## 暫定の代替（agent orchestration 実装前）

コンテナ内 sshd の代わりに、**Windows の VS Code を Mac へ Remote-SSH** し、Mac 上から
コンテナに入る運用に切り替えてください。

1. Mac 側で **Remote Login (sshd)** を有効化する（システム設定 > 一般 > 共有 > リモートログイン。
   既に有効な環境なら不要）。
2. Windows の VS Code に **Remote-SSH 拡張**を入れ、Mac（`takemi_ohama@<Mac-LAN-IP>`）へ接続する。
3. 接続した Mac 上で対象プロジェクトに移動してコンテナへ入る。従来どおり git worktree / AI CLI が使える。

   ```bash
   cd $DEVBASE_ROOT/projects/<project>
   devbase up      # 未起動なら
   devbase login   # 既定は index 1。複数コンテナなら `devbase login 2` のように index を指定
   ```

   （または `devbase list` の TUI からプロジェクト/コンテナを選んで login することもできる。）

## 今後

`devbase login`（= `docker exec`）が、コンテナに入る標準経路です。

当初は devbase 独自の agent orchestration（`docker exec` + tmux + VS Code Extension）を
追加する検討を進めていましたが、Claude アプリ本体に同等の機能が追加されたため**廃案**としました。
当時の検討記録は [`issues/old/i34-orcalike.md`](../../issues/old/i34-orcalike.md) にあります。
複数コンテナの AI セッションを束ねたい場合は、Claude アプリ側の機能を利用してください。

## クリーンアップ（任意）

生成済みの `~/.config/devbase/orca/ssh_config` と `/persistent/ai/ssh` は**自動削除しません**。
不要であれば手動で削除できます。
