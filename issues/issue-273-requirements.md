# #273: feat: devbase list の TUI で env を編集できるようにし、グループ・個人の区別と OpenBao の設定もそこで扱う

正は課題の本文（#273）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何をしたいか
>
> `devbase list` の TUI で、環境変数（env）の設定を今よりグラフィカルに行えるようにしたい。
>
> あわせて、env の設定の前提になっている次の 3 点を整える。
>
> 1. アカウントグループや、チーム共通・個人の区別に対応していない env のコマンドがある。それを整理する（例: #268 の `devbase env sync` は個人の参照を書き換えない）
> 2. キーを足したり編集したりしたとき、OpenBao を使っている環境では OpenBao 側の値も書き換える
> 3. OpenBao そのものへの接続先と認証情報（Token）も TUI から設定できるようにする
>
> ## 提案
>
> | 項目 | 内容 |
> | --- | --- |
> | 画面 | `devbase list` の TUI に env の一覧と編集の画面を足す。グループとチーム共通・個人の区別が見える形にする |
> | 操作 | キーの追加・編集・削除 |
> | 書き込み先 | 選んだグループとチーム共通・個人の参照へ書く。OpenBao を使っているときは OpenBao の値も書き換える |
> | OpenBao の設定 | 接続先（アドレス）と認証情報（Token）を TUI から設定できるようにする |
>
> ## 確かめること
>
> - env のサブコマンドのうち、グループとチーム共通・個人の区別を扱えていないものはどれか。それぞれ書き込み先・読み出し先がどうなっているか
> - 今の `devbase list` の TUI の作りに、編集の画面を足せるか（画面の切り替え・入力欄）
> - OpenBao を使っていない環境（ファイルの backend）で同じ画面がどう振る舞うか
> - Token のような秘密の値を、画面に出さずに入力・保存できるか。保存先はどこが適切か
>
> ## 決めること
>
> - TUI の画面の構成（一覧・編集・OpenBao の設定をどう並べるか）
> - env のコマンドを整理するとき、CLI と TUI で書き込み先の決め方をどう揃えるか
> - 利用者向けの文書（`docs/user/`）への追記
>
> ## 修正レイヤー
>
> - `devbase list` の TUI
> - `lib/devbase/commands/env.py`（グループ・チーム共通・個人の区別の整理）
> - OpenBao の backend（キーの書き換えと、接続先・Token の設定）
>
> ## 関連
>
> - #268 — `devbase env sync` が個人の参照にある `AWS_CONFIG_BASE64` を更新しない
>
> ## 由来
>
> 利用者からの要望（2026-09-24）。

## 目的

- `devbase list` の TUI の中で、env のキーの一覧・追加・編集・削除を、書き込み先（グループ・チーム単位 / 個人単位・共通 / プロジェクト）を見ながら行えるようにする
- CLI と TUI の書き込み先の決め方を 1 つの規則に揃え、持ち主の軸を持たないために個人単位の参照を取りこぼすコマンド（#268 の `env sync`）を無くす
- OpenBao を使う端末では、OpenBao の接続先とブートストラップ機密を TUI から直せるようにする

## 調べて分かったこと

「確かめること」の答え。以降の前提と受け入れ条件はこれを土台にする。

### env のサブコマンドとグループ・持ち主の軸

| サブコマンド | グループの指定 | 持ち主の指定 | 書き込み先 |
| --- | --- | --- | --- |
| `list` / `get` | `--group` | `--user` | 読むだけ（4 つの参照を読む） |
| `set` / `delete` / `edit` | `--group` | `--user`（`-p` と組み合わせて 4 通り） | 選んだ参照 |
| `init` | `--group` | なし | 対象のグループのチーム共通 |
| `sync` | なし（実行時のディレクトリで決まる） | なし | 対象のグループのチーム共通だけ（#268） |
| `project` | なし（そのプロジェクトのグループ） | なし | チームのプロジェクト |
| `export` / `import` | なし（対象のグループ） | なし | チーム単位の参照と控え |
| `exec` | なし | なし（4 層を合成） | 読むだけ |

