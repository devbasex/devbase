# #334: env init の AWS の方法 3 が取り込みの方針によらず AWS_DEFAULT_REGION をホストから書く

正は課題の本文（#334）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何が起きるか
>
> 1. **AWS の方法 3（Access Key）で、取り込みの方針に関係なく `AWS_DEFAULT_REGION` をホストの `~/.aws/config` の `[default]` から確認なしに書く。** 鍵は方針に従うが、region の自動取得には方針の分岐が無い（`lib/devbase/env/collectors/aws.py` の `_collect_access_keys` の末尾）。機密の値ではないが、#314 の「確認なしに取り込まない」から外れる。
> 2. **GCP の鍵ファイル名の正規化の警告**（`プロファイル名 '…' を … に正規化しました`）が、skip や非対話で取り込みを飛ばしたときにも出る。1 回の init で 2 回ずつ出る（表示だけ）。
>
> ## 再現（1）
>
> 1. `$DEVBASE_ROOT/secrets/host-import.yml` に `groups: {<グループ>: skip}` を書く
> 2. そのグループのプロジェクトで端末から `devbase env init` → 方法に 3 → 鍵の 2 問は空で答える
> 3. `AWS_DEFAULT_REGION: 自動取得完了 (ap-northeast-1)` と出て書かれる
>
> `bf2b929` でも再現する（`HostImport(policy=SKIP)` で `_collect_access_keys` を直接呼び、`REGION written: ap-northeast-1`。2026-09-29）。
>
> 方法 2（`_collect_sso_profile`、`aws.py:254-273`）も、利用者が入れたプロファイル名の region を方針によらずホストから読む。名前を利用者が明示するため害は小さい。
>
> ## 見つけた場所
>
> v4.0.0 のリリース後テスト（#314 の確認。一時的な DEVBASE_ROOT と偽の HOME、plaintext backend）。
>
> ## 受け入れ条件（案）
>
> - [ ] 方針が `skip`、または `ask` で断ったとき、方法 3 でも `AWS_DEFAULT_REGION` をホストから取らない
> - [ ] 取り込みを飛ばしたときは GCP の正規化の警告を出さない
>
> - [ ] 方法 2 の region の読み取りも同じ方針に従うか、従わないならその理由を仕様に書く
>
> ## 直す場所
>
> | 項目 | 内容 |
> | --- | --- |
> | 現れている場所 | （1）`lib/devbase/env/collectors/aws.py` の `_collect_access_keys` の末尾（`:301-309`）。（2）`lib/devbase/env/collectors/google.py:44` の `_safe_profile_name` が出す警告と、`lib/devbase/commands/env.py:963` の `_gcp_profile_files` が出す 2 回目 |
> | 直す場所 | （1）同じ `aws.py`。`host` の取り込みの方針を region の自動取得にも当てる。（2）GCP の鍵ファイルの発見を `google.py` の 1 関数にまとめ、`env._gcp_profile_files` もそれを使う。警告は純粋な `_safe_profile_name` から外し、取り込むと決めた後に 1 回だけ出す（手: 統合 consolidate_duplication） |
>
> （2）の発見の二重は、#310 の `_gcp_profile_files` の項と同じ場所である。
>
> 関連: #314（鍵の確認は入ったが、方法 3 を明示して選んだ経路の region が確認の外に残った） #310
>

## 目的

- `env init` が、取り込みの方針（`ask` / `skip` / `import`）に従わずにホストの `~/.aws/config` の region を参照へ書くことを無くす（#314 の「確認なしに取り込まない」を AWS の方法 2・方法 3 の region にも当てる）
- 取り込みを飛ばしたときに、GCP のプロファイル名の正規化の警告を出さない。取り込むときも 1 回の `env init` で同じ警告を 2 回出さない

## 前提

