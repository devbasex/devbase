# #400: base から使う者が見当たらない中身を外す（dockerd・aws-cdk-lib・root の uv・apt の片付けの漏れ）

正は課題の本文（#400）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> base イメージから、使う者が見当たらない中身と片付けの漏れを外す。base の削減の段階 1（段階 2・3 は別の課題）。利用者は 2026-10-03 にこの削減を承認した（MVV の P1）。
>
> | 中身 | 場所 | 大きさ | 外す根拠 |
> | --- | --- | --- | --- |
> | `docker-ce` と `containerd.io`（dockerd と containerd） | `containers/base/Dockerfile:71` | 106+81MB | DinD でだけ使う。`ENABLE_DIND` を設定している箇所はリポジトリとプラグインに見当たらない（`grep -rn ENABLE_DIND`）。プロジェクトはどれもホストの docker.sock を mount する。CLI・buildx・compose は残す |
> | グローバルの `aws-cdk-lib` | `Dockerfile:159` | 181MB | Node はグローバルのモジュールを `require` で探さず、CDK のアプリは自分の package.json に持つ。CLI の `aws-cdk` は残す |
> | root の uv | `Dockerfile:156` | 40MB | ubuntu の uv（`Dockerfile:211`）と重複し、root で uv を使う処理が見当たらない |
> | `/var/lib/apt/lists` | `Dockerfile:235-245` | 42MB | `playwright install --with-deps` の `apt-get update` が残し、片付けが消していない |
> | `/usr/share/doc`・`man`・`/usr/include/node` など | apt の層 | 約 75MB | 実行時に読まない。dpkg の path-exclude で入れない |
>
> 決めること:
> - DinD の扱い: `containers/base/dind` と entrypoint の `ENABLE_DIND` の分岐を消すか、dockerd を入れたうえで使う派生へ移すか
> - path-exclude で外す置き場の範囲（copyright は残す）
>
> 受け入れ条件（起票時）:
> 1. 建てた base（arm64・amd64）に上の中身が無い。docker CLI・`docker buildx`・`docker compose`・`aws-cdk`・ubuntu の uv は動く
> 2. 展開後の大きさが arm64 で 450MB 以上減る（`du -sbx /` の前後で測り、PR に記録する）
> 3. lfm が建ち、`tests/containers/` が通る（lfm は自分で docker-ce を入れるため影響しない見込み）
> 4. DinD を廃止するなら、利用者向けの文書と CHANGELOG に書く
>
> 由来: スプリント m7b の振り返りの後、利用者が「base が機能盛りすぎで肥大化している」として削減のプランを求めた（2026-10-03）。

## 目的

- base を建てる時間・取得する量・ディスクの消費を、誰も使っていない中身の分だけ減らす（MVV の Value 1「base を重くしすぎず、重い道具は派生イメージに置く」）
- 利用者がコンテナの中で使う道具（docker CLI・buildx・compose・`cdk`・uv）は、削減の前と同じに使える

## 調べて分かったこと（2026-10-03）

| 事実 | 出所 |
| --- | --- |
| lfm は base を `FROM` で継がず、`/entrypoint.sh` と `/usr/local/bin/dind` を base のイメージから `COPY --from` で取り込む。lfm は自分で `docker-ce`・`containerd.io` を入れ、`/etc/docker/daemon.json` に NVIDIA の runtime を書く（DinD を前提にした作り） | `containers/lfm/Dockerfile:55-60`・`:169-173`・`:175-200` |
| entrypoint の DinD の分岐は `ENABLE_DIND` が `true` / `1` のときだけ動き、最初に `/var/run/docker.sock` を消してから `dind dockerd` を起こし、`docker info` を最大 30 秒待つ | `containers/base/entrypoint.sh:862-` |
| `ENABLE_DIND` を設定している compose・env は、このリポジトリと手元のプラグイン（`repos/` の下）に見当たらない。利用者向けの文書（`docs/`・`README`・`CHANGELOG.md`）にも DinD の案内は無い | `grep -rn "ENABLE_DIND\|DinD" docs README* CHANGELOG.md lib` と `grep -rln "ENABLE_DIND\|dockerd" repos/ projects/`（当たりはプラグインの 1 つ（proj-a）の compose の注記だけで、ホストの dockerd を指す） |
| base を `FROM` で継ぐ派生イメージは bi-tools・general・go・latex・php・php85・trygroup。dpkg の設定（`/etc/dpkg/dpkg.cfg.d/`）は継がれ、latex・php・php85 は自分で `apt-get install` する | `containers/*/Dockerfile` |
| 派生イメージに root で uv を使う手順は無い（bi-tools の `uv tool install` は `USER ubuntu` の下） | `containers/bi-tools/Dockerfile:29-47` |
| `PATH` の `/root/.local/bin` は、lfm の設定の取り込みの試験が「lfm へ届けない要素」として名前で除外している | `tests/containers/test_lfm_base_settings.py:40`・`docs/specifications/lfm-base-settings.md:225` |
| CI はイメージを建てない。イメージの確かめは手元で行う | `.github/workflows/ci.yml` |

