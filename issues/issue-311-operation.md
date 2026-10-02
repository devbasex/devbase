# #311 実 OpenBao での書き込みの確認と、スプリント m7a のリリース後テスト（実行の記録）

記録の置き場は `issues/` にした（課題の要求・設計を置くディレクトリで、#311 を読む人が最初に開くため）。

プロジェクト・アカウントグループ・利用者の名前は、リポジトリに固有の語を残さないため `proj-a`（グループ `team-a`）・`proj-b`（`team-b`）・`proj-c`（`team-c`）・`<user>` に置き換えた。グループ `personal` はそのまま書く。

- 課題: https://github.com/devbasex/devbase/issues/311
- モード: `operation`（プロダクションコードも文書も変えず、実 OpenBao と手元の端末の状態だけを変える）
- 同じ機会に行うもの: スプリント m7a（PR #381）のリリース後テスト（#357・#224・#371・#334）
- 確かめる版: main `32c11b6`（v4.0.0 の後。m7a を含む）

## 実行の範囲

| 項目 | 内容 |
| --- | --- |
| 対象の系 | **本番系**: 実 OpenBao（`backend.yml` の `url`、mount `devbase`、`version: 2`・`layout: group`）、手元の Docker（利用者が作業中のコンテナ）。手元の端末のファイル（`~/.aws/config`・`$DEVBASE_ROOT/secrets/host-import.yml`） |
| 使う権限 | 利用者の OpenBao のトークン（devbase が端末の鍵から読む。値は記録しない）。手元の Docker デーモン |
| ホストからの前提 | Netskope の対策として `SSL_CERT_FILE`（Netskope の CA を足したバンドル）と、別置きの Python 3.12 の環境（`UV_PROJECT_ENVIRONMENT`）を付けて `bin/devbase` を打つ |

### 単位

| # | 単位 | 系 | 変更前 → 変更後 | 成否の決め方 |
| --- | --- | --- | --- | --- |
| U1 | TUI の「キーの一覧と編集」で、グループ personal の個人共通へキー `DEVBASE_TEST_311` を 1 つ足す（擬似端末を tmux の専用ソケットで動かす） | OpenBao | キー無し → `DEVBASE_TEST_311=probe-311` | `env get DEVBASE_TEST_311 --user --group personal` が `probe-311` を返す |
| U2 | 一覧の画面を取り、持ち主・適用範囲・グループの列が崩れずに並ぶかを見る | 読むだけ | — | 画面の写しを利用者が見る |
| U3 | U1 のキーを消す（`env delete DEVBASE_TEST_311 --user --group personal`） | OpenBao | `probe-311` → キー無し | `env get` が見つからないを返す |
| U4 | `~/.aws/config` の末尾にコメント 1 行を足し、`env sync --group team-a` を打つ | 端末のファイル・OpenBao | team-a の個人共通の `AWS_CONFIG_BASE64` が今の `~/.aws` の中身へ変わる | 出力が「AWS認証: 更新しました（個人共通）」だけで、Git・GCP は「変更なし」。チーム共通（`team/team-a/global`）の値は変わらない |
| U5 | `~/.aws/config` を控えから戻し、`env sync --group team-a` を打ち直す | 端末のファイル・OpenBao | U4 の値 → 元と同じ中身 | 個人共通の `AWS_CONFIG_BASE64` の中のファイル（tar の時刻を除く）が U4 の前に控えた値と一致する |
| U6 | `env sync --group team-b` を打つ（team-b の参照には `AWS_CONFIG_BASE64` が無く、`~/.aws` の控えのハッシュは古い） | OpenBao（書かない見込み） | 何も変わらない | 出力が「参照にキーが無い」の案内で、team-b の 2 つの参照のキーの数と値が前と同じ |
| U7 | 壊れた `secrets/host-import.yml` を一時に置き、`env sync --group team-a` を打ってから消す | 端末のファイル | ファイル無し → 壊れた内容 → ファイル無し | sync が終了コード 1 でファイルの場所を示して止まり、参照を書かない |
| R1 | base と派生イメージを `build --no-cache` で建て直す（proj-a・proj-b・proj-c） | 手元の Docker のイメージ | 33 時間前の base → main の base | 終了コード 0 |
| U8 | proj-a（team-a）・proj-b（team-b）・proj-c（team-c）を `up` する（**P3**） | 手元の Docker のコンテナ | 動いているコンテナを作り直す | `Account group: <名前>` が出る。コンテナの中で `env get` が読める |
| R2 | proj-a を `scale 3` で増やし、増やした dev-2・dev-3 だけに後処理（clone できなかった repo の案内・ウィンドウタイトル）が行われるかを見る（#224・#371）。`project post-start` を打ち直して 3 台へ行われるかを見る | 手元の Docker のコンテナ | dev-1 → dev-1〜3 | 出力の対象が増やした番号だけ。post-start が終了コード 0 |
| R3 | proj-a の dev-1〜3 を同時に `docker restart` し、3 台とも動き続けるかを見る（#357） | 手元の Docker のコンテナ | 同じ | 3 台とも `Up`、entrypoint の完了の印がある |
| R4 | proj-a を `scale 1` へ戻す | 手元の Docker のコンテナ | dev-1〜3 → dev-1 | dev-2・dev-3 が無い |
| R5 | `env init` の取り込みの方針 `skip` で方法 3 の region がホストから取られないこと（#334）を、**一時の `DEVBASE_ROOT`（file backend）**で確かめる | 一時ディレクトリだけ | — | 書かれた `AWS_DEFAULT_REGION` がホストの値でなく既定の `ap-northeast-1` |
| H1 | `claude` のログインと MCP のトークン（proj-a）、team-c・team-b での `claude` の起動 | 利用者の手 | — | 利用者が確かめる |

