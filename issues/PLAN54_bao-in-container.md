# PLAN54: base イメージに `bao` を入れ、起動中のコンテナから機密を取得・変更できるようにする

- 発端: #169
- ワークフローモード: `standard`
  - 根拠: base イメージ（`containers/base`）と、コンテナへ接続情報を渡す起動経路
    （`lib/devbase`）の 2 領域にまたがる本番の振る舞いの追加。公開インタフェース
    （コンテナ内で使える環境変数）が増える
- 閉じる課題: #169

## 依頼（原文）

> base イメージに OpenBao の CLI（`bao`）を入れる。そのうえで、**起動中のコンテナから、再起動せずに機密の変数を取得・変更できる**ようにする。

> | # | 内容 |
> | --- | --- |
> | 1 | base イメージに `bao` を入れる。版はサーバと同じ 2.6 系で固定する（2026-09-13 時点の最新は 2.6.2） |
> | 2 | 起動中のコンテナから、再起動せずに自分の機密を取得できる |
> | 3 | 起動中のコンテナから、再起動せずに自分の機密を変更できる（キーの追加・値の変更・キーの削除） |

> 管理者の作業（利用者の登録、端末の資格情報の発行と失効）も、base コンテナの `bao` で行う前提にする。サーバ側の管理スクリプトは `bao` を使う。

コメント（2026-09-14）:

> 着手時は「手元キャッシュとの整合」の論点を、確定仕様の「控えは読み取り専用（set / delete / edit は fetch で現物を読む）」の規則と突き合わせること。

## 目的

- 起動中の dev コンテナの中で `bao` が使え、再起動せずに自分の機密（`users/<user>/…`）を
  読み書きできる
- サーバと同じ 2.6 系の `bao` を、amd64 / arm64 の両方で、検証付きで入れる
- 管理者が base コンテナから `bao` で管理操作（carmo-cdk の `openbao-admin.sh`）を行える

## 前提

- 前提 1: backend が `openbao` でない端末でも base イメージに `bao` を入れる。サーバ側の
  運用リポジトリの管理スクリプトが `bao` に依存するためである。設定で入れ分けると、管理者の
  端末で `bao` が無い状態が起きる。
  成否の判定: `containers/base/Dockerfile` に `bao` の導入が条件なしで書かれている
- 前提 2: 版は `2.6.2` に固定し、Dockerfile の `ARG` で持つ。GitHub Releases の
  `checksums.txt` で検証する（署名の検証はしない。base イメージの他のツールも署名は
  見ていない）。成否の判定: 版を変えるのが `ARG` 1 行で済む
- 前提 3: コンテナの中で `bao` が接続に使うのは `BAO_ADDR`（環境変数）と token の 2 つで、
  `secrets/backend.yml` を読ませない（コンテナは `$DEVBASE_ROOT` を持たない）。
  ~~token も環境変数 `BAO_TOKEN`~~ → token はファイル `~/.vault-token` に置く（2026-09-14、
  設計の決定 1。環境変数だと `docker inspect` と子プロセスに残るため）。
  成否の判定: コンテナ内で `env | grep ^BAO_` が `BAO_ADDR` だけを返し、`~/.vault-token` が `0600` で存在する
- 前提 4: token は 1 時間で切れる（サーバの `token_max_ttl=1h`）。起動中のコンテナから
  取り直す手段が要る。**どの資格情報でどう取り直すか**は設計工程で決める（下の未決）。
  成否の判定: 設計文書の「決定の記録」にこの決定がある
- 前提 5: コンテナの `bao` で書き込んだ変更は、ホスト側の控え（`secrets/cache/`）には
  反映しない。控えは読み取り専用で、`set` / `delete` / `edit` は `fetch` で現物を読む
  （`docs/specifications/secret-backend.md`「失敗の種類とキャッシュ」）。次の `devbase up` が
  現物を読めば整合する。古い値になるのは不達時に控えから起動したときだけで、その旨は既存の
  警告が出る。成否の判定: ホスト側のコードにコンテナからの通知を受ける経路が無い
- 前提 6: 起動済みのプロセスの環境変数は変えられない。コンテナの中での「変更」は
  OpenBao 側の値の変更を指し、シェルへ読み直す手段は**案内**（`docs/user/`）で示す
  （例: `export KEY="$(bao kv get -field=KEY …)"`）。成否の判定: 起動中のプロセスの
  環境を書き換える仕組みを作らない
- 前提 7: backend が `openbao` でない端末（`age` / `plaintext`）では、~~`BAO_ADDR` /
  `BAO_TOKEN`~~ → `BAO_ADDR`（環境変数）と token（`~/.vault-token`）をコンテナへ渡さず
  （2026-09-14、決定 1 に揃えた）、それ以外の振る舞いは変えない（PLAN51 の前提 3）

## 対象範囲

含む:

