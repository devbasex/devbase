# PLAN55: `devbase up` の機密の注入を 1 回にし、サーバ backend の往復を設計の想定へ収める

- 発端: #168
- ワークフローモード: `standard`
  - 根拠: `devbase up` の起動経路（`lib/devbase/cli.py` / `lib/devbase/commands/container.py`）
    の振る舞いの変更。公開インタフェースは変えない。対象には `tests/cli/test_secret_injection.py`
    / `tests/cli/test_project_name_resolution.py` / `tests/commands/test_container_up_order.py`
    / `tests/env/test_openbao.py` がある
- 閉じる課題: #168

## 依頼（原文）

> `devbase up` は次の 2 か所で機密を注入する。
>
> 1. `cli._load_secret_env()` — dispatch の前に現在地のプロジェクトの機密を `runtime.inject()` で載せる
> 2. `commands/container._inject_secrets()` — プロジェクト解決の後に `clear_injected()` → `runtime.inject()` で載せ直す
>
> さらに `_ensure_env_files()` が `SecretStore.exists()` を 2 回呼ぶ。ファイル backend ではファイルの存在確認なので無視できるが、サーバ backend では `SecretStore` インスタンスごとに認証 + 参照ごとの GET が走るため、1 回の `up` で認証 2 回 + GET 10 回程度になる。
>
> 仕様（`docs/specifications/secret-backend.md` の「OpenBao との契約」）は「`devbase up` 1 回あたり認証 1 回 + 参照ごとに 1 回」を想定しており、`runtime.resolve()` 単位ではその回数に収まっているが（`tests/env/test_openbao.py` で固定）、CLI 全体としては超えている。
>
> ## 案
>
> - `_load_secret_env` の注入結果（`SecretEnv` と `SecretStore`）を dispatch 先へ渡し、プロジェクトが変わらないなら載せ直さない
> - `_ensure_env_files` は注入済みの `SecretEnv` から存在を判定する

## 目的

- `devbase up`（プロジェクト内から・`up <name>`・`project up <name>` の 3 経路）1 回の
  サーバへの往復を、仕様の「認証 1 回 + 参照ごとに 1 回（プロジェクト指定ありで 4 回）」に
  収める
- 注入の結果（環境変数へ載る値）と、プロジェクト切替時に切替元の機密が残らない性質を変えない

## 現状の往復（調査で確定した事実）

`up <name>` の経路で `SecretStore` が作られる箇所と、それぞれの往復:

| 箇所 | 何をするか | 認証 | GET |
| --- | --- | --- | --- |
| `cli._load_secret_env` | 現在地のプロジェクトで `runtime.inject` | 1 | 2 または 4 |
| `container._dispatch_lifecycle`（name 指定時） | `clear_injected` → `_inject_secrets` | 1 | 4 |
| `container._ensure_env_files` | `SecretStore.exists` × 2（同じインスタンス内なので GET は参照ごとに 1 回） | 1 | 2 |
| `container._run_deploy_pipeline` | `_inject_secrets(required=True)` | 1 | 4 |

合計: 認証 4 回、GET 12〜14 回（`_ensure_env_files` は控えのある参照でも GET する）。

## 前提

- 前提 1: 同じプロセスの中で、同じ `devbase_root` と同じプロジェクト名に対する解決結果は
  1 回の `runtime.resolve()` で足りる（`up` の途中で他の誰かがサーバ側を書き換えても、
  その `up` は最初に読んだ値で起動する。従来の 2 度注入でも途中で値が変わる保証は無かった）。
  成否の判定: 受け入れ条件 1 の回数
- 前提 2: プロジェクトが切り替わったとき（`up <name>` を別のプロジェクトの中から打つ）は、
  切替先で改めて解決する。このとき認証はプロセスで 1 回のまま（`SecretStore` を使い回す）
  でよい。成否の判定: 受け入れ条件 2
- 前提 3: ~~`_ensure_env_files` の存在判定は注入済みの結果で置き換える~~ → 存在判定の意味は
  変えず、注入と同じ `SecretStore` を使って往復だけを無くす（2026-09-14、設計の決定 4。
  `SecretEnv` で判定すると age の空ファイルの扱いが変わる）。
  成否の判定: 受け入れ条件 4
- 前提 4: ファイル backend（`age` / `plaintext`）の振る舞いと結果は変えない（PLAN51 前提 3）。
  成否の判定: 受け入れ条件 5

## 対象範囲

含む:

- `cli._load_secret_env` → `container` の各経路で `SecretStore` と解決結果を引き継ぐ仕組み
- ~~`_ensure_env_files` の存在判定を注入済みの結果で行うこと~~ → `_ensure_env_files` の
  存在判定で注入と同じ `SecretStore`（`runtime.store_for`）を使い、往復を無くすこと
  （判定の意味は変えない。2026-09-14、前提 3・設計の決定 4 に揃えた）
- `up` 1 回の往復回数を偽サーバで固定するテスト
- `docs/specifications/secret-backend.md`「OpenBao との契約」の該当箇所の追記（`plan-to-spec`）

