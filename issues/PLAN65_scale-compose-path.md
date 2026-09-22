# PLAN65: `devbase scale` の Compose 呼び出しを共通経路へ寄せ、`cmd_scale` の段階を分ける

対象 issue: devbasex/devbase#192

- ワークフローモード: `standard`
  - 根拠: `devbase scale` の本番の振る舞いを変える。変わるのは子プロセスへ渡る環境と、
    起動の対象に入るサービスの集合である。確定仕様 `docs/specifications/compose-profiles.md` も
    変える。構造変更の側も本番の振る舞いの変更を伴うため、対象のテストが薄くても
    `legacy-refactor` ではなく `standard` にする
- ベースブランチ: `release/v3.7.0`（release Pull Request は #212）
- 設計文書: `issues/PLAN65_scale-compose-path-design.md`

## 目的
- **確定仕様が約束している「`COMPOSE_PROFILES` を端末や `.env` に置いても devbase 経由の操作には
  効かない」が `devbase scale` でも成り立つ。** いまは成り立たない
- **`devbase up` と `devbase scale` が、同じ生成物に対して同じ規則で Compose を呼ぶ。** 起動の
  対象の決め方と子プロセスの環境が経路によって違わない
- **`cmd_scale` の段階に名前が付き、`cmd_up` と同じ形で読める。** `docker compose config
  --format json` を読む経路が 1 つになる
- **`devbase scale` の正常系の手順が、テストで固定される。** いまは 1 つも無い

## 実測: Compose を起動する経路（2026-09-22 / `release/v3.7.0` の先頭 688efde）
推測ではなく、この作業ツリーで採った値である。
`os.execvp` 系は 0 件、`Popen` / `check_output` / `subprocess.call` で Compose を起動する箇所も
0 件だった。Compose の起動はすべて `subprocess.run` である。

```
$ grep -rn "'docker', 'compose'\|\"docker\", \"compose\"" lib/
lib/devbase/utils/docker.py:56:    cmd = ['docker', 'compose']
lib/devbase/commands/container.py:266:    cmd = ['docker', 'compose']            # _compose_base_args
lib/devbase/commands/container.py:1648:            ['docker', 'compose', '-f', str(override_file), 'up', '-d', '--no-recreate'],
lib/devbase/commands/container.py:1792:        ['docker', 'compose', 'config', '--format', 'json'],
lib/devbase/commands/container.py:1970:        ['docker', 'compose', 'config', '--format', 'json'],
lib/devbase/editor/opener.py:396:    cmd = ["docker", "compose"]
```

**この grep だけでは数え足りない。** `_compose_base_args()`（266 行目）が返した配列を使う
呼び出し元は、行の上に `['docker', 'compose']` を持たない。呼び出し元まで辿ると、Compose を
起動する箇所は 8 か所で、**`compose_env()` を渡していないのは 2 か所**である。

| # | 場所 | 関数 | `compose_env()` | 起動するもの |
| --- | --- | --- | --- | --- |
| 1 | `utils/docker.py:64` | `docker_compose()` | 渡す | 共通経路そのもの |
| 2 | `container.py:281` | `_compose_run()` | 渡す | `ps` / `logs` |
| 3 | `container.py:291` | `_compose_lines()` | 渡す | `config --services` / `--profiles` |
| 4 | **`container.py:1413`** | **`cmd_login()`** | **渡さない** | `exec <dev>-<n> bash` |
| 5 | **`container.py:1648`** | **`cmd_scale()`** | **渡さない** | `up -d --no-recreate` |
| 6 | `container.py:1792` | `_resolve_dev_service()` | 渡す | `config --format json` |
| 7 | `container.py:1970` | `_read_compose_services()` | 渡す | `config --format json` |
| 8 | `editor/opener.py:396` | `_query_container_name()` | 渡す | `ps --format json` |

issue 本文は 5 だけを挙げているが、**4（`cmd_login`）も同じ穴である**。`tests/utils/test_docker_profiles.py:102`
のコメントも「決定 7 の棚卸しの 4 か所」と書いており、この 2 か所が棚卸しから漏れている。
`cmd_login` の扱いは設計の決定 2 で決める。

