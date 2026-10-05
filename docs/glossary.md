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
| アカウントグループ | — | DEVBASE_ACCOUNT_GROUP の値。プロジェクトの env での宣言が必須で、既定の値を持たない。default は予約語。ボリューム devbase_home_<group> の単位でもある | — | — | `docs/specifications/secret-backend.md` |
| グループの宣言 | — | projects/<name>/env に書いた空でない DEVBASE_ACCOUNT_GROUP の行。プロジェクトの所属グループを決める唯一の出所（操作の対象は --group・TUI の選択でも明示できる） | — | — | `docs/specifications/secret-backend.md` |
| グループのボリューム | — | アカウントグループごとの devbase_home_<group>。コンテナの /persistent/group にマウントされる | — | — | `docs/specifications/secret-backend.md` |
| グループを決めるコマンド | — | 宣言か --group からグループを決めないと先へ進めないコマンド。up / scale / 機密のコマンド / snapshot create | — | — | `docs/specifications/secret-backend.md` |
| ボリュームの移行 | — | 旧既定のボリュームの中身を、利用者が指定したグループのボリュームへ写す操作。元を残す | — | — | `docs/specifications/secret-backend.md` |
| 旧既定のボリューム | — | この変更より前に、宣言の無いプロジェクトが使っていた devbase_home_default。移行の元で、スナップショットの系列にも残る | — | — | `docs/specifications/secret-backend.md` |
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
| 同期の書き込み先 | — | env sync がキーごとに選ぶ参照。--user なら個人共通、無ければキーが現にある参照（両方なら個人共通、どちらにも無ければ個人共通。個人単位の参照を持たない backend では常にチーム共通） | — | — | `docs/specifications/secret-backend.md` |
| 同期済みハッシュの控え | — | env sync がソースファイルの位置とハッシュを記録する .env.sources[.<g>].yml。キャッシュ（機密の控え）とは別のもの | — | — | `docs/specifications/secret-backend.md` |
| 取り込み | — | ホストの資格情報ファイルから読んだ値を、対象のグループの参照へ書くこと。利用者がキーボードで入れた値を書くことは含まない | — | — | `docs/specifications/secret-backend.md` |
| 取り込みの候補 | — | collector がホストで見つけた、取り込める値のまとまり。GCP は鍵ファイル 1 つ、AWS はプロファイル 1 つ、Git はホストの Git の設定の全体が 1 件 | — | — | `docs/specifications/secret-backend.md` |
| 取り込みの方針 | — | 対象のグループについて、取り込みの候補を「尋ねる」「取り込まない」「取り込む」のどれで扱うか。呼び出し側が決めて collector へ渡す | — | — | `docs/specifications/secret-backend.md` |
| 取り込みを許すグループの設定 | — | 取り込みの方針をグループごとに名指しする端末の設定。$DEVBASE_ROOT/secrets/host-import.yml に置く。名指しの無いグループは「尋ねる」 | — | — | `docs/specifications/secret-backend.md` |
| AWS のプロファイル | — | ~/.aws/config の [default] または [profile <名前>] の節。compose のプロファイルとは別のもの | — | — | `docs/specifications/secret-backend.md` |
| 取り込みの選択 | — | 取り込みの候補のうち、利用者が選んだもの。AWS は選んだプロファイルの名前の並び、GCP は参照に書いたプロファイル。同期済みハッシュの控えに残り、sync はこの範囲だけを入れ直す | — | — | `docs/specifications/secret-backend.md` |
| AWS の丸ごとの取り込み | — | ~/.aws/config と ~/.aws/credentials を切り出さずにそのまま 1 つの tar にして AWS_CONFIG_BASE64 へ書くこと。#314 より前の振る舞い | — | — | `docs/specifications/secret-backend.md` |
| AWS のプロファイルの連なり | — | 選んだ AWS のプロファイルが動くのに要る節。sso_session が指す [sso-session <名前>] と、source_profile が指すプロファイル（たどれる限り） | — | — | `docs/specifications/secret-backend.md` |
| グループ名の例 | — | エラー文・使い方の文言がグループ名の書き方として示す名前。lib/devbase/env/groups.py の定義 1 か所から出す | — | — | `docs/specifications/secret-backend.md` |
| プロファイル名の正規化 | — | GCP の鍵ファイル名の拡張子を除いた部分のうち [A-Za-z0-9_] 以外を _ へ置き換え、GCP のプロファイル名（GCP_CREDENTIALS_BASE64_<名前> の <名前>）にすること | — | — | `docs/specifications/secret-backend.md` |
| ホストの region | — | ホストの ~/.aws/config の、選んだ AWS のプロファイルの節にある region の値。方法 3（Access Key）は [default]、方法 2（SSO Profile）は利用者が入れたプロファイル | — | — | `docs/specifications/secret-backend.md` |
| region の出所 | — | 参照の AWS_DEFAULT_REGION へ書く値をどこから得たか。参照に既にある・ホストの region・利用者の入力・既定の値（ap-northeast-1）の 4 つ | — | — | `docs/specifications/secret-backend.md` |
| 鍵の確認の結果 | — | AWS の方法 3 で、ホストの [default] の鍵を取り込むかを決めた結果。受け入れた・断った・確認しなかった（ホストに鍵が無い）の 3 つ。方針が import なら受け入れた。skip ならホストに鍵があれば断った、無ければ確認しなかったになる | — | — | `docs/specifications/secret-backend.md` |
| 鍵ファイルの発見 | — | ~/gcp-credentials/ の *.json を読み、正規化したプロファイル名から鍵ファイルへの対応を作ること。出力を持たない | — | — | `docs/specifications/secret-backend.md` |

