# PLAN67: base イメージに shellcheck を入れる

対象 issue: devbasex/devbase#249

- ワークフローモード: `standard`
  - 根拠: base イメージの本番の振る舞い（同梱するコマンド）を変える。base はすべての派生
    イメージとプロジェクトの土台で、変更は再ビルドした全員に届く（前例: PLAN63 / #160）
- ベースブランチ: `main`（`.ndf/worktree.json` に起点も本番のチャネルも宣言が無く、既定
  ブランチに落ちる）

## 目的

- **base と、base から派生したイメージのコンテナで、`shellcheck` がそのまま使える。**
  Bash スクリプトの静的検査を手元で走らせられ、shellcheck を内部で呼ぶ言語サーバ
  （bash-language-server）が診断を返せる状態にする
- **shellcheck が入っていないイメージは建たない。** 入れ損ないをビルドの時点で止める

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（devbase の CLI の引数・環境変数・コマンドは増減しない） |
| データ | 変わらない |
| 既存の振る舞い | **変わる。** base とその派生イメージのコンテナの `PATH` に `shellcheck` が加わる。イメージを建て直すまでは変わらない。CHANGELOG では Added に書く |
| イメージのサイズ | +約 25 MB（`shellcheck` と、依存で新しく入る `libnuma1` の 2 パッケージ） |
| 利用者の操作 | **`devbase build base --no-cache` が要る。`devbase up` だけでは反映されない。** 派生イメージ（`containers/general` など）を使っているプロジェクトは、その派生イメージも建て直す。稼働中のコンテナは `devbase down` → `devbase up` で作り直す |

## 前提

- **前提 1: 版は Ubuntu のアーカイブが配る版に任せ、固定しない。** 2026-09-23 の時点で
  `resolute/universe` の版は `0.11.0-2`（arm64 / amd64 とも）。`gh` / `terraform` / `nodejs` と
  同じく、建てた時点のアーカイブの版が入る
- **前提 2: CI はこの変更を検査しない。** `.github/workflows/ci.yml` にイメージを建てるジョブは
  無い（ジョブは python-syntax / lint / shellcheck / pytest の 4 つ）。イメージの中でしか
  確かめられない受け入れ条件の証跡は、**手元で建てたイメージから採って Pull Request 本文へ載せる**
- **前提 3: 対象は `FROM devbase-base:latest` の派生イメージに限る。** `general` / `go` /
  `latex` / `bi-tools` / `php` / `php85` / `trygroup` の 7 つがこれに当たり、base を建て直した
  後に建て直せば自動的に入る。`containers/lfm`（`FROM nvidia/cuda:...`）と `containers/snapshot`
  （`FROM ubuntu:26.04`）は base を継がない。lfm は base から `/usr/local` などを選んで `COPY`
  するだけで、`/usr/bin/shellcheck` は届かない

## 対象範囲

含む:

- `containers/base/Dockerfile` の 1 つ目の `RUN` の 1 回目の `apt-get install` の一覧への
  `shellcheck` の追加
- 同じ Dockerfile の版の確認の `RUN`（`gh --version && ... && session-manager-plugin --version`）
  への `shellcheck --version` の追加
- 回帰テスト（`tests/containers/`）
- 利用者向け文書（`docs/user/container-operations.md`）と CHANGELOG

含まない:

- bash-language-server の導入。Serena は自分で入れる。Claude Code の plugin LSP で要るかは
  devbasex/ai-plugins#818 の設計で決める（#249 の範囲外）
- `bin/devbase` の既存の指摘の片付けと、CI の severity の変更（#247）
- CI の ShellCheck ジョブを base の shellcheck へ寄せること。CI の runner はコンテナを使わない
- `containers/lfm` と `containers/snapshot` への導入（前提 3）
- 版の固定（前提 1）
- 派生イメージの Dockerfile の変更。いずれも `FROM devbase-base:latest` のため不要
- 新しい型・永続データ・画面・呼び出される約束の追加（そのためクラス図・ER 図・画面遷移図・
  API 仕様を作らない）

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | base イメージの構成物は `containers/base/` の直下に置く |
| コーディング規約 | `containers/base/Dockerfile` の既存の書き方に合わせる。追加の理由を直前のコメントに書く（PLAN63 の `poppler-utils` の行と同じ流儀） |
| テスト戦略 | `tests/containers/` の既存の流儀に合わせる。Docker に依存しない検査（Dockerfile の文字列）を既定にする（`test_base_dockerfile_fonts.py` / `test_base_dockerfile_bao.py`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 手元で全体テスト、`devbase build base --no-cache` と、派生イメージを建て直しての確認 |
| 確認してから行う | 前提 3 の範囲（lfm を含めないこと）。設計 Pull Request の承認で確かめる |
| 行わない | bash-language-server の導入、#247 の片付け、CI の変更、版の固定 |

## 実装計画

設計は [PLAN67_base-shellcheck-design.md](PLAN67_base-shellcheck-design.md)。
**タスクへの分解は実装の持ち場で `/ndf:implementation-plan` が行う。** ここでは触る対象だけを挙げる。