## 実測: 現状固定テスト
```
$ grep -rn "no-recreate" tests/
（0 件）
$ grep -rn "no-recreate" lib/
lib/devbase/commands/container.py:1645
lib/devbase/commands/container.py:1648
```

`cmd_scale` を呼ぶテストは 4 か所ある（`tests/cli/test_project_dispatch.py:366` は
`cmd_scale` を差し替える側で、本体は動かない）。

| 場所 | 何を固定しているか | `[4/5]` まで届くか |
| --- | --- | --- |
| `tests/commands/test_container_up_order.py:257` | グループ不一致で 1 を返す | 届かない |
| `tests/commands/test_container_up_order.py:290` | `_build_scaled_override` の例外で 1 を返す | 届かない |
| `tests/commands/test_container_context.py:254` | 接続先（`DOCKER_CONTEXT` / `DOCKER_GID`）の伝播。`@parametrize` で 2 ケース | **届く（唯一）** |
| `tests/cli/test_project_dispatch.py:362,366` | dispatch の引数の伝播（本体は差し替え） | 届かない |

**正常系を通る 1 本（`test_container_context.py:254`）はあるが、固定しているのは接続先の
伝播だけである。** 起動のコマンド列・子プロセスの `COMPOSE_PROFILES`・`_push_bao_token` と
`./deploy` の順序と範囲は、どのテストも固定していない。issue 本文の「正常系の手順を固定する
テストは無い」は、この意味では成り立つ。

## 確定仕様の食い違い
`docs/specifications/compose-profiles.md` は同じ文書の中で相反する 2 つを書いている。

| 行 | 内容 |
| --- | --- |
| 83-84 | 「`cmd_scale` が直接呼ぶ `docker compose -f <生成物> up -d --no-recreate` はこの対象に含めない。プロファイルの入口ではないためである」 |
| 388-389 | 「`COMPOSE_PROFILES` を端末や `.env` に置いても devbase 経由の操作には効かない。素の `docker compose` には従来どおり効く」 |

同じ約束は利用者向けの文書にもある（`docs/plugin-dev/compose-profiles.md:82`）。

## 影響
| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（CLI の引数・環境変数・サブコマンドは増減しない） |
| データ | 変わらない（`project.yml` / 生成物 `.docker-compose.scale.yml` の形は変わらない） |
| 既存の振る舞い | **変わる。** `devbase scale` の子プロセスの `COMPOSE_PROFILES` が常に `__devbase_none__` になり、起動の対象が既定のサービスに限られる。端末または `.env` に `COMPOSE_PROFILES` を置いた人にとっては、`devbase scale` がプロファイルのサービスを起動しなくなる |
| 確定仕様 | **変わる。** `docs/specifications/compose-profiles.md` の経路の表・コマンド列の表・運用・テスト観点 |
| ログ | 変わらない（`[1/5]`〜`[5/5]` の文字列と `Failed to start new containers` を保つ） |
| 利用者の操作 | 追加の操作は要らない。配布された版を使えばそのまま効く |

## 対象範囲
含む:

- `lib/devbase/commands/container.py`
  - `cmd_scale` の `docker compose ... up -d --no-recreate` を共通経路（`utils/docker.py` の
    `docker_compose()`）へ寄せる
  - `cmd_scale` の段階（`[1/5]`〜`[5/5]`）を関数へ抽出する
  - `_resolve_dev_service` と `_read_compose_services` の `docker compose config --format json` を
    1 つの関数へ統合し、共通経路へ寄せる
  - `cmd_login` の `docker compose exec` へ `compose_env()` を渡す（設計の決定 2）
- `docs/specifications/compose-profiles.md` — 経路の表・組み立てるコマンド列の表・運用・テスト観点
- `tests/commands/` — `devbase scale` の正常系の手順を固定するテスト（新規）
- `tests/utils/test_docker_profiles.py` — 「決定 7 の棚卸しの 4 か所」のコメントと、そこに並ぶ
  経路のテスト（棚卸しの中身が変わるため）

含まない:

- `cmd_up` / `_run_deploy_pipeline` / `cmd_down` / `cmd_profile_*` の振る舞い（読み替えの対象に
  入るだけで、コマンド列も順序も変えない）
