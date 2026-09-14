# PLAN55: `devbase up` の機密の注入を 1 回にし、サーバ backend の往復を設計の想定へ収める

- 発端: #168
- ワークフローモード: `standard`
  - 根拠: `devbase up` の起動経路（`lib/devbase/cli.py` / `lib/devbase/commands/container.py`）
    の振る舞いの変更。公開インタフェースは変えない。対象には `tests/cli/test_secret_injection.py`
    / `tests/cli/test_project_name_resolution.py` / `tests/commands/test_container_up_order.py`
    / `tests/env/test_openbao.py` / `tests/cli/tui/test_dispatch.py` がある
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
| `container._ensure_env_files` | `SecretStore.exists` × 1〜2（共通は常に、プロジェクトはローカル `.env` が無いときだけ。同じインスタンス内なので GET は参照ごとに 1 回） | 1 | 1〜2（ローカル `.env` が無ければ 2） |
| `container._run_deploy_pipeline` | `_inject_secrets(required=True)` | 1 | 4 |

合計: 認証 4 回、GET ~~12〜14~~ → 11〜14 回（2026-09-14、`_ensure_env_files` のプロジェクト側の
`exists` はローカル `.env` が無いときだけ呼ばれる。控えのある参照でも GET する点は変わらない）。

## 前提

- 前提 1: 同じプロセスの中で、同じ `devbase_root` と同じプロジェクト名に対する解決結果は
  1 回の `runtime.resolve()` で足りる（`up` の途中で他の誰かがサーバ側を書き換えても、
  その `up` は最初に読んだ値で起動する。従来の 2 度注入でも途中で値が変わる保証は無かった。
  ~~例外なし~~ → ただし、その `up` 自身が `env init` で書いた分は読み直す。2026-09-14、
  前提 5）。
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
- 前提 5: 共通機密が未作成で `_ensure_env_files` が子プロセスの `env init` を走らせたとき、
  `env init` が書いた変数はその `up` のコンテナへ渡る（今日はそうなっている。控えを持ち回る
  ことでこれを失わない。2026-09-14、設計の決定 5）。成否の判定: 受け入れ条件 8
- 前提 6: TUI（1 プロセスで操作を続ける）で機密を書いて（`env edit` など）から `up` したとき、
  書いた値がそのコンテナへ渡る（今日はそうなっている。TUI の書き込みは注入と別の `SecretStore`
  を通るので、控えを持ち回ることでこれを失わない。2026-09-14、設計の決定 3）。
  成否の判定: 受け入れ条件 9

## 対象範囲

含む:

- `cli._load_secret_env` → `container` の各経路で `SecretStore` と解決結果を引き継ぐ仕組み
- ~~`_ensure_env_files` の存在判定を注入済みの結果で行うこと~~ → `_ensure_env_files` の
  存在判定で注入と同じ `SecretStore`（`runtime.store_for`）を使い、往復を無くすこと
  （判定の意味は変えない。2026-09-14、前提 3・設計の決定 4 に揃えた）
- `up` 1 回の往復回数を偽サーバで固定するテスト
- TUI で機密を書いてから `up` したとき、書いた値で起動する性質を保つこと（2026-09-14、
  前提 6。TUI の往復数は引き続き条件にしない）
- `docs/specifications/secret-backend.md`「OpenBao との契約」の該当箇所の追記（`plan-to-spec`）

含まない:

- `down` / `logs` / `ps` など `required=False` の経路の往復（`up` ほど多くない。数えて
  仕様を超えていれば範囲外として起票する）
- `runtime.resolve()` の重ね順・`SecretStore` の HTTP の契約の変更
- TUI（1 プロセスで複数の操作を続ける経路）の往復。`_dispatch_lifecycle` の入口で
  `docker_context.reset()` と同じ扱いにするかは設計で決めるが、TUI の往復数は条件にしない
  （決まった: TUI の委譲層 `tui/dispatch.py` の入口で捨てる。2026-09-14、設計の決定 3）
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
- [ ] 前提: backend が `openbao`（偽サーバ）で `team/global` が未作成。`env init` の子プロセスは
      偽サーバへ `INIT_KEY=value` を保存して成功終了するものに差し替える
      操作: `web` の中で `devbase up`（docker の呼び出しは差し替える）
      結果: 起動時の環境変数と生成される構成に `INIT_KEY` が渡る。往復は認証 2 回、GET 8 回
      以下（`env init` の前に 4、書いた後に読み直して 4）（2026-09-14、前提 5）