含まない:

- `down` / `logs` / `ps` など `required=False` の経路の往復（`up` ほど多くない。数えて
  仕様を超えていれば範囲外として起票する）
- `runtime.resolve()` の重ね順・`SecretStore` の HTTP の契約の変更
- TUI（1 プロセスで複数の操作を続ける経路）の往復。`_dispatch_lifecycle` の入口で
  `docker_context.reset()` と同じ扱いにするかは設計で決めるが、TUI の往復数は条件にしない
- コンテナへの `bao` の導入（#169、PLAN54）

## 用語

| 用語 | 意味 |
| --- | --- |
| 注入 | `runtime.inject()` で `os.environ` に機密を載せること |
| 往復 | OpenBao への HTTP 要求 1 回。認証（`POST …/login`）と取得（`GET`）を分けて数える |
| 3 経路 | `devbase up`（プロジェクト内）/ `devbase up <name>` / `devbase project up <name>` |

## 受け入れ条件

- [ ] 前提: backend が `openbao`（偽サーバ）で、プロジェクト `web` の中から実行する
      操作: `devbase up`（docker の呼び出しは差し替える）
      結果: 偽サーバへの認証が 1 回、GET が 4 回（`team/global` / `team/projects/web` /
      `users/<user>/global` / `users/<user>/projects/web` が各 1 回）
- [ ] 前提: プロジェクト `api` の中から実行する
      操作: `devbase up web`
      結果: 認証 1 回。GET は 6 回以下（`api` の 4 参照と `web` の 4 参照のうち、共通の
      2 参照 `team/global` / `users/<user>/global` を 2 度取らない。切替先が分かった時点で
      解決するなら 4 回）。起動時の環境変数に `api` 固有のキーが残っていない
- [ ] 前提: `$DEVBASE_ROOT` の外から実行する（現在地にプロジェクトが無い）
      操作: `devbase up web`
      結果: 認証 1 回、GET 4 回
- [ ] 前提: 注入が済んでいる（`web` の中で `_load_secret_env` 相当を通した後）
      操作: `_ensure_env_files()` を呼ぶ
      結果: 偽サーバへの GET が増えない（判定の結果は変更前と同じ）
      ~~前提: `team/projects/web` がキー 0 件 → `env init` を起動しない~~（2026-09-14、
      設計の決定 4 で存在判定の意味を変えないことにした）
- [ ] backend が `age` のとき、`up` の 3 経路すべてで、環境変数に載る値・生成される
      `.docker-compose.scale.yml`・`env init` の起動の有無が変更前と同じ
      （既存の `tests/cli/` / `tests/commands/` が変更なしで通る）
- [ ] `tests/cli/test_project_name_resolution.py` の切替の回帰テスト（切替元の機密が残らない）が
      変更なしで通る
- [ ] `uv run pytest tests/` が全件通り、`ruff check lib` と `python -m compileall -q lib bin`
      が変更前と同じ結果

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | `up` 1 回の往復が認証 1 回 + 参照ごとに 1 回（実サーバの実測は 4 参照で 369〜392 ms。往復が半分以下になることを実機で確かめる） |
| 運用・保守性 | 往復回数を偽サーバのテストで固定し、経路を足したときに増えたことが分かる |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない |
| データ | 変わらない |
| 既存の振る舞い | `up` の途中で 2 度目の解決をしなくなる。`_ensure_env_files` がサーバへ問い合わせなくなる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run pytest tests/`（偽サーバは `tests/conftest.py` の `FakeOpenBao`） |
| 静的解析 | `ruff check lib`、`python -m compileall -q lib bin` |
| 手動確認 | 利用者の端末（backend `openbao`、PLAN53 の後）で `devbase --verbose up` のログの認証回数を数える。リリース後テストの工程で行う |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `docs/developer/architecture.md`。起動経路は `lib/devbase/cli.py` と `lib/devbase/commands/container.py`、解決は `lib/devbase/env/runtime.py` |
| コーディング規約 | `docs/developer/contributing.md`。CI は `compileall` / `ruff` / `shellcheck` |
| テスト戦略 | 往復回数は `FakeOpenBao` で固定（`tests/env/test_openbao.py` と同じ道具）。経路ごとの振る舞いは `tests/cli/` / `tests/commands/` の既存の harness に足す。実サーバは手動確認 |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、`ruff`。切替の回帰テストを壊さない |
| 確認してから行う | `runtime.inject` / `clear_injected` の引数の追加（他のモジュールが呼ぶ）。設計 Pull Request のマージ |
| 行わない | HTTP の契約の変更。TUI の往復の最適化。`required=False` の経路の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| ~~解決結果を引き継ぐ置き場~~ → 決まった: `runtime` モジュールが `SecretStore` を持ち回り、`_dispatch_lifecycle` の `finally` で捨てる（設計の決定 1・2） | 設計 Pull Request のマージで利用者が承認する | 設計 |
