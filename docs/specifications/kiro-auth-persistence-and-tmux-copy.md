# Kiro CLI 認証永続化と tmux コピー操作

## 概要

devbase コンテナは、Kiro CLI 2.x の認証状態と実行データをアカウントグループ単位で永続化する。
また、tmux の履歴をマウスで遡り、履歴上の文字列をドラッグ選択して `Ctrl+C` でコピーできる
既定設定を提供する。

## 対象範囲

- Kiro CLI が `~/.local/share/kiro-cli` に保存する状態
- base イメージに `/etc/tmux.conf` として配置する tmux の既定設定
- Kiro CLI のログイン方式と認証方式は変更しない
- `Ctrl+Home` の tmux キー割り当ては定義しない

## 仕様

### Kiro CLI の状態

`~/.local/share/kiro-cli` は、現在のアカウントグループに対応する
`/persistent/group/.local/share/kiro-cli` へのシンボリックリンクとする。Kiro CLI の認証状態は
AWS アカウントに紐づくため、全コンテナ共通の `/persistent/ai` には保存しない。

初回適用時にホーム側へ既存データがあり、グループ側の保存先がまだ存在しない場合は、リンクを
作成する前に既存データをグループ側へコピーする。グループ側にデータがある場合は上書きせず、
ホーム側が別グループへのシンボリックリンクである場合もリンク先をシード元として使用しない。

常に次の条件を保つ。

- 共通 AI 資産は `/persistent/ai`、アカウントに紐づく認証状態は `/persistent/group` に置く。
- 既存の永続データを起動時のシードで上書きしない。
- 異なるアカウントグループ間で Kiro CLI の状態を共有しない。

### tmux の履歴選択とコピー

base イメージの tmux は `mouse on` を既定とし、マウスホイールで copy-mode に入って履歴を
遡れるようにする。copy-mode と copy-mode-vi の双方で、履歴上のドラッグ開始時に新しい選択を
開始し、ボタンを離した後も選択範囲を保持する。

選択中の `Ctrl+C` は `copy-selection-and-cancel` を実行する。tmux は選択内容を OSC 52 経由で
端末のクリップボードへ送り、copy-mode を終了する。コピーせずに選択を終了する場合は `q` を
使用する。ペインをまたぐ端末側の選択には `Shift` + ドラッグを使用する。

`Ctrl+Home` は VS Code や Windows 側の操作と競合するため、tmux では割り当てない。

## データ・設定

| 対象 | 保存先または設定 | 共有範囲 |
| --- | --- | --- |
| Kiro CLI 2.x | `/persistent/group/.local/share/kiro-cli` | 同じアカウントグループ |
| tmux 既定設定 | `/etc/tmux.conf` | base イメージから作成したコンテナ |

tmux は `/etc/tmux.conf` の後に `~/.tmux.conf` を読むため、利用者は個人設定で既定値を上書き
できる。`~/.tmux.conf` 自体は永続化対象ではない。

## 運用

- Kiro CLI の状態はコンテナを再作成しても同じグループ volume に残る。
- アカウントグループを切り替えると、新しいグループの保存先へリンクを張り直す。
- tmux 設定の変更はイメージ再ビルドとコンテナ再作成後に適用される。稼働中セッションには
  `tmux source-file /etc/tmux.conf` で読み込める。
- 永続化を切り戻す場合も、コピー済みの Kiro CLI データは削除しない。

## テスト観点

- Kiro CLI のホームパスがグループ volume へのシンボリックリンクになること。
- 初回の既存ホームデータを保存し、既存のグループデータを上書きしないこと。
- グループ間および同一ホームでのグループ切り替え時に状態が混ざらないこと。
- Kiro CLI のホーム由来データを `/persistent/ai` からシードしようとしないこと。
- tmux が `mouse on` を解釈し、両copy-modeでドラッグ選択保持と `Ctrl+C` コピーを提供すること。
- `Ctrl+Home` のbindingが追加されていないこと。
- `~/.tmux.conf` が `/etc/tmux.conf` の既定値を上書きできること。

## 関連リンク

- [コンテナ操作ガイド](../user/container-operations.md)
