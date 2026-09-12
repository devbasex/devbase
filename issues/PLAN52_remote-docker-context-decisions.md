# PLAN52 設計: 決定の記録とテスト設計

この文書は決定の記録とテスト設計を扱う。

| 内容 | 文書 |
| --- | --- |
| 要求と受け入れ条件 | [PLAN52_remote-docker-context.md](PLAN52_remote-docker-context.md) |
| 構成要素と処理の流れ | [PLAN52_remote-docker-context-design.md](PLAN52_remote-docker-context-design.md) |

## 決定の記録

### 決定 1: context は環境変数 `DOCKER_CONTEXT` で全呼び出しへ伝える

docker CLI と compose の両方が `DOCKER_CONTEXT` を読み、`docker context use` の既定より
優先する。`subprocess` で CLI を叩く箇所は 30 か所近くあり、`--context` を引数に足す形では
1 か所の漏れがそのまま「一部だけ手元の daemon を触る」事故になる。環境変数なら、解決した
直後に `os.environ` へ載せるだけで、フックや `bin/devbase build` のような子プロセスにも
同じ値が届く。

`--context` を各コマンド引数に足す形は、`docker compose` が `--context` を受け付けない
（グローバルオプションとしてのみ）ため一様に書けず、採らない。

### 決定 2: `project.local.yml` は `project.yml` へマージせず、別の型で読む

issue は「深いマージ（local が勝つ）」を提案しているが、初期スコープの `docker` 節は
`project.yml` に存在しないキーであり、マージする対象が無い。`ProjectConfig` を変えなければ
`project.yml` の既存テストと検証がそのまま保たれ、`project.yml` に `docker:` を書いた事故を
「未知キー」として型で弾ける。`scale` / `open_editor` の個人上書きを足すときに、その時点で
マージの規則を決める。

### 決定 3: リモート扱いは「解決した context が現在の context と異なる」ことで決める

「設定があればリモート」とすると、手元の context 名を書いただけで gid の `docker run` と
`~` の書き換えが走り、ローカルの振る舞いが変わる。「解決した context が現在の context と
同じなら従来どおり」とすれば、設定の有無ではなく接続先の違いで扱いが変わる。現在の context
は `docker context show` で取る。daemon に接続せず、context が未指定のときは呼ばない。

`docker context inspect` で endpoint が `unix://` かどうかを見る形は採らない。Docker Desktop の
`desktop-linux` のように手元でも VM 越しの構成があり、「手元かどうか」を endpoint から
一意に読めない。

### 決定 4: CLI / env で別の context へ向けたときは、ファイルの `home` / `gid` を使わない

`home` と `gid` は機材の値であり、`docker.context` と組で意味を持つ。上書きで別ホストへ
向けた実行にファイルの値を持ち込むと、別ホストの HOME や gid が黙って入り、mount が空に
なるか docker.sock に触れない状態になる。上書き先が同じ名前なら（例: ファイルと同じ context
を CLI で明示した）ファイルの値を使う。

### 決定 5: gid は `docker run` の `stat` で取り、`.cache/docker-gid/<context>` に控える

リモート側の `/etc/group` は手元から読めない。docker.sock を bind mount したコンテナで
`stat -c %g` すれば、daemon が見ている gid がそのまま取れる。ssh 越しでも Docker Desktop の
VM でも同じ手順で済む。使うイメージは `alpine:3`。小さく、`stat` が `-c` を受け付ける。
毎回の `up` で `docker run` を挟むと 1〜2 秒増えるため、成功した値をファイルに控える。

`docker info` は gid を出さない。`ssh <host> getent group docker` は採らない。context の
ssh 設定を devbase が解釈し直すことになり、TCP+TLS の context では成り立たない。

### 決定 6: gid の控えは上書きし、履歴を持たない

控えるのは再取得できる値で、過去の値に意味が無い。リモート側で gid が変わることは稀で、
変わったら利用者がファイルを消すか `docker.gid` を書く。この文書の「未確認のまま残ること」には
載せず、ドキュメントの手順として書く。

