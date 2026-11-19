# 資料索引

ルートにあった運用・機能資料を用途別に配置しています。各資料のファイル名は維持し、変更履歴はルートの [CHANGELOG.md](../CHANGELOG.md) に残しています。

## 機能別資料

### ユーザー一覧・顧客申請・利用規約

- [顧客申請の取込・招待下書きの設定](features/users/CUSTOMER_APPLICATION_SETUP.md)
- [利用規約のタイトル選択](features/users/TERMS_HEADER_TITLE.md)

### Knowledge・INPUT

今回の移動対象であるルートMarkdownには、Knowledge・INPUT単独の専用資料はありません。既存の変更記録は [CHANGELOG.md](../CHANGELOG.md) を参照してください。今後専用資料を追加する際は、Knowledgeは docs/features/knowledge/、INPUTは docs/features/input/ に配置する方針です。空の分類フォルダーや新しい機能仕様書は今回作成していません。

## デプロイ・Azure移行

- [本番適用の準備と確認](deployment/PRODUCTION_RELEASE.md)
- [Azure App Serviceへのデプロイ手順](deployment/DEPLOY_AZURE.md)
- [Azure App Service移行ガイド](deployment/AZURE_MIGRATION_GUIDE.md)
- [Azure移行チェックリスト](deployment/AZURE_MIGRATION_CHECKLIST.md)

## 外部連携

### Google Chat

- [Azure Linuxの予約通知](integrations/google-chat/AZURE_SCHEDULED_GOOGLE_CHAT.md)
- [Windowsの予約通知タスク運用](integrations/google-chat/WINDOWS_SCHEDULED_GOOGLE_CHAT.md)

### Google Drive

- [月次レポートPDFのDrive保存](integrations/google-drive/GOOGLE_DRIVE_REPORT_PDF.md)

## 保守・整理記録

- [単発スクリプトの整理](maintenance/CODE_CLEANUP.md)

## 配置と参照の注意

- 文書内の相対リンクは移動後の場所を基準にしています。コマンド例の実行場所やアプリ・スクリプトの配置は、この資料移動では変更していません。
- 各資料の実施日・検証結果・適用状況は記載当時の記録です。今回の整理で本番確認や適用をやり直したものではありません。
- 保守記録内のOLD・バックアップ、運用手順内の仮想環境・ログ等へのリンクは、ローカル環境向けの既存参照です。Git上や別環境には存在しない場合があります。