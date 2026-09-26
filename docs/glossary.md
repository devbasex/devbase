# 用語集

この文書は `docs/glossary/glossary.json` から `glossary.py render` で作る。手で直さない。

## docker の接続先（`docker-context`）

devbase がどの docker daemon を操作するか（docs/specifications/remote-docker-context.md）

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| docker context | docker CLI が daemon への接続先を名前で切り替える仕組み（docker context ls の名前） | — | `docs/specifications/remote-docker-context.md` |
| 現在の context | DOCKER_CONTEXT と DOCKER_HOST を外した環境で docker context show が返す名前 | — | `docs/specifications/remote-docker-context.md` |
| 解決した context | 優先順位に従って devbase が決めた context 名。未指定なら None（従来どおり CLI に委ねる） | — | `docs/specifications/remote-docker-context.md` |
| リモート扱い | 解決した context が None でなく、現在の context と異なる（または現在の context を取得できない）状態 | — | `docs/specifications/remote-docker-context.md` |
| ローカル扱い | リモート扱いでない状態。従来と同じ振る舞い | — | `docs/specifications/remote-docker-context.md` |

## コマンドの入口（`cli`）

bin/devbase の位置引数の解決とビルドの入口（docs/specifications/cli-argument-resolution.md）

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 名前の形 | 親ディレクトリの直下の 1 つの名前として受け付ける形。[A-Za-z0-9][A-Za-z0-9._-]* の全体一致 | — | `docs/specifications/cli-argument-resolution.md` |
| name 解決 | 位置引数を名前として解釈し、projects/<name> へ cd して引数から取り除く処理 | — | `docs/specifications/cli-argument-resolution.md` |
| ラッパー | bin/devbase（bash 実装の入口。Python 実装のコマンドへ run_python で振り分ける） | — | `docs/specifications/cli-argument-resolution.md` |
| ショートカット | devbase up のように project を省いたトップレベルの同義語 | — | `docs/specifications/cli-argument-resolution.md` |
| 単体ビルド | $DEVBASE_ROOT/containers/<image> を devbase-<image>:latest として 1 つだけ作るビルド | — | `docs/specifications/cli-argument-resolution.md` |

## Compose の構成（`compose`）

devbase up が作る compose の生成物とプロファイル（docs/specifications/compose-profiles.md）

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| プロファイル | Compose の profiles: に書いた名前。付随サービス群をまとめる単位 | — | `docs/specifications/compose-profiles.md` |
| 既定のサービス | profiles: を持たないサービス。devbase up で起動する | — | `docs/specifications/compose-profiles.md` |
| プロファイルのサービス | そのプロファイルに属し、既定のサービスに含まれないサービス | — | `docs/specifications/compose-profiles.md` |
| 打ち消し用のプロファイル名 | __devbase_none__（定数 NO_PROFILE）。devbase が子プロセスの COMPOSE_PROFILES へ入れて利用者の指定を無効にする | — | `docs/specifications/compose-profiles.md` |
| 生成物 | devbase up がプロジェクト直下に作る .docker-compose.scale.yml | — | `docs/specifications/compose-profiles.md` |
| 開発サービス名 | get_dev_service_name() が返す名前（DEV_SERVICE_NAME、既定 dev）。生成物では <開発サービス名>-1..-N へ複製される | — | `docs/specifications/compose-profiles.md` |

## エディタで開く（`editor`）

up の後に VS Code で dev コンテナを開く（docs/specifications/editor-open.md）

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 窓 | dev コンテナへ Dev Containers 拡張で接続した VS Code のウィンドウ | — | `docs/specifications/editor-open.md` |
| 動いているインスタンス | docker ps に現れ、Compose のプロジェクトのラベルが対象のプロジェクトで、サービスのラベルが <開発サービス名>-<1 以上の数字> のコンテナ | — | `docs/specifications/editor-open.md` |

## 機密の置き場（`secret`）

