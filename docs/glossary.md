# 用語集

この文書は `docs/glossary/glossary.json` から `glossary.py render` で作る。手で直さない。

## docker の接続先（`docker-context`）

devbase がどの docker daemon を操作するか（docs/specifications/remote-docker-context.md）

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| docker context | — | docker CLI が daemon への接続先を名前で切り替える仕組み（docker context ls の名前） | — | — | `docs/specifications/remote-docker-context.md` |
| 現在の context | — | DOCKER_CONTEXT と DOCKER_HOST を外した環境で docker context show が返す名前 | — | — | `docs/specifications/remote-docker-context.md` |
| 解決した context | — | 優先順位に従って devbase が決めた context 名。未指定なら None（従来どおり CLI に委ねる） | — | — | `docs/specifications/remote-docker-context.md` |
| リモート扱い | — | 解決した context が None でなく、現在の context と異なる（または現在の context を取得できない）状態 | — | — | `docs/specifications/remote-docker-context.md` |
| ローカル扱い | — | リモート扱いでない状態。従来と同じ振る舞い | — | — | `docs/specifications/remote-docker-context.md` |

## コマンドの入口（`cli`）

bin/devbase の位置引数の解決とビルドの入口（docs/specifications/cli-argument-resolution.md）

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 名前の形 | — | 親ディレクトリの直下の 1 つの名前として受け付ける形。[A-Za-z0-9][A-Za-z0-9._-]* の全体一致 | — | — | `docs/specifications/cli-argument-resolution.md` |
| name 解決 | — | 位置引数を名前として解釈し、projects/<name> へ cd して引数から取り除く処理 | — | — | `docs/specifications/cli-argument-resolution.md` |
| ラッパー | — | bin/devbase（bash 実装の入口。Python 実装のコマンドへ run_python で振り分ける） | — | — | `docs/specifications/cli-argument-resolution.md` |
| ショートカット | — | devbase up のように project を省いたトップレベルの同義語 | — | — | `docs/specifications/cli-argument-resolution.md` |
| 単体ビルド | — | $DEVBASE_ROOT/containers/<image> を devbase-<image>:latest として 1 つだけ作るビルド | — | — | `docs/specifications/cli-argument-resolution.md` |
| プロジェクトとして数える名前 | — | projects/ の直下の名前のうち、同期・一覧・状態・機密の操作がプロジェクトとして扱うもの。空でなく . で始まらない名前（utils/names.py の counts_as_project） | — | — | `docs/specifications/cli-argument-resolution.md` |
| 名前の形の説明 | — | 名前の形を利用者へ説明する文（utils/names.py の NAME_FORM_HINT） | — | — | `docs/specifications/cli-argument-resolution.md` |

## Compose の構成（`compose`）

devbase up が作る compose の生成物とプロファイル（docs/specifications/compose-profiles.md）

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| プロファイル | — | Compose の profiles: に書いた名前。付随サービス群をまとめる単位 | — | — | `docs/specifications/compose-profiles.md` |
| 既定のサービス | — | profiles: を持たないサービス。devbase up で起動する | — | — | `docs/specifications/compose-profiles.md` |
| プロファイルのサービス | — | そのプロファイルに属し、既定のサービスに含まれないサービス | — | — | `docs/specifications/compose-profiles.md` |
| 打ち消し用のプロファイル名 | — | __devbase_none__（定数 NO_PROFILE）。devbase が子プロセスの COMPOSE_PROFILES へ入れて利用者の指定を無効にする | — | — | `docs/specifications/compose-profiles.md` |
| 生成物 | — | devbase up がプロジェクト直下に作る .docker-compose.scale.yml | — | — | `docs/specifications/compose-profiles.md` |
| 開発サービス名 | — | get_dev_service_name() が返す名前（DEV_SERVICE_NAME、既定 dev）。生成物では <開発サービス名>-1..-N へ複製される | — | — | `docs/specifications/compose-profiles.md` |

## エディタで開く（`editor`）

up の後に VS Code で dev コンテナを開く（docs/specifications/editor-open.md）

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 窓 | — | dev コンテナへ Dev Containers 拡張で接続した VS Code のウィンドウ | — | — | `docs/specifications/editor-open.md` |
| 動いているインスタンス | — | docker ps に現れ、Compose のプロジェクトのラベルが対象のプロジェクトで、サービスのラベルが <開発サービス名>-<1 以上の数字> のコンテナ | — | — | `docs/specifications/editor-open.md` |

## 機密の置き場（`secret`）