- 前提 1: ホストの `~/.aws/config` から読んだ region を参照の `AWS_DEFAULT_REGION` へ書くことは、用語集の「取り込み」に当たる（機密の値でなくても、ホストの資格情報ファイルから読んだ値だから）。利用者がキーボードで入れた region と、既定の値 `ap-northeast-1` を書くことは取り込みに当たらない
- 前提 2: 方法 2（SSO Profile）の region も取り込みの方針に従わせる（受け入れ条件（案）の 3 つ目は「従う」を採る）。プロファイル名を利用者が入れても、そのプロファイルの region を書くことまで同意したとは読まない
- 前提 3: 方針が `ask` のとき、ホストの region を使うかは質問を増やさずに確かめる。方法 3 で鍵の確認（「取り込みますか?」）を出したときはその答えを region にも当て、確認を出さなかったとき（ホストに鍵が無い）と方法 2 では、region の入力の既定の値としてホストの region を見せる（Enter で受け入れ、入力すればその値）
- 前提 4: 方針が `skip`（端末でない `ask` から落ちたものを含む）と、`ask` で断ったときは、ホストの `~/.aws/config` の region を読まず、今の「region が無いとき」と同じ入力（既定の値 `ap-northeast-1`）へ進む
- 前提 5: 方法 1（Config Files）の region は変えない。方法 1 の region は選んだ AWS のプロファイルの節から読み、その節は取り込みの選択に入っているため
- 前提 6: GCP の正規化の警告は、取り込むと決めた鍵の名前が正規化で変わったときに、その名前ごとに 1 回だけ出す。`env sync` と同期済みハッシュの控えの更新でプロファイル名から鍵ファイルを引くときは警告を出さない（名前は `env init` で正規化済みのため）
- 前提 7: 正規化した名前の衝突の警告（後の鍵ファイルを候補から外す）も、正規化の警告と同じ規則で出す。方針が `skip` なら出さない
- 前提 8: `env init` の取り込みの方針の決め方（#314 の `host-import.yml`・`HostImport`）は変えない

## 対象範囲

含む:
- AWS の方法 3（Access Key）が region をホストから読むかを、取り込みの方針で決める
- AWS の方法 2（SSO Profile）が region をホストから読むかを、取り込みの方針で決める
- GCP のプロファイル名の正規化の警告と衝突の警告を出す時機と回数
- GCP の鍵ファイルの発見を `env init` と `env sync` / 控えの更新で同じ 1 つの引き方にまとめること（警告を出さない引き方として）

含まない:
- 方法 1（Config Files）の region と、`import` の方針の振る舞い（前提 5）
- 方法 2 でホストのプロファイル名の一覧を表示すること（表示であり、参照へ書かない）
- 方法 2・方法 3 で `AWS_DEFAULT_REGION` が既に参照にあるときの扱い（今のまま）
- 正規化の規則そのもの（`[A-Za-z0-9_]` 以外を `_` へ置き換える）
- #310 に並ぶ `env.py` のほかの構造改善（`_gcp_profile_files` の名前の不揃い以外）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 取り込みの方針が決まった | `env init` が対象のグループと `host-import.yml` を読む | 設定が壊れていれば init を止める（#314 のまま） | — |
| E2 | AWS の認証方法が選ばれた | 利用者が 1〜4 を入れる | 読めない入力は方針の既定の方法へ落ちる（今のまま） | E1 |
| E3 | 方法 3 でホストの鍵の扱いが決まった | `[default]` に鍵がある。`import` は取り込み、`skip` は飛ばし、`ask` は確認を出す | 確認の入力が EOF なら断ったとみなす（既定 N） | E2 |
| E4 | region の出所が決まった（ホスト / 利用者の入力 / 既定の値） | 方法 2・方法 3 で `AWS_DEFAULT_REGION` が参照に無い | ホストに region が無ければ入力へ進む | 方法 3 は E3、方法 2 は E2 |
| E5 | `AWS_DEFAULT_REGION` を参照へ書いた | E4 | — | E4 |
| E6 | GCP の鍵ファイルの候補を見つけた | `~/gcp-credentials/` を読む | 衝突した名前は後の鍵ファイルを外す | E1 |
| E7 | 取り込む GCP の鍵が決まった | `import` は全部、`ask` は選んだもの、`skip` は無し | 0 件なら GCP の鍵と共通設定を書かない（今のまま） | E6 |
| E8 | 正規化と衝突の警告を出した | E7 で決めた鍵の名前が正規化で変わった、または衝突で外れた鍵がある | — | E7（E6 の時点では出さない） |
| E9 | 同期済みハッシュの控えを更新した | init の終わり、または `env sync` | — | E7。警告を出さない |

