# #335: 利用者に見える変更の Pull Request が CHANGELOG の [Unreleased] を更新しないまま main へ入る

正は課題の本文（#335）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何が起きたか
>
> ミッション group-required（#318、#315 のグループ宣言の必須化。破壊的変更）と #313（#312 の TUI の整理）は、CHANGELOG の `[Unreleased]` に項目を足さないまま main へマージされていました。v4.0.0 の release PR（#331）を作る段階で気づき、そこで書き足しています。snapshot-safety（#330）は、ミッションの Pull Request の中で CHANGELOG のコミットを足していました。
>
> main へのマージがそのまま配布になるため、CHANGELOG の書き漏れは利用者が破壊的変更を知らないまま受け取ることにつながります。
>
> ## やること（案）
>
> - 利用者に見える変更を main へ入れる Pull Request は、同じ Pull Request で `CHANGELOG.md` の `[Unreleased]` を更新する、と、貢献の手順（`CONTRIBUTING.md` と `docs/developer/contributing.md` の「PR プロセス」）と Pull Request のテンプレート（`.github/pull_request_template.md` の「動作確認」）に書く。リポジトリには `CLAUDE.md` も `AGENTS.md` も無い
> - 可能なら CI（`.github/workflows/ci.yml`。今は CHANGELOG を見る検査が無い）で、`lib/` `bin/` `containers/` を変える Pull Request が `CHANGELOG.md` を変えていないときに警告する
>
> ## 見つけた場面
>
> v4.0.0 のリリース（#331）と振り返り（#330 へのコメント）。

## 目的

- main へのマージがそのまま配布になるため、利用者に見える変更は main に入る時点で `CHANGELOG.md` の `[Unreleased]` に載っている状態にする
- 書き漏れを、リリース PR を作る段階ではなく、変更の Pull Request のレビューの段階で気づけるようにする

## 前提

依頼文から一意に決まらない点を、人へ問わずに次のとおり置く。どれも局所的に直せる（検査ジョブの設定か文書の 1 か所）。

- 前提 1: CHANGELOG の検査は**宛先が main の Pull Request だけ**で判定する。統合ブランチ（`release/**`・`mission/**`）宛てと積み重ねた Pull Request では警告しない。ミッションは統合ブランチから main への Pull Request の差分で `CHANGELOG.md` を変えていればよい（#330 の snapshot-safety のやり方を認める）
- 前提 2: 見張るパスは `lib/` `bin/` `containers/` に、利用者へ配る `install.sh` と `etc/`（シェル補完）を加えた 5 つとする。`docs/` `tests/` `issues/` `.github/` `.ndf/` などは見張らない
- 前提 3: 未記入の警告は**失敗にしない**。検査ジョブは警告を出しても success で終わる。見張るパスの変更にも、テストのための変更・利用者に見えないリファクタリングのように CHANGELOG が要らないものがあるため、要否の判断はレビュアーに残す
- 前提 4: 「`CHANGELOG.md` を変えた」は、Pull Request の差分（宛先ブランチとの merge base から head まで）に `CHANGELOG.md` が含まれることで判定する。`[Unreleased]` の節の中に足したかまでは見ない
- 前提 5: 未記入の警告は GitHub Actions の警告の注記（`::warning`）とジョブの要約（`$GITHUB_STEP_SUMMARY`）に出す。Pull Request へのコメントは書かない（fork からの Pull Request では書き込みの権限が無いため）
- 前提 6: CHANGELOG の検査は main のブランチ保護の必須チェックに加えない（警告であって止める検査ではないため）
- 前提 7: 差分を得られないとき（宛先ブランチを取れないなど）は、黙って警告なしで通さず、検査ジョブを失敗にして理由をログに出す。必須チェックではないのでマージは止まらない

## 対象範囲

含む:
- `CONTRIBUTING.md` の「Pull Request」の節と、`docs/developer/contributing.md` の「PR プロセス」に、CHANGELOG の規則を書く
- `.github/pull_request_template.md` の「動作確認」に、CHANGELOG の確認の項目を足す
- `.github/workflows/ci.yml` に CHANGELOG の検査を検査ジョブとして足す
- 判定を pytest で確かめるテスト
- `docs/specifications/ci-checks.md` に新しい検査ジョブを書き足す

含まない:
- CHANGELOG の項目を自動で書く・下書きを作ること
- `[Unreleased]` の節の中身（書き方・分類・文の質）を検査すること
- 警告を失敗に上げること、ブランチ保護の必須チェックへ加えること
- 統合ブランチ宛て・積み重ねた Pull Request での検査
- リリースの手順（リリース PR・タグ）の変更
- NDF の Skill（`pr` など、ai-plugins 側）の変更
- 既存の検査ジョブ（Python syntax check・Ruff lint・ShellCheck・Pytest）の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 作成者が main 宛ての Pull Request を開いた、または head へ push した | 作成者の操作（`pull_request` の opened / synchronize / reopened） | — | — |
| E2 | CHANGELOG の検査が Pull Request の差分のパスを集めた | E1 で CI が起動した | 差分を得られない → 検査ジョブを失敗にし理由をログに出す（前提 7） | E1 |
| E3 | CHANGELOG の検査が、見張るパスの変更があり `CHANGELOG.md` の変更が無いと判定し、未記入の警告を出した | E2 の結果 | — （警告を出してもジョブは success。前提 3） | E2 |
| E4 | 作成者が `CHANGELOG.md` の `[Unreleased]` を書き足して push した | E3 の警告かレビュー | — | E3（E1 に戻り、新しい実行では警告が出ない） |
| E5 | レビュアーが警告を見て、利用者に見えない変更と判断してマージした | レビュアーの判断 | — | E3 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 利用者に見える変更 | devbase を使う人がコマンド・イメージ・設定を通じて気づく振る舞いの変化。テストだけ・開発者向けの文書だけの変更は含まない |
| 見張るパス | CHANGELOG の検査が利用者に見える変更の手がかりとして見る場所（前提 2 の 5 つ） |
| CHANGELOG の検査 | main 宛ての Pull Request で、見張るパスを変えて `CHANGELOG.md` を変えていないときに未記入の警告を出す検査ジョブ |
| 未記入の警告 | CHANGELOG の検査が出す GitHub Actions の警告の注記。ジョブを失敗にしない |