機密の backend・参照・グループ・キャッシュ（docs/specifications/secret-backend.md）

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 参照 | — | 機密の宛先（SecretRef）。適用範囲（global / project）と持ち主（team / user）の組み合わせで 4 種。version: 2 ではグループも持つ | — | — | `docs/specifications/secret-backend.md` |
| アカウントグループ | — | DEVBASE_ACCOUNT_GROUP の値。未設定なら default。ボリューム devbase_home_<group> の単位でもある | — | — | `docs/specifications/secret-backend.md` |
| レイアウト | — | 置き場のパスの並び（layout）。flat（version: 1）と group（version: 2） | — | — | `docs/specifications/secret-backend.md` |
| 置き場のグループ名 | — | パスに入れる名前。グループ名を group_aliases で読み替えた後の名前 | — | — | `docs/specifications/secret-backend.md` |
| 対象のグループ | — | 1 回の操作が読み書きするグループ | — | — | `docs/specifications/secret-backend.md` |
| チーム単位の機密 | — | チームの全員が同じ値を使う機密 | — | — | `docs/specifications/secret-backend.md` |
| 個人単位の機密 | — | 利用者ごとに値が違う機密 | — | — | `docs/specifications/secret-backend.md` |
| backend | — | 参照に対して機密を読み書きする実装。plaintext / age / openbao。auto は存在による判定 | — | — | `docs/specifications/secret-backend.md` |
| ブートストラップ機密 | — | サーバ backend が接続に使う AppRole の role_id / secret_id。secrets/bootstrap.env.age に置く | — | — | `docs/specifications/secret-backend.md` |
| キャッシュ | — | サーバの内容と一致すると確かめられた機密を、参照ごとに age で暗号化して手元に控えたもの | — | — | `docs/specifications/secret-backend.md` |
| 機密の書き込みの知らせ | — | ファイルの backend へプロジェクトの機密を書くとき、名前が名前の形に合わなければ標準エラーへ出す 1 行 | — | — | `docs/specifications/cli-argument-resolution.md` |
| キーの行 | — | TUI の env の一覧の 1 行。キーと、そのキーがある参照（グループ・持ち主・適用範囲）の組。値の平文を持たない | — | — | `docs/specifications/secret-backend.md` |
| 勝つ行 | — | 同じキーの行のうち、一覧に並べた機密の参照の中で重ね順が最後の行。一覧の中だけの勝ち負けで、コンテナに渡る最終の値の行であることは保証しない | — | — | `docs/specifications/secret-backend.md` |
| 同期の書き込み先 | — | env sync がキーごとに選ぶ参照。--user なら個人共通、無ければキーが現にある参照（両方なら個人共通、どちらにも無ければ個人共通。個人単位の参照を持たない backend では常にチーム共通） | — | — | `docs/specifications/secret-backend.md` |
| 同期済みハッシュの控え | — | env sync がソースファイルの位置とハッシュを記録する .env.sources[.<g>].yml。キャッシュ（機密の控え）とは別のもの | — | — | `docs/specifications/secret-backend.md` |

## スナップショット（`snapshot`）

ホームのボリュームの世代と系列（docs/specifications/snapshot-series.md）

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 世代 | — | backups/<名前>/ の 1 つ。full.tar.zst 1 つと、0 個以上の incr-NNN.tar.zst からなる | — | — | `docs/specifications/snapshot-series.md` |
| 対象ボリュームの組 | — | snapshot.yml のエントリの volumes（マウント名 → ボリューム名） | — | — | `docs/specifications/snapshot-series.md` |
| 系列 | — | 対象ボリュームの組が同じ世代の集まり | — | — | `docs/specifications/snapshot-series.md` |
| 系列の最新の世代 | — | 系列の中で created_at が最も新しい世代 | — | — | `docs/specifications/snapshot-series.md` |
| 全体の上限 | — | 系列をまたいで数えた世代の数の上限（max_total） | — | — | `docs/specifications/snapshot-series.md` |

## base イメージの描画（`base-image`）

base イメージのフォントの解決（docs/specifications/base-image-rendering.md）

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 総称ファミリ | — | sans-serif / sans / serif / monospace。具体の書体名ではなく様式を指す指定 | — | — | `docs/specifications/base-image-rendering.md` |
| metric 互換 | — | 字幅・行送りが元の書体と一致する代替の書体 | — | — | `docs/specifications/base-image-rendering.md` |
| 受け皿 | — | イメージに実在しない書体名を指定されたときに末尾へ足すフェイス | — | — | `docs/specifications/base-image-rendering.md` |

## base イメージの tmux のコマンド（`tmux`）

