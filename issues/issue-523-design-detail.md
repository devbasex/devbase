# #523 の詳細設計: tmux でソケットを拾い直したときに合う remote-cli を PATH の先頭に入れる

設計文書 [issue-523-design.md](issue-523-design.md) の「構成要素」と「処理の流れ」を、ファイルと関数の粒度へ下ろした文書である。
決定の理由は設計文書の「決定の記録」にあり、ここでは繰り返さない。

この文書で使う語は設計文書の「用語」の表に従う。加えて次の語を使う。

| 語 | この文書での意味 |
| --- | --- |
| 拾い直した | 判定が使うと決めたソケット（`EditorContext.ipc_socket`）があり、それがペインの `VSCODE_IPC_HOOK_CLI` と違うこと。既存の `_prepare_ipc_env` の最初の分岐の条件（`ctx.ipc_socket and ctx.ipc_socket != stale_ipc`）と同じで、条件は変えない |
| ペインの PATH | `open_editor` が受け取った環境変数（既定は `os.environ`）の `PATH`。差し替える前の値 |
| 起動に渡す環境 | `_prepare_ipc_env` が返し、エディタの解決と起動に使う環境変数の辞書 |
| 見つからない理由 | 合う `remote-cli` を求められなかった理由を表す文字列。warning にそのまま載せる |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。新設のファイルは無い）:

```text
devbase/
├── CHANGELOG.md
├── docs/
│   ├── specifications/
│   │   └── editor-open.md
│   └── user/
│       └── environment-variables.md
├── lib/devbase/editor/
│   └── opener.py
└── tests/editor/
    └── test_opener.py
```

依存の向きは、テストから `opener.py` への一方向だけである。`opener.py` は新しい依存を持たない（標準ライブラリの `os` と
既存の `devbase.log` だけ）。文書の 3 ファイルはコードから参照されない。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `lib/devbase/editor/opener.py` | 変更 | 拾い直したとき、セッション環境の `VSCODE_GIT_ASKPASS_NODE` から合う `remote-cli` を求め、起動に渡す環境の `PATH` の先頭に入れ、その `PATH` でエディタを探す | 無し（同じモジュールの `_tmux_show_env`・`_is_remote_cli_dir`・`resolve_editor_display` を使う） |
| `tests/editor/test_opener.py` | 変更 | 新設の関数の単体テストと、拾い直したときの `open_editor` の起動の結果を見るテストを足す。既存のテストは変えない | `lib/devbase/editor/opener.py` |
| `docs/specifications/editor-open.md` | 変更 | 拾い直したときの `PATH` の扱いと、`VSCODE_GIT_ASKPASS_NODE` の要否を確定仕様に書く | 無し |
| `docs/user/environment-variables.md` | 変更 | 「tmux / screen 経由で使う場合」の節に、合う `remote-cli` を `PATH` の先頭に入れることと、`update-environment` に `VSCODE_GIT_ASKPASS_NODE` が要ることを書く | 無し |
| `CHANGELOG.md` | 変更 | `[Unreleased]` に、tmux で拾い直したときに手元の VS Code に窓が開くようになったことを書く | 無し |

用語集（`docs/glossary.md`・`docs/glossary/glossary.json`）は設計の段で反映済みで、実装の段では触らない。

## クラスと関数の構造

型の関係は設計文書の「構造」の節のとおりで、型の追加・変更は無い（`EditorContext`・`OpenPlan` は変えない）。
クラスは足さず、モジュールの最上位の関数で書く。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `lib/devbase/editor/opener.py` | モジュール `devbase.editor.opener` | 新設: `_editor_name`・`_is_executable_file`・`_find_matching_remote_cli`・`_prepend_path`。変更: `_prepare_ipc_env`（責務と失敗の扱い）・`open_editor`（`resolve_editor_cmd` に渡す `path` の決め方だけ。シグネチャ・責務・失敗の扱いは変えない） |
| `tests/editor/test_opener.py` | モジュール | 新設: 下の「単体テストの観点」の行ごとのテスト関数。既存の `_FakeTmuxEnv` を使い、tmux の差し替えを新設しない |

