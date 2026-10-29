# #507 Dev Containers の共有の置き場（`vscode` ボリューム）を dev インスタンスへ付ける

## 概要

dev インスタンスへ、Dev Containers 拡張が共有の置き場として認める名前のボリューム `vscode` を `/vscode` に付ける。
拡張のキャッシュと VS Code Server の新しい版の本体が docker ホストごとの置き場へ移り、VS Code を動かしている手元の端末の
キャッシュを通らなくなる。別の CPU 向けの拡張（`-linux-arm64` など）が手元のキャッシュを介して送られ、attach が止まる
問題（#507）を断つ。

## 背景

Dev Containers 拡張（0.469.0）の attach の振る舞い（`dist/extension/extension.js` で確かめた）:

| 条件 | 拡張のキャッシュ | 本体の新しい版 |
| --- | --- | --- |
| コンテナに名前が `vscode` のボリュームが付いていない（今の devbase） | 手元の端末の 1 つのフォルダー。attach のたびにコンテナから写し戻し、次の attach で拡張の名前の頭だけで選んで tar で送る。CPU の種類を見ない | `~/.vscode-server/bin/<commit>`（#463 の共通の VS Code Server ボリューム） |
| 付いている | `<マウント先>/vscode-server/extensionsCache`。root で写し、新しい 500 ファイルまで残す。取り込みはコンテナの中のリンク | `<マウント先>/vscode-server/bin/<linux-x64 などの build>/<commit>` に root で置き、`~/.vscode-server/bin/<commit>` にリンクを張る。新しい 9 版まで残す |

判定は `Mounts` の `Type === "volume" && Name === "vscode"` で、マウント先は問わない。

## 対象範囲

含む:

- `vscode` ボリュームの用意と、dev インスタンスへの付け方
- 起動の後の処理での、`/vscode/vscode-server/extensionsCache` の片付けと、`~/.vscode-server/bin/<commit>` のリンク切れの片付け

含まない:

- 手元の端末のキャッシュを devbase が消すこと（devbase からは届かない）
- `/vscode/vscode-server/bin` の本体の片付け（Dev Containers が 9 版まで残す）
- #463 の共通の VS Code Server ボリュームの役割の見直し（既にある版とリンクを持つ。別の課題にする）
- 確定仕様（`docs/specifications/vscode-server-volume.md`）の書き換え（確定仕様化で行う）

## 決定の記録

### 決定 1

ボリュームの名前は `vscode`、マウント先は `/vscode` とする。

- 名前は Dev Containers が決め打ちで探す値で、ほかの名前では効かない。devbase の決まり（`devbase_` で始める）から外れる
- マウント先は Dev Containers が自分で作るコンテナと同じ `/vscode` にする。同じ docker ホストの Dev Containers のコンテナと置き場を共有するが、どちらも同じ形で書くので壊れない
- 外部ボリューム（`external: true`）として宣言し、`devbase up` / `scale` が無ければ作る（#463 の共通の VS Code Server ボリュームと同じ扱い）
- プロジェクトの dev サービスが `/vscode` を自分で付けていれば足さない。使わない名前を宣言しない

### 決定 2

所有者は直さない。

- Dev Containers は共有の置き場へ root のシェル（`docker exec -u root`）で書き、開発ユーザーはリンクを辿って読むだけである
- 空のボリュームが root の持ち物で付いても困らない。#463 の `bin` のような所有者の直しは足さない

### 決定 3

起動の後の処理で、`/vscode/vscode-server/extensionsCache` を拡張ごとに新しい 3 版へ片付ける。

- Dev Containers は 500 ファイルまで残す。claude-code は 1 本 130MB 前後で更新が多く、数十 GB まで膨らみ得る（#463 では `extensionsCache/` が 3.4GB あった）
- 拡張は、ファイル名の最初の「`-` と数字」より前（`anthropic.claude-code-2.1.312-linux-x64` → `anthropic.claude-code`）で分ける。更新時刻（Dev Containers が使うたびに `touch` する）の新しい順に 3 つを残す
- 消すのは置き場の直下の通常のファイルだけで、リンクとディレクトリには触らない。置き場そのものがリンクなら何もしない
- コンテナの起動（entrypoint）でなく起動の後の処理で行う。イメージを建て直さずに効く。`docker exec -u 0` を 1 回打ち、失敗しても起動は止めない

### 決定 4

起動の後の処理で、`~/.vscode-server/bin/<commit>` のリンク切れを消す。

- Dev Containers が `/vscode` 側の古い版を消すと、`bin/` のリンクが切れて残る。中身は無いが、版の数だけ溜まる
- 消すのは、名前が 40 桁の 16 進で、リンクで、リンク先が無いものだけ。entrypoint の片付けの規則（リンクを版に数えず、辿らない）は変えない
- 決定 3 と同じ `docker exec -u 0` の 1 回で行う