## スナップショット（`snapshot`）

ホームのボリュームの世代と系列（docs/specifications/snapshot-series.md）

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 世代 | — | backups/<名前>/ の 1 つ。full.tar.zst 1 つと、0 個以上の incr-NNN.tar.zst からなる | — | — | `docs/specifications/snapshot-series.md` |
| 対象ボリュームの組 | — | snapshot.yml のエントリの volumes（マウント名 → ボリューム名） | — | — | `docs/specifications/snapshot-series.md` |
| 系列 | — | 対象ボリュームの組が同じ世代の集まり | — | — | `docs/specifications/snapshot-series.md` |
| 系列の最新の世代 | — | 系列の中で created_at が最も新しい世代 | — | — | `docs/specifications/snapshot-series.md` |
| 全体の上限 | — | 系列をまたいで数えた世代の数の上限（max_total） | — | — | `docs/specifications/snapshot-series.md` |
| 復元前バックアップ | — | restore が書き戻す前に自動で作るフルの世代 pre-restore-<時刻>。復元する世代の対象ボリュームの組を控える | — | — | `docs/specifications/snapshot-series.md` |

## base イメージの描画（`base-image`）

base イメージのフォントの解決（docs/specifications/base-image-rendering.md）

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 総称ファミリ | — | sans-serif / sans / serif / monospace。具体の書体名ではなく様式を指す指定 | — | — | `docs/specifications/base-image-rendering.md` |
| metric 互換 | — | 字幅・行送りが元の書体と一致する代替の書体 | — | — | `docs/specifications/base-image-rendering.md` |
| 受け皿 | — | イメージに実在しない書体名を指定されたときに末尾へ足すフェイス | — | — | `docs/specifications/base-image-rendering.md` |
| 追加の太さのフォント | — | fonts-noto-cjk-extra が入れる Noto CJK の Thin・Light・Medium・Black などのフェイス。標準の太さ（Regular・Bold）は fonts-noto-cjk にある | — | — | `docs/specifications/base-image-rendering.md` |

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
| 検査ジョブ | — | ci.yml の jobs の 1 つ（Python syntax check・Ruff lint・ShellCheck・Pytest の各版・まとめたチェックのジョブ pytest-all・CHANGELOG check・Proper term check） | — | — | `docs/specifications/ci-checks.md` |
| 統合ブランチ | — | 課題の Pull Request の宛先になる main 以外のブランチ（release/** と mission/**） | — | — | `docs/specifications/ci-checks.md` |
| 必須チェック | — | main の保護設定の required_status_checks に並ぶチェックの名前。すべてが合格しないと main へマージできない | — | — | `docs/specifications/ci-checks.md` |
| まとめたチェック | — | pytest の matrix の全版の結果を 1 つにまとめ、版に依存しない名前 Pytest で出るチェック。全版が成功したときだけ成功する | — | — | `docs/specifications/ci-checks.md` |
| トリガー | — | ci.yml の on: に書く 1 つのイベント（pull_request / push）と、その絞り込み（branches） | — | — | `docs/specifications/ci-checks.md` |
| 積み重ねた Pull Request | — | 宛先が main でも統合ブランチでもない、別の作業ブランチの Pull Request | — | — | `docs/specifications/ci-checks.md` |
| 指摘 | — | shellcheck が出す 1 件（SC の番号・水準・行） | — | — | `docs/specifications/ci-checks.md` |
| 抑止の注記 | — | 指摘を抑える shellcheck の指示（disable= / source=）と、抑える理由のコメントの組 | — | — | `docs/specifications/ci-checks.md` |
| 基準の版 | — | 手元の基準（devbase-base:latest の shellcheck）と CI の ShellCheck の検査ジョブが揃える shellcheck の版。現在は 0.11.0 | — | — | `docs/specifications/ci-checks.md` |
| ShellCheck の検査ジョブ | — | 検査ジョブのうち shellcheck ジョブ（bin/*・install.sh・containers/base のシェルスクリプトを検査する） | — | — | `docs/specifications/ci-checks.md` |
| base のシェルスクリプト | — | containers/base/ の直下の通常のファイルのうち、先頭行が sh か bash を指す shebang か、名前が .sh で終わるもの | — | — | `docs/specifications/ci-checks.md` |
| 検査の対象 | — | CI の ShellCheck ジョブで、引数に containers/base/ のパスを持つ shellcheck の行が並べたファイル | — | — | `docs/specifications/ci-checks.md` |
| shellcheck の指示 | — | # shellcheck で始まるコメント（disable= / source= / shell=）。disable= と source= は抑止の注記の指示の側で、shell= は指摘を抑えない | — | — | `docs/specifications/ci-checks.md` |
| 利用者に見える変更 | — | devbase を使う人がコマンド・イメージ・設定を通じて気づく振る舞いの変化。テストだけ・開発者向けの文書だけの変更は含まない | — | — | `docs/specifications/ci-checks.md` |
| 見張るパス | — | CHANGELOG の検査が利用者に見える変更の手がかりとして見る場所（lib/・bin/・containers/・install.sh・etc/） | — | — | `docs/specifications/ci-checks.md` |
| CHANGELOG の検査 | — | main 宛ての Pull Request で、見張るパスを変えて CHANGELOG.md を変えていないときに未記入の警告を出す検査ジョブ | — | — | `docs/specifications/ci-checks.md` |
| 未記入の警告 | — | CHANGELOG の検査が出す GitHub Actions の警告の注記。ジョブを失敗にしない | — | — | `docs/specifications/ci-checks.md` |
| 固有の語 | — | 作成者の所属組織・顧客・社内プロダクト・個人に固有の名前。公開のリポジトリの文書・コード・テストに置かない | — | — | `docs/specifications/ci-checks.md` |
| 語の一覧 | — | 固有の語の検査が探す固有の語を 1 行 1 語で並べた平文。CI では secret、手元では追跡されない置き場に置き、公開のファイルに書かない | — | — | `docs/specifications/ci-checks.md` |
| 固有の語の検査 | — | 語の一覧を読み、追跡されたファイルから固有の語を含む行を探すスクリプトと、それを Pull Request で走らせる CI のジョブ | — | — | `docs/specifications/ci-checks.md` |
| 当たり | — | 固有の語の検査が見つけた、語を含む行。パスと行番号の組で示し、行の本文と語を出さない | — | — | `docs/specifications/ci-checks.md` |
| 例外の位置 | — | 固有の語の検査が当たりにしない行。パスと行の形の組で定め、語を含まない | — | — | `docs/specifications/ci-checks.md` |

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
| ラッパーの複製 | — | exec_wrapper が一時ディレクトリの bin/devbase へ写した本物のラッパー。複製した位置から DEVBASE_ROOT が決まり、継承した値を見ない | — | — | `docs/specifications/cli-argument-resolution.md` |
| 偽の uv | — | PATH の先頭に置き、受け取った引数を UV: 行で出して 0 で終わる試験用の uv。ラッパーの外へ出る呼び出しはすべてこれを通る | — | — | `docs/specifications/cli-argument-resolution.md` |
| 経路の印 | — | build の振り分け先を見分ける出力の行。Python の経路は「 devbase.cli project build <引数>」で終わる UV: 行、shell の経路は === Building devbase images === の行 | — | — | `docs/specifications/cli-argument-resolution.md` |
| 差し替え先 | — | テストが monkeypatch で置き換える属性を持つモジュール。置き換える関数を実際に呼ぶモジュールにする | — | — | `docs/specifications/cli-argument-resolution.md` |

## イメージの継承（`image-lineage`）

base の設定が派生イメージへ届く道筋（containers/*/Dockerfile）

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 派生イメージ | — | FROM devbase-base:latest で base を継ぎ、道具を足すイメージ（containers/ の general・php など） | — | — | `docs/specifications/base-image-contents.md` |
| DinD | — | コンテナの中で dockerd を起こし、そのコンテナの中だけの docker を使う形。#400 で廃止し、ENABLE_DIND は知らせを 1 行出すだけになった。ホストの docker.sock を mount して使う形と区別する | — | — | `docs/specifications/base-image-contents.md` |
| 展開後の大きさ | — | 建てたイメージから作ったコンテナで du -sbx / を測ったバイト数。docker images の圧縮後の大きさとは違う | — | — | `docs/specifications/base-image-contents.md` |
| 文書の除外 | — | dpkg の path-exclude の設定で、apt が入れるパッケージから文書などの置き場を展開しないこと。base は Ubuntu の excludes に加え、changelog・info・/usr/include/node を外す（copyright は残す）。base を FROM で継ぐ派生イメージにも効く | — | — | `docs/specifications/base-image-contents.md` |
| 継承の連なり | — | プロジェクトの Dockerfile が FROM に取る devbase-* から、containers/<名前>/Dockerfile の FROM devbase-* を順にたどり、devbase-* を FROM に取らない段（今は devbase-base）で終わるイメージの並び | — | — | `docs/specifications/base-image-chain-build.md` |
| 直の親イメージ | — | プロジェクトか containers/<名前> の Dockerfile の中で、最初に devbase-* を指す FROM の行が名指すイメージ。devbase-<名前>:<タグ> の形で、タグが無ければ latest を補う | — | — | `docs/specifications/base-image-chain-build.md` |
| 直の親の読み方 | — | Dockerfile の 1 行から直の親イメージを読む規則。正規表現の値を lib/devbase/utils/dockerfile.py が正本として持ち、bin/devbase が同じ値を写す | — | — | `docs/specifications/base-image-chain-build.md` |
| プロジェクトの Dockerfile | — | プロジェクトの compose の構成で、開発サービス名のサービスの build（context と dockerfile。文字列の形は context だけ）が指す Dockerfile。開発サービスが build を持たないときは無い | — | — | — |
| Dockerfile の場所の決め方 | — | compose の構成からプロジェクトの Dockerfile のパスを決める規則。通常のビルドと --expires の判定が同じ規則を使う | — | — | — |

