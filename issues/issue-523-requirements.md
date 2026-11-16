# #523: エディタの自動オープン: tmux でソケットを拾い直しても PATH の code がデスクトップ版のままで、手元の VS Code に開かない

正は課題の本文（#523）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。設計承認ゲートで確かめるまで、利用者の決定として扱わない。扱いを書いた行は、設計者が決めた判断として読む。

この課題の本文は、起票者（AI）が事象の調査の記録として書いたものである。利用者の指示の原文は本文に引用されていない（「依頼（原文）」の節）。そのため、本文の条件はすべてこの節に並べる。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 拾い直したソケットに合う `remote-cli` を `PATH` の先頭に入れて直す（本文の「直し方の案」1） | 課題の本文「直し方の案」 | 受け入れ条件 1・2 |
| S2 | 合う `remote-cli` は、セッション環境の `VSCODE_GIT_ASKPASS_NODE` から求める | 課題の本文「直し方の案」1 | 受け入れ条件 2・前提 1 |
| S3 | `~/.vscode-server/cli/servers/*/server/bin/remote-cli/code` を探して使う（本文の「直し方の案」2） | 課題の本文「直し方の案」2 | 採らない（理由: 拾い直したソケットとサーバーの版がずれ得る。本文も 1 のほうを推している。前提 2） |
| S4 | 拾い直しが起きないときの振る舞いは変えない | 課題の本文「受け入れ条件」 | 受け入れ条件 6・7 |
| S5 | テストで、拾い直したソケットに合う `remote-cli` が `PATH` の先頭に入ることを確かめる | 課題の本文「受け入れ条件」 | 受け入れ条件 2（検証手段のテスト） |
| S6 | `remote-cli` の `code` を拾い直したソケットで起動すれば手元の PC の VS Code で開く | 課題の本文「確かめたこと」（終了コード 0 までを確認。手元で開くかは利用者が確認中） | 前提 3・手動確認 |

## 事象

手元の PC の VS Code から Remote-SSH で macOS のホストへ入り、ホストの tmux のペインで `devbase` からプロジェクト（`proj-a`）のエディタを開こうとすると、手元の PC の VS Code に窓が開かない。エラーも出ない。

`open_editor` の起動を差し替えて実行すると、起動されるのは `['code', '--folder-uri', 'vscode-remote://attached-container+…@ssh-remote+<host>/work']` で、`code` はホストの `/usr/local/bin/code`（デスクトップ版の VS Code の CLI）に解決される。

判定は正しい。実行の場面の判定は「VS Code の中・SSH 越し」を返し、方針は起動（`launch`）で、URI も SSH 先を経由する形（`…@ssh-remote+<host>/work`）で組まれている。違うのは起動する実行ファイルだけである。

原因: tmux のセッション環境から IPC ソケットを拾い直すと、エディタの自動オープンは `VSCODE_IPC_HOOK_CLI` だけを差し替え、`PATH` は変えない（`lib/devbase/editor/opener.py` の `_prepare_ipc_env`）。デスクトップ版の CLI は `VSCODE_IPC_HOOK_CLI` を読まない（`Visual Studio Code.app/Contents/Resources/app/out/cli.js` での出現は 0、`remote-cli` の `server-cli.js` では 1）。そのため手元の PC へは何も届かない。

## 期待する結果

拾い直したソケットと同じ VS Code サーバーの `remote-cli` の `code` が起動され、手元の PC の VS Code に dev コンテナへ接続した窓が開く。

## 再現手順

1. 手元の PC の VS Code から Remote-SSH で macOS のホストへ入る
2. ホストにデスクトップ版の VS Code が入っており、`/usr/local/bin/code` がその CLI を指している
3. 以前のクライアントで作った tmux のペインに attach する。ペインのシェルの `VSCODE_IPC_HOOK_CLI` が指すソケットはもう無く、`PATH` に Remote-SSH の `remote-cli` が入っていない
4. tmux のセッション環境には、いま attach しているクライアントの `VSCODE_IPC_HOOK_CLI`（接続できる）が入っている
5. そのペインで `devbase` からプロジェクトのエディタを開く

## 環境

- 確かめた版: `main` の 180a0c8（v4.2.0 の後）
- ホスト: macOS、Remote-SSH の VS Code サーバーは `~/.vscode-server/cli/servers/Stable-<commit>/server/` の配置
- 手元: VS Code（Remote-SSH）

## 用語

