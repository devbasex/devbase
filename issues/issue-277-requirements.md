# #277: ci: main の保護の必須チェックに pytest が入っておらず、テストが落ちたままマージできる

正は課題の本文（#277）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何を見つけたか
>
> `main` の保護の必須チェックに pytest が入っていない。必須は次の 5 つだけである
> （2026-09-26、`gh api repos/devbasex/devbase/branches/main/protection/required_status_checks -q .contexts`）。
>
> ```
> ["Python syntax check (3.10)","Python syntax check (3.11)","Python syntax check (3.12)","Ruff lint","ShellCheck"]
> ```
>
> CI の `pytest` ジョブ（`Pytest (Python 3.10)` / `Pytest (Python 3.13)`）は走るが、落ちていても `main` へマージできる。
>
> ## どこで見つけたか
>
> - issue #216 の本文「併せて、`main` の保護の必須チェックに pytest を入れるかを決める」
> - `.github/workflows/ci.yml` の `pytest` ジョブ（`name: Pytest (Python ${{ matrix.python-version }})`）
>
> ## なぜこの変更の範囲外なのか
>
> issue #216 はマイルストーン「02 CI と検査の網」の中で standard のモードで扱う。必須チェックの一覧は GitHub の保護設定（外部の系の状態）で、変更は operation のモードに当たる。リポジトリの差分では表せず、同じ Pull Request で確かめられない。#216 の受け入れ条件は `ci.yml` のトリガーだけにした。
>
> ## 直さないと何が起きるか
>
> - pytest が落ちた Pull Request を `main` へマージできる。`main` へのマージがそのまま配布になる運用のため、落ちたテストのまま利用者へ届きうる
> - 必須にする場合、matrix の名前（`Pytest (Python 3.10)` / `Pytest (Python 3.13)`）を必須チェックに書くことになり、matrix の版を変えるたびに保護設定も直す必要がある。その扱いも決める
>
> ## 由来
>
> issue #216

課題へのコメント（2026-09-28、ミッション m02-ci の設計メモ）:

> この課題の設計は #291（ci.yml のトリガーと検査ジョブの name を固定する回帰テスト）も範囲に含める。pytest を必須チェックに入れるときの job の name（matrix の版に依存しない名前にするか）を決め、#291 の回帰テストが固定する name の一覧にその名前を入れる。保護設定の変更そのもの（operation）は、ミッションの Pull Request が main へ入った後に承認を得て行う。

### 解釈

- pytest を `main` の必須チェックに入れる。入れるかどうかはもう問わない（コメントが入れる前提で設計を求めている）
- 必須チェックに書く名前は、pytest の matrix の版（今は 3.10 / 3.13）を変えても変わらない 1 つの名前にする。matrix の版ごとの名前を並べる案は、依頼の「版を変えるたびに保護設定も直す」を避けられないため採らない
- 変更は 2 つに分かれる。リポジトリの差分（`ci.yml` と回帰テスト）はこの課題の Pull Request で入れ、保護設定の変更は `main` へ入った後に承認を得て行う
- #291 の回帰テスト（トリガーと検査ジョブの名前の固定）も同じ設計で扱う

## 目的

- pytest が 1 本でも落ちた（または走り切らなかった）Pull Request を `main` へマージできない状態にする
- pytest の matrix の版を足し引きしても、`main` の保護設定を直さずに済む状態にする
- `ci.yml` のトリガーと検査ジョブの名前が崩れたとき、CI の pytest が落ちて気づける状態にする（#291）

## 前提

- 前提 1: 必須にする範囲は `main` だけとする。統合ブランチ（`release/**`・`mission/**`）には保護が無く（2026-09-28 時点）、この課題で足さない
- 前提 2: `main` の直近の CI はすべて成功している（2026-09-28、`ci.yml` の直近 40 回の結論がすべて `success`）。必須にした時点で、進行中の Pull Request が pytest の失敗で新たに止まることは無いと見なす
- 前提 3: 保護の方式は今の classic branch protection（`required_status_checks`・`strict: true`・チェックの提供元は GitHub Actions）のままとし、rulesets へは移さない（rulesets は 0 件）
- 前提 4: `enforce_admins` は無効のまま変えない。管理者が保護を越えてマージできる点はこの課題で扱わない
- 前提 5: 必須チェックの新しい名前は、既存の検査ジョブの名前（7 件）のどれとも重ならない。既存の 7 件の名前は変えない
- 前提 6: 必須チェックにするのは pytest の結果をまとめた 1 つのチェックとする。まとめ方（集約用のジョブを置くか、別の手段か）は `design` が決める

