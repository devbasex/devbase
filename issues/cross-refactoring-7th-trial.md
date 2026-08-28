# cross-refactoring 7 回目の実機試行

この Pull Request は devbase の機能変更ではない。ai-plugins が提供する NDF Skill
`cross-refactoring`（v8.6.0）を実機で確かめるための対象として作った。

改善はコンテナ構成の生成（`lib/devbase/volume/`）とスナップショット（`lib/devbase/snapshot/`）
の 2 つに限る。振る舞いは変えない。

## 試行の条件

| 項目 | 値 |
| --- | --- |
| 提案・レビュー | codex / gemini / kiro |
| 適用の母集合 | claude / codex / kiro |
| 対象範囲 | `lib/devbase/volume` `lib/devbase/snapshot` `tests/volume` `tests/snapshot` |
| 検証コマンド | `uv run --group dev python -m pytest -q` |
| ラウンド上限 | 6 |
| 修正の上限 | 1 |
| 分岐元 | `462b6e5`（`main` の 1 つ前） |

`main` の先頭 `678baeb` は `tests/containers/test_tmux_conf.py` が 1 件落ちるため、
その 1 つ前から分岐した。対象範囲の 4 パスは `678baeb` と同一である。

## 着手前のテスト

```
1474 passed, 1 skipped in 33.88s
```

## 記録

結果は ai-plugins の
[issues/issue-113-cross-refactoring-7th-trial.md](https://github.com/devbasex/ai-plugins/blob/main/issues/issue-113-cross-refactoring-7th-trial.md)
に残す。