## 受け入れ条件

文書:
- [ ] AC1: `CONTRIBUTING.md` の「Pull Request」の節に、利用者に見える変更を main へ入れる Pull Request は同じ Pull Request で `CHANGELOG.md` の `[Unreleased]` を更新する、という規則がある
- [ ] AC2: `docs/developer/contributing.md` の「PR プロセス」に AC1 と同じ規則があり、あわせて (a) CHANGELOG が要らない変更の例（テストだけ・開発者向けの文書だけ・利用者に見えないリファクタリング）、(b) 統合ブランチを通す変更は統合ブランチから main への Pull Request で書けばよいこと、(c) CI が未記入の警告を出すが失敗にはしないこと、が書いてある
- [ ] AC3: `.github/pull_request_template.md` の「動作確認」に、利用者に見える変更のときに `CHANGELOG.md` の `[Unreleased]` を更新したことを確かめるチェック項目が 1 つある

CHANGELOG の検査:
- [ ] AC4: 宛先が main の Pull Request で、見張るパスのファイルを 1 つ以上変え `CHANGELOG.md` を変えていないとき、CHANGELOG の検査が未記入の警告を 1 件出す。警告の文は `CHANGELOG.md` の `[Unreleased]` の更新を求め、変えた見張るパスのファイルを示す
- [ ] AC5: AC4 の場合でも、CHANGELOG の検査のジョブの結論は success である
- [ ] AC6: 見張るパスと `CHANGELOG.md` を両方変えた main 宛ての Pull Request では、未記入の警告を出さない
- [ ] AC7: 見張るパスを変えていない main 宛ての Pull Request（例: `docs/` と `tests/` だけ）では、未記入の警告を出さない
- [ ] AC8: 宛先が統合ブランチの Pull Request・積み重ねた Pull Request・`push` のイベントでは、未記入の警告を出さない
- [ ] AC9: 差分を得られないとき、CHANGELOG の検査のジョブは失敗し、ログに理由が出る
- [ ] AC10: 判定（変えたパスの一覧 → 警告の有無と示すファイル）は GitHub へ繋がずに pytest で確かめられ、AC4・AC6・AC7 の場合と、前提 2 の 5 つの見張るパスのそれぞれがテストにある
- [ ] AC11: 見張るパスの一覧は 1 か所で定義され、テストと検査ジョブが同じ定義を使う

退行しないこと:
- [ ] AC12: 既存の検査ジョブ（Python syntax check・Ruff lint・ShellCheck・Pytest）の定義とトリガーが変わらない。`uv run --locked pytest tests/ -q` が通る
- [ ] AC13: `docs/specifications/ci-checks.md` に CHANGELOG の検査（対象の Pull Request・見張るパス・警告であって失敗にしないこと）が書いてある

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 見張るパスを変えるときに直す場所は 1 か所（AC11） |
| セキュリティ | `pull_request` のイベントで動かし、`pull_request_target` を使わない。ジョブの権限は `contents: read` を超えない |
| 性能・拡張性 | CHANGELOG の検査のジョブは Docker もビルドも使わず、既存の検査ジョブより先に終わる |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（devbase のコマンド・イメージは変えない） |
| データ | 無し |
| 既存の振る舞い | main 宛ての Pull Request の CI に検査ジョブが 1 つ増える。既存の検査ジョブは変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --locked pytest tests/ -q`（判定のテストと、ci.yml の定義のテスト。`tests/ci/` に倣う） |
| 静的解析 | CI の既存の検査ジョブ（Ruff lint・ShellCheck）。検査の処理をシェルで書くならその対象に入れる |
| 手動確認 | 実装の Pull Request の上で CHANGELOG の検査が走り、success で終わることを `gh pr checks` で見る。警告が出る経路は AC10 の pytest で担保する |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | CI は `.github/workflows/ci.yml`、CI の仕様は `docs/specifications/ci-checks.md`、CI のテストは `tests/ci/`。`lib/` `bin/` には置かない（配布物に CI の処理を混ぜない） |
| コーディング規約 | `CONTRIBUTING.md` の「コーディング規約」と `docs/developer/contributing.md`。Python は PEP 8、シェルは ShellCheck の既定の水準で指摘 0 件 |
| テスト戦略 | 判定は単体テスト（パスの一覧を与える）。ci.yml の定義（トリガー・権限・既存ジョブの不変）は `tests/ci/test_shellcheck_job.py` と同じ形で ci.yml を読むテスト |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、ci.yml の既存ジョブを変えないこと、用語集の語で書くこと |
| 確認してから行う | ブランチ保護の必須チェックの変更、警告を失敗に上げること、見張るパスの追加・削除 |
| 行わない | CHANGELOG の自動生成、既存の CHANGELOG の書き直し、NDF の Skill の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 未記入の警告を失敗に上げるか（ラベルなどで明示的に外す仕組みと組み合わせる） | 保守者。警告を入れて数か月の書き漏れの件数を見てから | 次のメジャーリリースの前 |
| 見張るパスに `docs/user/` を加えるか（利用者向けの文書の変更も CHANGELOG に載せるか） | 保守者 | 期限なし（前提 2 のまま進める） |