tmux-first / tmux-clean / tmux-session が扱うセッションとクライアント

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| keeper | — | tmux-clean が必ず残すセッション。tmux の中なら今のセッション、外ならベース名に属するうち番号が最小のもの | — | — | `docs/specifications/base-shell-tests.md` |
| 実行元のクライアント | — | tmux-first を起動した端末として扱うクライアント。tmux の「現在のクライアント」のうち、最終操作が 10 秒以内のものだけを認める | — | — | `docs/specifications/base-shell-tests.md` |

## 継続的インテグレーション（`ci`）

GitHub Actions の CI（.github/workflows/ci.yml）と、そこで走る静的解析

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 検査ジョブ | — | ci.yml の jobs の 1 つ（Python syntax check・Ruff lint・ShellCheck・Pytest） | — | — | `docs/specifications/ci-checks.md` |
| 統合ブランチ | — | 課題の Pull Request の宛先になる main 以外のブランチ（release/** と mission/**） | — | — | `docs/specifications/ci-checks.md` |
| トリガー | — | ci.yml の on: に書く 1 つのイベント（pull_request / push）と、その絞り込み（branches） | — | — | `docs/specifications/ci-checks.md` |
| 積み重ねた Pull Request | — | 宛先が main でも統合ブランチでもない、別の作業ブランチの Pull Request | — | — | `docs/specifications/ci-checks.md` |
| 指摘 | — | shellcheck が出す 1 件（SC の番号・水準・行） | — | — | `docs/specifications/ci-checks.md` |
| 抑止の注記 | — | 指摘を抑える shellcheck の指示（disable= / source=）と、抑える理由のコメントの組 | — | — | `docs/specifications/ci-checks.md` |
| 基準の版 | — | 手元の基準（devbase-base:latest の shellcheck）と CI の ShellCheck の検査ジョブが揃える shellcheck の版。現在は 0.11.0 | — | — | `docs/specifications/ci-checks.md` |
| ShellCheck の検査ジョブ | — | 検査ジョブのうち shellcheck ジョブ（bin/*・install.sh・containers/base のシェルスクリプトを検査する） | — | — | `docs/specifications/ci-checks.md` |
| base のシェルスクリプト | — | containers/base/ の直下の通常のファイルのうち、先頭行が sh か bash を指す shebang か、名前が .sh で終わるもの | — | — | `docs/specifications/ci-checks.md` |
| 検査の対象 | — | CI の ShellCheck ジョブで、引数に containers/base/ のパスを持つ shellcheck の行が並べたファイル | — | — | `docs/specifications/ci-checks.md` |
| shellcheck の指示 | — | # shellcheck で始まるコメント（disable= / source= / shell=）。disable= と source= は抑止の注記の指示の側で、shell= は指摘を抑えない | — | — | `docs/specifications/ci-checks.md` |

## テストの実行環境（`test`）

pytest が走るプロセスの環境と、テストが起動する外部のプロセス

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 環境の隔離 | — | pytest を起動したシェルから継承した環境変数を、テストごとに既定の状態（未設定か固定値）へ戻すこと | — | — | `docs/specifications/test-environment-isolation.md` |
| 隔離の一覧 | — | テストの開始時に未設定へ戻す環境変数の名前と接頭辞。tests/conftest.py の ISOLATED_ENV と ISOLATED_ENV_PREFIXES | — | — | `docs/specifications/test-environment-isolation.md` |
| 隔離しない一覧 | — | lib/devbase が読むが、隔離の fixture が未設定へ戻さない変数の名前と、その理由。tests/conftest.py の NOT_ISOLATED_ENV | — | — | `docs/specifications/test-environment-isolation.md` |
| 読み取りの集合 | — | lib/devbase のソースから静的に集めた、環境変数として読む変数名の集合 | — | — | `docs/specifications/test-environment-isolation.md` |
| 漏れの検査 | — | 読み取りの集合が隔離の一覧と隔離しない一覧に収まっていることを確かめるテスト | — | — | `docs/specifications/test-environment-isolation.md` |
| 隔離した tmux サーバ | — | TMUX を外し、専用のソケットで起動した試験用の tmux サーバ。利用者の tmux サーバに触れない | — | — | `docs/specifications/base-shell-tests.md` |
| 故障の差し込み | — | 実物の tmux の前に置いたラッパーが、故障の表に当たる呼び出しだけを失敗させるか、呼び出しの前にセッションを消すこと | — | — | `docs/specifications/base-shell-tests.md` |
| 偽の date | — | PATH の先に置き、date +%s にだけ実時刻へ指定の秒数を足した値を返す試験用の date | — | — | `docs/specifications/base-shell-tests.md` |