## 前提

- 前提 1（DinD の扱い）: DinD は廃止しない。base から dockerd と containerd を外し、DinD を使える派生は lfm だけにする。lfm が取り込む `dind` と entrypoint の `ENABLE_DIND` の分岐は lfm で今と同じに動かす（置き場を base に残すか lfm へ移すかは設計で決める）。根拠: lfm は自分で dockerd を入れ NVIDIA の runtime を DinD 向けに設定しており、分岐を消すと lfm の機能が落ちる。起票時の選択肢「消す」「使う派生へ移す」のうち、退行の無い後者を採る
- 前提 2: base とその派生で `ENABLE_DIND=true` を設定している利用者はいない（上の調べ）。設定していた利用者は、base の建て直しの後に DinD が起きなくなる。この利用者へは CHANGELOG と entrypoint の知らせで伝え、lfm を使うよう案内する
- 前提 3: root で uv を使う利用者・処理は無い。base の root の uv を外した後、`sudo uv` は「コマンドが無い」で失敗してよい
- 前提 4: グローバルの `aws-cdk-lib` を `require` する利用者は無い。CDK のアプリは自分の `package.json` に `aws-cdk-lib` を持ち、`cdk synth` はそれを使う
- 前提 5: dpkg の除外設定は base を `FROM` で継ぐ派生イメージにも効き、派生が後から apt で入れるパッケージの文書も入らなくてよい（copyright は残る）。lfm は `/etc/dpkg` を取り込まないため対象外
- 前提 6: `/usr/include/node` を外すと、npm のネイティブアドオンのビルドでは node-gyp がヘッダを取得する（ネットワークが要る）。これを許す
- 前提 7: amd64 の確かめは、amd64 の Docker が動く端末（WSL2 の端末など）か、`docker buildx build --platform linux/amd64` で行う。どちらで行ったかを PR に書く
- 前提 8: 大きさは、建てた base から作ったコンテナで `du -sbx /` を測った値で比べる。前は main の Dockerfile で建てた base、後はこの変更の Dockerfile で建てた base とし、同じ端末・同じ日に建てる（apt と npm の取得する版の差を小さくする）

## 対象範囲

含む:
- base の Dockerfile から `docker-ce`・`containerd.io`・グローバルの `aws-cdk-lib`・root の uv を外す
- base の最後の利用者の層で残る `/var/lib/apt/lists` を消す
- dpkg の除外設定で、`/usr/share/doc`（copyright を除く）・`/usr/share/man` などの文書と `/usr/include/node` を入れない
- base に dockerd が無いときの entrypoint の `ENABLE_DIND` の扱い
- lfm の DinD を今と同じに動かすための、`dind` と分岐の置き場の調整
- 外した中身に触れる試験（`tests/containers/`）と仕様（`docs/specifications/`）の追従
- `CHANGELOG.md` の `[Unreleased]` への記入