### 決定 7: `~` の書き換えは生成物 `.docker-compose.scale.yml` に対して行う

起動時に `-f` で渡すのは生成物だけなので、生成の段階で書き換えれば全サービスの mount に
効く。元の `compose.yml` は共有ファイルであり触らない。`~` を展開するのは compose
クライアントだが、生成物に絶対パスが書かれていれば展開は起きず、そのままリモートへ渡る。

`docker compose config` を通して展開済みの構成を得てから差し替える形は採らない。環境変数
（機密を含む）が値へ展開された YAML を扱うことになる。`_load_compose_config` が YAML を
直接読む理由（機密を生成物へ書かない）と衝突する。

### 決定 8: `~user/...` と相対パスは書き換えず警告に留める

`~user` は手元でもリモートでも別のユーザの HOME を指し、`docker.home` では代替できない。
相対パスは compose クライアントが手元の絶対パスへ解決し、リモートには存在しない。どちらも
「正しい値」を devbase が推測できないため、黙って書き換えるより一覧で示す。

### 決定 9: shell の `build` は `--context` を `DEVBASE_DOCKER_CONTEXT` へ写し、docker 呼び出しを `env exec` 経由にする

`bin/devbase` は YAML を読めない。`--context` を引数から取り除いて環境変数に写せば、Python
側の `env exec` が同じ優先順位（env > ファイル）で解決できる。CLI より上の優先はもともと
無いので、env に写しても結果は変わらない。`docker buildx build` と `docker image inspect` の
直接呼び出しも `env exec` を通す。compose のビルドと同じ経路で `DOCKER_CONTEXT` を受け取る。

`bin/devbase` の冒頭で Python を 1 回呼んで `DOCKER_CONTEXT` を export する形は、全コマンドに
uv の起動が 1 回増えるため採らない。

### 決定 10: `DEVBASE_DOCKER_CONTEXT` を出力としても載せる

`up` は CLI の `--context` で上書きした値を `os.environ['DOCKER_CONTEXT']` に載せる。
一方 `bin/devbase build` → `env exec` の経路は context を**再解決**する。CLI の値は
子プロセスに届かないため、ファイルの値で `DOCKER_CONTEXT` が上書きされる。`DEVBASE_DOCKER_CONTEXT` に
同じ値を載せておけば、再解決でも同じ context に落ちる。

### 決定 11: エディタの `settings.context` は「明示 → devbase の解決結果 → 従来の推測」の順

`DEVBASE_EDITOR_DOCKER_CONTEXT` は「attach に使う context を手で決めたい」ための既存の
つまみで、devbase の解決結果より上に置く。解決結果があれば、ローカル端末でも
`settings.context` を付ける。無ければ従来どおり `ssh_host` があるときだけ
`docker context show` を使う。

### 決定 12: リモート扱いの `up` は自動スナップショットを飛ばす

自動スナップショットは「これから使うボリューム」を手元のディレクトリへ控えるものである。
リモート扱いではそのボリュームがリモートにある。`docker run -v <手元のパス>` は
リモートの空ディレクトリへ書く。飛ばして警告を出す。`devbase snapshot` の明示操作と
`down` のローテーションは context を解決せず、従来どおり手元を対象にする。

リモートのボリュームを `docker run ... | tar` の標準出力経由で手元へ運ぶ形は、差分世代の
仕組みごと作り直しになるため別課題にする。

## テスト設計