`resolve_editor_cmd`・`_launch`・`_tmux_show_env`・`_drop_vscode_ipc`・`detect_context` は変えない。

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `_editor_name` | `lib/devbase/editor/opener.py` | 新設 | `(environ: Mapping[str, str]) -> Optional[str]` | 起動するエディタのファイル名（`remote-cli` の中で確かめる名前）を返す | 無し | `DEVBASE_EDITOR` があればその先頭の語の末尾の名前（`os.path.basename`）、無ければ `"code"`。`DEVBASE_EDITOR` を語に分けられない・先頭の語の名前が空のときは `None` | 無し |
| `_is_executable_file` | `lib/devbase/editor/opener.py` | 新設 | `(path: str) -> bool` | パスが実行できる通常のファイルかを返す | 無し | `os.path.isfile(path)` かつ `os.access(path, os.X_OK)` のとき `True`。確かめの途中の `OSError`・`ValueError` は `False` | 無し |
| `_find_matching_remote_cli` | `lib/devbase/editor/opener.py` | 新設 | `(environ: Mapping[str, str], editor_name: Optional[str]) -> tuple[Optional[str], Optional[str]]` | セッション環境の `VSCODE_GIT_ASKPASS_NODE` から合う `remote-cli` のディレクトリを求める | 拾い直した後に呼ぶ（`environ` に `TMUX` がある） | 戻り値の 2 つのうち、ちょうど 1 つが `None` でない。1 つ目はディレクトリ（絶対パスで、末尾の名前が `remote-cli` で、中に `editor_name` の実行できる通常のファイルがある）、2 つ目は見つからない理由 | 外部コマンド `tmux show-environment VSCODE_GIT_ASKPASS_NODE` を 1 回起動する（`editor_name` が `None` のときは起動しない）。ファイルの属性を読む |
| `_prepend_path` | `lib/devbase/editor/opener.py` | 新設 | `(path: Optional[str], directory: str) -> str` | `directory` を先頭の要素にした `PATH` の文字列を返す | `directory` は空でない | `path` が `None` か空文字なら `directory` だけ。そうでなければ `directory + os.pathsep + path`（`path` の要素は並びも重複もそのまま） | 無し |
| `_prepare_ipc_env` | `lib/devbase/editor/opener.py` | 変更 | `(env: Mapping[str, str], ctx: EditorContext) -> Mapping[str, str]`（変えない） | 拾い直したソケットを起動に渡す環境へ入れ、合う `remote-cli` が求められればその `PATH` の先頭に足す | 変えない | 拾い直したとき: 戻り値の `VSCODE_IPC_HOOK_CLI` は `ctx.ipc_socket`。合う `remote-cli` があれば戻り値の `PATH` は `_prepend_path(ペインの PATH, ディレクトリ)`、無ければペインの `PATH` のまま。拾い直していないとき: 戻り値・ログ・tmux への問い合わせは変更前と同じ | 拾い直したとき、`_find_matching_remote_cli` を通じて外部コマンドを 1 回起動する。ログを出す（「ログと記録の出力」） |