- `docker_compose_up()` / `docker_compose_down()` / `compose_env()` のシグネチャの変更
- `_compose_run`（`devbase ps` / `devbase logs`）と `editor/opener.py` の `_query_container_name`。
  どちらも既に `compose_env()` を渡しており、この束の対象ではない
- `devbase scale` へのプロファイルの指定の口（`scale --profile` のような引数）の追加
- `pytest` の `DEVBASE_ROOT` の隔離（PR #217 の範囲）
- `cmd_scale` の前提の検査そのものの見直し（`new_scale <= current_scale` を警告で 1 にする
  今の判定は変えない）

## 前提
- **前提 1: 実害の観測は無い。** issue 本文のとおり、確定仕様の約束と実装が食い違っていることまでが
  分かっている。`COMPOSE_PROFILES` を置いたうえで `devbase scale` を打った事例の報告は無い。
  よって「壊れているものを直す」ではなく「約束と実装のどちらかへ揃える」変更である
- **前提 2: 実環境のプロジェクトで `devbase up` / `devbase scale` を実行して確かめない。** 実環境の
  コンテナは本番の系である。確認はコマンド列を組み立てる関数の単体の水準（`subprocess.run` の
  差し替え）で行う。実 docker と実 `DEVBASE_ROOT` には触れない
- **前提 3: `release/v3.7.0` を base にした Pull Request では CI が 1 件も動かない。**
  `.github/workflows/ci.yml` の `on.pull_request.branches` が `main` だけのためである。
  `gh pr checks` は `no checks reported` を返し、`mergeStateStatus` は `CLEAN` を返す（#216）。
  検証は手元で行い、証跡を Pull Request 本文へ載せる
- **前提 4: pytest は実環境の `DEVBASE_ROOT` を継承する。** その隔離は別の束（PR #217、未マージ）が
  入れる。この束のテストは既存の流儀（各テストが自分で `monkeypatch.setenv`）に合わせる
- **前提 5: `docs/specifications/compose-profiles.md` は別の束（PR #213、未マージ）も触る。**
  #213 の hunk は 193 行目付近の 1 行だけで、この束が触る節（74-84 / 107-120 / 386-390 / 445 付近）
  とは重ならない。後からマージする側が競合を解く
- **前提 6: プロファイルを持たないプロジェクトの振る舞いは変わらない。** 確定仕様 141 行目の
  「プロファイルを持たないプロジェクトでは、`up` / `down` / `scale` が扱うコンテナの集合と順序は
  変わらない」を保つ

## 受け入れ条件: 仕様と振る舞い（A・B）
### 仕様の食い違いの解消（A-1〜A-5）

- [ ] **A-1: `lib/` の中で `compose_env()` を渡さずに `docker compose` を起動する箇所が 0 件になる。**
  上の実測の 8 か所を 1 件ずつ辿り、`compose_env()` を直接渡すか `docker_compose()` を経由する
  ことを確かめる。現状は 2 件（`cmd_scale:1648` と `cmd_login:1413`）が満たしていない。
  `grep -rn "'docker', 'compose'\|\"docker\", \"compose\"" lib/` の件数は 6 → 3 に減る。
  残るのは `utils/docker.py` の共通経路・`_compose_base_args`・`editor/opener.py` の 3 つである
- [ ] **A-2: `docs/specifications/compose-profiles.md` の中に、`cmd_scale` を共通経路の対象から
  外す記述が残っていない。** 現状 83-84 行目の「`cmd_scale` が直接呼ぶ …… この対象に含めない」が
  消え、経路の表（74-81 行目）に `scale` の起動の経路が載る
- [ ] **A-3: 「組み立てるコマンド列」の表に `devbase scale` の行がある。** 行は
  `up -d --no-recreate <既定のサービス...>` と、子プロセスの `COMPOSE_PROFILES` が
  `__devbase_none__` であることを示す
- [ ] **A-4: `docs/plugin-dev/compose-profiles.md:82` の約束に例外を足さずに済む。**
  その約束は「端末と `.env` の `COMPOSE_PROFILES` は devbase 経由の操作に効かない」である。
  足す必要が出たら、共通経路へ寄せる判断（設計の決定 1）を見直す