## コンテナの起動（`container-start`）

base イメージの entrypoint が起動のたびに行う用意（containers/base/entrypoint.sh）

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 共通のボリューム | — | すべてのコンテナが /persistent/ai にマウントする 1 つのボリューム。グループをまたいで共有する | — | — | `docs/specifications/container-link-stage.md` |
| 共有のリンク | — | グループのボリュームの .claude の下にあり、共通のボリュームの同じ名前のエントリを指す symlink。plugins / skills / commands / CLAUDE.md / settings.json の 5 本。同じグループのすべてのコンテナが同じ 1 本を使う | — | — | `docs/specifications/container-link-stage.md` |
| リンクの段 | — | entrypoint が AI 設定のリンクを張る処理（devbase_setup_ai_settings の 1 回の実行） | — | — | `docs/specifications/container-link-stage.md` |
| 同時起動 | — | 2 つ以上のコンテナのリンクの段が、時間の上で重なって走ること | — | — | `docs/specifications/container-link-stage.md` |
| 修正前の手順 | — | #357 より前のリンクの張り方。「正しいかを見る → 違えば消す → ln -s」を、ほかのコンテナを待たずに行う | — | — | `docs/specifications/container-link-stage.md` |
| リンクの段のロック | — | 共通のボリュームのルートのディレクトリに対して取る排他のロック。修正後のリンクの段どうしを 1 つずつ順に走らせる。ボリュームにファイルを作らない | — | — | `docs/specifications/container-link-stage.md` |
| ロックの待ち時間の上限 | — | リンクの段のロックを待つ秒数の上限（10 秒）。超えたら警告を出してロックなしで進む | — | — | `docs/specifications/container-link-stage.md` |
| 張り替えの試行 | — | リンクを「消す → 張る → 読み直して確かめる」1 回ぶん。正しくなるまで試行の上限（20 回）まで繰り返し、張る操作の成否ではなく読み直した結果で成功を決める | — | — | `docs/specifications/container-link-stage.md` |
| 入れ子のリンク | — | リンクの位置がディレクトリを指すリンクになっているときに、張る操作がその先のディレクトリの中へ作るリンク（例: 共通のボリュームの plugins/plugins）。修正前の手順が同時起動で作ることがある | — | — | `docs/specifications/container-link-stage.md` |
| 排他の作成 | — | エントリを「無いときだけ作る」操作。同時に呼んでも作れるのは 1 つのプロセスだけで、作れたプロセスだけがそのエントリの中身を書く。退避とプレースホルダが使う | — | — | `docs/specifications/container-link-stage.md` |
| 置き換わった共有のリンク | — | 共有のリンクの位置にある、symlink ではない通常のファイル。コンテナの中のツールが一時ファイルの rename で settings.json へ書くと、共有のリンクがこれに置き換わる | — | — | `docs/specifications/container-link-stage.md` |
| 共有のリンクの控え | — | 置き換わった共有のリンクの中身が共通のボリュームの実体と違うとき、リンクの段が張り直す前にグループのボリュームの同じ .claude の下へ別の名前で残したファイル。自動では消さず、共通のボリュームへも取り込まない | — | — | `docs/specifications/container-link-stage.md` |

