# #533 の詳細設計: ホストの userns の制限を外す・戻す・確かめる

設計文書 `issues/issue-533-design.md` の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。新設する関数が 2 つ以上あるため、別ファイルにした。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
bin/devbase
etc/
  devbase-completion.bash
  _devbase
lib/devbase/
  cli.py
  commands/
    init.py
    userns.py *
  utils/
    userns.py *
tests/
  conftest.py
  utils/test_userns.py *
  commands/test_userns_command.py *
  commands/test_init_userns.py *
  cli/test_prefix_resolution.py
  cli/test_completion.py
  cli/test_wrapper_dispatch.py
```

依存の向き: `cli.py` → `commands/userns.py` → `utils/userns.py`、`commands/init.py` → `utils/userns.py` の一方向で、`utils/userns.py` は `devbase.log` と標準ライブラリだけに依存する。`utils/userns.py` は表示を持たず、出す行を返り値（`Report` の `lines`）に入れ、コマンドの側が出す。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `lib/devbase/utils/userns.py` | 新設 | 値を読み、権限を選び、devbase の sysctl の設定ファイルを置く・消す | 標準ライブラリ・`devbase.log` | `HostPaths`・`Privilege`・`Report`・`ContainerKernel` | `read_runtime_value`・`config_value`・`later_overrides`・`in_container`・`choose_privilege`・`probe_container_kernel`・`status`・`apply`・`revert`（新設） |
| `lib/devbase/commands/userns.py` | 新設 | `status` / `apply` / `revert` の出力と終了コード | `utils/userns.py` | モジュール | `cmd_userns`（新設） |
| `lib/devbase/commands/init.py` | 変更 | 最後に `apply` を呼ぶ | `utils/userns.py` | モジュール | `_apply_userns`（新設）、`cmd_init`（変更） |
| `lib/devbase/cli.py` | 変更 | `userns` の parser・振り分け・前方一致 | `commands/userns.py` | モジュール | `_create_parser`・`_expand_argv`・`_ROOT_COMMANDS`・`SUBCMD_MAP`・`TOP_PREFIX_PREFERENCES`（変更） |
| `bin/devbase` | 変更 | `userns` を Python へ渡し、`u` を `up` に残す | — | — | `resolve_command`・振り分けの `case`（変更） |
| `etc/devbase-completion.bash`・`etc/_devbase` | 変更 | `userns` と `status apply revert` を補完する | — | — | コマンドの一覧（変更） |
| `tests/conftest.py` | 変更 | ホストのパスと runner を差し替え、環境変数を隔離する | `utils/userns.py` | — | autouse の fixture（新設）、`ISOLATED_ENV`（変更） |

## userns の判定と変更（`lib/devbase/utils/userns.py`）

満たす決定: 決定 3・4・5・7、受け入れ条件 1〜4・6〜16・18・19

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `read_runtime_value` | `lib/devbase/utils/userns.py` | 新設 | `(paths: HostPaths) -> int \| None` | proc のキーを読み、0 / 1 を返す。キーが無ければ `None` |
| `config_value` | 同上 | 新設 | `(paths: HostPaths) -> tuple[int \| None, Path \| None]` | sysctl.d の読む順で最後にキーを書くファイルと、その値を返す |
| `later_overrides` | 同上 | 新設 | `(paths: HostPaths) -> list[Path]` | devbase のファイルより辞書順で後にあり、キーへ 0 以外を書くファイルを返す |
| `in_container` | 同上 | 新設 | `(paths: HostPaths) -> bool` | コンテナの印（`/.dockerenv`・`/run/.containerenv`）があるかを返す |
| `choose_privilege` | 同上 | 新設 | `(paths: HostPaths, runner: Runner) -> Privilege` | `ROOT` / `SUDO` / `SUDO_PROMPT` / `NONE` を選ぶ。尋ねはしない |
| `probe_container_kernel` | 同上 | 新設 | `(runner: Runner, timeout: float = 20.0) -> ContainerKernel` | 設計文書の「処理の流れ」の表のとおりにコンテナが動くカーネルの値を読む |
| `status` | 同上 | 新設 | `(paths: HostPaths \| None = None, runner: Runner \| None = None) -> Report` | 3 つの値を読み、行を組む。何も変えない |
| `apply` | 同上 | 新設 | `(paths: HostPaths \| None = None, runner: Runner \| None = None) -> Report` | 設計文書の「処理の流れ」の図のとおりに外す |
| `revert` | 同上 | 新設 | `(paths: HostPaths \| None = None, runner: Runner \| None = None) -> Report` | 目印のある devbase のファイルを消し、`sysctl --system` を打つ |

- `config_value`
  - 事前条件: 無し（ディレクトリが無ければ飛ばす）
  - 事後条件: `paths.sysctl_dirs` の `*.conf` を、同じ名前は先のディレクトリ（`/etc` → `/run` → `/usr/local/lib` → `/usr/lib`）のものだけ残し、名前の辞書順に並べて読んだときの最後の値。`=` の前後の空白とキーの `.` / `/` の違いを同じに扱う。行頭の `-`（失敗を無視する印）を外して読む。読めないファイルは飛ばす
  - 副作用: 無し（読むだけ）
- `choose_privilege`
  - 事後条件: `os.geteuid() == 0` なら `ROOT`。そうでなく `sudo -n true` が 0 なら `SUDO`。そうでなく `paths.tty` を読み書きで開けるなら `SUDO_PROMPT`。それ以外は `NONE`。`sudo` が無ければ `NONE`
  - 副作用: `sudo -n true` の起動（標準入力は `DEVNULL`）
- `apply`
  - 事後条件: 返す `Report.ok` が真なら、今の値と設定ファイルの上の値がどちらも 0 か、変えないと決めた（キーが無い・コンテナの中）。偽なら devbase のファイルは呼ぶ前と同じ
  - 副作用: devbase のファイルの書き込み（`install -m 0644 <一時ファイル> <devbase のファイル>`。`SUDO` / `SUDO_PROMPT` は頭に `sudo`）、`sysctl -p <devbase のファイル>`、`SUDO_PROMPT` の `sudo -v`（tty から読む）、docker の起動。一時ファイルは `tempfile` で作り、終わりに消す
- `revert`
  - 事後条件: `Report.ok` が真なら devbase のファイルが無い。目印の無いファイルは残る
  - 副作用: `rm -f <devbase のファイル>`（権限は `choose_privilege` で選ぶ）、`sysctl --system`

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `read_runtime_value` | 読めない・0 / 1 以外 | 権限・壊れた値 | `OSError`・`int()` | `None` と同じに扱い、行に理由を足す | 「ホスト: 読めない（理由）」。`apply` は変えずに 0 | 無し。再実行できる |
| `choose_privilege` | 権限が無い | root でなく、sudo が無いかパスワードが要り tty も無い | 返り値 `NONE` | 止める | 打つコマンド（`sudo install …` と `sudo sysctl -p …`）。`apply` 1 | 何も書いていない。再実行できる |
| `apply` | `sudo -v` が通らない | パスワードの誤り・中断 | 終了コード | 止める | 打つコマンド。1 | 何も書いていない |
| `apply` | 書き込みが失敗する | 読み取り専用の `/etc` など | `install` の終了コード | 止める | 失敗の標準エラーの先頭の行。1 | 一時ファイルを消す |
| `apply` | `sysctl -p` が失敗する・その後の値が 0 でない | 読み取り専用の `/proc/sys`・LSM が拒む | 終了コード・`read_runtime_value` | 書いたファイルを消して止める | 「戻した」と理由。1 | 書いたファイルを消す。消せなければパスを出す |
| `probe_container_kernel` | docker が無い・届かない・時間切れ | 未導入・daemon 停止 | `FileNotFoundError`・終了コード・`TimeoutExpired` | 既定値で続ける（未確認） | 「コンテナが動くカーネル: 未確認（理由）」。終了コードに数えない | 無し |
| `probe_container_kernel` | `--pull never` が未知のオプション | 古い docker | 終了コードと標準エラー | 既定値で続ける（未確認） | 同上 | 無し |
| `revert` | 目印が無い | 利用者が同じパスに置いた | 1 行目 | 止める | 「devbase のファイルではないので消さない」。1 | 何も消さない |
| `revert` | `sysctl --system` が失敗する | 他のファイルの誤り | 終了コード | ファイルは消したまま報告する | 「消したが読み直しに失敗した。再起動で反映される」。1 | 再実行で読み直せる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `apply` | キーが無い | 変えない。`probe_container_kernel` へ |
| `apply` | キーがある・コンテナの中 | 変えない。「ホストで打つ」と出す |
| `apply` | 今の値 0・設定ファイルの上の値 0 | 変えない。「既に 0」 |
| `apply` | 今の値 0・設定ファイルの上の値 1 | 書き、「再起動の後も 0」と出す |
| `apply` | 今の値 1 | 説明を出し、権限を選んで書く |
| `apply` | 書いた後に `later_overrides` が空でない | パスを挙げて警告する（`ok` は真のまま） |
| `apply` | コンテナが動くカーネルの値が 1 で、ホストの値が 0 か `None` | 変え方の案内を足す（`ok` は変えない） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `KEY` | 定数 | `lib/devbase/utils/userns.py` | `kernel.apparmor_restrict_unprivileged_userns` |
| `MARKER` | 定数 | 同上 | `# devbase:` |
| `HostPaths` | 型（凍結した dataclass） | 同上 | `proc_key=/proc/sys/kernel/apparmor_restrict_unprivileged_userns`、`sysctl_dirs=(/etc/sysctl.d, /run/sysctl.d, /usr/local/lib/sysctl.d, /usr/lib/sysctl.d)`、`devbase_file=/etc/sysctl.d/60-devbase-userns.conf`、`container_markers=(/.dockerenv, /run/.containerenv)`、`tty=/dev/tty` |
| `DEFAULT_PATHS` / `DEFAULT_RUNNER` | モジュールの既定 | 同上 | `HostPaths()` / `subprocess.run`。テストの fixture が差し替える |
| `Privilege` | 型（Enum） | 同上 | `ROOT` / `SUDO` / `SUDO_PROMPT` / `NONE` |
| `ContainerKernel` | 型 | 同上 | `value: int \| None`、`source: str`（`image` / `docker-info` / `none`）、`reason: str` |
| `Report` | 型 | 同上 | `ok: bool`、`changed: bool`、`lines: list[str]`、`warnings: list[str]` |
| `PROBE_IMAGE` | 定数 | 同上 | `devbase-base:latest` |
| `PROBE_TIMEOUT` | 定数 | 同上 | 20 秒 |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `apply` | 変える前の説明・結果・打つコマンド・警告 | コマンドの側が `info`（説明・結果）と `warning`（警告・権限が無い） | sudo のパスワード（devbase は読まない） |
| `probe_container_kernel` | 打った docker のコマンドと終了コード | `debug` | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `probe_container_kernel` | `docker_context.Runner`・`run_docker` | `grep -n "Runner\|def run" lib/devbase/utils/docker_context.py` | `Runner` の型を使う。`run_docker` は失敗を `DevbaseError` に変えるので、未確認へ倒すこの関数では使わない |
| `choose_privilege` | sudo を扱う既存の部品 | `grep -rn "sudo" lib/devbase` | 無い。新設する |
| `config_value` | sysctl.d を読む既存の部品 | `grep -rn "sysctl" lib/devbase` | 無い。新設する |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `read_runtime_value` | `0`・`1` を読む | 改行つき・ファイルが無い | 読めない・`2` |
| `config_value` | `/usr/lib` の 10 が 1、`/etc` の 60 が 0 → 0 と 60 のパス | 同じ名前が `/etc` と `/usr/lib` にある・`-` の付いた行・`/` で書いたキー | 読めないファイルを飛ばす |
| `later_overrides` | 99 が 1 を書く → そのパス | 99 が 0 を書く → 空 | — |
| `choose_privilege` | root → `ROOT` | `sudo -n` 0 → `SUDO`、tty だけ → `SUDO_PROMPT` | sudo が無い → `NONE` |
| `probe_container_kernel` | イメージあり → `--pull never` の `docker run` | イメージ無し・`apparmor` 無し → 制限は効かない | docker 無し・時間切れ・未知のオプション → 未確認 |
| `apply` | 受け入れ条件 1・2・7 | 6・8・9・10 | 3・4・11・16 |
| `revert` | 12 | ファイルが無い | 目印が無い・`sysctl --system` の失敗 |

