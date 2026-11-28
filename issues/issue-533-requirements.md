## 何が起きたか

2026-11-28、Ubuntu 24.04 のホストで devbase の dev コンテナを使う利用者が、コンテナの中で codex の読み取り専用の起動（bubblewrap）を使おうとした。bubblewrap は user namespace を作るが、Ubuntu 24.04 は `kernel.apparmor_restrict_unprivileged_userns = 1`（`/usr/lib/sysctl.d/10-apparmor.conf`）で、AppArmor のプロファイルを持たない特権の無いプロセスが user namespace を作ることを止める。利用者はホストに手で `/etc/sysctl.d/60-devbase-userns.conf`（`kernel.apparmor_restrict_unprivileged_userns = 0`）を置き、`sysctl -p` で 0 にした。

利用者は、この設定を devbase のインストールのときに自動で入るようにしたい（Mac / Linux を問わず）。

## 種類

機能追加（devbase のインストールがホストのカーネルの設定を扱う約束は今の SSOT に無い）

## 今の姿

- インストールは `install.sh`（`curl -fsSL https://dl.basex.jp/i | bash`）が clone か pull の後に `bin/devbase init` を 1 回呼ぶ。`devbase init`（`lib/devbase/commands/init.py` の `cmd_init`）は rc・補完・plugins を整え、sudo を使わず、ホストの設定を変えない（導入の手引き（`docs/user/getting-started.md`）、インストーラの配信（`docs/developer/installer-hosting.md`））
- devbase は Linux（native の dockerd）と macOS（Docker Desktop など VM の中の dockerd）の両方で動く。コンテナはホストではなく dockerd が動くカーネルを共有する
- **ホストの設定だけでは codex は動かない。** 0 にした後も、Docker の既定の seccomp が `unshare` を止める（`docker run --rm --entrypoint bash devbase-base:latest -c 'unshare -Ur true'` → `Operation not permitted`）。プロジェクトの compose に `security_opt: [seccomp=unconfined, apparmor=unconfined]` を足すと `unshare -Ur true` と `codex sandbox -- true` が終了コード 0 になった（2026-11-28、この端末）。`security_opt` はこの課題の範囲外である

## 何を足すか・変えるか

| | 今 | 足した後 |
| --- | --- | --- |
| インストール（`install.sh` → `devbase init`） | ホストのカーネルの設定に触れない | Linux のホストに userns の制限があれば、説明を出してから devbase の sysctl の設定ファイルを置き、その場で 0 にする。権限が得られなければ変えずに手順を出し、インストールは止めない |
| 値の確かめ | 利用者が `/proc/sys/kernel/apparmor_restrict_unprivileged_userns` を自分で読む | `devbase userns status` が、ホストの値・設定ファイル・コンテナが動くカーネルの値を出す |
| 戻し方 | 利用者がファイルを消して `sysctl --system` を打つ | `devbase userns revert` が devbase の置いたファイルだけを消し、`sysctl --system` で読み直す |
| macOS など、コンテナが VM の中で動く端末 | 何も無い | コンテナが動くカーネルの値を読み、制限があれば変え方の案内を出す。VM の設定は自動では変えない |

## 今の利用者への影響と互換

- 既にインストールした利用者は、`install.sh` を打ち直すか `devbase init` を打ったときに初めて設定が入る。`git pull` だけでは入らない
- 手で同じ値を入れた端末（この端末のように `/etc/sysctl.d/60-devbase-userns.conf` を置いた端末）では、何も書き足さない
- トップレベルのコマンドに `userns` が増える。`devbase u` は今と同じく `up` に解決する

## なぜ困るか

ホストごとに手で sysctl を入れないと、コンテナの中の codex の読み取り専用の起動が user namespace を作れずに止まる。手で入れる手順はどこにも書かれておらず、端末を変えるたびに同じ調査をやり直す。

## 目的