## 起動の後の処理（`post-start`）

devbase up / scale がコンテナを起動して待ち、その後にホストの側から行う処理（lib/devbase/commands/container.py）

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 起動の後の処理 | — | up と scale が、起動の待ちの後にホストの側から行う処理の並び。不足リポジトリの報告・./deploy の実行・token の配布・窓のタイトルの設定・エディタの自動オープンの 5 つ | — | — | `docs/specifications/post-start.md` |
| インスタンスごとの処理 | — | 起動の後の処理のうち、インスタンスの 1 つ 1 つに行う 4 つ（エディタの自動オープンを除いたもの） | — | — | `docs/specifications/post-start.md` |
| 起動の待ち | — | up と scale が、起動したインスタンスの entrypoint の完了を、制限時間まで待つ段 | — | — | `docs/specifications/post-start.md` |
| 起動できたインスタンス | — | 起動の待ちで、entrypoint の完了を確かめられたインスタンス | — | — | `docs/specifications/post-start.md` |
| 起動できなかったインスタンス | — | 起動の待ちの間に終了した・見つからない・制限時間までに entrypoint の完了を確かめられなかった、のどれかに当たるインスタンス | — | — | `docs/specifications/post-start.md` |
| 増やしたインスタンス | — | devbase scale が足した番号のインスタンス（前の scale の値 + 1 から、新しい scale の値まで） | — | — | `docs/specifications/post-start.md` |
| 後処理の対象 | — | インスタンスごとの処理を行うインスタンスの集合。up は起動できたインスタンス、scale は増やしたインスタンスのうち起動できたもの、やり直しは動いていて entrypoint の完了の印を確かめられたインスタンス | — | — | `docs/specifications/post-start.md` |
| 起動の結果 | — | 起動の待ちが決めた、インスタンスごとの「起動できた」「起動できなかった（理由つき）」の内訳 | — | — | `docs/specifications/post-start.md` |
| 起動できなかった理由 | — | 起動できなかったインスタンスに 1 つ付く区分。終了した・見つからない・時間切れの 3 つ | — | — | `docs/specifications/post-start.md` |
| 起動の後の処理の段 | — | 後処理の対象を受けて、インスタンスごとの処理を決まった順に行う 1 つの関数 | — | — | `docs/specifications/post-start.md` |
| 起動の後の処理のやり直し | — | 動いていて entrypoint の完了の印を確かめられたインスタンスへ、インスタンスごとの処理から ./deploy を除いた 3 つを行い直すコマンド（devbase project post-start） | — | — | `docs/specifications/post-start.md` |

