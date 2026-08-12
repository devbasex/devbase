# i28: Docker コンテナのゾンビプロセス蓄積を `init: true` 注入で解消する

## 関連リンク

- Issue: https://github.com/devbasex/devbase/issues/28
- 関連 issue: https://github.com/devbasex/ai-plugins/issues/21

## 概要

`generate_scaled_compose()` が生成する各サービスに `init: true` を `setdefault` で注入し、
docker がコンテナ PID 1 に tini を挿入するようにする。これにより orphan プロセスが自動 reap され、
ゾンビ (`<defunct>`) の蓄積が解消される。

## 問題・背景

- devbase コンテナの PID 1 は entrypoint の `tail -f /dev/null` であり、SIGCHLD を受けても orphan を reap しない。
- このため `nohup ... & disown` で起動・終了したプロセスがゾンビ化して蓄積する。
- 特に `ndf:cross-review` skill は codex/gemini CLI を `nohup & disown` で起動し `monitor.py` で監視するため、
  以下の二次被害が出る:
  1. ゾンビに対しても `kill -0` が成功し、`monitor.py` が「実行中」と誤判定 → hard timeout (420s) まで待たされる
  2. PID 1 が reap しないためコンテナ再起動まで蓄積し続ける
- Docker Compose の `init: true` は 20KB の軽量 init (tini) を PID 1 として挿入し、シグナル転送 + ゾンビ reap を行う。
  `PR_SET_CHILD_SUBREAPER` より単純で確実。

### 注入箇所が `generate_scaled_compose()` で十分な根拠

`devbase up` (`cmd_up`) は **常に** `generate_scaled_compose()` を呼び、生成した
`.docker-compose.scale.yml` **単独** で `docker compose up` する (`lib/devbase/commands/container.py:175,179`)。
scale=1 でも同経路を通るため、ここへの注入で全 `devbase up` ケースを網羅できる。
ベース `compose.yml` テンプレート側の変更は不要。

## 修正対象

- `lib/devbase/volume/compose.py` — `generate_scaled_compose()` の 2 つのサービス生成ループ
- `tests/volume/test_compose.py` (新規) — `init` 注入のユニットテスト
- `tests/volume/__init__.py` (新規) — テストパッケージ初期化

## タスク分解

### Task 1: dev インスタンスへの `init` 注入

- **対象ファイル:** `lib/devbase/volume/compose.py`
- **変更内容:** dev インスタンス複製ループ (`for i in range(1, scale + 1)` 内) で
  `service.setdefault('init', True)` を追加する。`setdefault` のため、ユーザーが明示的に
  `init: false` を指定していれば尊重して上書きしない。

### Task 2: non-dev サービスへの `init` 注入

- **対象ファイル:** `lib/devbase/volume/compose.py`
- **変更内容:** non-dev サービス複製ループ (`for service_name, service_config in services.items()` 内) で
  `copied.setdefault('init', True)` を追加する。mysql / valkey 等にも tini を挿入し安全側に倒す。

### Task 3: ユニットテスト追加

- **対象ファイル:** `tests/volume/test_compose.py` (新規), `tests/volume/__init__.py` (新規)
- **変更内容:** 一時 `compose.yml` を用意して `generate_scaled_compose()` を呼び、生成された
  `.docker-compose.scale.yml` を読み戻して以下を検証する:
  - dev-1 (および scale>1 の各 dev-i) に `init: true` が付く
  - non-dev サービス (例: mysql) に `init: true` が付く
  - 明示的に `init: false` を指定したサービスは `false` のまま (setdefault の尊重)

## 影響範囲

- `devbase up` / `devbase scale` 経由で生成される全コンテナ。挙動は「PID 1 が tini になる」のみで、
  entrypoint (`tail -f /dev/null`) は tini の子プロセスとして従来どおり動作する。後方互換。
- `init: false` を明示指定済みのプロジェクトには影響しない。

## テスト計画

- [ ] `pytest tests/volume/test_compose.py` が通る (init 注入 / false 尊重)
- [ ] 既存テストにリグレッションがない (`pytest tests/`)
- [ ] (手動) `devbase up && devbase login` 後 `ps -p 1 -o comm=` が `tini` を返す
- [ ] (手動) `nohup sleep 1 & disown; sleep 3; ps aux | grep 'Z.*defunct'` がゾンビを出さない

## PR 計画 (単一 PR)

| 項目 | 値 |
|---|---|
| 種別 | 単一 PR (release ブランチ不要) |
| branch 名 | `fix/i28-docker-init-zombie-reap` |
| base | `main` |
| 根拠 | 変更は 1 実装ファイル + テストのみ・結合度低・依存タスクなし (差分 ~60 行) |
