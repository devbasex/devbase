# PLAN70: コンテナを作り直しても残るシェルの設定の読み込み先を用意する

対象 issue: devbasex/devbase#253

- ワークフローモード: `standard`
  - 根拠: base イメージの本番の振る舞い（対話シェルの初期化）と、entrypoint が張る永続化の
    symlink を変える。base はすべての派生イメージとプロジェクトの土台で、変更は建て直した
    全員に届く（前例: PLAN67 / #249）
- ベースブランチ: `main`

## 目的

- **利用者やツールが足したシェルの設定が、コンテナの作り直しで消えない。** 置き場所の
  ディレクトリへ `*.sh` を 1 つ置けば、以後に開く対話シェルで読み込まれる。対話シェルは
  `devbase login` / tmux の窓 / VS Code の端末である。`devbase down` → `devbase up` の後も
  読み込まれ続ける
- **ツールが置き場所を見つけられる。** 対話シェルでない処理（Claude Code の中の Bash など）
  からも、環境変数で置き場所のパスを知れる。ツールは `~/.bashrc` を書き換えなくてよい
- 最初の使い手は ai-plugins の中継（devbasex/ai-plugins#928）で、決まった名前をその実装が使う

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | **増える。** コンテナの環境変数 `DEVBASE_SHELLRC_DIR` と、置き場所 `~/.shellrc.d/`。ai-plugins#928 がこの 2 つを使う。名前の決定は設計の決定 1・2 |
| データ | **増える。** アカウントグループのボリューム（`/persistent/group`）に `.shellrc.d/` ができる。既存のエントリは変わらない |
| 既存の振る舞い | **変わる。** base とその派生イメージの `~/.bashrc` が、末尾で置き場所の `*.sh` を読む。置き場所が空なら見かけの振る舞いは変わらない |
| 利用者の操作 | **`devbase build base --no-cache` が要る。`devbase up` だけでは反映されない**（`entrypoint.sh` と `~/.bashrc` はイメージの中にある）。派生イメージを使うプロジェクトはその派生イメージも建て直し、稼働中のコンテナは `devbase down` → `devbase up` で作り直す |

## 前提

- **前提 1: 対話シェルは bash だけである。** base に zsh は入っていない（2026-09-24 の
  `devbase-base:latest` で `command -v zsh` が空）。`~/.zshrc` はインストーラ（uv / agy）が
  PATH の行を書くために作ったもので、読むシェルがいない。devbase 自身の起動定義
  （`/etc/devbase/ai-cli-aliases.sh`）も `~/.bashrc` からしか読まれていない
- **前提 2: `devbase login` は `docker compose exec ... bash` で、対話の非ログインシェルを
  開く。** `~/.bashrc` が読まれる。tmux の窓はログインシェルで、`~/.profile` が `~/.bashrc` を読む
- **前提 3: `~/.bashrc` は先頭で非対話シェルを帰す**（Ubuntu の既定の `case $- in *i*)`）。
  置き場所の `*.sh` が読まれるのは対話シェルだけである。非対話の処理へ届けるのは環境変数
  だけにする
- **前提 4: 対象は `FROM devbase-base:latest` の派生イメージに限る。** `containers/lfm` は
  base の `entrypoint.sh` をコピーするだけで `~/.bashrc` も `ENV` も継がない（PLAN67 の
  前提 3 と同じ）
- **前提 5: CI はイメージを建てない。** イメージの中でしか確かめられない条件の証跡は、手元で
  建てたイメージから採って Pull Request 本文へ載せる

## 対象範囲

含む:

- 置き場所のディレクトリを、分類 B（アカウントグループ単位）の永続化のエントリに加えること
- 置き場所の `*.sh` を読む処理（読み込み器）を base イメージへ置き、`~/.bashrc` の末尾から
  `ai-cli-aliases.sh` の**後に**読むこと
- 置き場所のパスを示すコンテナの環境変数
- 回帰テスト（`tests/containers/`）、利用者向け文書、CHANGELOG