**テストの穴（version 2 で `url` だけを変えたとき）**はコードの変更のため、この実行から外して起票する。

## 取り消しの手段

| # | 区分 | 手段 |
| --- | --- | --- |
| U1 | 戻せる | U3 そのもの。KV の版の履歴には `probe-311` の版が残る（機密ではない値） |
| U3 | 戻せる | 戻す必要が無い（元の状態へ戻す単位） |
| U4 | 戻せる | U5 そのもの。U4 の前に今の値を端末の一時ファイル（権限 600、記録には残さない）へ控え、U5 で中身が一致しなければ `env set AWS_CONFIG_BASE64 --user --group team-a` で控えの値を書き戻す |
| U5 | 戻せる | 戻す必要が無い |
| U6 | 戻せる | 書かない見込み。書かれた場合はそのキーを `env delete --user --group team-b` で消す |
| U7 | 戻せる | ファイルを消す（`rm secrets/host-import.yml`。今は無いことを確かめ済み） |
| R1 | 戻せない（実質） | 古いイメージはタグが外れ、dangling として残る間は ID で戻せるが、手順として保証しない。影響は手元の端末だけで、建て直しは何度でもできる |
| U8 | **戻せない** | 動いているコンテナが作り直され、コンテナの中の未保存の作業・実行中のプロセス（Claude のセッションを含む）が止まる。ボリュームの中身は残る |
| R2・R4 | 戻せる | `scale 1` で元の台数へ戻す |
| R3 | **戻せない** | dev-1〜3 の中の実行中のプロセスが止まる（R2 で足した dev-2・dev-3 と、U8 で作り直した dev-1） |
| R5 | 戻せる | 一時ディレクトリを消す |

## 承認

2026-10-02、利用者が U1〜U7（実 OpenBao への書き込み）と R1〜R4・U8（コンテナの作り直し。P3）の全部の実行を承認した。

## 実行の記録

ホストの `bin/devbase` には上の Netskope の対策の環境変数を付けた（下のコマンドでは省く）。比べるための参照の控えは、
端末の一時ディレクトリ（権限 700）に JSON で取り、記録には値を出さない。比べた結果（変わったキーの名前）だけを残す。

### U1: TUI でキーを足す

対象の系: OpenBao（本番）　取り消し: 戻せる（U3）

