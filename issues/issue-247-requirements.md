# #247: chore: shellcheck bin/devbase の既存 9 件を片付け、CI の severity を warning へ下げる

正は課題の本文（#247）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何を見つけたか

**`shellcheck bin/devbase` は 9 件の指摘を出すが、CI はそれを一生見ない。**

PR #213 の完了判定が既存の失敗として「`shellcheck bin/devbase` が exit=1。
`release/v3.7.0` 時点でも同じ指摘が出るため、この PR が原因ではない」と記録した。
その後、v3.7.0 のどの Pull Request もこの指摘を拾っていない。

内訳（shellcheck 0.11.0、`shellcheck -f gcc bin/devbase`、2026-09-26）:

| コード | 水準 | 件数 | 行 |
| --- | --- | --- | --- |
| SC2155 | warning | 5 | 37, 38, 147, 153, 163 |
| SC2015 | info | 1 | 37 |
| SC1091 | info | 3 | 50, 61, 378 |

`severity: warning` へ下げるには、**SC2155 の 5 件を直せば足りる。**

CI の ShellCheck は `severity: error` で走る。

```yaml
# .github/workflows/ci.yml:34-45
  shellcheck:
    name: ShellCheck
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run ShellCheck on bin/
        uses: ludeeus/action-shellcheck@master
        with:
          scandir: ./bin
          severity: error
      - name: Run ShellCheck on install.sh
        run: shellcheck --severity=error install.sh
```

9 件は warning / info 級のため、**`severity: error` の閾値では 1 件も報告されない。**
`main` の CI（run 35794620399、`fd5fa48`）でも ShellCheck は success である。
つまり「CI が通っている」ことと「`shellcheck` がきれいである」ことが乖離したままになる。

`install.sh` は既定の水準で 0 件のため、`--severity=error` も同時に外せる。

## どこで見つけたか

v3.7.0 の振り返り（起票の取りこぼしを拾う工程）。

- PR #213 の本文「未検証の項目 / 既存の失敗」
- `/Users/takemi_ohama/devbase/.github/workflows/ci.yml:34-45`

`gh issue list --repo devbasex/devbase --state all --search "shellcheck"` は #216 / #141 / #139 の
3 件で、**この 9 件を扱う課題は無い。**

## なぜこの変更の範囲外なのか

v3.7.0 の 8 課題はいずれも `bin/devbase` のシェルの品質を対象にしていない。
PR #213 は文書だけを変える Pull Request で、9 件はその前から出ている。

## 直さないと何が起きるか

- 手元で `shellcheck bin/devbase` を打った人が毎回 exit=1 を見る。**この 9 件が既知なのか
  新しく増えたのかを、記憶か `git stash` でしか区別できない**
- 閾値を下げられないため、`bin/devbase` へ新しく入った warning 級の問題も CI では止まらない。
  `bin/devbase` は位置引数の解決（#146 / #142 / #196 / #200）を持つ要所である

## 直し方の案

| 案 | 内容 |
| --- | --- |
| A | SC2155 の 5 件を直してから CI の `severity` を `warning` へ下げる。**以後は増えない** |
| B | 直さないものに `# shellcheck disable=SCxxxx` と理由を添え、残りを直してから `severity` を下げる |
| C | `severity: error` のまま、別のジョブで `warning` 以上を**報告だけ**する（失敗させない）。増えたことは見えるが、止まらない |

`bin/devbase` は `devbase` 本体の入口であり、A か B が望ましい。

## 由来

issue #208（v3.7.0 の振り返り、PR #212 / PR #213）

## 依頼（原文）

> | 案 | 内容 |
> | --- | --- |
> | A | SC2155 の 5 件を直してから CI の `severity` を `warning` へ下げる。**以後は増えない** |
> | B | 直さないものに `# shellcheck disable=SCxxxx` と理由を添え、残りを直してから `severity` を下げる |
> | C | `severity: error` のまま、別のジョブで `warning` 以上を**報告だけ**する（失敗させない）。増えたことは見えるが、止まらない |
>
> `bin/devbase` は `devbase` 本体の入口であり、A か B が望ましい。

（上の「直し方の案」から）

## 目的

- `shellcheck bin/devbase` を既定の水準（style まで）で 0 件にし、CI の `bin/` と `install.sh` の検査を既定の水準で走らせる。以後、新しい指摘は CI で止まる（案 A と B の組み合わせ。直すものは直し、直すと振る舞いが変わりうるものは抑止の注記にする）

## 前提