含まない:

- `~/.zshrc` への読み込みの追加（前提 1。設計の決定 5）
- ai-plugins 側の実装（中継の本体・`~/.shellrc.d/` へ置くファイル。ai-plugins#928）と、中継の
  alias が `claude --dangerously-skip-permissions` を上書きする件（ai-plugins#936）
- ai-plugins#928 への名前の連絡（実装の持ち場以降で行う）
- `containers/lfm` と `containers/snapshot`（前提 4）
- 分類 A（全コンテナ共通）の置き場所。グループをまたいで効かせたい設定の置き場所は作らない
- 置き場所へ最初から入れておくファイル（雛形・例）

## 用語

| 用語 | 意味 |
| --- | --- |
| 置き場所 | 読み込み用のディレクトリ。コンテナの中では `~/.shellrc.d/`、実体は `/persistent/group/.shellrc.d/` |
| 読み込み器 | 置き場所の `*.sh` を名前の順に読むシェルの断片。base イメージが `/etc/devbase/` に置く |
| 分類 B | `entrypoint.sh` の `DEVBASE_GROUP_SETTINGS`。アカウントグループ単位で `/persistent/group` へ symlink するエントリ |

## 受け入れ条件

**実測の基準**: 以下の「現状」は 2026-09-24 に手元の `devbase-base:latest`（arm64、作成
2026-09-24T00:46Z）で採った。

### 残ること（#253 の中心）

- [ ] 1. 建て直したイメージで作ったコンテナで、`~/.shellrc.d` が `/persistent/group/.shellrc.d`
      を指す symlink である。その先は開発ユーザー（`ubuntu`）の所有のディレクトリである。
      現状は `~/.shellrc.d` が無い
- [ ] 2. **前提**: コンテナの `~/.shellrc.d/` に `alias plan70probe='echo kept'` を書いた
      `plan70.sh` がある
      **操作**: `devbase down` → `devbase up` で作り直し、`devbase login` で入る
      **結果**: `plan70probe` が `kept` を出す
- [ ] 3. 同じアカウントグループの別のコンテナ（`--index=2` など）の対話シェルでも、2 の
      `plan70probe` が効く。別のアカウントグループのコンテナでは効かない
      検証: entrypoint の関数のテストで、置き場所がグループの根の下へ張られることを固定する。
      実機ではグループを 1 つ確かめる

### 読み込み

- [ ] 4. 置き場所の `*.sh` は、ファイル名の昇順で全部読まれる。`10-a.sh` と `20-b.sh` が同じ
      alias を定義すると、`20-b.sh` の定義が残る
- [ ] 5. 名前が `*.sh` でないファイル（`x.txt` / `README`）、`.` で始まる名前のファイル、ディレクトリは読まれない
- [ ] 6. 置き場所が無い・空・`*.sh` が 1 つも無いとき、対話シェルの起動は何も出力せず、
      直後の `$?` が 0 である。読み込みの前に `shopt -s failglob` が有効でも同じである。
      読み込みの後、`failglob` は読み込みの前の状態に戻っている
- [ ] 7. 1 つのファイルが構文の誤りで失敗しても、名前の順で後ろのファイルは読まれる
      （誤りの行は標準エラーに出てよい）
- [ ] 8. 置き場所のファイルで `alias claude=...` を定義すると、`/etc/devbase/ai-cli-aliases.sh`
      の定義より勝つ（読む順が `ai-cli-aliases.sh` の後である）
- [ ] 9. 読み込みの後、読み込み器が使った変数がシェルに残らず、利用者が先に置いた同じ名前でない
      変数（たとえば `f`）の値も変わらない
- [ ] 10. 置き場所のパスは `DEVBASE_SHELLRC_DIR` が指すディレクトリで、変数が空か未設定なら
      `$HOME/.shellrc.d` である

### 見つけ方