tmux の専用ソケット（`tmux -L devbase311`）の上で `bin/devbase list` を起動し、下部メニューの「環境変数」→「キーの一覧と編集」→「共通」→
グループ `personal` →「＋ キーを追加」→ キー名 `DEVBASE_TEST_311` → 持ち主「個人」→ 値 `probe-311`（伏せ字の入力欄には `*********` と出た）。

```text
DEVBASE_TEST_311 を設定しました (devbase/users/<user>/personal/global)
```

```text
$ bin/devbase env get DEVBASE_TEST_311 --user --group personal
probe-311
終了コード: 0
```

反映の確認: TUI とは別の経路（`env get`）で、`users/<user>/personal/global` に書かれたことを確かめた。**合格。**

### U2: 一覧の列の見た目

キーを足した後の一覧（120 桁の端末。値は TUI が伏せている）:

```text
? キーの一覧 30 件（参照 2 件・backend openbao）(↑↓ 移動 / Enter 決定 / ←・Esc 戻る / Ctrl-C 中止): (Use arrow keys)
 » ＋ キーを追加
   AWS_CONFIG_BASE64                チーム 共通               personal   ******
   AWS_DEFAULT_REGION               チーム 共通               personal   ******
   …（チーム 共通 の 12 行）
   CONTEXT7_API_KEY                 個人   共通               personal   ******
   DEVBASE_OPEN_EDITOR              個人   共通               personal   ******
   DEVBASE_TEST_311                 個人   共通               personal   ******
   …（個人 共通 の残り 15 行）
   SSH_AUTHORIZED_KEYS              個人   共通               personal   ******
```

持ち主（チーム / 個人）・適用範囲（共通）・グループの列は全行で同じ桁に並び、崩れていない。同じキー（`GH_TOKEN` など）は
チームと個人の 2 行とも出ており、勝ち負けの印は無い（#312 のとおり）。**合格**（擬似端末の画面で確かめた。利用者の端末の目視は H1 で併せて頼む）。

### U3: 足したキーを消す

対象の系: OpenBao（本番）　取り消し: 不要（元へ戻す単位）

```text
$ bin/devbase env delete DEVBASE_TEST_311 --user --group personal
DEVBASE_TEST_311 を削除しました (devbase/users/<user>/personal/global)
終了コード: 0
$ bin/devbase env get DEVBASE_TEST_311 --user --group personal
Error: 変数 'DEVBASE_TEST_311' は設定されていません
終了コード: 1
```

`env list --group personal` は チーム 12 変数・個人 17 変数で、U1 の前と同じ。**合格。**

### U4: `env sync` が個人共通の `AWS_CONFIG_BASE64` だけを変える

対象の系: 端末の `~/.aws/config`・OpenBao（本番）　取り消し: 戻せる（U5）

前提: team-a の控え（`.env.sources.team-a.yml`）の AWS は `tar_base64`。`AWS_CONFIG_BASE64` は個人共通にだけあり、チーム共通には無い。
実行の前に控えのハッシュと今のファイルを比べ、AWS・Git・GCP とも「変更なし」であることを確かめた。`~/.aws/config` は控えへ写した。

```text
$ printf '\n# devbase-311 probe\n' >> ~/.aws/config
$ bin/devbase env sync --group team-a
AWS認証: 更新しました（個人共通）
Git認証: 変更なし
GCP認証 (default): 変更なし
同期完了 (1件更新)
終了コード: 0
```

反映の確認（sync とは別の経路で、2 つの参照を読み直して実行の前の控えと比べた）:

```text
team 変わったキー: なし
user 変わったキー: ['AWS_CONFIG_BASE64']
  中のファイルが同じ: False / probe 行を含む: True
```

**合格。** AWS の行が出て、変わったのは個人共通の `AWS_CONFIG_BASE64` だけ。チーム共通（24 キー）はどの値も変わっていない。

### U5: 元へ戻す

対象の系: 端末の `~/.aws/config`・OpenBao（本番）　取り消し: 不要

```text
$ cp -p <控え> ~/.aws/config      # cmp で一致を確かめた
$ bin/devbase env sync --group team-a
AWS認証: 更新しました（個人共通）
Git認証: 変更なし
GCP認証 (default): 変更なし
同期完了 (1件更新)
終了コード: 0
```