- [ ] **A-5: `tests/utils/test_docker_profiles.py` の「棚卸しの N か所」のコメントと、そこに
  並ぶテストが、変更後の経路の一覧と一致する。** いまのコメントは 4 か所と書き、
  `cmd_scale` と `cmd_login` を数えていない

### `devbase scale` の振る舞い（B-1〜B-5）

`subprocess.run` を差し替えた単体のテストで確かめる。実 docker には触れない。

- [ ] **B-1: `devbase scale` が起動に使う子プロセスの環境の `COMPOSE_PROFILES` が
  `__devbase_none__` である。** 呼び出し側の `os.environ` に `COMPOSE_PROFILES=web` を置いた
  状態でも同じ値になる
- [ ] **B-2: 呼び出し側の `os.environ` は書き換わらない。** `cmd_scale` の前後で
  `os.environ.get('COMPOSE_PROFILES')` が変わらない
- [ ] **B-3: 組み立てるコマンド列が
  `docker compose -f <生成物> up -d --no-recreate <既定のサービス...>` である。**
  `-f` に渡るのは `_build_scaled_override` が返したパスで、サービス名は
  `default_services(<生成物>)` が返した一覧の全件、その並びのままである
- [ ] **B-4: 起動が 0 以外で終わったら、`Failed to start new containers` を error で出して 1 を返す。**
  例外は送出しない（`subprocess.CalledProcessError` が `cmd_scale` の外へ出ない）
- [ ] **B-5: プロファイルを持たない生成物では、起動の対象に入るサービスの集合が変更前と一致する。**
  比べるのは、`default_services` が返す一覧と、変更前にサービス名を付けずに起動したときの
  対象である。`config --services` の出力を差し替えて示す

## 受け入れ条件: テスト・構造・退行（C・D・E）
### 正常系の手順の固定（C-1〜C-4）

- [ ] **C-1: `devbase scale` の正常系を通すテストが存在し、次の順序を固定する。**
  `grep -rn "no-recreate" tests/` が 1 件以上になる

  ```
  _check_group_consistency → write_scale → ensure_volumes → ensure_network
    → _build_scaled_override → default_services → 起動 → wait_for_containers_ready
    → _push_bao_token → ./deploy
  ```
- [ ] **C-2: `_push_bao_token` と `_run_deploy_script_for_instances` に渡る範囲が
  `current_scale + 1` から `new_scale` までである。** 既にあるインスタンスを含めない
- [ ] **C-3: 既にある 4 か所の `cmd_scale` のテストが、書き換えずに通る。**
  グループ不一致で 1、`_build_scaled_override` の例外で 1、context の伝播、dispatch の伝播
- [ ] **C-4: `pytest tests/` の全件が、変更の前後で同じ結果になる**（前提 4 のとおり、実行時は
  各テストが自分で `monkeypatch.setenv` する既存の流儀に従う）

### 構造（D-1〜D-4）

- [ ] **D-1: `docker compose config --format json` を起動する箇所が 1 か所になる。**
  `grep -rn "'config', '--format', 'json'" lib/` が 1 件（現状 2 件）
- [ ] **D-2: 統合した後も 2 つの呼び出し元の契約が変わらない。**
  `_resolve_dev_service` は、終了コードが非 0 のときと JSON として読めないときに `None` を返す。
  `_read_compose_services` の契約を引き継ぐ側は、JSON として読めないときに
  `json.JSONDecodeError` を伝播する
- [ ] **D-3: `cmd_scale` の本体が 40 行以下になる**（現状 86 行）。抽出した段階の関数が
  `[1/5]`〜`[5/5]` のログ文字列をそのまま持つ
- [ ] **D-4: `cmd_scale` と `cmd_up` の段階の対応が、設計文書の表と一致する。** 段階の番号の
  文字列（`[2.5/5]` を含む）は変えない

## 受け入れ条件: 退行しないこと（E）

- [ ] **E-1: `devbase up` のコマンド列と順序が変わらない。**
  `tests/commands/test_container_up_order.py` を書き換えずに通す
- [ ] **E-2: `devbase scale` はプロファイルのサービスを複製しない**（確定仕様 386-387 行目）。
  生成物の `profiles:` は保たれたままで、`scale` の対象に入らない
