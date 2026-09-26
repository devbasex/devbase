# #216: ci: リリースブランチ宛の Pull Request で検査ジョブが 1 件も動かない（on.pull_request.branches が main だけ）

正は課題の本文（#216）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何を見つけたか

`.github/workflows/ci.yml` のトリガーが `main` 宛だけになっているため、リリースブランチ宛の
Pull Request では検査ジョブが 1 件も動かない。

```yaml
# .github/workflows/ci.yml
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
```

PR #213（base = `release/v3.7.0`）で実測した結果:

```
$ gh pr checks 213
no checks reported on the 'feature/v3.7.0-docs-enumeration' branch

$ gh pr view 213 --json statusCheckRollup,mergeStateStatus
{"checks":0,"mergeStateStatus":"CLEAN","mergeable":"MERGEABLE"}
```

`mergeStateStatus` は `CLEAN` を返すため、**検査が通ったのか、そもそも動いていないのかを
Pull Request の画面から区別できない**。

**影響が出るのは `issue-plan-strategy` の multi-PR 運用のときに限る。** 2026-09-26 時点で
リリースブランチは無く、単発の Pull Request は `main` 宛で検査が走る。

**`main` の保護の必須チェックに pytest が入っていない。** 必須になっているのは
Python syntax check（3.10 / 3.11 / 3.12）・Ruff lint・ShellCheck の 5 つだけである
（2026-09-26、`gh api repos/devbasex/devbase/branches/main/protection/required_status_checks`）。

## どこで見つけたか

- `.github/workflows/ci.yml`（`on.push.branches` / `on.pull_request.branches`）
- PR #213 https://github.com/devbasex/devbase/pull/213 の実装レビュー（`/ndf:cross-review`）。
  収束の判定で `state.py judge` が `CI_VERDICT=unverified` を返し、
  「継続的統合を確かめられないまま収束する」と警告したことが発端

## なぜこの変更の範囲外なのか

#208 / #195 の受け入れ条件は `docs/` の列挙と手順を実装に合わせることだけで、
継続的インテグレーションの設定は対象に入っていない。PR #213 の差分は
`docs/` の 8 ファイルのみ（非 docs の差分 0 件）で、`.github/` を触っていない。

## 直さないと何が起きるか

`issue-plan-strategy` の multi-PR 運用では、課題ごとの Pull Request の base が
`release/vX.Y.Z` になる。そのため:

- 個別の Pull Request は **python-syntax / Ruff / ShellCheck / pytest のいずれも通らないまま**
  リリースブランチへ取り込まれる。最初に検査が走るのは、まとめてリリースブランチを `main` へ
  出す Pull Request の時点になる
- そこで落ちたとき、どの個別 Pull Request が原因かを切り分ける手掛かりが無い
- #214 で提案している「argparse と文書の一致をテストで固定する」テストを追加しても、
  そのテストがいちばん必要な `docs/` の Pull Request（base = リリースブランチ）では動かない

`main` 宛の Pull Request でも、pytest が落ちたままマージできる。

## 直し方の案

`on.pull_request.branches` と `on.push.branches` に `release/**` を足す。
（併せて、リリースブランチへの push でも走らせるかは運用の判断）

```yaml
on:
  push:
    branches: [main, 'release/**']
  pull_request:
    branches: [main, 'release/**']
```

併せて、`main` の保護の必須チェックに pytest を入れるかを決める。

## 由来

PR #213（issue #208 / #195）の実装レビューと完了判定の途中で発見した。

## 依頼（原文）

> `on.pull_request.branches` と `on.push.branches` に `release/**` を足す。
> （併せて、リリースブランチへの push でも走らせるかは運用の判断）
>
> 併せて、`main` の保護の必須チェックに pytest を入れるかを決める。

（上の「直し方の案」から。マイルストーン 6「02 CI と検査の網」の conductor の補足:）

> ndf のミッション運用では課題の PR は `mission/<名前>` 宛てに出る。#216 は `release/**` だけでなく mission/** も対象の宛先に含めること
>
> main の保護の必須チェックに pytest を足すことは operation で、このミッションに混ぜない。やるべきなら out-of-scope で起票する

## 目的