- 根拠: `lib/devbase/commands/env.py` の `_target_env`（140 行付近）・`cmd_env_sync`（508 行付近）・`_update_source_metadata`（1199 行付近）、`docs/specifications/secret-backend.md` の「参照の持ち主と `--user`」と「`init` / `sync` / `project` / `export` / `import`」の節
- 検索: `--personal` / `--shared` / `--team` は `lib/` に 0 件。持ち主を選ぶフラグは `--user` だけである
- `sync` が個人単位の参照を書かないのは仕様の決定で、`tests/commands/test_env_user_axis.py` の `test_commands_without_the_owner_axis_reject_user` が固定している

### 今の TUI

- `lib/devbase/tui/` は questionary（prompt_toolkit）の選択メニューと 1 行入力を順に出す作りで、全画面の画面管理は持たない
- env のメニュー（`lib/devbase/tui/actions_env.py` の `_ENV_OPS`）は「変数一覧 (グローバル)」「edit」「sync」「project」「init」の 5 つで、どれも `--user` / `--group` / `-p` を渡さない。キー単位の get / set / delete は TUI から意図して外してある
- 操作は `tui.dispatch.dispatch_group` で CLI と同じハンドラ `cmd_env` へ委譲する。ロジックを二重に持たない
- 伏せ字の入力は `lib/devbase/env/io_common.py` の `getpass`（標準エラー）と、`env backend use` の TTY での伏せ字入力がある。`tui.menu` には伏せ字の入力欄が無い

### ファイルの backend

- 平文・age の backend は個人単位の参照を持たない（`has_user_refs=False`）。`--user` の書き込みは入口で拒み、`--group` は `version: 2` の openbao 以外で終了コード 2 になる

### OpenBao の接続先と認証情報

- 接続先は `$DEVBASE_ROOT/secrets/backend.yml` の `openbao.url`（`https` 必須、`http` はループバック宛てだけ）。認証は AppRole で、`role_id` / `secret_id` を age で暗号化した `secrets/bootstrap.env.age`（ブートストラップ機密）に置く。書くのは `devbase env backend use openbao --url … --role-id … --secret-id-stdin` か TTY の伏せ字入力
- token は実行のたびに AppRole で取り直し、ホストのディスクへ保存しない（`docs/specifications/secret-backend.md` の「token は実行のたびに取り直し、ホストのディスクへ保存しない」）。token を直接入力して保存する仕組みは無い
- 検索: `BAO_TOKEN|VAULT_TOKEN|BAO_ADDR|VAULT_ADDR` の `lib/` での当たりは、コンテナへ渡す側の `BAO_ADDR`（`commands/container.py`）とコメントだけ。`keychain|keyring` は `lib/` で 0 件

## 前提

