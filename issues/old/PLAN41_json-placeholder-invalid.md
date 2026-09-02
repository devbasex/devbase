# PLAN41: 新規アカウントグループのプレースホルダが不正な JSON になる不具合を直す

## 関連リンク

- issue: [#136](https://github.com/devbasex/devbase/issues/136)
- 発見の経緯: [#133](https://github.com/devbasex/devbase/issues/133) の対応で `with` / `kkg`
  グループを新設したところ、両方で Claude Code が起動しなかった
- 参考: `containers/base/entrypoint.sh`（`devbase_ensure_entry` / `devbase_link_setting` /
  `devbase_seed_group_settings`）、`tests/containers/test_entrypoint_ai_settings.py`
- 経緯: `issues/PLAN39_account-group-volume-separation.md`（この経路を導入した計画）

## モード

`standard` — 本番の振る舞い（コンテナ起動時のプレースホルダ生成）のバグ修正。公開
インタフェースもデータ移行も伴わない。対象には既存テストが 24 件あり、現状固定の土台がある。

## 目的と非目的

達成したい状態:

- **新規アカウントグループの初回起動で Claude Code が起動する。** プレースホルダとして
  作られる `.claude.json` が妥当な JSON になる。
- 同じ壊れ方をしうる他のエントリ（`settings.json` 等）も同時に塞ぐ。個別対応にすると
  次にファイルエントリを足した人が同じ穴を踏む。
- 既存グループ（`default` を含む）の中身を**壊さない**。実体があるものには触れない。

やらないこと:

- `devbase_seed_group_settings` の `default` 限定ガードを外すこと。非 default グループを
  シードすると PLAN39 で分離したはずの他社の認証情報をコピーしてしまう。**ガードは正しい**。
- 永続化の分類（分類 A / B）の見直し。
- 既に 0 バイトで作られてしまったファイルの自動修復。運用で `{}` を書けば済む
  （実施済み）ため、起動時に中身を書き換える処理は入れない。

## 前提

現行 `main`（`09701e9`）上で確認済み。

### 前提 1: プレースホルダは 0 バイトで作られる

`containers/base/entrypoint.sh:258-270`。

```sh
devbase_ensure_entry() {
    local path="$1"
    mkdir -p "$(dirname "$path")"
    if [ -e "$path" ]; then
        return 0
    fi
    if devbase_is_file_entry "$path"; then
        : > "$path"          # ← 0 バイト
    else
        mkdir -p "$path"
    fi
}
```

呼び出し元は `devbase_link_setting`（283 行）で、`devbase_setup_ai_settings` が
`DEVBASE_GROUP_SETTINGS` / `DEVBASE_SHARED_SETTINGS` の各エントリに対して呼ぶ。

### 前提 2: `default` グループだけがシードで救われている

`devbase_seed_group_settings`（349-355 行）は `default` でしか動かない。

```sh
if [ "$group" != "default" ]; then
    return 0
fi
```

`default` は `/persistent/ai` から中身のある `.claude.json` がコピーされ、
`devbase_ensure_entry` の `[ -e "$path" ]` で早期 return する。非 default グループは
シードを飛ばすため、**必ず** 0 バイトのプレースホルダになる。

実機での確認:

| グループ | `/persistent/group/.claude.json` | Claude Code |
|---|---|---|
| `with` | 0 バイト | 起動不可 |
| `kkg` | 0 バイト（8/29 の作成時から） | 起動不可 |
| `default` | 124307 バイト | 正常 |

### 前提 3: 影響を受けるのは今のところ `.claude.json` だけ

`DEVBASE_FILE_ENTRIES` の各エントリが実際にどの経路を通るかを実機で確認した。

| エントリ | `devbase_ensure_entry` に渡るか | 空だと壊れるか | 現状 |
|---|---|---|---|
| `.claude.json` | 渡る（`DEVBASE_GROUP_SETTINGS`） | 壊れる | **顕在** |
| `settings.json` | 渡る（`DEVBASE_SHARED_CLAUDE_SETTINGS`） | 壊れる | 潜在。共通ボリュームに実体があるため未発生 |
| `CLAUDE.md` | 渡る（同上） | 壊れない | 影響なし |
| `.credentials.json` | **渡らない**（`.claude` ごと symlink されるため） | 壊れる | 影響なし |
| `history.jsonl` | 渡らない（同上） | 壊れない（JSON Lines） | 影響なし |

`.credentials.json` と `history.jsonl` は Claude Code が書くまで存在しない。

## 設計と代替案

### 採用案: 拡張子ではなく「エントリ名」で初期内容を決める

`DEVBASE_FILE_ENTRIES` と対になる `DEVBASE_JSON_FILE_ENTRIES` を置き、
`devbase_ensure_entry` が JSON エントリには `{}` を書く。

```sh
# 空で作ると不正な JSON になるエントリ。プレースホルダは {} にする。
DEVBASE_JSON_FILE_ENTRIES=(
    ".claude.json"
    ".credentials.json"
    "settings.json"
)
```

採用の理由:

- `DEVBASE_FILE_ENTRIES` が既に**エントリ名の列挙**という形をとっている。同じ形に揃えれば、
  新しいファイルを足す人が「ファイルか / ディレクトリか」「空でよいか / JSON か」の
  2 つを同じ場所で判断できる。
- 拡張子判定（`*.json`）は過去に事故を起こしている。`.jsonl` が `*.json` にマッチせず
  `history.jsonl` がディレクトリとして作られた経緯が `DEVBASE_FILE_ENTRIES` の
  コメントに残っている。同じ轍を踏まない。

`.credentials.json` を含める判断: 現状は `ensure_entry` に渡らないため無害だが、将来
分類が変わって渡るようになったときに空で作られると壊れる。`{}` は「`claudeAiOauth` キーが
無い」＝未認証として解釈されるため、ファイルが無いのと同じ扱いになる。空ファイルより安全。

### 代替案 1: `.claude.json` だけを特別扱いする

顕在化しているのはこれだけなので最小差分にはなる。**採らない。** `settings.json` は
共通ボリュームを新規に作れば同じ壊れ方をする。今直せる穴を残す理由がない。

### 代替案 2: 非 default グループもシードする

`devbase_seed_group_settings` のガードを外せば実体がコピーされて空にならない。
**採らない。** PLAN39 が分離した目的（他社テナントの認証情報を持ち込まない）を壊す。
`.claude.json` には MCP の接続情報や作業ディレクトリ履歴が入るため、コピーは事故になる。

### 代替案 3: 起動のたびに 0 バイトのファイルを検出して `{}` を書く

既に壊れたボリュームも自動修復できる。**採らない。** 「実体があるものには触れない」という
`devbase_ensure_entry` の契約を壊す。利用者が意図的に空にしたファイルを書き換える経路にも
なる。既存の被害は 2 ボリュームだけで、手当ては済んでいる。

## 受け入れ条件

1. 新規（非 default）アカウントグループの初回起動で、`~/.claude.json` の実体が `{}` になる
2. `settings.json` も同様に `{}` で作られる
3. `history.jsonl` と `CLAUDE.md` は従来どおり空で作られる（JSON Lines / Markdown は空で妥当）
4. ディレクトリエントリの扱いは変わらない
5. **実体があるファイルの中身は書き換えられない**（冪等性。2 回目以降の起動で `{}` に
   戻らない）
6. `default` グループのシード経路は従来どおり動く
7. 既存テスト 24 件が通る

## タスク

- Task 1: 受け入れ条件 1・3・5 を落とす失敗テストを書く（現状固定 → 赤を確認）
- Task 2: `DEVBASE_JSON_FILE_ENTRIES` と `devbase_ensure_entry` の分岐を実装（緑）
- Task 3: 受け入れ条件 2・4・6・7 のテストを足し、全体を通す
- Task 4: `docs/` に記載があれば更新する
- Task 5: cross-review → PR → マージ

## 検証

- `pytest tests/containers/test_entrypoint_ai_settings.py`
- `pytest tests/`（全体）
- `ruff check --select=E9,F63,F7,F82 lib`（CI と同じ）
- ShellCheck（CI が `bin/` を見る。`containers/` は対象外だが手元で確認する）

## 注意

`entrypoint.sh` の変更は `devbase up` では反映されない。イメージの再ビルド
（`devbase build --no-cache`）が要る。実機確認はビルド後に行う。