U4 の前の控えと比べた結果:

```text
team 変わったキー: なし
user 変わったキー: ['AWS_CONFIG_BASE64']
  中のファイルが同じ: True / probe 行を含む: False
```

値の文字列は tar の時刻だけが違い、中のファイル（`config`・`credentials`）は実行の前と同じ。`env set` での書き戻しは要らなかった。**合格。**

### U6: 参照に無いキーは足さない

対象の系: OpenBao（本番。書かない見込み）　取り消し: 不要（書かれなかった）

前提: team-b の控えの AWS は今のファイルとハッシュが違う（`check_changed` が真）。team-b の 2 つの参照には `AWS_CONFIG_BASE64` も GCP の鍵も無い。

```text
$ bin/devbase env sync --group team-b
AWS認証: 参照にキーが無いため書きません。取り込むなら devbase env init --reset、手で入れるなら devbase env set
Git認証: 変更なし
GCP認証 (default): 参照にキーが無いため書きません。取り込むなら devbase env init --reset、手で入れるなら devbase env set
同期完了 (変更なし)
終了コード: 0
```

```text
team 変わったキー: なし
user 変わったキー: なし
```

**合格。** ファイルが変わっていても、参照に無いキーは足さない。

### U7: `host-import.yml` の誤りで止まる

対象の系: 端末の `secrets/host-import.yml`（実行の前は無かった）　取り消し: ファイルを消した

```text
$ cat secrets/host-import.yml
groups:
  team-a: maybe
$ bin/devbase env sync --group team-a
Error: /Users/<user>/devbase/secrets/host-import.yml: groups.team-a の方針が不正です: 'maybe' (受け付ける値: ask, skip, import)
終了コード: 1
$ rm secrets/host-import.yml
```

U5 の後の控えと比べ、team-a の 2 つの参照はどちらも「変わったキー: なし」。**合格。** 参照を開く前に止まり、場所と受け付ける値を示した。

### R5: `env init` が取り込みの方針 `skip` で region をホストから取らない（#334）

対象の系: 一時の `DEVBASE_ROOT`（`main` を detached で取った worktree。`secrets/backend.yml` が無く file backend）と、偽の `HOME`
（`~/.aws/config` の `[default]` の region を `eu-west-3`、`credentials` に偽の鍵）。実 OpenBao と利用者のホームには触らない。
取り消し: 一時の worktree と偽の `HOME` を消した。

手元のホストの `[default]` の region は既定の値と同じ `ap-northeast-1` で、ホストから取ったかを区別できないため偽の `HOME` を使った。
標準入力を端末でなくして、方針を「尋ねる」から `skip` へ落とした（#334 前提 4 の「端末でない `ask` から落ちたもの」）。

方法 3（Access Key）:

```text
$ printf '<API キー 6 つは空>\n3\n…' | HOME=<偽の HOME> bin/devbase env init
標準入力が端末でないため、ホストの資格情報は取り込みません (グループの指定なし)。取り込むなら secrets/host-import.yml で import を指定します
…
選択 [1/2/3/4] (デフォルト: 4): AWS認証: 標準入力が端末でないため飛ばしました (グループの指定なし)
AWS_ACCESS_KEY_ID (空でスキップ): AWS_SECRET_ACCESS_KEY (空でスキップ): AWS_DEFAULT_REGION (デフォルト: ap-northeast-1):
終了コード: 0
$ grep AWS .env
AWS_DEFAULT_REGION=ap-northeast-1
```

方法 2（SSO Profile。プロファイル名 `default` を入れた）:

```text
AWS_PROFILE (空でスキップ): AWS認証: 標準入力が端末でないため飛ばしました (グループの指定なし)
AWS_DEFAULT_REGION (デフォルト: ap-northeast-1): AWS_SSO_URL (空でスキップ):
終了コード: 0
$ grep AWS .env
AWS_DEFAULT_REGION=ap-northeast-1
AWS_PROFILE=default
```

**合格。** どちらも region の入力の既定はホストの `eu-west-3` ではなく `ap-northeast-1` で、書かれた値も `ap-northeast-1`。
GCP の正規化の警告は出ていない（鍵ファイルが無いため、警告の回数そのものはこの確認の対象外）。