- [ ] 前提: backend が `openbao`（偽サーバ）で `team/global` に `REVIEW_KEY=old` がある。TUI の
      起動相当（`_load_secret_env`）を通した後、同じプロセスで TUI の `env edit`（エディタは
      `REVIEW_KEY=new` を保存するものに差し替える）を実行する
      操作: 同じプロセスで TUI の `up web`（docker の呼び出しは差し替える）
      結果: 起動時の環境変数と生成される構成の `REVIEW_KEY` が `new`（2026-09-14、前提 6）

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
| 既存の振る舞い | `up` の途中で 2 度目の解決をしなくなる。`_ensure_env_files` がサーバへ問い合わせなくなる。共通機密が未作成で `env init` を走らせたときだけ、書いた後に読み直す（その `up` に限り認証 1 回 + GET 4 回が足される）。TUI は操作の入口で控えを捨て、起動時に読んだ値を最初の操作にも引き継がない（TUI の操作 1 回あたり認証 1 回 + GET 4 回。今日より少ない） |

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

## 実装計画

設計は `issues/PLAN55_up-single-injection-design.md`（マージ済み #176）。タスクは設計の
「構成要素」の行から導く。1 タスクが独立して検証できる単位にし、失敗するテスト → 最小実装 →
整理の順で進める。

### 修正対象

- `lib/devbase/env/runtime.py`、`lib/devbase/commands/container.py`、`lib/devbase/tui/dispatch.py`
- `tests/env/test_runtime_store.py`（新設）、`tests/cli/test_up_roundtrips.py`（新設）、
  `tests/cli/tui/test_dispatch.py`（足す）、`tests/conftest.py`（autouse で `release_store()`）

### Task 1: `runtime.store_for` / `release_store` と、`resolve` / `inject` / `child_env` の切り替え

- **対象ファイル:** `lib/devbase/env/runtime.py`、`tests/env/test_runtime_store.py`
- **変更内容:** モジュールの控え（`_store` / `_store_root`）と 2 関数を足す。`store` 引数が
  `None` のとき `store_for(root)` を使う。明示的に渡された `store` は控えに入れない
- **満たす受け入れ条件:** `store_for` の規則の表（設計）
- **進め方:** 同一性・`root` 変更・解放後の作り直し・明示 `store` を控えない、の 4 テストを先に書く

### Task 2: `_ensure_env_files` を持ち回った store に切り替え、`env init` の後に捨てる

- **対象ファイル:** `lib/devbase/commands/container.py`、`tests/cli/test_up_roundtrips.py`
- **変更内容:** `SecretStore(devbase_root)` → `runtime.store_for(devbase_root)`。`env init` の
  子プロセスから戻ったら終了コードによらず `runtime.release_store()`（決定 5）
- **満たす受け入れ条件:** 4（GET が増えない）、8（`env init` が書いた値で起動する）
- **進め方:** 偽サーバで注入 → `_ensure_env_files()` → GET 件数不変のテスト、`subprocess.run` を
  偽サーバへ書くスタブに差し替えて `_run_deploy_pipeline` へ渡る `SecretEnv` を見るテスト

### Task 3: `_dispatch_lifecycle` の `finally` で捨てる（3 経路の往復を固定）

- **対象ファイル:** `lib/devbase/commands/container.py`、`tests/cli/test_up_roundtrips.py`
- **変更内容:** `finally` に `runtime.release_store()` を並べる
- **満たす受け入れ条件:** 1・2・3（認証 1 回 + GET 4 / ≤6 / 4）、5・6（既存テスト無変更）
- **進め方:** `cli._load_secret_env` → `container.cmd_project(ns)` を偽サーバ + docker 差し替えで
  走らせ、`openbao.logins` と GET の `kv_path` の集合を固定する

### Task 4: TUI の委譲の入口で捨てる

- **対象ファイル:** `lib/devbase/tui/dispatch.py`、`tests/cli/tui/test_dispatch.py`
- **変更内容:** `_preserve_cwd_env` の入口で `runtime.release_store()`（決定 3）
- **満たす受け入れ条件:** 9、決定 3 の規則
- **進め方:** `store_for(root)` で控えを作ってから `dispatch_group` の handler 内で別インスタンスに
  なるテスト、`env edit`（エディタのスタブ）→ `up` で新しい値が渡るテスト

### Task 5: 既存テストの独立性

- **対象ファイル:** `tests/conftest.py`
- **変更内容:** autouse fixture で各テストの前後に `runtime.release_store()`。モジュールの控えが
  テストをまたいで残らない
- **満たす受け入れ条件:** 5・7
- **進め方:** `uv run pytest tests/` 全件

### リスクと対処

| リスク | 対処 |
| --- | --- |
| `tests/cli/` の既存 harness が `SecretStore(root)` を直接作り `monkeypatch` している | Task 5 の autouse fixture。実装時に `grep -rn "SecretStore(" tests/` で数える |
| `container.py` は 1300 行超で、`_ensure_env_files` と `_dispatch_lifecycle` が離れている | 触るのは 2 関数の数行。タスクごとにテストを通す |

### 切り戻し手順

- 差分を戻すだけ（永続データ・スキーマの変更なし）。`release_store()` を呼ばない古い経路が
  残っても、`SecretStore` を作り直す従来の動きに戻るだけで壊れない

### 完了の定義

- [ ] 受け入れ条件 1〜9 をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run pytest tests/` / `ruff check lib` / `python -m compileall -q lib bin` が exit=0
