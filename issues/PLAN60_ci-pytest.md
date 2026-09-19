# PLAN60: CI で tests/ の pytest を実行する

対象 issue: devbasex/devbase#141

- ワークフローモード: `light`
  - 根拠: 変えるのは CI の構成（`.github/workflows/ci.yml`）と、CI で通すためのテスト側の調整だけで、
    本番の振る舞いも本番コード（`bin/` `lib/` `etc/` `containers/`）の構造も変えない

## 依頼（原文）

> `tests/` に pytest のテスト一式（`tests/cli` `tests/commands` `tests/project` `tests/snapshot` など 10 ディレクトリ）がありますが、`.github/workflows/ci.yml` はこれを実行しません。
>
> テストを足しても、それが壊れたことに CI が気づきません。
>
> 導入時には次を確認する必要があります。
>
> - `uv` のセットアップ手順（`pyproject.toml` / `uv.lock` がある）
> - 実 `docker` を必要とするテストがある場合の切り分け（マーカーか、CI では除外するか）

## 目的

- `tests/` のテストが壊れた変更を、Pull Request の時点で CI が落とすようにする

## 前提

- 前提 1: CI の Python は `pyproject.toml` の `requires-python` の下限と、手元で使う版（2026-09-18 時点で uv が選ぶ 3.13）の 2 つで足りる。
  既存の `python-syntax` ジョブの行列（3.10 / 3.11 / 3.12）には合わせない。pytest の実行時間が 3 倍になる
  割に、版の差で落ちる箇所は `compileall` の行列が既に構文で拾っている
- 前提 2: 依存は `uv.lock` から `uv sync --locked` で入れる。lock と `pyproject.toml` が食い違えば CI が落ちる
- 前提 3: 実 docker・実 OpenBao・ネットワークを要るテストは、CI では走らせない。手元で走る
  テストのうち CI で落ちるものは、**テストの側を直して**（環境の隔離・スタブ）CI でも通るようにする。
  `skip` で逃がすのは、実機そのものを確かめるテストに限る
- 前提 4: CI の実行環境に `DEVBASE_ROOT` は無い。手元の実行はシェルの `DEVBASE_ROOT` を継承するため、
  両方で同じ結果になるテストだけを CI に載せる

## 対象範囲

含む:

- `.github/workflows/ci.yml` に pytest のジョブを足す
- CI の環境（`DEVBASE_ROOT` 無し・docker 無し・Linux）で落ちるテストの修正
- 開発者向け文書の CI の説明（`docs/developer/contributing.md` / `CONTRIBUTING.md` に記載があれば）

含まない:

- 本番コードの変更
- カバレッジの計測・閾値
- ruff の検査範囲の拡大（`--select` の追加）
- 既存ジョブ（`python-syntax` / `lint` / `shellcheck`）の変更

## 受け入れ条件

- [ ] 1. `main` 宛ての Pull Request と `main` への push で、`tests/` 全体の pytest を実行するジョブが走る
- [ ] 2. そのジョブは `uv.lock` から依存を入れ（`uv sync --locked` 相当）、lock に無い依存を取りに行かない
- [ ] 3. この Pull Request の CI で、そのジョブが成功する
- [ ] 4. テストを 1 件わざと失敗させた状態で、そのジョブが失敗する（確かめた後にその変更は戻す）
- [ ] 5. 手元（`DEVBASE_ROOT` を持つシェル）の `uv run pytest tests/` の結果が、この変更の前後で変わらない
      （通っていたテストが落ちない。件数の変化は CI 向けに skip へ回したものの数と一致する）
- [ ] 6. CI で skip するテストがあれば、1 件ごとに skip の理由がテストの中に書かれ、実機を要するテストに限られる

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト（手元） | `uv run pytest tests/ -q` |
| テスト（CI 相当） | `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` |
| CI | Pull Request の checks（`gh pr checks`） |
| 静的解析 | 既存の `python-syntax` / `lint` / `shellcheck` のジョブ |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 手元と CI 相当の両方で pytest を回す |
| 確認してから行う | テストを skip へ回すこと（理由を条件 6 のとおり残す） |
| 行わない | 本番コードの変更、既存ジョブの変更 |