含まない:
- 段階 2・3 の削減（重い道具を派生イメージへ移す・版の固定の見直し）。別の課題
- lfm の中身の削減（lfm が自分で入れる `docker-ce` などは触らない）
- base の他の道具の追加・版の変更
- イメージを建てる CI の追加
- 利用者の環境で `devbase up` を実行して確かめること（P3。リリース後テストで利用者が行う）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 変更前の base を建て、展開後の大きさを測った | 実装の着手 | 建たなければ main の不具合として別に起票し、測りを止める | — |
| E2 | 変更後の base を arm64 で建てた | Dockerfile の変更 | 建たなければ変更を直す | E1 |
| E3 | 変更後の base を amd64 で建てた | E2 の成功 | 建たなければ変更を直す（アーキの差は `case` の分岐で起きやすい） | E2 |
| E4 | 建てた base の中身を確かめた（外した物が無く、残す道具が動く） | E2・E3 の成功 | 残す道具が動かなければ変更を直す | E2・E3 |
| E5 | 展開後の大きさを測り、減った量を PR に記録した | E4 の成功 | 450MB に届かなければ、届かない理由と内訳を PR に書いて人へ戻す | E1・E4 |
| E6 | 変更後の base から lfm と、`FROM` で継ぐ派生イメージを建てた | E2 の成功 | `COPY --from` の取り込み元が消えて建たなければ、取り込みの置き場を直す | E2 |
| E7 | lfm で `ENABLE_DIND=true` のコンテナを起こし、DinD の dockerd が応答した | E6 の成功 | 応答しなければ `dind` か分岐の置き場の調整を直す | E6 |
| E8 | base（またはその派生）で `ENABLE_DIND=true` のコンテナを起こし、entrypoint が dockerd の無いことを知らせて起動を続けた | E2 の成功 | 起動が止まる・30 秒待つ・ホストの docker.sock が消えるなら分岐を直す | E2 |
| E9 | 利用者が base を建て直した | main へのマージ（配布） | 建たなければ利用者は前のイメージで動き続ける。リリース後テストで見つける | E2〜E8・マージ |

## 用語

| 用語 | 意味 |
| --- | --- |
| DinD | コンテナの中で dockerd を起こし、そのコンテナの中だけの docker を使う形。`ENABLE_DIND` で有効にする。ホストの docker.sock を mount して使う形と区別する |
| 展開後の大きさ | 建てたイメージから作ったコンテナで `du -sbx /` を測ったバイト数。`docker images` の圧縮後の大きさとは違う |
| 文書の除外 | dpkg の `path-exclude` の設定で、apt が入れるパッケージから文書などの置き場を展開しないこと |

## 受け入れ条件