- `containers/base/Dockerfile` への `bao` 2.6.2 の導入（amd64 / arm64、checksums.txt で検証）
- backend が `openbao` のとき、`devbase up` がコンテナへ `BAO_ADDR`（環境変数）と、有効な token を
  書いた `~/.vault-token` を渡すこと（~~`BAO_ADDR` と有効な `BAO_TOKEN`~~ 2026-09-14、決定 1 に揃えた）
- 起動中のコンテナから token を取り直す手段（方式は設計で決める）
- 利用者への案内（`docs/user/env-backend.md`）: パスの形、`kv put` と `kv patch` の違い、
  シェルへの読み直し、token の期限と取り直し
- `docs/specifications/secret-backend.md` への確定仕様の追記（`plan-to-spec`）

含まない:

- 起動中のプロセスの環境変数を書き換えること
- コンテナからの書き込みをホスト側の控えへ反映すること（前提 5）
- チーム共通（`team/…`）への書き込み（サーバのポリシーが `devbase-team-writer` に限る。
  `bao` はそのまま使えるため devbase 側で作るものは無い）
- `devbase up` の 2 度注入の解消（#168、PLAN55）
- 管理スクリプト（carmo-cdk `bin/openbao-admin.sh`）の変更

## 用語

| 用語 | 意味 |
| --- | --- |
| `bao` | OpenBao の CLI。接続先は環境変数 `BAO_ADDR` から、token は環境変数 `BAO_TOKEN` が無ければ `~/.vault-token` から読む |
| token | AppRole ログインで得る `client_token`。TTL 1 時間、延長不可 |
| 端末の資格情報 | AppRole の `role_id` / `secret_id`。ホストの `secrets/bootstrap.env.age` にある |
| 控え | ホスト側 `secrets/cache/` の age 暗号化キャッシュ。読み取り専用 |

## 受け入れ条件

- [ ] `devbase build`（base イメージ）の後、`docker run --rm devbase-base:latest bao version` が
      `OpenBao v2.6.2` を含む行を出す。amd64 と arm64 のどちらのホストでも同じ
- [ ] ダウンロードした tar.gz の SHA-256 が `checksums.txt` と一致しないとき、イメージの
      ビルドが失敗する（`Dockerfile` の該当行を読み、`sha256sum -c` 相当の検証があること
      で判定する）
- [ ] 前提: backend が `openbao` で `devbase env backend test` が通る
      操作: `devbase up` の後に `docker exec <dev> bash -lc 'bao kv get -mount=devbase -field=<KEY> users/<user>/global'`
      結果: ホストの `devbase env get --user <KEY>` と同じ値を返す
- [ ] 前提: 同上
      操作: コンテナ内で `bao kv patch -mount=devbase users/<user>/global NEW_KEY=v1`、
      続けてホストで `devbase env get --user NEW_KEY`
      結果: `v1`（ホスト側は控えではなく現物を読む）
- [ ] 前提: 同上
      操作: コンテナ内で `bao kv get -mount=devbase team/global` と
      `bao kv put -mount=devbase team/global X=1`
      結果: 前者は成功、後者は 403 で終了コード 2（devbase 側で何も作らない。サーバの
      ポリシーの確認）
- [ ] 前提: コンテナ起動から 1 時間以上経ち、~~`BAO_TOKEN`~~ → `~/.vault-token` の token が
      切れている（2026-09-14、決定 1 に揃えた）
      操作: 設計で決めた取り直しの手段を実行してから `bao kv get …`
      結果: 再起動せずに値を読める
- [ ] backend が `age` の端末で `devbase up` したとき、コンテナの `env` に `BAO_ADDR` が無く、
      `~/.vault-token` が作られず、生成される `.docker-compose.scale.yml` が変更前と同じ
- [ ] token の値がログ（`devbase --verbose up` の出力）と `.docker-compose.scale.yml` と
      `docker inspect` の `Env` に書かれない（~~compose には変数名だけを列挙する~~ →
      token は `docker exec` の stdin で渡す。2026-09-14、設計の決定 1）
- [ ] `uv run pytest tests/` が全件通り、`ruff check lib` と `shellcheck` が変更前と同じ結果
- [ ] `tests/containers/` に `Dockerfile` の `bao` の導入行を固定するテストがある
      （版・アーキテクチャ・検証の 3 点）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| セキュリティ | `secret_id` の置き場所を広げない方式を既定とする（設計で判断し、広げる方式を採るなら「決定の記録」に理由を書く）。token は compose ファイルへ書かない。`bao` の導入はチェックサムで検証する |