- 前提 1: 指摘の一覧は shellcheck 0.11.0（`devbase-base:latest` に入っている版）で数える。`bin/devbase` は 9 件（SC2155 ×5・SC2015 ×1・SC1091 ×3）、`bin/rc` と `install.sh` は 0 件（2026-09-26、`main` = `5edc75f`）
- 前提 2: 水準は `warning` でなく既定（style まで）へ戻す。`install.sh` と `bin/rc` は既定で 0 件で、`containers/base/tmux-*` の検査も既定で走っている（`ci.yml` のコメント）。`bin/devbase` の 9 件（info の 4 件を含む）を片付ければ揃えられる
- 前提 3: `bin/devbase` は `set -e` で動く。`local x=$(f)` / `export X=$(f)` を宣言と代入に分けると、`f` が非 0 を返したときに `set -e` で止まるようになる。当たる箇所: 163 行の `check_base_image_dependency`（devbase-* を使わない Dockerfile で 1 を返す）、37 行の `DOCKER_GID`（Darwin 以外で `/etc/group` に docker が無いと `grep` が 1 を返す）
- 前提 4: 振る舞いを変えないことを優先する。直すと振る舞いが変わりうる指摘は、`# shellcheck` の指示と理由を添えて残すか、非 0 を明示的に受ける形（`|| true` など）で直す。どちらにするかは指摘ごとに設計で決める
- 前提 5: CI の shellcheck の版は手元の基準（base イメージの 0.11.0）に揃える。ubuntu-latest の runner の shellcheck と `ludeeus/action-shellcheck` の既定の版は 0.11.0 と限らず、版が違うと指摘の数が変わる。base イメージの版は Ubuntu のアーカイブに従い固定しない（`docs/specifications/base-image-shellcheck.md` の「版」）ため、base の版が上がったら CI の版を手で上げる

## 対象範囲

含む:
- `bin/devbase` の 9 件の指摘を、1 件ずつ直すか抑止の注記にするかを決めて片付けること
- `.github/workflows/ci.yml` の ShellCheck ジョブの `bin/` と `install.sh` の水準を既定へ戻すこと
- CI の ShellCheck ジョブの shellcheck の版を 0.11.0 に揃えること

含まない:
- `bin/devbase` の指摘以外の書き換え
- `containers/base/` のシェルの指摘（#259）
- shellcheck 以外の静的解析（Ruff の規則の拡大など）
- 設計のクラス図: 変える対象（`bin/devbase` の Bash と `ci.yml`）は型を持たない
- 設計の非機能設計表: この仕様は非機能の条件を持たない（取得する shellcheck の完全性の確かめ方は設計の決定に置く）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | `bin/devbase` を変える Pull Request を出した | 開発者 | — | — |
| E2 | ShellCheck ジョブが既定の水準で `bin/` と `install.sh` を検査した | E1 | 指摘が 1 件でもあればジョブが失敗する | 既存の 9 件が片付いている |
| E3 | base イメージの shellcheck の版が上がった | Ubuntu のアーカイブの更新と base の再ビルド | CI の版と食い違い、手元と CI で指摘の数が変わる | — |

E3 は受け入れ条件にせず、前提 5 の運用（手で上げる）に置く。

## 用語

| 用語 | 意味 |
| --- | --- |
| 指摘 | shellcheck が出す 1 件（SC の番号・水準・行） |
| 抑止の注記 | 指摘を抑える `# shellcheck` の指示と、抑える理由のコメントの組 |

## 受け入れ条件

- [ ] shellcheck 0.11.0 の既定の水準で `shellcheck bin/devbase bin/rc install.sh` が 0 件（exit 0）
- [ ] 抑止の注記を置いた行には、抑える理由が同じ行か直前の行に書いてある（指示だけの行が無い）
- [ ] CI の ShellCheck ジョブの `bin/` と `install.sh` の検査は `severity` の指定を持たない（既定の水準で走る）
- [ ] CI の ShellCheck ジョブのログに、使った shellcheck の版として `0.11.0` が出る（`bin/` と `install.sh` の両方の検査が同じ版を使う）
- [ ] 振る舞いが変わらない: devbase-* を使わない Dockerfile のプロジェクトで `devbase build` の shell 経路が、`check_base_image_dependency` の非 0 で止まらずプロジェクトのビルドへ進む（テストで固定する）
- [ ] 振る舞いが変わらない: `DOCKER_GID` は Darwin で `0`、それ以外で `/etc/group` の docker の gid、docker の行が無ければ空で、いずれの場合も `bin/devbase` が止まらない。`COMPOSE_PROJECT_NAME` はカレントディレクトリの名前（テストで固定する）
- [ ] `uv run --locked pytest tests/ -q` の件数が減らず、すべて通る

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない |
| 既存の振る舞い | 変わらない（変わらないことをテストで固定する） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 静的解析 | `docker run --rm -v "$PWD":/w -w /w --entrypoint shellcheck devbase-base:latest bin/devbase bin/rc install.sh` |
| テスト | `uv run --locked pytest tests/ -q` |
| 手動確認 | 実装 Pull Request の ShellCheck ジョブが成功し、ログに `version: 0.11.0` が出る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 振る舞いのテストは `tests/cli/` の下（`bin/devbase` を subprocess で起動する既存のテストに合わせる） |
| コーディング規約 | `CONTRIBUTING.md`。シェルは shellcheck の既定の水準 |
| テスト戦略 | 振る舞いが変わりうる 2 か所（前提 3）を、`bin/devbase` を実際に起動する統合テストで固定する |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 指摘ごとの判断（直す / 抑止の注記）を設計の決定に残す |
| 確認してから行う | 振る舞いが変わりうる書き換え（行わずに抑止の注記にする） |
| 行わない | 指摘の無い行の整形 |