- [ ] 1. 変更後の base（arm64・amd64）で、`dockerd`・`containerd` がコマンドとして見つからない（`command -v` が失敗する）
- [ ] 2. 変更後の base（arm64・amd64）で、ubuntu の利用者として `docker --version`・`docker buildx version`・`docker compose version`・`cdk --version`・`uv --version` がどれも終了コード 0 で版を出す
- [ ] 3. 変更後の base で、`npm ls -g aws-cdk-lib` に `aws-cdk-lib` が無く、`/root/.local/bin/uv` が無い
- [ ] 4. 変更後の base で、`/var/lib/apt/lists` の下に `lock` と `partial` のほかのファイルが無い
- [ ] 5. 変更後の base で、`/usr/share/man` の下と `/usr/include/node` にファイルが無く、`/usr/share/doc` の下には `copyright` のほかのファイルが無い。`/usr/share/doc/<パッケージ>/copyright` は apt で入れたパッケージについて残る
- [ ] 6. arm64 で、変更前の base と変更後の base の展開後の大きさの差が 450MB（450,000,000 バイト）以上ある。前後の値と差を PR に記録する
- [ ] 7. 変更後の base から lfm が建ち、lfm に `dockerd` と `/usr/local/bin/dind` があり、`ENABLE_DIND=true` で起こした lfm のコンテナの中で `docker info` が DinD の dockerd から応答する
- [ ] 8. 変更後の base から、`FROM devbase-base:latest` で継ぐ派生イメージ（bi-tools・general・go・latex・php・php85・trygroup）が建つ
- [ ] 9. dockerd の無いイメージで `ENABLE_DIND=true` を設定して起こすと、entrypoint は dockerd が無いことを 1 行で出して DinD の手順を飛ばし、起動を続ける。`/var/run/docker.sock` を消さず、`docker info` を待たない
- [ ] 10. `ENABLE_DIND` を設定しない base のコンテナの起動は、変更の前と同じ順で同じ用意を行う（entrypoint の既存の試験が通る）
- [ ] 11. base の Dockerfile が 1〜5 を満たす形であることを、Docker を起こさない試験（`tests/containers/` の Dockerfile を読む試験）が固定する。外した物を Dockerfile へ戻すと、その試験が名前を挙げて落ちる
- [ ] 12. `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` が通る
- [ ] 13. `CHANGELOG.md` の `[Unreleased]` に、base から dockerd・containerd・グローバルの `aws-cdk-lib`・root の uv・文書を外したことと、DinD は lfm で使えることを書く
- [ ] 14. base の Dockerfile の差分は「対象範囲」の含むに挙げた物を外す変更だけで、ほかの道具の追加・削除・版の変更を含まない

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | arm64 で展開後の大きさが 450MB 以上減る（受け入れ条件 6） |
| 移行性 | 利用者は base を建て直すだけで移る。ボリューム・設定・機密は触らない。戻すときは前の Dockerfile で建て直す |
| システム環境 | arm64 と amd64 の両方で建ち、受け入れ条件 1〜5 を満たす |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | devbase の CLI は変わらない。base のイメージから `dockerd`・`containerd`・グローバルの `aws-cdk-lib`・root の uv・文書が消える。base とその派生での `ENABLE_DIND=true` は DinD を起こさなくなる（lfm は変わらない） |
| データ | 変わらない |
| 既存の振る舞い | base の entrypoint の `ENABLE_DIND` の分岐（dockerd が無いときの扱い）。派生イメージで apt が入れるパッケージの文書が入らなくなる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `devbase build` で base と lfm・派生を建てる（建て方は設計で決める） |
| テスト | `env -u DEVBASE_ROOT uv run --locked pytest tests/ -q` |
| 静的解析・型検査 | `uvx ruff check --select=E9,F63,F7,F82 lib`・`python3 .github/scripts/proper_term_check.py`・base のシェルスクリプトの ShellCheck（開発参加ガイドの「CI が実行するもの」） |
| 手動確認（マージ前） | arm64 で建てた base と lfm で受け入れ条件 1〜7・9 を確かめ、出力と大きさを PR に記録する。amd64 で建てた base で受け入れ条件 1〜5 を確かめ、どの端末・どの方法で建てたかを PR に記録する |
| 手動確認 | 利用者が自分の環境で base を建て直し、普段のプロジェクトで docker・`cdk`・uv が使えることを確かめる（リリース後テスト。P3 のため利用者が行う） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md` の「このリポジトリ」。イメージの定義は `containers/`、試験は `tests/containers/`、確定仕様は `docs/specifications/` |
| コーディング規約 | `AGENTS.md` の「書き方」と開発参加ガイド。シェルは ShellCheck を通す |
| テスト戦略 | Dockerfile の中身は Docker を起こさない試験で固定し（既存の `test_base_dockerfile_*.py` と同じ形）、entrypoint の分岐は既存の entrypoint の試験の形で確かめる。イメージを建てて確かめる条件は手動確認（マージ前）で PR に記録する |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 全体のテスト・lint・固有の語の検査を push の前に打つ。base を建てる前に `docker system df` で空きを見て、作業用のタグは記録の後に消す（`AGENTS.md` の落とし穴） |
| 確認してから行う | 対象範囲の外の道具を base から外す・足す・版を変える（P1） |
| 行わない | 利用者の環境で `devbase up` を実行する（P3）。タグ・Release を作る（P2）。lfm の中身を削る。プラグインのリポジトリの compose を直す |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| `dind` と entrypoint の `ENABLE_DIND` の分岐の置き場（base に残して lfm が取り込み続けるか、lfm へ移すか） | 設計（`design`） | 設計 PR |
| dpkg の除外設定で外す置き場の一覧（`/usr/share/doc`・`man`・`info`・`locale`・`/usr/include/node` のどこまでか）と、除外設定を置く位置（最初の `apt-get install` より前） | 設計（`design`） | 設計 PR |
| 外した後の `PATH` の `/root/.local/bin` を残すか消すか（lfm の設定の取り込みの試験の除外表に影響する） | 設計（`design`） | 設計 PR |
| amd64 を建てる端末と方法（前提 7） | 実装の担当 | 手動確認（マージ前） |