| 用語 | 意味 |
| --- | --- |
| セッション環境 | tmux のサーバーがセッションごとに持つ環境変数。`update-environment` に挙げた変数は attach のたびにいまのクライアントの値へ更新される。ペインのシェルの環境変数はペインを作ったときの値のまま残る |
| IPC ソケットの拾い直し | ペインのシェルの `VSCODE_IPC_HOOK_CLI` が使えないとき、セッション環境の `VSCODE_IPC_HOOK_CLI` が接続できればそちらを使うこと（[エディタで開く](docs/specifications/editor-open.md)・[環境変数ガイド](docs/user/environment-variables.md) の tmux の節） |
| remote-cli | Remote-SSH などの VS Code サーバーが持つ CLI の置き場（`<サーバーのディレクトリ>/bin/remote-cli`）。中の `code` は `VSCODE_IPC_HOOK_CLI` のソケットを通じて、手元の VS Code に窓を開かせる |
| デスクトップ版の code | ホストに入れたデスクトップ版の VS Code の CLI。`VSCODE_IPC_HOOK_CLI` を読まず、ホストの VS Code に向けて開く |

## 前提

- 前提 1: 合う `remote-cli` は、セッション環境の `VSCODE_GIT_ASKPASS_NODE`（VS Code サーバーの `node` のパス）から求める。`node` の置かれたディレクトリの `bin/remote-cli` を合う `remote-cli` とする。現行の配置（`~/.vscode-server/cli/servers/Stable-<commit>/server/node`）と旧い配置（`~/.vscode-server/bin/<commit>/node`）のどちらでも、この関係が成り立つ。`VSCODE_GIT_ASKPASS_NODE` は tmux の `update-environment` の既定に入っていないため、[環境変数ガイド](docs/user/environment-variables.md) の推奨設定（`set -ga update-environment " VSCODE_IPC_HOOK_CLI … VSCODE_GIT_ASKPASS_NODE …"`）を入れた利用者でだけセッション環境に入る
- 前提 2: セッション環境から合う `remote-cli` が求められないとき（`VSCODE_GIT_ASKPASS_NODE` が無い・消されている・求めたディレクトリに `code` が無い）は、ほかの場所を探さない。`PATH` を変えずにこれまでどおり起動し、合う `remote-cli` が見つからなかったことと、推奨設定の場所を警告のログに出す。サーバーのディレクトリを探して使う方法は、拾い直したソケットと版がずれ得るため採らない
- 前提 3: 拾い直したソケットと合う `remote-cli` の `code` で URI を開けば、手元の PC の VS Code に窓が開く。起票の時点で確かめたのは終了コード 0 までで、窓が開くことは未確認（手動確認で確かめる）
- 前提 4: `DEVBASE_EDITOR` で実行ファイルを指定しているときも、`PATH` の差し替えは同じに行う。`DEVBASE_EDITOR` が絶対パスなら、その実行ファイルがそのまま使われる（`PATH` で探さないため）。Cursor など VS Code 以外のサーバーでも、前提 1 の関係が成り立てば同じに扱う

## 対象範囲

含む:
- IPC ソケットを拾い直したときに、セッション環境から合う `remote-cli` を求め、エディタを探す `PATH` と起動するエディタへ渡す `PATH` の先頭に入れること
- 合う `remote-cli` を求められなかったときの警告のログ
- 上の振る舞いを確かめる単体テスト
- 確定仕様（[エディタで開く](docs/specifications/editor-open.md)）と[環境変数ガイド](docs/user/environment-variables.md)の tmux の節への反映
- `CHANGELOG.md` の `[Unreleased]` の記入

含まない:
- 拾い直しが起きないときの振る舞い（ペインのソケットが生きている・tmux の外・セッション環境に生きたソケットが無い）
- `VSCODE_GIT_ASKPASS_NODE` が無いときに VS Code サーバーのディレクトリを探す方法（前提 2）
- 実行の場面の判定（VS Code の中か・SSH 越しか）、方針の決め方、URI の組み立て
- ペインのシェル自身の環境変数を追随させる仕組み（シェルのフック）
- tmux の `update-environment` の既定を利用者の設定へ書き込むこと

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 利用者が tmux のペインで `devbase` からエディタを開いた | `devbase up` の後のエディタの自動オープン・`devbase open` | — | — |
| E2 | ペインのシェルの IPC ソケットを使えないと判定した | E1 | 判定できない（tmux 外・tmux の失敗）ときは拾い直しをせず、これまでの振る舞い | E1 |
| E3 | セッション環境から接続できる IPC ソケットを拾い直した | E2 | 拾えなければ IPC 無しとして扱う（これまでの振る舞い。この課題の範囲外） | E2 |
| E4 | セッション環境から VS Code サーバーの `node` のパスを読んだ | E3 | 無い・消されている → 前提 2 の警告、`PATH` は変えない（受け入れ条件 4） | E3 |
| E5 | 合う `remote-cli` を求め、`PATH` の先頭に入れた | E4 | 求めたディレクトリに `code` が無い → 前提 2 の警告、`PATH` は変えない（受け入れ条件 5） | E4 |
| E6 | 起動するエディタを `PATH` から求めた | E5 か E4・E5 の失敗 | 見つからなければ skip（これまでの振る舞い） | E3 の後 |
| E7 | エディタを、拾い直したソケットと差し替えた `PATH` で起動した | E6 | 起動の失敗は警告にして `up` を続ける（これまでの振る舞い） | E6 |