`open_editor` の変える箇所は、`resolve_editor_cmd` に渡す `path` の決め方だけである（「処理の手順」の 2 つ目の表）。

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_find_matching_remote_cli` | `tmux` を起動できない・2 秒で返らない・終了コードが 0 でない・出力が想定外 | `tmux` が `PATH` に無い、サーバーが応答しない | 既存の `_tmux_show_env`（例外と終了コードを見て `None` を返す） | 既定値で続ける（見つからない理由「未設定」を返す） | 呼び出し元の warning 1 行。終了コードは変わらない | 後始末は無い。次の `devbase open` で読み直す |
| `_find_matching_remote_cli` | 値が無い・空 | 利用者の `update-environment` に `VSCODE_GIT_ASKPASS_NODE` が無く、セッションを作ったときの環境にも無い | `_tmux_show_env` の戻り値が `None` | 既定値で続ける（理由「未設定」） | 同上 | 利用者が `~/.tmux.conf` に設定を足し、attach し直せば次から求められる |
| `_find_matching_remote_cli` | 値が消されている | attach してきたクライアントが `VSCODE_GIT_ASKPASS_NODE` を持たない（`-VSCODE_GIT_ASKPASS_NODE`） | `_tmux_show_env` の戻り値が `_TMUX_REMOVED` | 既定値で続ける（理由「消された」） | 同上 | VS Code の統合ターミナルから attach し直せば次から求められる |
| `_find_matching_remote_cli` | 値が絶対パスでない | セッション環境の値を別のクライアントやツールが書いた | `os.path.isabs` | 既定値で続ける（理由「絶対パスでない」） | 同上 | 後始末は無い |
| `_find_matching_remote_cli` | 求めたディレクトリに実行できるエディタが無い | VS Code サーバーの版が消された・配置が違う・`DEVBASE_EDITOR` の名前のファイルが `remote-cli` に無い・`editor_name` が `None` | `_is_executable_file` が `False` | 既定値で続ける（理由「実行できるエディタが無い」） | 同上 | 後始末は無い |
| `_is_executable_file` | 属性を読めない | 権限・パスの長さ・値に NUL が入っている | `os.path.isfile`・`os.access` が投げる `OSError`・`ValueError` | 既定値で続ける（`False`） | 無し（呼び出し元が warning にする） | 後始末は無い |
| `_prepare_ipc_env` | 合う `remote-cli` を求められない | 上の `_find_matching_remote_cli` の 5 行のいずれか | `_find_matching_remote_cli` の 2 つ目の戻り値が `None` でない | 既定値で続ける（`PATH` を変えずに起動に渡す環境を返す。起動は止めない） | warning 1 行（理由・手元のエディタで起動すること・環境変数ガイドの tmux の設定の案内）。`devbase up` / `devbase open` の終了コードは変わらない | 後始末は無い。設定を直して `devbase open` を打ち直せる |

起動そのものの失敗（`Popen` の例外）は既存の `open_editor` が warning にしており、扱いを変えない。

## 処理の手順

`_find_matching_remote_cli` の判定表（上から順に当て、最初に当たった行で返す）:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_find_matching_remote_cli` | `editor_name` が `None` | `(None, 理由「実行できるエディタが無い」)`。tmux へ問い合わせない |
| `_find_matching_remote_cli` | `_tmux_show_env` が `_TMUX_REMOVED` | `(None, 理由「消された」)` |
| `_find_matching_remote_cli` | `_tmux_show_env` が `None` | `(None, 理由「未設定」)` |
| `_find_matching_remote_cli` | 値が絶対パスでない | `(None, 理由「絶対パスでない」)` |
| `_find_matching_remote_cli` | `os.path.join(os.path.dirname(値), "bin", "remote-cli", editor_name)` が `_is_executable_file` で `False` | `(None, 理由「実行できるエディタが無い」と求めたディレクトリ)` |
| `_find_matching_remote_cli` | それ以外 | `(os.path.join(os.path.dirname(値), "bin", "remote-cli"), None)` |

求めるディレクトリは、`node` の置かれたディレクトリに `bin/remote-cli` を連結して作る。`os.path.normpath` を掛けないため、
末尾の名前は必ず `remote-cli` になり、決定 3 の「名前が `remote-cli`」はこの作り方で満たす（`_is_remote_cli_dir` で
確かめ直す行は置かない。当たらない分岐はテストで踏めないため）。テストは足したディレクトリの末尾の名前を見る。