- [ ] **E-3: 既に動いているプロファイルのサービスを `devbase scale` が停止しない。**
  `--no-recreate` の起動で、対象に入らないサービスへは触れない（`up` と違い、`scale` は
  停止の段を持たない）

## 検証手段
| 条件 | 手段 |
| --- | --- |
| A-1 / D-1 | `grep -rn` の出力を Pull Request 本文へ貼る |
| A-2 / A-3 / A-4 | 変更後の `docs/specifications/compose-profiles.md` の該当の節と `grep -n "cmd_scale" docs/specifications/compose-profiles.md` |
| B-1〜B-5 / C-1〜C-2 | 新しいテスト `tests/commands/test_container_scale_order.py` の `pytest` |
| C-3 / C-4 / E-1 | `pytest tests/` の全件。変更前の結果と並べて Pull Request 本文へ載せる |
| D-2 | 統合した関数の単体のテスト（終了コード非 0 / 不正 JSON / 正常の 3 通り） |
| D-3 | `python - <<'EOF'` で `cmd_scale` の行数を数える、または差分の行数 |
| E-2 / E-3 | 生成物に `profiles:` を含むサービスを置いたテストで、起動の対象の一覧を検査 |

CI は動かない（前提 3）。上の手段はすべて手元で実行し、証跡を Pull Request 本文へ載せる。

## 実装計画

設計文書の「実装の分け方」の章が、2 本の Pull Request の内訳・対象ファイル・依存の順序を
持つ。この文書は受け入れ条件の側だけを持つ。

**どの条件がどちらの Pull Request で満たされるかは、設計文書の「受け入れ条件とどちらの
Pull Request が対応するか」の表が決める。** A-1 の `grep` の件数（6 → 3）と A-5 の棚卸しの
一致は 1 本目で満たす。D-3・D-4（`cmd_scale` の本体 40 行以下と段階の対応）だけが 2 本目で
満たす条件である。

## 未確認のまま残ること
- **`--no-recreate` と明示したサービス名の組み合わせで、Compose が依存先をどう扱うか**の実測は
  行わない。確定仕様 92-95 行目に、打ち消し用のプロファイル名を入れれば依存先としての起動も
  止まると書いてある。それを前提にする。実 docker で確かめるには実環境のプロジェクトが要る
  ため（前提 2）行わない
- **`COMPOSE_PROFILES` を置いたうえで `devbase scale` を打っている利用者がいるか**は分からない。
  いた場合、この変更でプロファイルのサービスが起動しなくなる。切り戻しは配布の版を戻すこと
  （`devbase scale` に専用の退避の口は設けない）

## 境界
| 区分 | 内容 |
| --- | --- |
| 常に行う | 変更範囲の pytest の実行、既存テストの全件実行、`grep` による数え直し |
| 確認してから行う | 確定仕様の約束の書き換え（この文書の受け入れ条件がその確認である）、`utils/docker.py` の関数のシグネチャの変更 |
| 行わない | 実環境のプロジェクトでの `devbase up` / `devbase scale` の実行、`cmd_up` の振る舞いの変更、依頼範囲外のリファクタリング |

## 前提とする取り決め
- ブランチ戦略と Pull Request の運用は `ndf-policies` に従う。base は `release/v3.7.0`
- 構造を変える変更は、振る舞いを変えないことをテストで守る（`refactoring`）
- コミットと Pull Request の本文の作法は `markdown-writing` に従う

## 依頼（原文）
issue #192 の本文から、決めることと採る手・順序の指定を原文のまま写す。

> ## 決めること
>
> - `devbase scale` の Compose 呼び出しを共通経路へ寄せるか、それとも仕様の「devbase 経由の
>   操作には効かない」を `scale` 除外込みへ書き直すか。決め方によって、抽出した後の関数の境界が変わる

> **採る手**: 統合（`consolidate_duplication`）。Compose の呼び出しを共通経路へ寄せてから、段階を抽出する。
>
> **順序**: 仕様（`scale` を共通経路の対象にするか決める）→ 共通経路へ寄せる → 現状固定テストを足す → 関数を分ける。