- devbase をインストールした Linux のホストで、userns の制限が手を動かさずに外れ、再起動の後も外れたままである
- ホスト全体のセキュリティを緩める変更であることを利用者が知り、同じコマンドで戻せる
- macOS など、devbase が直接変えられない端末では、今の値と変え方が分かる

## 対象範囲

含む:
- `devbase init`（`install.sh` が呼ぶ）の最後に、ホストの userns の制限を外す処理を足す
- `devbase userns status` / `apply` / `revert` を足す（`apply` は `init` が呼ぶものと同じ）
- Linux のホスト: キーがある（1 / 0）・キーが無い・root で動く・パスワード無しの sudo・端末で sudo のパスワードを尋ねられる・どれも無い、の扱い
- コンテナが動くカーネルの値を、手元に既にあるイメージを使うコンテナから読む（取得しない）
- 利用者へ出す説明（何を変えるか・なぜか・何が緩むか・どう戻すか）と、変えないことにした理由
- 変えることを止める環境変数（`DEVBASE_SKIP_USERNS`）
- テストが実のホストの sysctl と sudo に触らないこと

含まない:
- 生成する compose（`.docker-compose.scale.yml`）へ `security_opt`（`seccomp=unconfined`・`apparmor=unconfined`）を足すこと。足すかは #534 で決める（ホストの設定だけでは codex は動かない、を前提にする）
- macOS の Docker Desktop・Colima・OrbStack などの VM の中の設定を devbase が変えること。案内だけを出す
- リモートの docker context（別ホストの Docker への dev コンテナ起動（`docs/specifications/remote-docker-context.md`））の接続先のホストの設定を変えること・確かめること
- `kernel.unprivileged_userns_clone`（古い Debian）・`user.max_user_namespaces` など、ほかの user namespace の制限
- bubblewrap だけに user namespace を許す AppArmor のプロファイルを入れること
- `devbase up` や `devbase build` の中で値を確かめること

## 前提

- 前提 1: `kernel.apparmor_restrict_unprivileged_userns` は AppArmor を持つカーネル（Ubuntu 23.10 以降など）にだけあり、`/proc/sys/kernel/apparmor_restrict_unprivileged_userns` が無いカーネルでは、この制限は効かない
- 前提 2: コンテナの中から読む `/proc/sys/kernel/apparmor_restrict_unprivileged_userns` は、そのコンテナが動くカーネルの値である（この端末で、ホストで 0 にした後にコンテナから読んで 0 だった。2026-11-28）
- 前提 3: macOS の Docker Desktop の VM にこのキーがあるか・値は何かは確かめていない。確かめるのは実装の後の macOS の端末である（リリース後テスト）

## 受け入れ条件