- 前提 1: 依頼文の「認証情報（Token）」は、ブートストラップ機密（AppRole の `role_id` / `secret_id`）を指すものとして扱う。TUI は `role_id` / `secret_id` を入力させ、既存の `secrets/bootstrap.env.age` へ保存する。token そのものを入力・保存する形は採らない（ホストのディスクへ token を置かない既存の決定を守るため）。token の直接入力が要るかは未決に置く
- 前提 2: TUI は今の questionary の選択メニューと入力欄の作りの中で作る。全画面の TUI の枠組み（Textual など）や新しい依存パッケージを足さない。「グラフィカル」は、一覧の各行に置き場（グループ・持ち主・共通 / プロジェクト）が並んで見え、矢印とEnterで選んで編集できることを指す
- 前提 3: TUI の env の編集は CLI の `set` / `delete` のハンドラへ委譲する。TUI で選んだ（グループ・持ち主・共通 / プロジェクト）は、CLI の（`--group`・`--user`・`-p` + 対象のプロジェクト）へ 1 対 1 に写す。TUI だけにある書き込みの経路を作らない
- 前提 4: `env sync` の書き込み先は、同期するキーごとに「対象のグループの個人共通とチーム共通のうち、そのキーが現にある参照」とする。両方にあれば個人共通（重ね順で個人共通がチーム共通に勝ち、チーム共通を書いてもコンテナの値が変わらないため）。どちらにも無ければ今と同じチーム共通。`--user` を付けると個人共通へ書く
- 前提 5: 同期の控えへソース（aws など）を登録する判定は、対象のグループの個人共通とチーム共通のどちらかにキーがあることとする（#268 の原因 2）
- 前提 6: `init` / `project` / `export` / `import` の持ち主の軸は今回変えない（チーム単位のまま）。`init` と `project` は既定値を集めてチームで共有する操作、`export` / `import` はバンドルの形式がチーム単位の参照を前提とするため。個人単位の値は TUI のキー編集（前提 3）と CLI の `set --user` で書ける
- 前提 7: TUI の値の入力は既定で伏せ字にする。入力した値は画面・ログ・プロセスの引数のどれにも出さない。複数行の値（改行を含む値）は TUI では扱わず、`edit` を案内する
- 前提 8: OpenBao の設定の画面は、backend が既に openbao の端末でだけ編集を許す。ファイルの backend の端末では、今の backend の名前と、切り替えの CLI（`devbase env backend use openbao` と `devbase env backend migrate --to openbao`）を示すだけにする。TUI から backend を切り替え・移行しない（機密の移し替えを伴い、失敗時の切り戻しを TUI の中で扱えないため）

## 対象範囲

含む:
- `devbase list` の TUI の env メニューに「キーの一覧と編集」と「OpenBao の接続設定」を足す
- 一覧: 対象のグループ（`version: 2` のときだけ選べる）と、共通 / プロジェクト（プロジェクトを選ぶ）で絞った参照の中身を、行ごとに置き場を添えて出す
- キーの追加・値の変更・削除（前提 3 の委譲）
- `env sync` の書き込み先と控えの登録の判定（前提 4・5、#268）と、`env sync` の `--user` / `--group`
- 控えに登録の無いソースのキーがどこかの参照にあるとき、`env sync` が「ソース未登録」の 1 行を出す（#268 の期待する挙動の案）
- OpenBao の接続先（`url`）とブートストラップ機密（`role_id` / `secret_id`）の TUI からの変更と、変更後の接続の確認
- `docs/user/`（env の文書と CLI リファレンス）と `docs/specifications/secret-backend.md` の追記・書き換え

