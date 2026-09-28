# #305: テストがラッパーの関数を sed で消して eval し、パスフレーズのテストが再公開の getpass に依存している（実装の内部への依存）

正は課題の本文（#305）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

起票時の本文（PR #301 の構造改善で見つけた範囲外の指摘）をそのまま写す。

> ## 何を見つけたか
>
> PR #301 の構造改善（cross-refactoring）で、テストが実装の内部に依存している箇所の提案が 2 件出たが、PR には入らなかった。
>
> | 対象 | 何が起きているか | 結果 |
> | --- | --- | --- |
> | `tests/cli/test_build_image_argument.py` の `_run_wrapper` | `bin/devbase` の `run_python` / `cmd_build` / `compose_with_secrets` と `DEVBASE_ROOT` の代入を `sed` で消してから `eval` している。関数の表記と内部構造に依存し、本物の `cmd_build` の経路を確かめない。`tests/cli/conftest.py` には、本物のラッパーを一時ディレクトリへ複製して `uv` の境界だけを差し替える `exec_wrapper` が既にある | 締め切りまでにコミットが無く、見送り |
> | `tests/cli/test_env_import.py` / `test_env_export.py` のパスフレーズのテスト | `io_import` / `io_export` の `_read_passphrase` と、`getpass` の再公開に依存している。実体は `io_common.read_passphrase` へ移っているのに、テストの monkeypatch のためだけの未使用の import が両方の実装ファイルに残っている | テストの期待値を変えたため取り消し（構造改善では期待する出力を変えない） |
>
> 改修計画の各項目の手順: https://github.com/devbasex/devbase/pull/301#issuecomment-5845057976 （I-009・I-010）
>
> ## どこで見つけたか
>
> PR #301 の検査のプランの cross-refactoring（提案元 codex）。
>
> ## なぜこの変更の範囲外なのか
>
> PR #301 は名前の規則の整理（#276 #227 #222 #265）で、この 2 つのテストファイルと `io_common` の読み込みは受け入れ条件のどれにも関わらない。cross-refactoring の `--scope` がファイル単位のため、既存のテストへ提案が向いた。
>
> ## 直さないと何が起きるか
>
> 振る舞いの不具合ではない。ただし `bin/devbase` の関数の並びや名前を変えると `_run_wrapper` の `sed` が黙って外れ、テストが本来の経路を見なくなる恐れがある。`io_import` / `io_export` の未使用の import は、モジュールを分けるときの妨げになる。
>
> ## 由来
>
> PR #301

## 目的

- `bin/devbase` の関数の並び・名前・`DEVBASE_ROOT=` の行を変えても、`build` の振り分けのテストが黙って本来の経路を外れない状態にする
- パスフレーズの読み取りのテストが、`io_import` / `io_export` の中の `getpass` の再公開に頼らない状態にし、テストのためだけの未使用の import を実装から消す

## 前提

- 前提 1: `build` の振り分けのテストは、`tests/cli/conftest.py` の `exec_wrapper`（本物の `bin/devbase` を一時ディレクトリへ複製し、偽の `uv` だけを差し替える）へ移す。Python の経路は偽の `uv` が出す `UV:` 行の末尾 ` devbase.cli project build <引数>` で、shell の経路は `=== Building devbase images ===` の行で見分ける（2026-09-28 に手元で `build` / `build --no-cache` / `build --project-no-cache` / `build base` / `build --expires=7` を打ち、この 2 つで区別できることと、shell の経路の `docker` 呼び出しがすべて `uv` を通ることを確かめた）
- 前提 2: これはテストの入れ替えで、`bin/devbase` と `lib/devbase` の振る舞い（出力・終了コード）は変えない。構造改善（期待する出力を変えない）ではなく、テストの観測点を変える通常の変更として扱う
- 前提 3: `io_import._read_passphrase` / `io_export._read_passphrase` は、実装の中（`_decrypt_if_needed` など）から呼ばれているので残す。消すのは `import getpass  # noqa: F401` の 2 行だけとする
- 前提 4: パスフレーズのテストの `getpass.getpass` の差し替えは、実際に `getpass` を呼ぶ `devbase.env.io_common` の側（または標準ライブラリの `getpass` そのもの）へ向ける。どちらにするかは設計で決める
- 前提 5: 同じ `sed` のハーネスを持つほかの 4 ファイル（`test_wrapper_dispatch.py` / `test_open_command.py` / `test_wrapper_build_context.py` / `test_project_name_resolution.py`）は、この課題に含めない（起票時の指摘が `test_build_image_argument.py` だけのため）

## 対象範囲

含む:
- `tests/cli/test_build_image_argument.py` の `_run_wrapper` と、それを使う振り分けのテスト（7 関数、parametrize を含めて 8 ケース）を `exec_wrapper` へ移す
- `tests/cli/test_env_import.py` / `test_env_export.py` のパスフレーズのテスト（import 3 件、export 4 件）の `getpass` の差し替え先を変える
- `lib/devbase/env/io_import.py` / `io_export.py` の、テストのためだけの `import getpass` を消す

含まない:
- 前提 5 のほかの 4 ファイルの `sed` のハーネス（#339）
- `bin/devbase` の `cmd_build` と振り分けの実装の変更
- `io_common.read_passphrase` の振る舞いの変更と、`_read_passphrase` の削除・改名
- `test_build_image_argument.py` の Python 側（`container.cmd_build(image=...)`）のテスト
- 設計のクラス図（型を足さず、`WrapperRoot` を含むどの型も変えないため対象が無い）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | テストが本物のラッパーの複製を起動した | pytest が振り分けのテストを実行した | 複製や偽の `uv` が作れず、テストがエラーで止まる（黙って通らない） | — |
| E2 | ラッパーが `build` の引数を Python と shell のどちらかへ振り分けた | E1 | 期待と違う経路へ行くと `UV:` 行か `=== Building` 行の判定で落ちる | E1 |
| E3 | テストが `getpass.getpass` を差し替えた | パスフレーズのテストを実行した | 差し替え先の名前が無いと monkeypatch が `AttributeError` で落ちる | — |
| E4 | `read_passphrase` が tty では `getpass`、パイプでは `readline` で読んだ | E3 の後に `_read_passphrase` を呼んだ | tty で EOF なら呼び出し元のエラー（`ImportError` / `ExportError`）になる | E3 |