- [ ] 1. 前提: Linux のホストでキーが 1、devbase の sysctl の設定ファイルが無く、root で動く。操作: `devbase userns apply`。結果: `/etc/sysctl.d/60-devbase-userns.conf` が 1 行目に目印のコメント（`# devbase:` で始まる）と `kernel.apparmor_restrict_unprivileged_userns = 0` を持って作られ、`/proc/sys/kernel/apparmor_restrict_unprivileged_userns` が 0 になり、終了コードが 0
- [ ] 2. 前提: 条件 1 と同じで、root でなく `sudo -n true` が通る。結果: 同じファイルが sudo で作られ、値が 0 になり、終了コードが 0
- [ ] 3. 前提: root でなく、`sudo -n true` が通らず、`/dev/tty` を開ける。結果: sudo がパスワードを尋ねる前に、変えるキー・置くファイルのパス・緩むこと・戻すコマンド（`devbase userns revert`）が出る
- [ ] 4. 前提: root でなく、`sudo -n true` が通らず、`/dev/tty` を開けない。結果: ファイルも値も変わらず、利用者が打つコマンドが出て、`apply` の終了コードが 1
- [ ] 5. `devbase init` は、`apply` が終了コード 1 で終わっても、終了コード 0 で終わり、続く rc・補完の処理を飛ばさない
- [ ] 6. 前提: 値が 0 で、設定ファイルの上でも 0 になる（読む順で最後に当たるファイルが 0 を書く）。結果: ファイルを書かず、sudo を呼ばず、「既に 0」と出て、終了コードが 0
- [ ] 7. 前提: 値が 0 だが、設定ファイルの上では 1 になる（`sysctl -w` だけで 0 にした）。結果: 設定ファイルを置き、再起動の後も 0 になることを示す行が出る
- [ ] 8. 前提: `/proc/sys/kernel/apparmor_restrict_unprivileged_userns` が無い（macOS・AppArmor を使わない distro）。結果: sudo を呼ばず、ファイルを書かず、終了コードが 0
- [ ] 9. 前提: コンテナの中で動く（`/.dockerenv` か `/run/.containerenv` がある）。結果: ファイルも値も変えず、ホストで打つよう出て、終了コードが 0
- [ ] 10. 前提: 読む順で devbase のファイルより後のファイル（例 `99-hardening.conf`）が 1 を書く。結果: そのファイルのパスを挙げて、再起動の後に 1 へ戻ると警告し、そのファイルを書き換えない
- [ ] 11. 前提: ファイルを置いた後の `sysctl -p` が失敗する。結果: 置いたファイルを消し、値は変わらず、終了コードが 1
- [ ] 12. `devbase userns revert` は、目印のある `/etc/sysctl.d/60-devbase-userns.conf` を消して `sysctl --system` を打ち、その後の値を出して、終了コードが 0
- [ ] 13. `devbase userns revert` は、同じパスに目印の無いファイルがあれば消さずに理由を出し、終了コードが 1。ファイルが無ければ何もせず終了コードが 0
- [ ] 14. `devbase userns status` は、ホストの値・設定ファイル（パスと目印の有無）・コンテナが動くカーネルの値（読めないときは「未確認」と理由）を出し、何も変えず、終了コードが 0
- [ ] 15. コンテナが動くカーネルの値は、手元にある `devbase-base:latest` を `--pull never` で起動したコンテナから読む。イメージが無いときは取得せず、`docker info` の `SecurityOptions` に `apparmor` が無ければ「制限は効かない」、あれば「未確認」と出す
- [ ] 16. コンテナが動くカーネルの値が 1 で、ホストの変更で変わらない（VM かリモート）とき、`apply` と `init` は VM の設定を変えずに、変え方の案内を出す
- [ ] 17. 前提: `DEVBASE_SKIP_USERNS` に空でない値がある。結果: `devbase init` は `apply` を呼ばず、飛ばしたことを 1 行出す
- [ ] 18. `apply` / `revert` が起動する子プロセス（sudo・sysctl・docker）は標準入力を受け継がない（`curl | bash` の残りのスクリプトを読み込まない）
- [ ] 19. テストの全体（`env -u DEVBASE_ROOT uv run --locked pytest tests/ -q`）は、キーが 1 の Linux の端末で走らせても `/etc/sysctl.d` を変えず、sudo を起動しない（退行しない）
- [ ] 20. `devbase u` は今と同じく `up` に解決する（退行しない）。前提の確認: `TOP_PREFIX_PREFERENCES` に `u` は無く、`up` が `u` で始まる唯一のトップレベルのコマンドである（2026-11-28、`lib/devbase/cli.py` の `_expand_argv`）
- [ ] 21. `devbase userns` は `devbase --help`・bash と zsh の補完・`bin/devbase` の振り分けに現れる

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| セキュリティ | ホスト全体の userns の制限を外すことを、変える前に毎回出す。devbase が書くのは `/etc/sysctl.d/60-devbase-userns.conf` の 1 つだけで、ほかの sysctl のファイルを書き換えない |
| 運用・保守性 | 戻し方が 1 つのコマンド（`devbase userns revert`）と、devbase が無くても打てる手順（ファイルを消して `sysctl --system`）の両方で出る |
| 性能・拡張性 | `init` が待つ docker の呼び出しは 1 回あたり 20 秒で打ち切り、打ち切ったら「未確認」で続ける |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。トップレベルのコマンド `userns` と環境変数 `DEVBASE_SKIP_USERNS` が増える。既存のコマンドと引数は変わらない |
| データ | 無し（ホストに `/etc/sysctl.d/60-devbase-userns.conf` が増える） |
| 既存の振る舞い | `devbase init` が最後に sudo を求めることがある |