含まない:
- token を直接入力・保存する仕組み（前提 1）
- TUI からの backend の切り替えと `backend migrate`（前提 8）
- `init` / `project` / `export` / `import` への持ち主の軸の追加（前提 6）
- 複数行の値の TUI での編集（前提 7）
- `mount` / `user` / `layout` / `group_aliases` の TUI からの変更（CLI の `env backend use` で行う）
- 全画面の TUI の枠組みへの作り替え（前提 2）
- 既存の env メニュー 5 つの削除・名前の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 利用者が TUI で env のキーの一覧を開いた | env メニューで「キーの一覧と編集」を選ぶ | backend の読み出しに失敗（接続・403）したら理由を 1 行出してサブメニューへ戻る | — |
| E2 | 対象のグループと共通 / プロジェクトが決まった | `version: 2` ならグループを選ぶ（既定は `$DEVBASE_ROOT/env` のグループ）。プロジェクトならプロジェクトを選ぶ | Esc でサブメニューへ戻る。プロジェクトが 0 件なら共通だけを出す | E1 |
| E3 | 参照の中身が行として並んだ | E2 | 個人単位の参照を持たない backend では個人単位の行を出さない（エラーにしない） | E2 |
| E4 | 利用者がキーを追加・変更した | 一覧で行か「キーを追加」を選び、持ち主を選んで値を入力する | 名前が不正・値が空・改行を含むときは書かずに理由を出して入力へ戻る | E3 |
| E5 | 参照が保存された（openbao ではサーバの値とキャッシュが更新された） | E4 の確定、`set` のハンドラへの委譲 | 版の食い違い（CAS）は上書きせず、読み直しを促す。403 は権限の不足として出す | E4 |
| E6 | 利用者がキーを削除した | 行を選んで削除を確定する | E5 と同じ | E3 |
| E7 | `env sync` が同期するキーの書き込み先を決めた | `devbase env sync`（CLI・TUI） | 対象のグループの参照を読めなければ何も書かずに 1 | — |
| E8 | `env sync` が控えに無いソースのキーを見つけた | E7 の中で、控えに無いソースのキーが参照にある | — | E7 |
| E9 | 利用者が OpenBao の接続設定を開いた | env メニューで「OpenBao の接続設定」を選ぶ | backend が openbao でなければ案内だけを出して戻る（前提 8） | — |
| E10 | 接続先かブートストラップ機密が保存された | E9 で値を入力して確定する | `url` の形が不正なら保存しない。入力が空の欄は変えない | E9 |
| E11 | 保存した設定で接続を確かめた | E10 の直後に自動で | 認証・接続に失敗したら理由を出す。保存した設定は残し、直す入力へ戻れる | E10 |

- E4 の持ち主の選択は、個人単位の参照を持つ backend のときだけ出す（E3 と同じ条件）
- E11 は E10 の後でだけ起きる。保存の前に確かめる形は、`url` だけを直すときに古いブートストラップ機密で失敗するため採らない

## 用語

| 用語 | 意味 |
| --- | --- |
| 参照 | 機密の宛先（`SecretRef`）。適用範囲（共通 / プロジェクト）と持ち主（チーム / 個人）の組み合わせ。`version: 2` ではグループも持つ |
| 対象のグループ | 1 回の操作が読み書きするグループ |
| チーム単位の機密 | チームの全員が同じ値を使う機密。依頼文の「チーム共通」 |
| 個人単位の機密 | 利用者ごとに値が違う機密。依頼文の「個人」 |
| ブートストラップ機密 | サーバ backend が接続に使う AppRole の `role_id` / `secret_id`。依頼文の「認証情報（Token）」（前提 1） |
| キャッシュ | サーバの内容と一致すると確かめられた機密を、参照ごとに age で暗号化して手元に控えたもの |

## 受け入れ条件

### TUI のキーの一覧

- [ ] env メニューに「キーの一覧と編集」と「OpenBao の接続設定」が並び、既存の 5 つの操作は同じ名前・同じ順で残る
- [ ] 前提: openbao `version: 2`、グループ `nyle` のチーム共通に `A`、個人共通に `B` がある
      操作: 「キーの一覧と編集」でグループ `nyle`・共通を選ぶ
      結果: `A` の行に「チーム」、`B` の行に「個人」、両方の行にグループ `nyle` と「共通」が出る
- [ ] 一覧の値は伏せ字で出る。値の平文は画面の出力に含まれない
- [ ] 同じキーがチーム共通と個人共通の両方にあるとき、両方の行が出て、コンテナで勝つ方（個人共通）の行に印が付く
- [ ] `version: 2` でない端末では、グループを選ぶ入力が出ない
- [ ] ファイルの backend（平文・age）では、個人単位の行も、持ち主を選ぶ入力も出ない。エラーにならない
- [ ] backend の読み出しが失敗（接続できない・403）すると、理由の 1 行を出してサブメニューへ戻り、TUI は終わらない

### TUI のキーの追加・変更・削除

- [ ] 前提: openbao。操作: 個人・共通・グループ `nyle` を選び、キー `K`・値 `v` を追加する
      結果: `devbase env get K --user --group nyle` が `v` を返し、偽の OpenBao サーバの `users/<user>/nyle/global` に `K=v` がある