### テストの穴の起票

`version: 2` で `url` だけを変えたときのテストの穴は、コードの変更のため #382 として起票した（マイルストーン 7）。

### R1: base と派生イメージの建て直し

対象の系: 手元の Docker のイメージ　取り消し: 実質戻せない（建て直しは何度でもできる）

```text
$ bin/devbase build proj-a --no-cache                  # base を含めてキャッシュなし
終了コード: 0
$ bin/devbase build proj-b --project-no-cache
終了コード: 0
$ bin/devbase build proj-c --project-no-cache
終了コード: 0
```

`devbase-base:latest`・`proj-a:latest`・`proj-b:latest`・`proj-c:latest` が当日に作り直された（`docker images`）。**合格。**

### U8: 3 つのグループのプロジェクトを `up` する（#315 の手動確認）

対象の系: 手元の Docker のコンテナ（本番。P3）　取り消し: 戻せない（動いていたコンテナを作り直した）

| プロジェクト | `up` の終了コード | entrypoint の `Account group:` の行（`docker logs`） | コンテナの中の確認 |
| --- | --- | --- | --- |
| proj-a（team-a） | 0 | `Account group: team-a (gcloud account: <team-a の利用者>, CLOUDSDK_CONFIG: /persistent/group/gcloud)` | `gh auth status` は GH_TOKEN でログイン済み。`gcloud config get account` は team-a の利用者。共有のリンク 5 本が `/persistent/ai/.claude/…` を指す |
| proj-b（team-b） | 0 | `Account group: team-b (gcloud account: <team-b のサービスアカウント>, …)` | `AWS_PROFILE=team-b`。Git と GH_TOKEN の変数 5 つが入る。共有のリンク 5 本が正しい |
| proj-c（team-c） | 0 | `Account group: team-c (gcloud account: unset, …)` | `AWS_PROFILE=team-c`。Git と GH_TOKEN の変数 5 つが入る。共有のリンク 5 本が正しい。`devbase_home_team-c` は既にあった |

3 つとも `bao の token を書きました: <コンテナ名>` が出て、コンテナの中の機密の環境変数がグループの値で入っている。**合格。**
proj-c は実行の前は止まっていた（`up` で scale 2 で起動した）。H1 の確認のために動かしたままにしている。

`claude` のログインと MCP のトークンは H1 で利用者が確かめる。

### R2: `scale` の後処理が増やしたインスタンスだけに行われる（#224・#371）

対象の系: 手元の Docker のコンテナ　取り消し: R4

```text
$ bin/devbase project scale proj-a 3
…
[4/5] Starting new containers (2..3)...
Using --no-recreate to avoid restarting existing containers...
 Container proj-a-dev-1 Running
 …（dev-2・dev-3 を作って起動）
[5/5] Waiting for new containers to be ready...
All containers ready
bao の token を書きました: proj-a-dev-2, proj-a-dev-3
=== Scale completed successfully ===
Container scale: 1 -> 3
終了コード: 0
```

ウィンドウタイトルの書き込み先（コンテナの中の `~/.vscode-server/data/Machine/settings.json`）の `window.title` と更新時刻:

```text
dev-1: "proj-a-dev-1${separator}${dirty}${activeEditorShort}"   更新 2026-09-29 17:26（scale で触られていない）
dev-2: "proj-a-dev-2${separator}${dirty}${activeEditorShort}"   更新 2026-10-02 19:25:57（scale の時刻）
dev-3: "proj-a-dev-3${separator}${dirty}${activeEditorShort}"   更新 2026-10-02 19:25:57（scale の時刻）
```

clone できなかった repo の案内は出なかった（3 台とも clone に失敗していない）。

```text
$ bin/devbase project post-start proj-a
bao の token を書きました: proj-a-dev-1, proj-a-dev-2, proj-a-dev-3
=== Post-start completed ===
終了コード: 0
```