E3 の「確認を出さなかった」（ホストに鍵が無い）と「断った」を E4 が分けて扱う点は前提 3・前提 4 に移した。E8 を E7 の後に置く順序は前提 6 に移した。

## 用語

| 用語 | 意味 |
| --- | --- |
| 取り込み | 用語集のとおり（コンテキスト secret） |
| 取り込みの方針 | 用語集のとおり |
| 取り込みの候補 | 用語集のとおり |
| 取り込みの選択 | 用語集のとおり |
| AWS のプロファイル | 用語集のとおり |
| プロファイル名の正規化 | GCP の鍵ファイル名の拡張子を除いた部分のうち `[A-Za-z0-9_]` 以外を `_` へ置き換え、GCP のプロファイル名（`GCP_CREDENTIALS_BASE64_<名前>` の `<名前>`）にすること |

## 受け入れ条件

AWS の方法 3（Access Key）。いずれも参照に `AWS_DEFAULT_REGION` が無く、ホストの `~/.aws/config` の `[default]` に `region = ap-southeast-2` がある状態で始める（既定の値 `ap-northeast-1` と区別するため）。

- [ ] 方針が `skip` で方法 3 を選び、鍵の 2 問を空で答え、region の入力を空で答えると、参照の `AWS_DEFAULT_REGION` は `ap-northeast-1` になり、`ap-southeast-2` は書かれない
- [ ] 方針が `skip` で方法 3 を選ぶと、出力に「`AWS_DEFAULT_REGION: 自動取得完了`」が出ない
- [ ] 端末でない標準入力で方針 `ask` が `skip` へ落ちたときも、方法 3 で `ap-southeast-2` が書かれない
- [ ] 方針が `ask` でホストに鍵があり、確認に `N` と答えて region の入力を空で答えると、`AWS_DEFAULT_REGION` は `ap-northeast-1` になる
- [ ] 方針が `ask` でホストに鍵があり、確認に `y` と答えると、`AWS_DEFAULT_REGION` は `ap-southeast-2` になる（region の入力を出さない）
- [ ] 方針が `ask` でホストに鍵が無いとき、region の入力の既定の値として `ap-southeast-2` を見せ、空で答えると `ap-southeast-2`、`us-east-1` と入れると `us-east-1` が書かれる
- [ ] 方針が `import` で方法 3 を選ぶと、今と同じく `AWS_DEFAULT_REGION` は `ap-southeast-2` になり、region の入力を出さない

AWS の方法 2（SSO Profile）。参照に `AWS_DEFAULT_REGION` が無く、ホストの `~/.aws/config` の `[profile dev]` に `region = ap-southeast-2` がある状態で、プロファイル名に `dev` と入れる。

- [ ] 方針が `skip` で region の入力を空で答えると、`AWS_DEFAULT_REGION` は `ap-northeast-1` になる
- [ ] 方針が `ask` のとき、region の入力の既定の値として `ap-southeast-2` を見せ、空で答えると `ap-southeast-2` が書かれる
- [ ] 方針が `import` なら、今と同じく `AWS_DEFAULT_REGION` は `ap-southeast-2` になり、region の入力を出さない

GCP のプロファイル名の正規化。`~/gcp-credentials/` に `my-proj.json`（正規化で `my_proj` になる）と `plain.json` がある状態で `env init` を 1 回走らせる。

- [ ] 方針が `skip` なら、出力に「プロファイル名 'my-proj' を 'my_proj' に正規化しました」が 1 度も出ない
- [ ] 方針が `ask` で何も選ばずに断ると、正規化の警告が 1 度も出ない
- [ ] 方針が `ask` で `plain` だけを選ぶと、正規化の警告が 1 度も出ない
- [ ] 方針が `ask` で `my_proj` を選ぶと、正規化の警告がちょうど 1 回出る
- [ ] 方針が `import` なら、正規化の警告がちょうど 1 回出る
- [ ] `~/gcp-credentials/` に `a-b.json` と `a_b.json` があり方針が `skip` なら、衝突の警告が出ない。方針が `import` なら衝突の警告がちょうど 1 回出る
- [ ] 鍵ファイルが正規化した名前で参照にある状態で `env sync` を走らせると、正規化の警告が出ず、`GCP_CREDENTIALS_BASE64_my_proj` の鍵ファイルを今と同じく `my-proj.json` として引く

