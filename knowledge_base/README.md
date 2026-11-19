# Knowledge Base

Django アプリケーションの機能・運用資料の入口です。

## 機能別 Python

[機能別構成ガイド](tenasapo_knowledge/features/README.md) に分割範囲と既存実装の所在を記載しています。

| メニュー・用途 | 配置先 |
| --- | --- |
| Knowledge（現在は便利機能の表示・お気に入り） | [features/knowledge](tenasapo_knowledge/features/knowledge/views.py) |
| INPUT（入力ハブ・便利機能の登録） | [features/input](tenasapo_knowledge/features/input/views.py) |
| ユーザー管理（新規作成・デモ／契約発行） | [features/users](tenasapo_knowledge/features/users/views.py) |
| マニュアル | [features/manuals](tenasapo_knowledge/features/manuals/views.py) |
| 集計（投稿上限設定） | [features/reports](tenasapo_knowledge/features/reports/views.py) |
| 共通の権限・履歴処理 | [features/shared](tenasapo_knowledge/features/shared/access.py) |

**全機能の移動はまだ完了していません。** 記事編集・ユーザー一覧・集計本体などは [既存ビュー](tenasapo_knowledge/views.py) に残っています。旧 import と URL は維持しています。

## 資料・運用補助

- [資料索引](docs/README.md)：ユーザー機能、Azure 配備、Google 連携、保守資料。
- [補助スクリプト索引](scripts/README.md)：開発、配備、ユーザー、Knowledge ごとの Python 実装。ルートの既存実行入口は互換性のため残しています。
- [変更履歴](CHANGELOG.md)

## 配置を維持するもの

- [manage.py](manage.py)、[設定](config/settings.py)、[依存関係](requirements.txt) は起動・配備の入口としてルート構成を維持。
- モデル・マイグレーション・テストは Django の検出・既存 CI を維持するため今回一括移動しません。
- テンプレート・静的ファイルも既存の探索パスを維持します。
- ローカル DB、秘密設定、アップロード、バックアップ、OLD は機能別整理の移動対象ではありません。Git 除外の規則は [.gitignore](.gitignore) を参照。

検証には [CI 用設定](config/settings_ci.py) を使います。DB 移行や OAuth 登録等の運用スクリプトを、整理確認のために実行しないでください。