- [ ] 11. 建て直したイメージで作ったコンテナで、**非対話**の次のコマンドが
      `/home/ubuntu/.shellrc.d` を出す。現状は何も出さず終了コード 1
      `docker exec <container> printenv DEVBASE_SHELLRC_DIR`

### 退行しないこと

- [ ] 12. `/etc/devbase/ai-cli-aliases.sh` の定義と起動オプションは変わらない（既存の
      `tests/containers/test_ai_cli_aliases.py` が通る）
- [ ] 13. 分類 A・B の既存のエントリの張り先と、`default` グループの初回シードの結果は変わらない
      （既存の `tests/containers/test_entrypoint_ai_settings.py` が通る）
- [ ] 14. `~/.zshrc` は変わらない
- [ ] 15. `uv run --locked pytest tests/ -q` が終了コード 0
- [ ] 16. `devbase build base --no-cache` が arm64 で成功する
- [ ] 17. 次の 4 か所に置き場所がある。表の 2 か所を除く 2 か所は、`DEVBASE_SHELLRC_DIR` と、
      **反映に `devbase build base --no-cache` が要る**ことを書いている
      - `docs/user/container-operations.md` の「ボリューム構造」の表の `devbase_home_{group}` の行
        （用途の列）
      - 同じ文書の「AI 設定の永続化」の分類 B の表の行
      - 同じ文書の新しい小節（置き場所の使い方・読む順・対話シェルだけで読まれること）
      - `CHANGELOG.md` の `[Unreleased]` の `### Added`

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| セキュリティ | 置き場所に書けるのは、同じアカウントグループのボリュームに書ける者だけである。既に `~/.claude`（hooks を含む）へ書ける者と同じ範囲で、新しい書き手を増やさない。devbase は置き場所へ何も書かない |
| 性能・拡張性 | 置き場所が空のとき、対話シェルの起動にかかる追加の処理は、ディレクトリの有無の判定と 1 回のグロブで終わる（外部コマンドを起動しない） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --locked pytest tests/ -q`（受け入れ条件 4〜10・12〜15 と、1・3 の関数の部分） |
| イメージの中 | `devbase build base --no-cache` の後、`docker run --rm --entrypoint /bin/bash devbase-base:latest -c '...'` と `docker exec`（受け入れ条件 1・11・14）。出力を Pull Request 本文へ貼る |
| 実機の手順 | 受け入れ条件 2・3 は、実際のプロジェクトで `devbase down` → `devbase up` を挟んで確かめる |
| CI | **イメージを建てない**（前提 5）。CI が緑でも受け入れ条件 1・2・3・11・16 は確かめていない |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | base イメージの構成物は `containers/base/` の直下に置く。永続化のエントリは `entrypoint.sh` の一覧で持つ（PLAN39） |
| コーディング規約 | `containers/base/Dockerfile` と `entrypoint.sh` の既存の書き方に合わせる。理由を直前のコメントに書く |
| テスト戦略 | Docker に依存しない検査を既定にする。読み込み器は `test_ai_cli_aliases.py` と同じく一時ディレクトリで source して確かめ、entrypoint は `DEVBASE_ENTRYPOINT_LIB_ONLY=1` で関数を呼ぶ（`test_entrypoint_ai_settings.py`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 手元で全体テスト、`devbase build base --no-cache` と、作り直したコンテナでの確認 |
| 確認してから行う | 名前（`~/.shellrc.d`・`DEVBASE_SHELLRC_DIR`）と、zsh を含めないこと。設計 Pull Request の承認で確かめる |
| 行わない | ai-plugins 側の実装、`~/.zshrc` の変更、lfm への導入、置き場所への既定ファイルの配置 |

## 実装計画

設計は [PLAN70_shellrc-dir-design.md](PLAN70_shellrc-dir-design.md)。
**タスクへの分解は実装の持ち場で `/ndf:implementation-plan` が行う。**

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| amd64 での建て直し | 手元は arm64 のみ。変更はシェルの断片と symlink の一覧で、アーキテクチャに依存しない |
| Claude Code の Bash からの見え方 | 環境変数（受け入れ条件 11）までを確かめる。Claude Code が対話シェルの設定を取り込むかは Claude Code 側の振る舞いで、この変更の範囲外 |

## 依頼（原文）

#253:

> ## 何をしたいか
>
> コンテナを作り直しても残るシェルの設定の置き場所（読み込み用のディレクトリ）を 1 つ用意し、`~/.bashrc` と `~/.zshrc` がその中の `*.sh` を読み込むようにしてほしい。
>
> 今は `~/.bashrc` と `~/.zshrc` がイメージの層にあり、コンテナを作り直すと Dockerfile が書いた中身へ戻る。利用者やツールが足した alias は作り直しで消える。
>
> ## 例: ai-plugins の中継（devbasex/ai-plugins#928）
>
> ai-plugins の ndf プラグインは、`claude` を中継（`relay.py`）で包む alias を、利用者の明示の操作（`/ndf:install-wrapper`）で入れる。中継の本体は `~/.claude/ndf/relay.py` に置く（`~/.claude` は `/persistent/group/.claude` への symlink で、作り直しでも残る）。alias の行は `~/.claude/ndf/shellrc` に書く。
>
> 足りないのは、この `shellrc` を読み込む 1 行を、作り直しで消えない形で置く場所である。`~/.bashrc` に書くと作り直しで消える。
>
> ## 提案
>
> | 項目 | 提案 |
> | --- | --- |
> | 置き場所 | 分類 B（アカウントグループ単位）の新しい項目として `~/.shellrc.d/` を `/persistent/group/.shellrc.d/` へ張る（`entrypoint.sh` の `DEVBASE_GROUP_SETTINGS` に足す）。ndf の中継の本体が `~/.claude`（分類 B）にあるので、同じグループの単位にそろえる |
> | 読み込み | Dockerfile が `~/.bashrc` と `~/.zshrc` の末尾へ、`. /etc/devbase/ai-cli-aliases.sh` の**後に**次を足す: `for f in "$HOME"/.shellrc.d/*.sh; do [ -r "$f" ] && . "$f"; done; unset f`（zsh では一致が無いときの誤りを避けるため `setopt local_options null_glob` 相当の扱いが要る） |
> | 見つけ方 | 読み込むディレクトリのパスを、コンテナの環境変数 `DEVBASE_SHELLRC_DIR` で示す（対話シェルでない処理、たとえば Claude Code の中の Bash からも見えるように、rc ではなくコンテナの環境に置く）。ツールはこの変数があればそこへ 1 ファイルを置き、`~/.bashrc` を書き換えない |
>
> **汎用にする理由:** 読み込むのは ndf 固有のファイルではなく、ディレクトリの中の `*.sh` 全部にする。devbase は 4 つの AI CLI と利用者自身の設定を載せるため、ndf のパス（`~/.claude/ndf/shellrc`）を devbase に書くと、ndf の置き場所が変わるたびに devbase を直すことになる。ndf 側はこのディレクトリへ `ndf-relay.sh`（`shellrc` を読む 1 行）を置く。
>
> 名前（`~/.shellrc.d`・`DEVBASE_SHELLRC_DIR`）は案で、devbase の命名に合わせて決めてよい。決まった名前を ai-plugins#928 の実装が使う。
>
> ## 気をつけること
>
> - **読み込みの順序:** `ai-cli-aliases.sh` の後に読むと、利用者の定義が devbase の定義より勝つ。ndf の中継の alias が `claude --dangerously-skip-permissions` を上書きする件は ai-plugins 側の不具合として devbasex/ai-plugins#936 で扱う
> - 分類 B なので、同じアカウントグループの全コンテナで同じ設定が効く
>
> ## 由来
>
> devbasex/ai-plugins#928（設計 PR devbasex/ai-plugins#932 の関門 1 で、利用者が「中継の alias を永続化される rc ファイルへ書き、devbase がそれを読み込む」と決めた。2026-09-23）