**合格。** `scale` の token とウィンドウタイトルは増やした dev-2・dev-3 だけに行われ、`post-start` は動いている 3 台すべてに行われた
（ウィンドウタイトルは既に同じ値のため書き直されない）。1 台が起動できなかったときに残りへ後処理が行われること（#371）は、
実環境で落とす手段が無いためテスト（`tests/commands/` の post-start の試験）に任せた。

### R3: 同じグループの 3 台を同時に起こし直す（#357）

対象の系: 手元の Docker のコンテナ　取り消し: 戻せない（dev-1〜3 の中のプロセスが止まる）

1 回目: リンクが正しい状態で、3 台を同時に `docker restart` した（3 つのコマンドを背景で同時に打った）。3 台とも `running`（RestartCount 0）、
entrypoint の完了の印（`/tmp/entrypoint-ready`。起動ごとに消える）が 3 台とも立ち、共有のリンク 5 本が正しい。

2 回目（受け入れ条件 1 と同じ前提）: グループのボリュームの `.claude/settings.json` を、共通のボリュームの `settings.json` と同じ中身の
通常のファイルに置き換えてから、3 台を同時に `docker restart` した。

```text
（前）-rw------- 1 ubuntu ubuntu 7279 … /persistent/group/.claude/settings.json      共通の settings.json の sha256 先頭 81bec25dac11
（後）lrwxrwxrwx 1 ubuntu ubuntu 36 … /persistent/group/.claude/settings.json -> /persistent/ai/.claude/settings.json
      共通の settings.json の sha256 先頭 81bec25dac11
dev-1: Removing existing /persistent/group/.claude/settings.json... / Creating symlink: … -> /persistent/ai/.claude/settings.json
dev-2: ✓ /persistent/group/.claude/settings.json (symlink exists)
dev-3: ✓ /persistent/group/.claude/settings.json (symlink exists)
{'proj-a-dev-1': 'running 0', 'proj-a-dev-2': 'running 0', 'proj-a-dev-3': 'running 0'}
```

**合格。** 1 台が張り直し、残る 2 台は張り直されたリンクを確かめただけで、3 台とも落ちなかった。共通の `settings.json` の中身は変わっていない。

### R4: proj-a を 1 台へ戻す

計画では `scale 1` で戻すとしていたが、`scale` は台数を減らせない（何も変えずに終了コード 1）:

```text
$ bin/devbase project scale proj-a 1
Scaling project 'proj-a' from 3 to 1 containers (dev service: dev)
Warning: New scale (1) is not greater than current scale (3)
To scale down, use 'devbase container down' first, then 'devbase container up' with desired scale
終了コード: 1
```

`down` → `up` だと dev-1 も作り直すため、R2 で足した 2 台のコンテナだけを消した:

```text
$ docker rm -f proj-a-dev-2 proj-a-dev-3
終了コード: 0
```

proj-a は dev-1 だけが動いている。`scale` で作られたボリューム（`devbase_vscode_proj-a_2`・`devbase_vscode_proj-a_3`・`devbase_work_3`）は
残した（次に `scale` したときにそのまま使われ、消すと作業ツリーの中身を失うため、消すかは利用者が決める）。

### H1: 利用者の確認

2026-10-02、利用者が次の 3 つを確かめ、「すべて問題なし」と答えた。

- proj-a（team-a）で `claude` がログイン済みで起動し、MCP のトークンが `devbase_home_default` の頃と同じに使える
- proj-b（team-b）と proj-c（team-c）で `claude` が起動・ログインできる
- 手元の端末の TUI のキーの一覧の列が崩れていない

proj-c は利用者の判断で動かしたままにした。

## 結果

| 単位 | 結果 |
| --- | --- |
| U1〜U7（#311 の実 OpenBao の書き込み） | すべて合格。U1・U4 の変更は U3・U5 で戻し、実行の前と同じ状態（KV の版の履歴に `probe-311` と U4 の版が残る） |
| U8・H1（#315 の手動確認） | 合格 |
| R1〜R5（スプリント m7a のリリース後テスト） | 合格。R4 は計画の手段（`scale 1`）が使えず、増やした 2 台のコンテナを直接消した |

部分適用・戻せなかった単位は無い。テストの穴は #382 として起票した。