## 対象範囲

含む:

- `ci.yml` に、pytest の matrix の全版の結果をまとめ、版に依存しない名前で出るチェックを足すこと
- `ci.yml` のトリガーの形と検査ジョブの名前（まとめたチェックを含む）を固定する回帰テスト（#291）
- `docs/specifications/ci-checks.md` と `docs/developer/contributing.md` の、検査ジョブの名前・必須チェックの記述の更新
- `main` へ入った後に行う保護設定の変更の手順と、その確認のコマンド（本文の「検証手段」に書く。実行は承認の後）

含まない:

- 統合ブランチへの保護の追加（前提 1）
- `enforce_admins`・レビューの必須化など、必須チェック以外の保護設定（前提 4）
- pytest の matrix の版そのものの変更、テストの中身の変更
- 既存の検査ジョブ（Python syntax check・Ruff lint・ShellCheck）のまとめ直しや名前の変更（前提 5）
- rulesets への移行（前提 3）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | pytest の matrix の各版が終わった（成功・失敗・取り消し・時間切れ） | Pull Request か push で CI が起動した | 1 版でも成功以外なら E2 が失敗になる | — |
| E2 | pytest をまとめたチェックの結論が出た | E1 の全版が終わった | まとめたチェックが `failure` になる。E1 が成功以外でも `skipped` にはならない（`skipped` は保護設定で合格と扱われるため） | E1 |
| E3 | 回帰テストが `ci.yml` の形を確かめた | Pytest のジョブが走った | トリガーか名前の一覧が崩れていれば pytest が落ち、E2 が `failure` になる | — |
| E4 | この課題の Pull Request が `main` へ入った | 人のマージ | — | E2 が `main` 宛てで成功している |
| E5 | `main` の必須チェックにまとめたチェックの名前が足された | 承認を得た operation（`gh api` で保護設定を更新） | 名前の綴りが違えば、以降の `main` 宛ての Pull Request でそのチェックが「待ち」のまま残り、マージできない。そのときは保護設定を戻す | E4（先に足すと、まとめたチェックを出さない進行中の Pull Request が止まる） |
| E6 | pytest が落ちた Pull Request の `main` へのマージが拒まれた | E5 の後に、pytest が落ちた Pull Request をマージしようとした | — | E5 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 必須チェック | `main` の保護設定の `required_status_checks` に並ぶチェックの名前。すべてが合格しないと `main` へマージできない |
| 検査ジョブ | （用語集の語）`ci.yml` の jobs の 1 つ |
| 統合ブランチ | （用語集の語）`release/**` と `mission/**` |

## 受け入れ条件

リポジトリの差分（この課題の Pull Request で確かめる）:

- [ ] `ci.yml` に、pytest の matrix の全版を待つチェックが 1 つあり、その名前は `${{ matrix... }}` を含まない固定の文字列である
- [ ] pytest の matrix の全版が成功したとき、まとめたチェックの結論は `success` になる
- [ ] pytest の matrix のどれか 1 版が失敗・取り消し・時間切れのとき、まとめたチェックの結論は `failure` になり、`skipped` にならない
- [ ] まとめたチェックは、Pull Request の宛先が `main`・統合ブランチ・それ以外のどれでも出る（トリガーは今の `ci.yml` と同じ）
- [ ] 既存の検査ジョブの名前 7 件（`Python syntax check (3.10)` / `(3.11)` / `(3.12)`・`Ruff lint`・`ShellCheck`・`Pytest (Python 3.10)`・`Pytest (Python 3.13)`）は変わらない
- [ ] 回帰テスト（#291）が、`on.pull_request` が `branches` / `branches-ignore` を持たないことを確かめ、持たせると落ちる
- [ ] 回帰テスト（#291）が、`on.push.branches` が `main`・`release/**`・`mission/**` の 3 つとちょうど一致することを確かめ、足し引きすると落ちる
- [ ] 回帰テスト（#291）が、検査ジョブの名前の一覧（既存 7 件の元になる `name` と matrix の値、とまとめたチェックの名前）を確かめ、どれかを変えると落ちる
- [ ] 回帰テストが、まとめたチェックが pytest のジョブを待ち、pytest が成功以外のときも走って `failure` を返す形であることを確かめ、待ちを外すか常に走る指定を外すと落ちる
- [ ] `docs/specifications/ci-checks.md` の「pytest で確かめないもの」「止める pytest は無い」の記述が、回帰テストの名前を指す記述へ改まっている
- [ ] `docs/specifications/ci-checks.md` と `docs/developer/contributing.md` に、まとめたチェックの名前と、それが `main` の必須チェックであること（E5 の後の状態）が書かれている