| 受け入れ条件 | 何で確かめるか |
| --- | --- |
| `project.local.yml` が無い / 空のとき従来どおり | `tests/project/test_local_config.py`: 無いディレクトリと空ファイルで既定値。`tests/commands/test_container_context.py`: `subprocess.run` を差し替えて子プロセスの env に `DOCKER_CONTEXT` が無いこと |
| `docker.context` が全子プロセスへ載る | `test_container_context.py`: `up` の全 `subprocess.run` 呼び出しの `env`（または `os.environ`）に `DOCKER_CONTEXT` があること |
| 最上位・`docker` 節の未知キー、型・値の検証 | `test_local_config.py`: 各 `ConfigError` のメッセージに使えるキー・理由が含まれる |
| `project.yml` の `docker:` を案内付きで拒む | `tests/project/test_config.py` に 1 件追加 |
| 壊れた YAML | `test_local_config.py`: ファイル名を含む `ConfigError` |
| 優先順位 CLI > env > ファイル > 未指定 | `tests/utils/test_docker_context.py`: 組み合わせの表で `ContextChoice` を検証 |
| env の空文字は未指定 | 同上 |
| `--context` を受け付けるコマンド | `tests/cli/test_project_dispatch.py` 系: parser が各サブコマンドで `--context` を取ること |
| 存在しない context は docker のエラーで止まる | 手動確認（docker の判定に委ねるため単体テストにしない） |
| ローカル扱いで `DOCKER_GID` が変わらない | `test_container_context.py`: 現在の context と同じ名前を設定し、`DOCKER_GID` が元のまま |
| `docker.gid` 明示 | `test_docker_context.py`: `DockerTarget.gid` と反映後の `os.environ` |
| gid の自動取得と控え | `test_docker_context.py`: `runner` を差し替え、1 回目は `docker run` が呼ばれて控えが書かれ、2 回目は呼ばれない |
| gid 取得の失敗 | `test_docker_context.py`: 非ゼロ / 非整数の出力で `DevbaseError`。`test_container_context.py`: `up` が非ゼロで終わり compose を呼ばない |
| `~` の展開（短い書式・`~` 単独・長い書式） | `tests/volume/test_bind_mounts.py` |
| `~user` と相対パスは警告のみ | 同上 |
| ローカル扱いでは書き換えない | `tests/volume/test_compose.py` 系: `home` を渡さない生成で `~` が残る |
| `home` 無しのリモート扱いで警告 | `test_bind_mounts.py` + `caplog` |
| 絶対パスと named volume を触らない | `test_bind_mounts.py` |
| `down` / `ps` / `logs` / `login` の伝播 | `test_container_context.py`: 各 cmd の子プロセス env |
| shell `build` の伝播 | `tests/cli/test_wrapper_build_context.py`: `docker` と `uv` を偽コマンドに差し替え、`--context` が取り除かれて `DEVBASE_DOCKER_CONTEXT` に写ること。`env exec` 経由で `DOCKER_CONTEXT` が届くこと |
| `up` からの自動ビルド | `test_container_context.py`: `_run_build` の子プロセス env に `DOCKER_CONTEXT` と `DEVBASE_DOCKER_CONTEXT` |
| `env exec` | `tests/cli/test_secret_injection.py` 系に追加: 子プロセス env の `DOCKER_CONTEXT` |
| 自動スナップショットの回避 | `test_container_context.py`: リモート扱いで `SnapshotManager.create` が呼ばれず警告が出る |
| `snapshot` 系と `down` のローテーションは変わらない | 既存テスト（`tests/snapshot/`）が書き換えなしで通る |
| ローカル端末 + context あり → `settings.context` 付きフラット URI | `tests/editor/test_opener.py` |
| ローカル端末 + context 無し → 従来 | 既存テストが通る |
| Remote-SSH + context あり → ネスト URI + `settings.context` | `test_opener.py` |
| Remote-SSH + context 無し → 従来の推測 | 既存テストが通る |
| `DEVBASE_EDITOR_DOCKER_CONTEXT` が優先 | `test_opener.py` |
| フラット URI の提示 | `test_opener.py` + `caplog` |
| `project.yml` 単体の検証が変わらない | `tests/project/test_config.py` が書き換えなしで通る |
| 機密の値が現れない | `test_container_context.py`: 機密を載せた状態で警告・控え・info に値が無い |
| Git から除外 | `git check-ignore .cache/docker-gid/x projects/x/project.local.yml` |
| `status` が変わらない | `tests/commands/test_status_account_group.py` が書き換えなしで通る |
| 性能（docker 呼び出し回数） | `test_container_context.py`: ローカル扱いの `up` で `docker context show` / `docker run alpine` が呼ばれない |