## 受け入れ条件

- [ ] 1. 再現手順の条件（ペインの `PATH` の先頭の `code` がデスクトップ版で、セッション環境のソケットだけが接続できる）で、セッション環境に `VSCODE_GIT_ASKPASS_NODE=<サーバー>/node` があり `<サーバー>/bin/remote-cli/code` が実在するとき、起動するコマンドの先頭の `code` は `<サーバー>/bin/remote-cli/code` に解決される
- [ ] 2. 1 の起動で、エディタへ渡す環境の `PATH` の先頭の要素は `<サーバー>/bin/remote-cli` で、`VSCODE_IPC_HOOK_CLI` は拾い直したソケットである。`PATH` の 2 番目以降は、ペインの `PATH` の要素を同じ順で持つ（単体テストで確かめる）
- [ ] 3. 1 のとき、使った `remote-cli` のディレクトリを info のログに 1 行出す
- [ ] 4. ソケットを拾い直したが、セッション環境の `VSCODE_GIT_ASKPASS_NODE` が未設定か消されている（`-VSCODE_GIT_ASKPASS_NODE`）とき、`PATH` は変えず、合う `remote-cli` が見つからなかったことと[環境変数ガイド](docs/user/environment-variables.md)の tmux の設定を案内する警告を 1 行出す。起動はこれまでどおり行う
- [ ] 5. ソケットを拾い直したが、`VSCODE_GIT_ASKPASS_NODE` から求めたディレクトリに実行できる `code` が無いとき、`PATH` は変えず、4 と同じ形の警告を出す
- [ ] 6. 拾い直しが起きないとき（ペインのソケットが接続できる・tmux の外・セッション環境に接続できるソケットが無い）は、起動するコマンドと渡す環境が変更前と同じである。`tests/editor/` の既存のテストが変更なしで通る
- [ ] 7. ペインのソケットを以前のクライアントのものとして外し、セッション環境からも拾えなかったとき、`PATH` から `remote-cli` を除いて手元の `code` を探す振る舞いは変わらない
- [ ] 8. 確定仕様（[エディタで開く](docs/specifications/editor-open.md)）と[環境変数ガイド](docs/user/environment-variables.md)の tmux の節に、拾い直したときに合う `remote-cli` を `PATH` の先頭に入れること・それに `VSCODE_GIT_ASKPASS_NODE` を `update-environment` に入れる必要があることが書かれている

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 追加の外部コマンドは、拾い直したときの `tmux show-environment VSCODE_GIT_ASKPASS_NODE` の 1 回だけで、既存の待ち時間の上限（2 秒）に従う。拾い直しが起きないときは追加の呼び出しをしない |
| 運用・保守性 | 合う `remote-cli` が見つからなかった理由を、利用者が直し方まで辿れる警告で出す（受け入れ条件 4・5） |
| セキュリティ | セッション環境の値から求めたパスは、`remote-cli` という名のディレクトリで、中に実行できる `code` があるときだけ `PATH` に入れる |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（コマンドの引数・環境変数の名前・設定のキーは増えない） |
| データ | 無し |
| 既存の振る舞い | tmux でソケットを拾い直したときに起動するエディタの実行ファイルと、エディタへ渡す `PATH` が変わる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `env -u DEVBASE_ROOT uv run --locked pytest tests/editor -q` |
| 静的解析・型検査 | `uvx ruff check --select=E9,F63,F7,F82 lib` |
| 手動確認 | 利用者が、手元の PC の VS Code → Remote-SSH → macOS のホストの tmux のペイン（再現手順の状態、`update-environment` に `VSCODE_GIT_ASKPASS_NODE` を入れたもの）で `devbase open proj-a` を実行し、手元の PC の VS Code に dev コンテナへ接続した窓が開くことを見る（リリース後テスト） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「このリポジトリ」。実装は `lib/devbase/editor/`、テストは `tests/editor/` |
| コーディング規約 | `AGENTS.md` の「書き方」と周りのコードの書き方。確認は検証手段の静的解析 |
| テスト戦略 | 単体テストで担保する。tmux とソケットは既存のテストと同じく差し替え口で与え、実の tmux サーバーと VS Code に触れない（`AGENTS.md` の落とし穴の tmux の行）。手元の PC で窓が開くことは手動確認で見る |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | `tests/editor` のテストと静的解析の実行、`CHANGELOG.md` の `[Unreleased]` の記入 |
| 確認してから行う | 無し（公開インタフェースの変更・依存パッケージの追加は無い） |
| 行わない | 利用者の tmux 設定（`~/.tmux.conf`）の書き換え、範囲外のエディタの自動オープンの整理、実の tmux サーバーに触れるテスト |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 前提 2（`VSCODE_GIT_ASKPASS_NODE` が無いときにサーバーのディレクトリを探さない）でよいか | 利用者 | 設計承認ゲート |