## 用語

| 用語 | 意味 |
| --- | --- |
| userns の制限 | `kernel.apparmor_restrict_unprivileged_userns` が 1 で、AppArmor のプロファイルを持たない特権の無いプロセスが user namespace を作れない状態 |
| devbase の sysctl の設定ファイル | devbase が置く `/etc/sysctl.d/60-devbase-userns.conf`。1 行目の目印のコメント（`# devbase:` で始まる）で devbase が置いたものと見分ける |
| 設定ファイルの上の値 | sysctl.d の読む順（ファイル名の辞書順で最後のものが勝つ）で、起動のときにキーへ入る値 |
| コンテナが動くカーネル | dockerd が動くカーネル。Linux の native の dockerd ではホストと同じで、macOS や docker context の接続先では別である |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` |
| 静的解析・型検査 | `uvx ruff check --select=E9,F63,F7,F82 lib`、`python3 .github/scripts/proper_term_check.py` |
| 手動確認（マージ前） | 受け入れ条件 1・12 を、キーのある Linux の端末で利用者が `devbase userns apply` / `revert` を打って確かめる（実のホストの設定を変えるため、自動のテストでは確かめない） |
| 手動確認 | 受け入れ条件 3・8・15・16 を、macOS（Docker Desktop）の端末で `install.sh` を打ち直して確かめる。VM の中の値と、出た案内を記録する |

## リリース後の確認の計画

| 条件 | 経路を動かすバージョン | 確かめる時点 |
| --- | --- | --- |
| 3. tty で sudo を尋ねる前に説明が出る（`curl \| bash` の経路） | 配ったバージョン | このリリースの後 |
| 8. macOS ではホストを変えない | 配ったバージョン | このリリースの後 |
| 15. コンテナが動くカーネルの値を読む（Docker Desktop） | 配ったバージョン | このリリースの後 |
| 16. VM の値が 1 のときの案内（Colima など） | 配ったバージョン | このリリースの後 |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 変える前に説明を出す。devbase のファイルだけを書き、消す |
| 行わない | ほかの sysctl のファイルを書き換える・消す。VM やリモートのホストを変える。イメージを取得する。実装の確かめで `devbase up` を打つ（P3） |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 置き場を `devbase init` と新しいサブコマンドにし、`install.sh` は変えない | 起動指示の「どこに置くか」 | 受け入れ条件 1〜5・17 |
| S2 | 権限が得られないときも install を止めない | 起動指示の「失敗しても install を止めるか」 | 受け入れ条件 4・5 |
| S3 | macOS などの VM の中は自動で変えず、案内だけにする（「Mac / Linux 問わず」を、Mac では値を確かめて案内する、と読む） | 起動指示の「変える必要がある場合の手段と永続性」・VM の再起動で戻り得ること | 受け入れ条件 15・16、含まない |
| S4 | `security_opt` を compose に足すことは含めない | 起動指示 | 含まない |
| S5 | 変えることを止める環境変数を置く | ホスト全体を緩める変更であること | 受け入れ条件 17 |

## リクエスト（原文）

> devbase をインストールする際、自動的にホストの `kernel.apparmor_restrict_unprivileged_userns=0` が入るようにしたい（Mac / Linux 問わず）

（書き手: 利用者、日付: 2026-11-28。conductor の起動指示に引用された文）

> seccomp/apparmor の `security_opt` を devbase が生成する compose に自動で足すかは、依頼の範囲外として「含まない」に書き、out-of-scope で起票する（足すかの判断は別の課題）。設計には「ホストの設定だけでは codex は動かない」ことを前提として書く

（書き手: AI の起動指示、日付: 2026-11-28）

