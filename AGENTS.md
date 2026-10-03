# AGENTS.md

このリポジトリで作業する AI エージェント向けの取り決め。人向けの手順は
[開発参加ガイド](docs/developer/contributing.md) が正本で、ここには写さない。ここに置くのは、
エージェントが最初に知っておくべきことと、実際に踏んだ落とし穴である。

## このリポジトリ

devbase は Docker Compose で開発環境を立ち上げる CLI である。プロジェクトはプラグインとして外部の
リポジトリから入れる。全体像は [アーキテクチャ概要](docs/developer/architecture.md) にある。

| 置き場 | 中身 |
| --- | --- |
| `bin/devbase` | CLI の入口（Bash）。Python へ渡す |
| `lib/devbase/` | Python の実装。主な開発対象 |
| `containers/` | イメージの定義。`base` がすべての派生イメージの土台 |
| `tests/` | pytest |
| `docs/specifications/` | 確定仕様。振る舞いの正本 |
| `docs/glossary.md` | 用語集。`docs/glossary/glossary.json` から作る |
| `issues/` | 実装計画と設計の作業文書。確定したら `docs/specifications/` へ移して消す |
| `projects/*`・`plugins/*`・`repos/` | 手元に入れたプラグイン。git の管理外（`.gitignore`） |

## 判断の基準

[プロジェクト MVV](.ndf/mvv.md) に従う。次の 3 つは必ず人の承認を得てから行う。

| # | 操作 |
| --- | --- |
| P1 | base イメージに入れる道具や、その版を変える |
| P2 | タグ・GitHub Release を作る |
| P3 | 実際の利用者の環境で `devbase up` を実行する |

`main` は開発の起点であり、同時に本番の配布のチャネルでもある（`install.sh` が `main` を取得する）。
**`main` へのマージがそのまま利用者への配布になる。** `main` へ直接 push しない。

## 検査

push の前に、CI と同じ検査を手元で打つ。

```bash
env -u DEVBASE_ROOT uv run --locked pytest tests/ -q   # 全体のテスト
uvx ruff check --select=E9,F63,F7,F82 lib              # CI と同じ lint
python3 .github/scripts/proper_term_check.py           # 固有の語の検査
```

- **`env -u DEVBASE_ROOT` を外さない。** pytest は起動したシェルの `DEVBASE_ROOT` を継ぐ。`up` / `scale` の
  テストは backend を隔離しないと、実のサーバーと実の docker に触る
- **固有の語の検査が `語の一覧が無いため飛ばした` と出たら、検査はしていない。** 終了コードは 0 でも当たりが無い
  という意味ではない。実機の記録は書き始めから `proj-a`・`team-a`・`<user>` のような中立の呼び名で書く
- シェルスクリプトの ShellCheck は `devbase-base:latest` の shellcheck で打てる（コマンドは開発参加ガイドの
  「CI が実行するもの」）

## Pull Request

- 利用者に見える変更は、同じ Pull Request で `CHANGELOG.md` の `[Unreleased]` を書く。開発者向けの文書だけ・
  テストだけの変更は書かない
- `main` は strict（ブランチが最新でないとマージできない）。続けてマージすると 2 本目以降が BEHIND になる。
  1 本ずつ `main` を取り込み、CI を待ってからマージする
- 並行する Pull Request は `CHANGELOG.md` の `[Unreleased]` と `docs/glossary/glossary.json` でぶつかりやすい。
  両側の記入を残して解く。`docs/glossary.md` は `glossary.json` から `glossary.py render`（NDF の道具）で作り直し、手で直さない
- 作業は `.worktrees/<ブランチ名>` の worktree で行う。メインディレクトリに差分を残さない

## 落とし穴

| 状況 | 起きること | どうするか |
| --- | --- | --- |
| `containers/base/entrypoint.sh` などイメージに入るファイルを変えた | `devbase up` だけでは反映されない | base を建て直してから確かめる |
| base を建てて比べる | base は 1 本で数 GB ある。並べて残すとディスクが埋まり Docker が止まる | 建てる前に `docker system df` を見る。作業用のタグは記録を書いたら消す |
| `projects/*` の `compose.yml` やフックを直したい | `projects/*` はプラグインのリポジトリ（`repos/` の下）への symlink で、このリポジトリの管理外 | Pull Request はプラグインのリポジトリへ出す |
| `projects/*` へ `cd` してから `../../` を書く | symlink の先から解決され、devbase の外を指す | 絶対パスで書く |
| macOS で検査が通る | CI（Linux）と `ln` などの既定の振る舞いが違う。同時実行の競合が CI でだけ出た例がある | 振る舞いが分かれるオプションは明示する（例: `ln -P`） |
| tmux を使うテストを書く | `$TMUX` があると `TMUX_TMPDIR` は無視され、`kill-server` が実のサーバーを落とす | `env -u TMUX` で起動するか、`-S` / `-L` で別のソケットを使う |
| 社内向けの引き渡しや手順を書く | 社内のホスト名やアカウント ID が公開される | `issues/internal/` か `issues/*.internal.md`・`issues/*-handover.md` に置く（git の管理外） |

## 書き方

- コード・文書・コミットメッセージ・Pull Request は日本語で書く。周りのコードの書き方に合わせる
- 設計文書の決定は `## 決定の記録` の下に `### 決定 N` の形で置く
- 用語は [用語集](docs/glossary.md) の語を使う。新しい語を足すときは `docs/glossary/glossary.json` を直す
