# /ndf:review 実行 (cross-review codex / round 3)

PR #75 を **codex の観点でレビューし、gh api で直接 PR に投稿** してください。

## 必須コンテキスト
- repo: devbasex/devbase
- PR: #75
- commit_id (headRefOid): f8231341fb4dfa044e1b27ee4e698058b7ff28b2
- worktree: /var/folders/qz/qxt0p_y15xv5jg4x77zmtxj80000gp/T/ndf-worktrees/devbasex--devbase/pr75 （**ファイル読み取りは必ず此処の絶対パスを使う**）
- event_downgrade: true
  - true の場合: payload の `event` は `COMMENT` にすること。
    ただし body 先頭 prefix の `<event>` には **本来の intent** を書く。
- 既存コメントスナップショット: /var/folders/qz/qxt0p_y15xv5jg4x77zmtxj80000gp/T/ndf-worktrees/devbasex--devbase/pr75/.cross_review/cross-review-pr75-existing-comments.txt （重複指摘禁止）

## 出力契約
- review body の **先頭行** に必ず以下を入れる（fence 不要、Markdown 見出しとして）:
  ```
  ## 🤖 cross-review | round 3 | codex | <event(intent)>
  ```
  例: `## 🤖 cross-review | round 3 | codex | REQUEST_CHANGES`
  - `<event>` は **本来の intent** (REQUEST_CHANGES / APPROVE / COMMENT)

### 出力に **含めてはいけないもの**（Resolve 負荷を増やすため）
- ❌ **「良い点」/「Strengths」/「評価できる点」 section** — body にも書かない
- ❌ **対応アクションが無いインラインコメント** — 観察・感想・現状説明だけは禁止
- ❌ **nit / スタイル指摘のインライン化** — 好みの問題はコメント化しない (無視する)
- ❌ **コード引用 (``` ... ```) だけで指摘内容が無いコメント**
- ❌ **`event=COMMENT` での雑感投稿** — 直すべき点が無ければ `APPROVE` にする

### インラインコメントの書式
- `[重要度 / カテゴリ]` プレフィックス必須 (例: `[major / 正確性]`)
- 重要度は `critical` / `major` / `minor` のみ使う (nit はインライン化しない)
- 本文は **1 コメント = 1 修正アクション** で完結させる。1〜2 文で具体的な修正提案を書く

### body (総評) の書き方
- 設計レベル・PR 横断の **修正提案のみ** 書く
- 書くことが無ければ prefix 行 + 1 行サマリだけで良い (褒め言葉や評価文は不要)

- 投稿後、サマリを **/var/folders/qz/qxt0p_y15xv5jg4x77zmtxj80000gp/T/ndf-worktrees/devbasex--devbase/pr75/.cross_review/codex-review-pr75-result.json** に書く:
  ```json
  {
    "event": "REQUEST_CHANGES",
    "posted_as": "COMMENT",
    "comments_count": 5,
    "review_url": "https://github.com/.../pull/75#pullrequestreview-...",
    "by_severity": {"critical": 0, "major": 3, "minor": 2, "nit": 0}
  }
  ```
- payload（全コメント詳細）は **/var/folders/qz/qxt0p_y15xv5jg4x77zmtxj80000gp/T/ndf-worktrees/devbasex--devbase/pr75/.cross_review/codex-review-pr75-round3-payload.json** に保存
  （振動検知用、`{ "comments": [{path, line, body, severity}, ...] }` 形式）

## 守るべきこと
- リポジトリ編集は行わない（コード修正は別ステップ）
- worktree 外のパスは触らない
- gh api 失敗時は err.log にエラー詳細を残して即時終了