保護設定（`main` へ入った後、承認を得た operation で確かめる）:

- [ ] `gh api repos/devbasex/devbase/branches/main/protection/required_status_checks -q .contexts` の出力が、既存の 5 件とまとめたチェックの名前の 6 件になる
- [ ] 足した後の最初の `main` 宛ての Pull Request で、まとめたチェックが「待ち」のまま残らず `success` として照合される
- [ ] `strict` は `true` のまま、既存の必須チェック 5 件は消えていない

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | pytest の matrix の版を足し引きしても、保護設定を直さずにまとめたチェックの照合が続く |
| 性能・拡張性 | まとめたチェックの追加で、CI の 1 回の所要（最も遅いジョブの終わりまで）が 1 分を超えて延びない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（`bin/devbase` の入出力に触れない） |
| データ | 無い |
| 既存の振る舞い | 1 回の CI で出るチェックが 7 件から 8 件になる。E5 の後、pytest が落ちた Pull Request は `main` へマージできなくなる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --locked pytest tests/ -q`（回帰テストを含む） |
| 静的解析 | `ruff check --select=E9,F63,F7,F82 lib`、`python -m compileall -q lib bin` |
| CI での確認 | この課題の Pull Request の検査で、まとめたチェックが `success` で出ることを `gh api repos/devbasex/devbase/commits/<sha>/check-runs` で見る |
| 失敗の経路 | pytest を 1 版だけ落とす使い捨てのコミットを積んだ Draft の Pull Request で、まとめたチェックが `failure`（`skipped` でない）になることを check-runs で見て、Pull Request を閉じる |
| 保護設定の変更（E5） | 承認の後、`gh api -X POST repos/devbasex/devbase/branches/main/protection/required_status_checks/contexts` にまとめたチェックの名前を渡す。前後で `-q .contexts` を打って差分を記録する |
| 保護設定の確認（E6） | E5 の後の最初の `main` 宛ての Pull Request で、`gh api repos/devbasex/devbase/commits/<sha>/status` とマージ可否（`mergeable_state`）を見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | CI の定義は `.github/workflows/ci.yml`。CI の形を固定するテストは `tests/ci/`（既存の `test_shellcheck_job.py` と並べる） |
| コーディング規約 | `ci.yml` のコメントは既存に揃えて日本語。Python は Ruff の既存の設定 |
| テスト戦略 | `ci.yml` の形は PyYAML で読む単体テストで固定する（`on` は PyYAML で `True` のキーになる）。失敗の経路の実際の結論（`failure` か `skipped` か）は GitHub の上でしか見られないため、Draft の Pull Request で 1 度確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | pytest の全体の実行、既存の検査ジョブの名前の保持、仕様と開発者ガイドの同時更新 |
| 確認してから行う | `main` の保護設定の変更（E5。承認を得てから）、失敗の経路を確かめる使い捨ての Pull Request を出すこと |
| 行わない | 統合ブランチへの保護の追加、`enforce_admins` の変更、既存の検査ジョブの名前の変更、pytest の matrix の版の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| まとめたチェックの名前（例: `Pytest`）と、まとめ方（`needs` と `if: always()` の集約用のジョブか、別の手段か） | `design`（この課題の設計 PR） | 設計 PR のレビューまで |
| E5（保護設定の変更）を行う時点と承認者 | 利用者（ミッション m02-ci の Pull Request が `main` へ入った後） | ミッションの Pull Request のマージ後 |
| 既存の必須チェック 5 件も、版に依存しない名前へまとめ直すか | 利用者（別の課題として起票するかを含む） | この課題の範囲外。仕上げの振り返りまで |
