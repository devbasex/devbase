# Contributing to devbase

devbase へのコントリビューションを歓迎します。バグ報告、機能提案、ドキュメント改善、コード変更のいずれも受け付けています。

## 始める前に

- バグ報告・機能提案は [GitHub Issues](https://github.com/devbasex/devbase/issues) にお願いします。
- 大きな変更を加える場合は、事前に Issue を立てて方針を相談してください。
- 既存の Issue・PR と内容が重複していないか確認してください。

## 開発フロー

1. リポジトリを fork し、作業用ブランチを切ります。
   ```bash
   git clone https://github.com/<your-account>/devbase.git
   cd devbase
   git checkout -b feature/<topic>
   ```
2. 変更を加え、ローカルで動作確認します。
3. コミットし、自分の fork に push します。
4. `devbasex/devbase` の `main` に対して Pull Request を作成します。

`main` への直接 push は禁止されています。すべての変更は PR 経由で取り込まれます。

## コミットメッセージ

日本語または英語のいずれでも構いません。1 行目には変更の種別と要約を簡潔に記述してください。

例:

```
追加: PHP 8.5 ベースの開発コンテナ
修正: スナップショット世代管理のオフバイワン
```

## コーディング規約

- Python: PEP 8 に準拠。CLI 実装は `lib/devbase/` 配下にあります。
- Shell: Bash / Zsh の双方で動作することを確認してください。
- ドキュメントは `docs/` 配下に配置し、図表は Mermaid または PlantUML を使用してください（ASCII アートは原則使用しない）。

詳細は [docs/developer/contributing.md](docs/developer/contributing.md) を参照してください。

## Pull Request

- PR テンプレートに沿って、変更内容と動作確認手順を記載してください。
- レビュー指摘があった場合は、追加コミットで対応します（force push は避けてください）。
- CI が通っていることを確認してから merge ready にしてください。

## ライセンス

コントリビュートいただいた変更は、本プロジェクトの [MIT License](LICENSE) のもとで配布されることに同意したものとみなします。