## 受け入れ条件

振り分けのテスト（`tests/cli/test_build_image_argument.py`）:

- [ ] `tests/cli/test_build_image_argument.py` に `_run_wrapper` と `sed` による本文の削除・`eval` が無い（`grep -nE "_run_wrapper|sed -e|eval " tests/cli/test_build_image_argument.py` が 0 件）
- [ ] `containers/base` がある状態で `build base` を打つと、`UV:` 行が ` devbase.cli project build base` で終わり、`=== Building devbase images ===` が出ないことをテストが確かめる
- [ ] `build base --no-cache` で `UV:` 行が ` devbase.cli project build base --no-cache` で終わる（引数の順序が保たれる）ことをテストが確かめる
- [ ] `build` だけを打つと `=== Building devbase images ===` が出て、` devbase.cli project build` で終わる `UV:` 行が無いことをテストが確かめる
- [ ] `build --no-cache` で shell の経路に入り、`--no-cache` が shell の経路のビルドへ届く（`UV:` 行のどれかが `--no-cache` を含む）ことをテストが確かめる
- [ ] `build --project-no-cache` で shell の経路に入り、` devbase.cli project build` で終わる `UV:` 行が無い（shell と Python の間で再帰しない）ことをテストが確かめる
- [ ] `build --expires` / `build --expires=7` / `build base --expires=7` で、`UV:` 行がそれぞれ ` devbase.cli project build <同じ引数>` で終わることをテストが確かめる
- [ ] 振り分けのテストは実環境の `DEVBASE_ROOT` を継承しても一時ディレクトリの `projects/` と `containers/` だけを見る（実環境に同名のプロジェクトがあっても結果が変わらない）

パスフレーズのテスト:

- [ ] `tests/` に `devbase.env.io_import.getpass` と `devbase.env.io_export.getpass` への参照が無い（`grep -rnE "io_(import|export)\.getpass" tests/` が 0 件）
- [ ] `lib/devbase/env/io_import.py` と `io_export.py` に `import getpass` が無い（`grep -n "^import getpass" lib/devbase/env/io_import.py lib/devbase/env/io_export.py` が 0 件）
- [ ] tty の入力では `getpass.getpass` がプロンプト `passphrase: ` で呼ばれ、標準入力が消費されないことを、import と export の両方でテストが確かめる
- [ ] パイプの入力では `getpass.getpass` が呼ばれず 1 行を読むことを、import と export の両方でテストが確かめる
- [ ] パイプの `hunter2\r\n` が `hunter2` になることをテストが確かめる（export の既存テストを保つ）
- [ ] tty で `getpass.getpass` が `EOFError` を投げると、import では `ImportError`、export では `ExportError` になり、メッセージに「パスフレーズを読み取れません」を含むことをテストが確かめる

退行しないこと:

- [ ] `uv run --locked pytest tests/ -q` がすべて通る
- [ ] `bin/devbase` と `lib/devbase` の差分が、`io_import.py` / `io_export.py` の `import getpass` の 2 行の削除だけである

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | `bin/devbase` の関数の定義の順序を入れ替えても、振り分けのテストの結果が変わらない（`sed` の範囲指定に頼らない） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（`devbase` の CLI も `devbase.env` の公開関数も変えない） |
| データ | 無し |
| 既存の振る舞い | 変わらない。`io_import` / `io_export` の属性 `getpass` が無くなる（テストの差し替え先としてだけ使われていた） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --locked pytest tests/ -q`（CI の `.github/workflows/ci.yml` と同じ） |
| 対象のテスト | `uv run --locked pytest tests/cli/test_build_image_argument.py tests/cli/test_env_import.py tests/cli/test_env_export.py -q` |
| 静的な確認 | 受け入れ条件の `grep` の 3 本が 0 件 |
| 手動確認 | 無し（すべて自動で確かめられる） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | ラッパーを実プロセスで起動するハーネスは `tests/cli/conftest.py` の `exec_wrapper` を使う（同ファイルの冒頭の説明）。新しいハーネスを別に作らない |
| コーディング規約 | 既存のテストの書き方（`stdout_field` での行の取り出し、受け入れ条件を docstring に書く）に揃える |
| テスト戦略 | 振り分けは実プロセスの結合テスト（本物のラッパー + 偽の `uv`）、パスフレーズは単体テスト（`monkeypatch` で `sys.stdin` と `getpass.getpass` を差し替える） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 変更の前後で全体の pytest を通す。移したテストが元のテストと同じ入力を確かめる |
| 確認してから行う | `bin/devbase` と `lib/devbase` の、`import getpass` の削除以外の変更 |
| 行わない | 前提 5 のほかの 4 ファイルの書き換え、`_read_passphrase` の削除・改名、テストの対象の振る舞いの変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| `getpass.getpass` の差し替え先を `devbase.env.io_common.getpass.getpass` にするか、標準ライブラリの `getpass.getpass` にするか（前提 4） | 設計（`design`） | 設計 PR まで |
| 前提 5 のほかの 4 ファイルの `sed` のハーネスを別の課題に起こすか | 設計（`design`）。起こすなら `out-of-scope` で起票する | 設計 PR まで |