### 修正対象

- `containers/base/Dockerfile`
- `tests/containers/test_base_dockerfile_shellcheck.py`（新設）
- `docs/user/container-operations.md`
- `CHANGELOG.md`

### 切り戻し手順

- データの移行は無い。変わるのはイメージの中身だけである
- 戻すには次の 4 つを順に行う。
  1. ブランチの revert
  2. `devbase build base --no-cache`
  3. 使っている派生イメージの建て直し
  4. 稼働中のコンテナの作り直し（`devbase down` → `devbase up`）
- **`devbase rebuild` はここでは使えない。** `devbase build --expires=7` のシノニムで、期限内なら
  ビルドを飛ばす。コンテナも作り直さない

## 受け入れ条件

**実測の基準**: 以下の「現状」は 2026-09-23 に手元の `devbase-base:latest`（Ubuntu 26.04.1 /
arm64、作成 2026-09-22T23:10:59Z）で採った。導入の結果は、同じイメージから作った一時コンテナへ
`apt-get install -y --no-install-recommends shellcheck` して採った。

### 使えること（#249 の受け入れ条件）

- [ ] 1. 建てた base イメージで次のコマンドが終了コード 0 で終わり、`version:` の行を出す。
      現状は `shellcheck` が `PATH` に無い
      `docker run --rm --entrypoint /bin/bash devbase-base:latest -c 'shellcheck --version'`
- [ ] 2. 派生イメージ `devbase-general:latest` と `devbase-php:latest` でも、同じコマンドが
      終了コード 0 で終わる。どちらも base を建て直した後に建てたものを使う
- [ ] 3. **診断を返す。** 建てた base イメージの中で `echo $foo` の 1 行を持つ Bash スクリプトへ
      `shellcheck` を走らせると、出力に `SC2086` を含み、終了コード 1 で終わる。
      言語サーバが使うのはこの診断で、版を出すだけでは確かめたことにならない

### 入れ損ないを止めること

- [ ] 4. 版の確認の `RUN` に `shellcheck --version` がある。対象は
      `gh --version && ... && session-manager-plugin --version` の行で、
      **shellcheck が無ければ `docker build` がこの行で失敗する**
      検証: `tests/containers/` の Dockerfile の文字列検査
- [ ] 5. `shellcheck` は 1 つ目の `RUN` の 1 回目の `apt-get install` の一覧にあり、
      `shellcheck` を入れるための `RUN` は増えていない（`apt-get update` は 2 回のまま）
      検証: 同上

### 退行しないこと

- [ ] 6. 依存を含めて新しく入るのは `shellcheck` と `libnuma1` の 2 パッケージである。
      建てたイメージの中で次の値の合計が 30720（KB。30 MB）以下である。arm64 の実測は
      `shellcheck` 単体で 24971
      `dpkg-query -W -f='${Installed-Size}\n' shellcheck libnuma1`
      **合否を `docker images` の前後の差で決めない。** `--no-cache` の建て直しは
      `claude` / `nodejs` などの取得物も新しい版へ入れ替える。差にこの変更以外の増減が混ざる
- [ ] 7. `uv run --locked pytest tests/ -q` が終了コード 0
- [ ] 8. `devbase build base --no-cache` が arm64 で成功する
- [ ] 9. 次の 2 か所に shellcheck があり、どちらも**反映に `devbase build base --no-cache` が
      要る**ことを書いている
      - `docs/user/container-operations.md` の base の道具の説明
      - `CHANGELOG.md` の `[Unreleased]` の `### Added`

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --locked pytest tests/ -q` |
| イメージの中 | `devbase build base --no-cache` と派生イメージの建て直しの後、`docker run --rm --entrypoint /bin/bash <image> -c '...'`（受け入れ条件 1・2・3・6）。出力を Pull Request 本文へ貼る |
| CI | **イメージを建てない**（前提 2）。CI が緑でも受け入れ条件 1〜3・6・8 は確かめていない |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| amd64 での建て直し | 手元は arm64 のみ。amd64 はアーカイブの `Packages.gz` に `0.11.0-2`（Installed-Size 22867 KB、依存は arm64 と同じ）があることまで確かめた |
| 言語サーバからの診断 | bash-language-server は入れない（範囲外）。確かめるのは shellcheck 単体の診断まで（受け入れ条件 3） |

## 依頼（原文）

#249:

> **base イメージに `shellcheck` を入れる。** コンテナの中で Bash スクリプトの静的検査と、言語サーバによる Bash の診断を使えるようにする。
>
> `containers/base/Dockerfile` の `apt-get install -y --no-install-recommends` の一覧に `shellcheck` を足す。末尾の版の確認（187 行目の `RUN ... --version`）に `shellcheck --version` を加える。
>
> - [ ] base から作ったコンテナで `shellcheck --version` が終了コード 0 で終わる
> - [ ] 派生イメージ（general / php など）でも同じく使える
>
> 範囲外: bash-language-server の導入。Serena は自分で入れる。Claude Code の plugin LSP で要るかは devbasex/ai-plugins#818 の設計で決める / 既存の指摘の片付け（#247）