`_prepare_ipc_env` と `open_editor` の判定表:

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_prepare_ipc_env` | 拾い直した、合う `remote-cli` がある | `VSCODE_IPC_HOOK_CLI` を差し替え、`PATH` を `_prepend_path(ペインの PATH, ディレクトリ)` にする。既存の info「拾い直しました」と、新しい info 1 行を出す |
| `_prepare_ipc_env` | 拾い直した、合う `remote-cli` が無い | `VSCODE_IPC_HOOK_CLI` だけ差し替える。既存の info「拾い直しました」と、新しい warning 1 行を出す |
| `_prepare_ipc_env` | 拾い直していない | 変更前と同じ（`_find_matching_remote_cli` を呼ばない） |
| `_prepare_ipc_env` | 拾い直した、かつ `ctx.ipc_from_previous_client` | 上の 2 行のどちらかに続けて、既存の「以前のクライアントのもの」の info を出す。`ctx.ipc_socket` があるので IPC は外さない（変更前と同じ） |
| `open_editor` | 起動に渡す環境の `PATH` がペインの `PATH` と違う、または IPC を外した | `resolve_editor_cmd(env, path=起動に渡す環境の PATH)`。起動も同じ `PATH` で実行ファイルを探すため（決定 5）、確かめと起動が同じファイルを指す |
| `open_editor` | それ以外 | `resolve_editor_cmd(env, path=None)`（変更前と同じ。実行中のプロセスの `PATH` で探す） |

`open_editor` は `_prepare_ipc_env` を呼ぶ前にペインの `PATH`（`env.get("PATH")`）を控え、呼んだ後の値と比べる。IPC を
外したときは `PATH` が変わらないこともある（ペインの `PATH` に `remote-cli` が無いとき）ため、既存の `dropped_ipc` の条件も残す。

- 繰り返しの終わる条件: 繰り返しは無い（`_prepend_path` は文字列の連結だけで、`PATH` の要素を走査しない）
- 計算量の注意: 無し。追加の外部コマンドは拾い直したときの 1 回で、上限は既存の `_TMUX_TIMEOUT`（2 秒）

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `_ASKPASS_NODE_VAR` | 定数 | `lib/devbase/editor/opener.py` | `"VSCODE_GIT_ASKPASS_NODE"`。tmux への問い合わせと warning の文面の両方がこの定数を使う |
| `_REMOTE_CLI_UNSET` | 定数（見つからない理由） | `lib/devbase/editor/opener.py` | `"tmux のセッション環境に VSCODE_GIT_ASKPASS_NODE がありません"` |
| `_REMOTE_CLI_REMOVED` | 定数（見つからない理由） | `lib/devbase/editor/opener.py` | `"tmux のセッション環境の VSCODE_GIT_ASKPASS_NODE が消されています (-VSCODE_GIT_ASKPASS_NODE)"` |
| `_REMOTE_CLI_NOT_ABSOLUTE` | 定数（見つからない理由） | `lib/devbase/editor/opener.py` | `"VSCODE_GIT_ASKPASS_NODE が絶対パスではありません"` |
| `_REMOTE_CLI_NO_EDITOR` | 定数（見つからない理由の書式） | `lib/devbase/editor/opener.py` | `"{directory} に実行できる {name} がありません"`。`editor_name` が `None` のときの `name` は `"エディタ"` |
| `_TMUX_TIMEOUT` | 定数（既存） | `lib/devbase/editor/opener.py` | `2.0`。新しく持たず、`_tmux_show_env` を通じて使う |
| `_TMUX_REMOVED` | 定数（既存） | `lib/devbase/editor/opener.py` | 消された印。新しく持たない |

見つからない理由の区分は、設計文書の 3 つ（未設定・消された・実行できるエディタが無い）に、不変条件 I3 の「絶対パスで
ない」を分けて 4 つにした。warning が直し方を示せるよう、絶対パスでない値を「未設定」に混ぜない。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `devbase` の 2 回の tmux の呼び出し（判定のソケットの読み・探索の `VSCODE_GIT_ASKPASS_NODE` の読み）の間に、別のクライアントが同じセッションへ attach する | tmux のセッション環境 | 取らない（決定 1 で許容した。2 つの読みは続けて行う呼び出しで、間に利用者の操作は挟まらない） | 読むだけで書かない。食い違ったときの結果は設計文書の「未確認のまま残ること」にある。`devbase open` を打ち直せば読み直す |
| 同じホストで複数の `devbase open` / `devbase up` が同時に走る | tmux のセッション環境・VS Code サーバーのディレクトリ | 取らない（どちらも読むだけで、起動に渡す環境は呼び出しごとの複製） | 冪等。中断しても残るものは無い |
| 確かめた後、起動するまでに VS Code サーバーの版が消される | `<サーバー>/bin/remote-cli/<エディタ>` | 取らない | 起動の失敗は既存の `open_editor` の warning になり、`up` は続く。打ち直すと警告の経路に入る |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_prepare_ipc_env` | 合う `remote-cli` を足したこと。1 行: 「拾い直したソケットに合う remote-cli を PATH の先頭に入れました (<ディレクトリ>)」 | info（`devbase.log` のロガー） | ペインの `PATH` の全体 |
| `_prepare_ipc_env` | 合う `remote-cli` が見つからなかったこと。1 行: 「拾い直したソケットに合う remote-cli が見つかりません (<見つからない理由>)。PATH は変えずに <エディタの名前> で起動します。tmux の update-environment に VSCODE_GIT_ASKPASS_NODE を入れると次から直ります (環境変数ガイドの tmux 設定を参照してください)」 | warning（同上） | セッション環境のほかの変数（`VSCODE_NONCE`・`GIT_ASKPASS` などの認証に関わる値）。`VSCODE_GIT_ASKPASS_NODE` だけを読む |
| `_find_matching_remote_cli` | 出さない（理由は戻り値で返し、ログは呼び出し元がまとめて 1 行にする） | — | — |