## ブラウザ（`browser`）

ブラウザの派生イメージに入るブラウザと、base が作るその置き場（containers/base/Dockerfile / containers/browser/Dockerfile）

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| ブラウザの置き場 | — | Playwright がブラウザを取得して置くディレクトリ。環境変数 PLAYWRIGHT_BROWSERS_PATH が指す /opt/ms-playwright。base は空のまま作り、ブラウザの派生イメージが中身を持つ。コンテナの利用者が書き込める | — | — | `docs/specifications/base-image-rendering.md` |
| Playwright の Chromium | — | playwright install chromium がブラウザの置き場へ取得する Chromium（版ごとのディレクトリ）。ブラウザの派生イメージに入る。amd64 と arm64 の両方で取れる | — | — | `docs/specifications/base-image-rendering.md` |
| システムの Chrome | — | apt で入る google-chrome-stable。/usr/bin/google-chrome にあり、Playwright を介さずに使う道具が呼ぶ。base にはどちらのアーキにも入れない（#401） | — | — | `docs/specifications/base-image-rendering.md` |
| snap スタブ | — | Ubuntu の chromium-browser パッケージ。中身は chromium の snap を入れる案内だけで、コンテナの中では Chromium として起動しない | — | — | `docs/specifications/base-image-rendering.md` |
| ブラウザの依存パッケージ | — | playwright install の --with-deps が apt で入れるパッケージ。共有ライブラリ（libnss3 など）とフォント（fonts-liberation・fonts-ipafont-gothic・fonts-wqy-zenhei など）を含む。base は、このうち fonts-liberation・fonts-ipafont-gothic・fonts-wqy-zenhei・libnss3 などを明示して持つ | — | — | `docs/specifications/base-image-rendering.md` |
| ブラウザの派生イメージ | — | containers/browser（タグ devbase-browser:latest）。base を継ぎ、Playwright の Chromium・ブラウザの依存パッケージ・追加の太さのフォントを足す | — | — | `docs/specifications/base-image-rendering.md` |