## userns のコマンド（`lib/devbase/commands/userns.py`）

満たす決定: 決定 1、受け入れ条件 12〜14・21

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `cmd_userns` | `lib/devbase/commands/userns.py` | 新設 | `(args) -> int` | `args.subcommand`（`status` / `apply` / `revert`）で `utils/userns.py` を呼び、`Report` の行を出して終了コードを返す |

- `cmd_userns`
  - 事後条件: `status` は常に 0。`apply` / `revert` は `Report.ok` が真なら 0、偽なら 1。サブコマンドが無ければ使い方を出して 2
  - 副作用: 標準出力・標準エラーへの出力だけ

## `cmd_init` の最後の手順（`lib/devbase/commands/init.py`）

満たす決定: 決定 2・3、受け入れ条件 5・17

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_apply_userns` | `lib/devbase/commands/init.py` | 新設 | `() -> None` | `DEVBASE_SKIP_USERNS` を見て飛ばすか `apply` を呼び、結果を出す。例外を外へ出さない |
| `cmd_init` | 同上 | 変更 | 変えない | 補完の登録の後・最後の案内の前に `_apply_userns()` を呼ぶ。返り値は呼ぶ前と同じ規則で決める |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_apply_userns` | `apply` が `ok` 偽で返る | 権限が無いなど | `Report.ok` | 警告にして続ける | 打つコマンドと「後で `devbase userns apply` を打つ」。`init` は 0 | `devbase userns apply` で再実行できる |
| `_apply_userns` | 想定外の例外 | 実装の誤り | `except Exception` | 警告にして続ける | 「userns の確かめに失敗した: <例外>」。`init` は 0 | 同上 |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_apply_userns` | `apply` を 1 回呼ぶ | `DEVBASE_SKIP_USERNS=""` は呼ぶ、`=1` は呼ばない | `ok` 偽・例外でも `cmd_init` が 0 |

## CLI の登録（`cli.py`・`bin/devbase`・補完）

満たす決定: 決定 1、受け入れ条件 20・21

| 写し | 変える中身 |
| --- | --- |
| `_create_parser` | `userns` の parser と、`status` / `apply` / `revert` の subparser（`dest='subcommand'`） |
| `_ROOT_COMMANDS` | `'userns': ('devbase.commands.userns', 'cmd_userns', True)` |
| `_expand_argv` の `commands`・`SUBCMD_MAP` | `userns` と `('userns',): ['status', 'apply', 'revert']` |
| `TOP_PREFIX_PREFERENCES`・`bin/devbase` の `resolve_command` の優先 | `'u': 'up'` |
| `_NO_SECRET_INJECTION` | `('userns', None)`（機密を使わない） |
| `bin/devbase` の振り分け・`resolve_command` の一覧 | `userns` を Python の側へ |
| `etc/devbase-completion.bash`・`etc/_devbase` | トップレベルの一覧に `userns`、サブコマンドに `status apply revert` |

## テストの隔離（`tests/conftest.py`）

満たす決定: 決定 7、受け入れ条件 19

| 足すもの | 中身 |
| --- | --- |
| autouse の fixture | `devbase.utils.userns.DEFAULT_PATHS` を `tmp_path` の下のパス（proc のキーは作らない）へ、`DEFAULT_RUNNER` を呼ばれたら `AssertionError` を出すものへ差し替える。userns のテストは自分の runner を引数で渡す |
| `ISOLATED_ENV` | `DEVBASE_SKIP_USERNS` |