既存の info「拾い直しました」と「以前のクライアントのもの」、既存の warning「接続できません」は文面も条件も変えない。
拾い直したときに出るのは、既存の info に加えて、上の 2 行のどちらか 1 行だけである（受け入れ条件 3・4）。

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_editor_name` | `resolve_editor_display`（`DEVBASE_EDITOR` を語に分け、無ければ `["code"]`） | `grep -n "def resolve_editor" lib/devbase/editor/opener.py` | 使う。先頭の語を取り、`os.path.basename` を掛ける。`shlex` の `ValueError` だけをこの関数で受けて `None` にする |
| `_editor_name` | `resolve_editor_cmd` | 同上 | 使えない。実在の確かめ（`shutil.which`）を含み、ペインの `PATH` に無い名前で `None` を返すため、名前だけが要る場面に合わない |
| `_is_executable_file` | `_which`（`shutil.which` の包み）に `path=<remote-cli のディレクトリ>` を渡す | `grep -n "_which\|shutil.which" lib/devbase/editor/opener.py`、`python3 -c "import inspect,shutil; print(inspect.getsource(shutil._access_check))"` | 使えない。ディレクトリでないことしか見ず通常のファイルかを見ない、名前に `/` を含むと `path` を無視する、の 2 点で決定 3 の確かめにならない |
| `_is_executable_file` | リポジトリの中の実行権の確かめ | `grep -rn "os.access\|X_OK" lib --include=*.py` | 使えない。当たりは `lib/devbase/commands/env.py:1662` の `os.R_OK` の 1 件だけで、実行権を見る部品は無い |
| `_find_matching_remote_cli` | `_tmux_show_env`（セッション環境の読み・消された印・2 秒の上限・例外の吸収） | `grep -n "def _tmux" lib/devbase/editor/opener.py` | 使う。消されたと未設定を分けるため、`_tmux_env` ではなく `_tmux_show_env` を使う |
| `_find_matching_remote_cli` | `lib/devbase/editor/vscode_server.py` の関数（`bin_fix_script`・`prepare_bin_dir`・`shared_cache_prune_script`・`prune_shared_cache`） | `grep -n "^def " lib/devbase/editor/vscode_server.py` | 使えない。どれもコンテナの中の VS Code サーバーのキャッシュを扱い、ホストのサーバーのディレクトリを求める部品ではない。サーバーのディレクトリを探す方法は決定 1 で採らない |
| `_find_matching_remote_cli` | `_is_remote_cli_dir`（`PATH` の要素の末尾が `remote-cli` か） | `grep -n "_is_remote_cli_dir" lib/devbase/editor/opener.py` | 使わない。求めるディレクトリは作り方で末尾が `remote-cli` に決まり、確かめ直す分岐が当たらない（「処理の手順」） |
| `_prepend_path` | `_drop_vscode_ipc` の `PATH` の組み直し、リポジトリの中の `os.pathsep` の扱い | `grep -rn "os.pathsep" lib --include=*.py` | 使えない。当たりは `opener.py` の `_drop_vscode_ipc`（要素を除く）だけで、先頭に足す部品は無い |

## 単体テストの観点

テストは `tests/editor/test_opener.py` に置き、tmux は既存の `_FakeTmuxEnv`（変数ごとの出力を返す）を `opener.subprocess.run`
の差し替えに使う。VS Code サーバーの配置は `tmp_path` に `node` と `bin/remote-cli/<エディタ>` を作って与え、ソケットの
接続は既存のテストと同じく `_socket_connectable` を差し替える。実の tmux サーバーに触れない。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_editor_name` | `DEVBASE_EDITOR` が無ければ `"code"`。`"cursor --reuse-window"` なら `"cursor"` | `DEVBASE_EDITOR=/opt/bin/cursor` なら `"cursor"`。`DEVBASE_EDITOR=""`（空）は未設定と同じく `"code"`（`resolve_editor_display` に従う） | 引用符の閉じない値で `None`。`'""'` で `None` |
| `_is_executable_file` | 実行権のある通常のファイルで `True` | シンボリックリンクの先が実行できる通常のファイルなら `True` | 実行権の無いファイル・ディレクトリ・無いパス・NUL を含むパスで `False`（例外を投げない） |
| `_find_matching_remote_cli` | `VSCODE_GIT_ASKPASS_NODE=<tmp>/server/node` で `<tmp>/server/bin/remote-cli/code` があれば、ディレクトリ `<tmp>/server/bin/remote-cli` と理由 `None`。末尾の名前は `remote-cli`。旧い配置（`<tmp>/bin/<commit>/node`）でも同じ。tmux の呼び出しは `VSCODE_GIT_ASKPASS_NODE` の 1 回 | `editor_name="cursor"` で `remote-cli/cursor` だけがあれば見つかり、`remote-cli/code` だけなら見つからない | 異常系の処理の 5 行ごと: tmux の失敗（`_FakeRun(returncode=1)`）と値なしで「未設定」、`-VSCODE_GIT_ASKPASS_NODE` で「消された」、相対パスで「絶対パスでない」、`code` が無い・実行できない・ディレクトリで「実行できるエディタが無い」。`editor_name=None` で tmux を呼ばない。サーバーのディレクトリを別に置いても値が無ければ見つからない（I1） |
| `_prepend_path` | `"/a:/b"` と `"/rc"` で `"/rc:/a:/b"` | `path=None` と `path=""` で `"/rc"`（空の要素を作らない）。ペインの `PATH` に別の `remote-cli` があっても除かず、並びを保つ | 無し（例外の経路が無い） |
| `_prepare_ipc_env` | 拾い直して合う `remote-cli` があれば、`PATH` の先頭がそのディレクトリ・2 番目以降がペインの要素と同じ並び・`VSCODE_IPC_HOOK_CLI` が拾い直したソケット、info 1 行（受け入れ条件 2・3） | ペインの `PATH` が無いとき `PATH` はディレクトリだけ。`ipc_from_previous_client` と拾い直しが重なっても IPC を外さない | 合う `remote-cli` が無い 4 つの理由で `PATH` がペインのまま、warning 1 行に理由と `update-environment` の案内（受け入れ条件 4・5）。拾い直していないとき `VSCODE_GIT_ASKPASS_NODE` を問い合わせない（受け入れ条件 6・性能の条件） |
| `open_editor` | 再現手順の条件（ペインの `PATH` の先頭にデスクトップ版を模した `code`、セッション環境だけが接続できるソケット、合う `remote-cli`）で、差し替えた起動の受け取る `cmd[0]` を起動に渡す環境の `PATH` で解決すると `<サーバー>/bin/remote-cli/code`（受け入れ条件 1） | `DEVBASE_EDITOR` が絶対パスのとき、起動するコマンドの先頭の語は絶対パスのままで、`PATH` は差し替わる（前提 4） | 合う `remote-cli` が無いとき、起動は行われ戻り値は `"launch"`。拾い直さない経路（ペインのソケットを使う・tmux の外・セッション環境から拾えない）と IPC を外す経路の既存のテストが変更なしで通る（受け入れ条件 6・7） |