| 運用・保守性 | 版の更新が `ARG` 1 行。token 切れのときの症状（403）と対処を `docs/user/` に書く |
| システム環境 | amd64 / arm64 の両方でビルドできる。backend が `openbao` でない端末は影響を受けない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | コンテナ内の環境変数 `BAO_ADDR` とファイル `~/.vault-token` が増える（backend が `openbao` のときだけ）。ホストのコマンド `devbase env token` が増える |
| データ | スキーマ変更なし。`secrets/backend.yml` の項目が増えるかは設計で決める |
| 既存の振る舞い | base イメージに `bao` が入る（全端末）。`openbao` の端末では `devbase up` がコンテナへ環境変数 `BAO_ADDR` を追加で渡し、`~/.vault-token` を書く |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `devbase build` → `devbase up <project>` |
| テスト | `uv run pytest tests/` |
| 静的解析 | `ruff check lib`、`shellcheck --severity=error bin/devbase install.sh`、`python -m compileall -q lib bin` |
| 手動確認 | 利用者の端末（backend `openbao`、PLAN53 の後）で受け入れ条件 3〜6 を実行する。リリース後テストの工程で行う |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `docs/developer/architecture.md`。base イメージは `containers/base/`、起動経路は `lib/devbase/commands/container.py` と `lib/devbase/project/runtime.py`（`container_env`）、OpenBao の HTTP は `lib/devbase/env/openbao.py` |
| コーディング規約 | `docs/developer/contributing.md`。CI は `compileall` / `ruff` / `shellcheck` |
| テスト戦略 | `tests/containers/` で Dockerfile とエントリポイントの文言を固定、`tests/env/test_openbao.py` の偽サーバで HTTP の往復を固定、`tests/commands/` で `up` が渡す環境変数を固定。実サーバへの接続は手動確認 |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、`ruff`、`shellcheck`。`bao` の版は `ARG` で持つ |
| 確認してから行う | 端末の `secret_id` をコンテナへ渡す方式の採用。`secrets/backend.yml` の項目の追加。設計 Pull Request のマージ |
| 行わない | 起動中のプロセスの環境変数の書き換え。ホスト側の控えへの反映。管理スクリプトの変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| ~~token を取り直す手段~~ → 決まった: ホストの `devbase env token` が起動中のコンテナの `~/.vault-token` を書き換える（設計の決定 2。(b) `secret_id` をコンテナへ渡す案と (c) OIDC 案は採らない） | 設計 Pull Request のマージで利用者が承認する | 設計 |

## 実装計画

設計は `issues/PLAN54_bao-in-container-design.md`（マージ済み #175）。PLAN55 (#177) が先に入ったため、
`_push_bao_token` の token は `runtime.store_for(root)`（注入と同じ `SecretStore`）から取る
（設計「処理の流れ」の表「PLAN55 の後」）。

| Task | 対象ファイル | 変更内容 | 満たす受け入れ条件 | 進め方 |
| --- | --- | --- | --- | --- |
| 1 | `containers/base/Dockerfile`、`tests/containers/test_base_dockerfile_bao.py` | `ARG BAO_VERSION=2.6.2`、tar.gz + `checksums.txt` を取得し `sha256sum -c`、`bao` だけを `/usr/local/bin` へ | 1・2・10 | 文言を固定するテスト → Dockerfile。実ビルドは手で 1 度 |
| 2 | `lib/devbase/env/openbao.py`、`tests/env/test_openbao.py` | `issue_token()`（期限内なら再ログインしない） | 6 の土台 | 偽サーバで login 回数を固定 → 実装 |
| 3 | `lib/devbase/env/container_token.py`、`tests/env/test_container_token.py` | `push(names, token, runner=)`: `docker exec -i` + `mktemp` → `mv -f`、token は stdin のみ | 8 | runner のスタブで argv / input / 文言を固定 → 実装 |
| 4 | `lib/devbase/commands/container.py`、`tests/commands/test_container_bao.py` | openbao のとき `dev_environment` に `BAO_ADDR`、[5/6] の後に `_push_bao_token`（失敗は警告） | 7 | up の harness で compose 引数と docker exec の有無を固定 → 実装 |
| 5 | `lib/devbase/commands/env.py`、`lib/devbase/cli.py`、`tests/commands/test_env_token.py` | `devbase env token [--print] [--context NAME]`、`SUBCMD_MAP`、`_NO_SECRET_INJECTION` | 6 | 設計の状況表の行ごとにテスト → 実装 |
| 6 | `docs/user/env-backend.md` | 「コンテナの中から `bao` を使う」の節 | F4 | 文書 |

設計からの追加（実装で決めたこと）: `cmd_scale` も構成を作り直すため、`up` と同じく `BAO_ADDR` を足し、
増やしたインスタンス（`current_scale + 1`〜）へ token を書く（2026-09-14。設計は `up` だけを挙げていたが、
`scale` で増えたコンテナに `BAO_ADDR` と token が無い状態を作らないため）。

リスク: `container.py` は 1300 行超。触るのは `_run_deploy_pipeline` と `cmd_up` の後処理の数行に限る。
切り戻し: 差分を戻すだけ（永続データなし）。イメージは再ビルドで元に戻る。