- どのブランチ宛ての Pull Request でも、`main` 宛てと同じ検査ジョブが走る状態にする。検査が動いていないのに `mergeStateStatus` が `CLEAN` になる状態をなくす

## 前提

- 前提 1: 課題の Pull Request の宛先になる `main` 以外のブランチ（統合ブランチ）は、`issue-plan-strategy` の `release/**` と、NDF のミッション運用の `mission/**` の 2 系統である（2026-09-26 時点。`git branch -a` にどちらもまだ無い）
- 前提 2: Pull Request の検査は宛先を絞らない。積み重ねた Pull Request（feature 宛ての feature）も検査の対象にする。宛先の名前の規則が増えるたびに `ci.yml` を直す必要をなくすため
- 前提 3: `main` の保護の必須チェック（`Python syntax check (3.10)` / `(3.11)` / `(3.12)`・`Ruff lint`・`ShellCheck`）はジョブの `name` で照合される。`name` を変えると必須チェックが「待ち」のまま残る
- 前提 4: 必須チェックの一覧の変更は GitHub の保護設定（外部の系の状態）の変更で、このミッション（standard）では扱わない。#277 に起票済み

## 対象範囲

含む:
- `.github/workflows/ci.yml` の `on.pull_request` と `on.push` のトリガー

含まない:
- `main` の保護の必須チェックに pytest を足すこと（#277、operation）
- ジョブの中身・`name`・matrix の変更、`concurrency` の追加
- `ci.yml` 以外のワークフロー

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | Pull Request を開いた（または push で更新した） | 開発者・エージェント | 検査ジョブが起動しない → 画面に検査が 0 件と出て `CLEAN` になる | — |
| E2 | 検査ジョブが起動した | E1 | 起動しない宛先が残る | E1 |
| E3 | 統合ブランチへ Pull Request を取り込んだ（push が起きた） | マージ | 取り込み後の先端が検査されない | E2 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 検査ジョブ | `ci.yml` の jobs の 1 つ（Python syntax check・Ruff lint・ShellCheck・Pytest） |
| 統合ブランチ | 課題の Pull Request の宛先になる `main` 以外のブランチ（`release/**` と `mission/**`） |

## 受け入れ条件

- [ ] 宛先が `main` / `release/v9.9.9` / `mission/m6-ci-checks` / それ以外の任意の名前のブランチである Pull Request のいずれでも、`Python syntax check`（3 版）・`Ruff lint`・`ShellCheck`・`Pytest`（2 版）の 7 件の検査ジョブが起動する（`gh pr checks` で 7 件が出る）
- [ ] `main`・`release/**`・`mission/**` への push で同じ 7 件が起動する。それ以外のブランチへの push では起動しない（Pull Request の検査と二重に走らせない）
- [ ] 検査ジョブの `name` は変わらない（`main` の保護の必須チェック 5 件がそのまま照合される。`main` 宛ての Pull Request で必須チェックが「待ち」で残らない）
- [ ] ワークフローの記述が正しい: `actionlint .github/workflows/ci.yml` が 0 件、または実装 Pull Request 自身の検査で GitHub がワークフローを読み込めている（検査ジョブが起動している）
- [ ] `ci.yml` の差分は `on:` の節の中だけにある（`jobs:` の差分が 0 行）

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない |
| 既存の振る舞い | `main` 以外宛ての Pull Request と統合ブランチへの push で検査が走るようになる。CI の実行数が増える |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 静的解析 | `actionlint .github/workflows/ci.yml`（手元に無ければ CI の読み込みで代える） |
| テスト | `uv run --locked pytest tests/ -q`（トリガーを固定するテストを置く場合） |
| 手動確認 | このミッションの実装 Pull Request（宛先 `mission/**`）で `gh pr checks` が 7 件を返す。任意の名前の宛先は、捨てのブランチ宛ての下書きの Pull Request で 1 回確かめて閉じる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | CI は `.github/workflows/ci.yml` の 1 本に置く |
| コーディング規約 | `CONTRIBUTING.md` |
| テスト戦略 | トリガーの形は実装 Pull Request の実際の起動で確かめる。固定のテストを置くかは設計で決める |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存のテストの実行 |
| 確認してから行う | 保護設定の変更（このミッションでは行わない。#277） |
| 行わない | ジョブの改名、無関係なワークフローの整理 |