## 依頼（原文）

利用者の指示の原文は、課題の本文に引用されていない。起票時の課題の本文（起票者の調査の記録）をそのまま写す。

> ## 起きていること
>
> 手元の PC の VS Code から Remote-SSH で macOS のホストへ入り、そこの tmux の中で `devbase` からプロジェクト（`proj-a`）のエディタを開こうとすると、手元の PC の VS Code に窓が開かない。エラーも出ない。
>
> ## 再現の条件
>
> - 手元の PC の VS Code → Remote-SSH → macOS のホスト
> - ホストにデスクトップ版の VS Code が入っていて、`/usr/local/bin/code` がその CLI を指している
> - `devbase` を tmux のペインの中で実行する。ペインは以前のクライアントの環境で作られていて、`VSCODE_IPC_HOOK_CLI` のソケットはもう無く、`PATH` に Remote-SSH の `remote-cli` が入っていない
> - tmux のセッション環境には、いま attach しているクライアントの `VSCODE_IPC_HOOK_CLI`（接続できる）がある
>
> ## 原因
>
> `lib/devbase/editor/opener.py` の `_prepare_ipc_env` は、tmux のセッション環境からソケットを拾い直すと `VSCODE_IPC_HOOK_CLI` だけを差し替え、`PATH` は変えない。`resolve_editor_cmd` が引く `code` はペインの `PATH` の先頭にある `/usr/local/bin/code`（デスクトップ版）になる。
>
> デスクトップ版の CLI（`Visual Studio Code.app/Contents/Resources/app/out/cli.js`）は `VSCODE_IPC_HOOK_CLI` を読まない（ファイル中の出現は 0。`remote-cli` の `server-cli.js` では 1）。そのため手元の PC へは何も届かず、ホスト側のデスクトップ版に向けて開こうとして終わる。
>
> 判定そのものは正しい。`detect_context` は `in_vscode=True`・`is_ssh=True` を返し、方針は `launch`（VS Code 統合ターミナル経由）で、URI もネストした authority（`…@ssh-remote+<host>/work`）で組まれている。違うのは起動する実行ファイルだけ。
>
> ## 確かめたこと
>
> - `open_editor` の起動を差し替えて実行すると、起動されるのは `['code', '--folder-uri', 'vscode-remote://attached-container+…@ssh-remote+<host>/work']` で、`code` は `/usr/local/bin/code` に解決される
> - 同じ URI と拾い直したソケットを、`~/.vscode-server/cli/servers/Stable-<commit>/server/bin/remote-cli/code` で実行すると終了コードは 0（手元の PC で開くかは利用者が確認中）
>
> ## 直し方の案
>
> 1. ソケットを拾い直したときに、それに合う `remote-cli` を `PATH` の先頭に入れる。セッション環境の `VSCODE_GIT_ASKPASS_NODE`（`…/cli/servers/Stable-<commit>/server/node`）から、サーバーのディレクトリと `bin/remote-cli` が分かる
> 2. `in_vscode` かつ `is_ssh` で、解決した `code` が `remote-cli` の下に無いときは、`~/.vscode-server/cli/servers/*/server/bin/remote-cli/code` を探して使う
>
> 1 のほうが、拾ったソケットとサーバーの版がずれない。
>
> ## 当面の回避策
>
> tmux の外の、Remote-SSH の統合ターミナルを新しく開き、そこで `devbase` を実行する。
>
> ## 受け入れ条件
>
> - 上の条件（tmux のペインの `PATH` がデスクトップ版の `code` を指し、セッション環境のソケットだけが生きている）で、起動する `code` が `remote-cli` のものになる
> - テストで、拾い直したソケットに合う `remote-cli` が `PATH` の先頭に入ることを確かめる
> - 拾い直しが起きないときの振る舞いは変えない

（書き手: 起票者、日付: 2026-11-16。書き手が利用者でないため、利用者の指示の原文として扱わない）