退行しないこと:

- [ ] AWS の方法 3 で、#314 の鍵の扱い（`skip` で鍵を書かない・`ask` で断ると鍵を書かない・`import` で鍵を書く）が変わらない
- [ ] 方法 2・方法 3 で `AWS_DEFAULT_REGION` が既に参照にあれば、今と同じくその値を残し、ホストを読まない
- [ ] 方法 1 の region の振る舞いと出力が変わらない
- [ ] GCP の取り込みで参照へ書くキーと値（`GCP_CREDENTIALS_BASE64_<名前>`・`GCP_ACTIVE_PROFILE` ほか）が、どの方針でも今と同じになる
- [ ] 既存のテスト（`tests/env/test_host_import.py`・`tests/env/test_collector_host.py`・`tests/commands/test_env_host_import.py` ほか）がすべて通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| セキュリティ | 方針が `skip`（端末でない `ask` を含む）のとき、方法 2・方法 3 は `~/.aws/config` の region を参照へ書かない。テストは偽の HOME に置いた `~/.aws/config` で確かめ、利用者の実際の `~/.aws` を読まない |
| 運用・保守性 | region をホストから取らなかったときは、今の `skip` の知らせ（`取り込まない設定のため飛ばしました` / `標準入力が端末でないため飛ばしました`）と同じ形の 1 行で分かる。方法 3 で鍵と region の両方を飛ばしても、この知らせは 1 回の選択で 1 行にとどめる。新しい警告の種類は足さない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | コマンドとオプションは変わらない。方針が `skip` / `ask` の方法 2・方法 3 で、region の入力が出るか・その既定の値が変わる |
| データ | 参照のキーの形、同期済みハッシュの控えの形は変わらない。移行は無い |
| 既存の振る舞い | `skip` / `ask` の方法 2・方法 3 の region。GCP の正規化と衝突の警告の時機と回数 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run pytest tests/env tests/commands -q`（偽の HOME と一時的な `DEVBASE_ROOT`、plaintext backend。端末かどうかは `host_import.resolve(..., interactive=)` で与える） |
| 静的解析 | `uv run ruff check lib tests` と `python -m py_compile` の CI の検査ジョブ |
| 手動確認 | 端末で、一時的な `DEVBASE_ROOT` と偽の HOME（`~/.aws/config` に region、`~/gcp-credentials/my-proj.json`）、`host-import.yml` に `groups: {<グループ>: skip}` を置き、`devbase env init` で方法 3 を選んで鍵と region を空で答える。`ap-northeast-1` が書かれ、GCP の正規化の警告が出ないことを見る（再現（1）の逆）。実際の利用者の環境では行わない |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 取り込みの方針の判断は collector の外（`host_import`）で決め、collector は `HostImport` に従う（`lib/devbase/env/host_import.py` の I1）。GCP の鍵ファイルの発見は `lib/devbase/env/collectors/google.py` の 1 か所に置き、`commands/env.py` はそれを使う |
| コーディング規約 | `CONTRIBUTING.md` と ruff の設定（`pyproject.toml`）。ログは `devbase.log` の logger |
| テスト戦略 | collector の単体テスト（`tests/env/`）で方針ごとの書き込みと出力を、コマンドの層のテスト（`tests/commands/test_env_host_import.py`）で 1 回の `env init` の警告の回数を確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 偽の HOME でのテスト、ruff、既存テストの実行、CHANGELOG への利用者に見える変更の記入 |
| 確認してから行う | 方針・`host-import.yml` の形を変えること、参照のキーの形を変えること |
| 行わない | 利用者の実際の `~/.aws` / `~/gcp-credentials` を読むテスト、実際の利用者の環境での `env init`、#310 のほかの項目の構造改善 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 方法 2 の region を方針に従わせる（前提 2）のを採るか。従わせない場合は理由を仕様に書く | 設計 PR のレビューで利用者 | 設計 PR のマージまで |
| `ask` でホストの region を入力の既定の値として見せる（前提 3）か、y/N の確認を 1 問足すか | 設計（`design`）で決め、設計 PR のレビューで利用者が確かめる | 設計 PR のマージまで |