- [ ] 追加・変更の後、`devbase env get` はキャッシュの古い値でなく新しい値を返す
- [ ] TUI の追加・変更・削除は `cmd_env` の `set` / `delete` へ、選んだ置き場に対応する `group` / `user` / `project` の属性で委譲される（写しの表は設計で固定する）
- [ ] 値の入力は伏せ字で、入力した値が画面の出力・ログ・子プロセスの引数に出ない（擬似端末のテストで確かめる）
- [ ] 改行を含む値・空の値・`DEVBASE_ACCOUNT_GROUP`・名前の形に合わないキーは保存されず、理由が出て入力へ戻る
- [ ] 削除は確認の入力で「はい」を選んだときだけ行われ、Esc と「いいえ」では参照が変わらない
- [ ] 版の食い違い（サーバが CAS の 400 を返す）では上書きせず、読み直しを促す 1 行が出る
- [ ] ファイルの backend でチーム共通へ追加すると、`$DEVBASE_ROOT/.env`（age なら `secrets/global.env.age`）に書かれる

### `env sync` の書き込み先（#268）

- [ ] 前提: openbao `version: 2`、`AWS_CONFIG_BASE64` が個人共通にだけあり、`~/.aws/config` を変えた
      操作: `devbase env sync`
      結果: 個人共通の `AWS_CONFIG_BASE64` が新しい値になり、チーム共通に `AWS_CONFIG_BASE64` が作られない。AWS の同期の行が出る
- [ ] キーがチーム共通にだけあるときは、今と同じくチーム共通が更新される
- [ ] キーが個人共通とチーム共通の両方にあるときは、個人共通だけが更新される
- [ ] キーがどちらにも無いときは、`--user` 無しでチーム共通、`--user` 付きで個人共通に書かれる
- [ ] 控えの aws の登録は、キーが個人共通にだけあるときも行われる
- [ ] 控えに登録の無いソースのキーが参照にあると、「ソース未登録」を含む 1 行が出る
- [ ] `env sync --group <g>` は `<g>` の参照と `.env.sources.<g>.yml` を使う。`version: 2` でない端末で `--group` を付けると終了コード 2
- [ ] ファイルの backend で `env sync --user` は終了コード 2 で拒まれ、チーム共通へ落ちない
- [ ] ファイルの backend での `env sync`（`--user` 無し）の書き込み先と出力は変わらない

### OpenBao の接続設定

- [ ] backend が openbao でない端末では、今の backend の名前と `devbase env backend use openbao` / `devbase env backend migrate --to openbao` の案内だけが出て、`secrets/backend.yml` と `secrets/bootstrap.env.age` は変わらない
- [ ] openbao の端末で `url` だけを変えると、`backend.yml` の `openbao.url` だけが変わり、`bootstrap.env.age` と `mount` / `user` / `layout` / `group_aliases` は変わらない
- [ ] `https` でない `url`（ループバック宛ての `http` を除く）は保存されず、理由が出る
- [ ] `role_id` / `secret_id` の入力は伏せ字で、入力した値が画面の出力・ログ・子プロセスの引数に出ない
- [ ] `secret_id` を変えると `bootstrap.env.age` の中身が新しい値になり、ホストのディスクに token が書かれない
- [ ] 空のまま確定した欄は変わらない
- [ ] 保存の後に接続の確認（`env backend test` と同じ判定）が走り、成功・失敗のどちらかの結果が出る。失敗しても保存した設定は残る

### 退行しないこと