機密の backend・参照・グループ・キャッシュ（docs/specifications/secret-backend.md）

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 参照 | 機密の宛先（SecretRef）。適用範囲（global / project）と持ち主（team / user）の組み合わせで 4 種。version: 2 ではグループも持つ | — | `docs/specifications/secret-backend.md` |
| アカウントグループ | DEVBASE_ACCOUNT_GROUP の値。未設定なら default。ボリューム devbase_home_<group> の単位でもある | — | `docs/specifications/secret-backend.md` |
| レイアウト | 置き場のパスの並び（layout）。flat（version: 1）と group（version: 2） | — | `docs/specifications/secret-backend.md` |
| 置き場のグループ名 | パスに入れる名前。グループ名を group_aliases で読み替えた後の名前 | — | `docs/specifications/secret-backend.md` |
| 対象のグループ | 1 回の操作が読み書きするグループ | — | `docs/specifications/secret-backend.md` |
| チーム単位の機密 | チームの全員が同じ値を使う機密 | — | `docs/specifications/secret-backend.md` |
| 個人単位の機密 | 利用者ごとに値が違う機密 | — | `docs/specifications/secret-backend.md` |
| backend | 参照に対して機密を読み書きする実装。plaintext / age / openbao。auto は存在による判定 | — | `docs/specifications/secret-backend.md` |
| ブートストラップ機密 | サーバ backend が接続に使う AppRole の role_id / secret_id。secrets/bootstrap.env.age に置く | — | `docs/specifications/secret-backend.md` |
| キャッシュ | サーバの内容と一致すると確かめられた機密を、参照ごとに age で暗号化して手元に控えたもの | — | `docs/specifications/secret-backend.md` |

## スナップショット（`snapshot`）

ホームのボリュームの世代と系列（docs/specifications/snapshot-series.md）

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 世代 | backups/<名前>/ の 1 つ。full.tar.zst 1 つと、0 個以上の incr-NNN.tar.zst からなる | — | `docs/specifications/snapshot-series.md` |
| 対象ボリュームの組 | snapshot.yml のエントリの volumes（マウント名 → ボリューム名） | — | `docs/specifications/snapshot-series.md` |
| 系列 | 対象ボリュームの組が同じ世代の集まり | — | `docs/specifications/snapshot-series.md` |
| 系列の最新の世代 | 系列の中で created_at が最も新しい世代 | — | `docs/specifications/snapshot-series.md` |
| 全体の上限 | 系列をまたいで数えた世代の数の上限（max_total） | — | `docs/specifications/snapshot-series.md` |

## base イメージの描画（`base-image`）

base イメージのフォントの解決（docs/specifications/base-image-rendering.md）

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 総称ファミリ | sans-serif / sans / serif / monospace。具体の書体名ではなく様式を指す指定 | — | `docs/specifications/base-image-rendering.md` |
| metric 互換 | 字幅・行送りが元の書体と一致する代替の書体 | — | `docs/specifications/base-image-rendering.md` |
| 受け皿 | イメージに実在しない書体名を指定されたときに末尾へ足すフェイス | — | `docs/specifications/base-image-rendering.md` |

## base イメージの tmux のコマンド（`tmux`）

tmux-first / tmux-clean / tmux-session が扱うセッションとクライアント

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| keeper | tmux-clean が必ず残すセッション。tmux の中なら今のセッション、外ならベース名に属するうち番号が最小のもの | — | — |

## 継続的インテグレーション（`ci`）

GitHub Actions の CI（.github/workflows/ci.yml）と、そこで走る静的解析

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 検査ジョブ | ci.yml の jobs の 1 つ（Python syntax check・Ruff lint・ShellCheck・Pytest） | — | — |
| 統合ブランチ | 課題の Pull Request の宛先になる main 以外のブランチ（release/** と mission/**） | — | — |
| トリガー | ci.yml の on: に書く 1 つのイベント（pull_request / push）と、その絞り込み（branches） | — | — |
| 積み重ねた Pull Request | 宛先が main でも統合ブランチでもない、別の作業ブランチの Pull Request | — | — |
| 指摘 | shellcheck が出す 1 件（SC の番号・水準・行） | — | — |
| 抑止の注記 | 指摘を抑える # shellcheck の指示と、抑える理由のコメントの組 | — | — |
| 基準の版 | 手元の基準（devbase-base:latest の shellcheck）と CI の ShellCheck の検査ジョブが揃える shellcheck の版。現在は 0.11.0 | — | — |
| ShellCheck の検査ジョブ | 検査ジョブのうち shellcheck ジョブ（bin/*・install.sh・containers/base/tmux-* を検査する） | — | — |

## テストの実行環境（`test`）

pytest が走るプロセスの環境と、テストが起動する外部のプロセス

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 環境の隔離 | pytest を起動したシェルから継承した環境変数を、テストごとに既定の状態（未設定か固定値）へ戻すこと | — | — |
| 隔離した tmux サーバ | TMUX を外し、専用のソケットで起動した試験用の tmux サーバ。利用者の tmux サーバに触れない | — | — |
