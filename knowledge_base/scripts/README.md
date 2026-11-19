# 用途別の補助スクリプト

| 用途 | 実装 | 互換入口（workspace root） |
| --- | --- | --- |
| ローカル開発環境セットアップ | [development/locally_dev.py](development/locally_dev.py) | [../locally_dev.py](../locally_dev.py) |
| PostgreSQL データ移行補助 | [deployment/migrate_to_postgres.py](deployment/migrate_to_postgres.py) | [../migrate_to_postgres.py](../migrate_to_postgres.py) |
| Google OAuth 登録 | [users/register_google_oauth.py](users/register_google_oauth.py) | [../register_google_oauth.py](../register_google_oauth.py) |
| FAQ カテゴリ投入 | [knowledge/seed_categories_2plus.py](knowledge/seed_categories_2plus.py) | [../seed_categories_2plus.py](../seed_categories_2plus.py) |

## 実行契約と注意

- 既存のルート入口からのファイル実行を維持する。新実装もファイル単体実行、workspace root からのモジュール実行に対応する。
- 各実装の `BASE_DIR` は `Path(__file__).resolve().parents[2]`、すなわち workspace root。現在の作業ディレクトリは変更しない。単体実行時にも同ルートを `sys.path` に補う。
- 開発補助はルートの仮想環境・環境設定・[../manage.py](../manage.py) を参照する。コマンド実行時の作業ディレクトリもルート。
- 移行補助のバックアップ先はルートの sqlite_backup.json。既存の接続確認、dumpdata、migrate、loaddata、検証の処理順序は変更していない。
- **これらは実行すると依存関係のインストールや DB 書き込みを行う運用スクリプト。調査目的で実行しない。** 特に移行補助の DB 設定・接続処理の妥当性は今回の整理で改修・実環境検証していない。
- import だけでは Django 初期化、OAuth 値の取得、モデルの読み込み、DB 操作は行わない。必要な処理は明示的な `main()` 呼び出しで開始する。
- 既存の `DJANGO_SETTINGS_MODULE` は上書きしない。未指定の実行時のみ本番用の既定設定を使うため、運用前に設定を確認する。
- 旧公開関数と `CATEGORY_DATA` はルートからも同一オブジェクトを再公開する。実装内の依存先をモックする場合は新実装のモジュールを対象にする。
- 旧文書内の親階層を前提とした移行補助の実行例ではなく、上表の実在するルート入口または新実装を使用する。

## 顧客申請のGAS連携

- [users/customer_application_bridge.gs](users/customer_application_bridge.gs)：GAS発信型の回答取込・承認後のGmail下書き作成。Webアプリ公開はしません。
- [users/appsscript.customer_application.json](users/appsscript.customer_application.json)：専用GASプロジェクトのマニフェスト内容。
- [設定・本番の手動確認手順](../docs/features/users/CUSTOMER_APPLICATION_SETUP.md)：共有秘密、固定実行ユーザー、0091適用と初回確認の順序。
- [users/test_customer_application_bridge.cjs](users/test_customer_application_bridge.cjs)：外部通信しないGASモックテスト。

最初は `testNexusBridge` → `syncNexusApplications` → Nexusで1件承認 → `processNexusInvitationDrafts` の順に手動確認します。定期トリガーは検証後にだけ明示的に作成してください。自動承認・メール自動送信は行いません。

## 安全な単体テスト

[../tenasapo_knowledge/test_script_entrypoints.py](../tenasapo_knowledge/test_script_entrypoints.py) は標準の `unittest` で実行でき、Django のテスト DB を作成しない。

テストは設定初期化・管理コマンド・モデル・ファイル書き込みをモックする。隔離 subprocess は秘密値を引き継がず、設定／モデル import、秘密ファイル読み出し、DB 接続、ファイル書き込みを禁止し、CLI が `main()` に到達した時点で本体実行前に停止する。DB 移行や OAuth 登録・カテゴリ投入を実際に実行するテストではない。