- [ ] 既存のテスト（`uv run --locked pytest tests/ -q`）がすべて通る
- [ ] CLI の `set` / `delete` / `edit` / `list` / `get` の宛先と出力は変わらない
- [ ] `init` / `project` / `export` / `import` は `--user` を今と同じく拒む

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 一覧を 1 回開いたときの OpenBao への要求は、認証 1 回と、選んだ置き場の参照の取得（共通なら 2 回、プロジェクトなら 4 回）に収まる。`LIST` を使わない |
| 運用・保守性 | 失敗の表示は、どの参照（グループ・持ち主・共通 / プロジェクト）で何が起きたかを 1 行で示す。値は含めない |
| セキュリティ | 機密の値・`role_id`・`secret_id` は伏せ字で入力し、画面の出力・ログ・子プロセスの引数・一時ファイル以外のディスクに平文で残らない。token をホストのディスクへ保存しない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`env sync` に `--user` / `--group` が増え、書き込み先の決め方が前提 4 に変わる。TUI の env メニューに 2 つの操作が増える。ほかの CLI は変わらない |
| データ | スキーマの変更なし。`env sync` が個人共通へ書くようになる。`backend.yml` / `bootstrap.env.age` の形は変わらない |
| 既存の振る舞い | キーが個人共通にある端末の `env sync` が個人共通を更新するようになる（#268 の直し）。`docs/specifications/secret-backend.md` の「`init` / `sync` / `project` / `export` / `import` は `--user` を受け付けない」を、`sync` を除く形に書き換える |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `devbase list`（TTY）→ env メニュー |
| テスト | `uv run --locked pytest tests/ -q`。偽の OpenBao サーバ（`tests/conftest.py` の `openbao` / `openbao_root`）と `tests/cli/tui/` の擬似端末のテスト（`test_menu_pty.py` の形）を使う |
| 静的解析・型検査 | `python -m compileall -q lib bin`、`ruff check --select=E9,F63,F7,F82 lib`（CI と同じ） |
| 手動確認 | 人が実際の端末エミュレータで、一覧の行の置き場の列が崩れずに並び、伏せ字の入力欄に文字が出ないことを見る。実際の OpenBao サーバで TUI からキーを 1 つ足して `devbase env get` で読めることを見る（マージ後の実機確認） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | TUI は `lib/devbase/tui/`、env のコマンドは `lib/devbase/commands/env.py`、backend は `lib/devbase/env/`。TUI は `tui.dispatch` で `cmd_env` へ委譲し、機密の読み書きを TUI に直接書かない |
| コーディング規約 | `CONTRIBUTING.md` と既存コードの形。確認は CI の静的解析 |
| テスト戦略 | 書き込み先の規則は `tests/commands/` の CLI の層、backend の要求は `tests/env/` と偽の OpenBao サーバ、TUI の振る舞い（委譲の属性・伏せ字）は `tests/cli/tui/` の単体と擬似端末で確かめる。テストは `DEVBASE_ROOT` と HOME を tmp へ隔離した fixture の上で動かし、実サーバ・実 docker に触れない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、CI と同じ静的解析、`docs/user/` と仕様の追記 |
| 確認してから行う | 前提 1・4・8 を覆す変更（token の保存、sync の既定の書き込み先、TUI からの backend 切り替え）、依存パッケージの追加 |
| 行わない | 範囲外の env コマンドの作り替え、`backend.yml` / `bootstrap.env.age` の形の変更、実サーバの機密を触る試験 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| token（`BAO_TOKEN`）の直接入力・保存が要るか。要るなら保存先（ホストのディスクに置かない決定との両立） | 依頼者 | 設計 PR のレビューまで |
| `env sync` でキーがどちらの参照にも無いときの既定（今はチーム共通。#268 は `~/.aws/credentials` の静的キーがチーム全員の読める置き場へ入る危険を挙げている） | 依頼者 | 設計 PR のレビューまで |
| TUI の画面の並び（一覧の列・操作の置き方）の細部 | 設計（`design`） | 設計 PR |
| #268 をこの課題で閉じるか（本仕様は #268 の直しを含む） | 依頼者 | 設計 PR のレビューまで |
