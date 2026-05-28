# Azure 移行チェックリスト

## 1. ローカル環境での準備（このステップを完了してから Azure へ移行）

### Django 設定
- [ ] `DEBUG = False` に設定（settings.py で環境変数から読み込み）
- [ ] `SECRET_KEY` を環境変数から読み込み（`.env.example` に記載）
- [ ] `ALLOWED_HOSTS` に本番ドメインを追加
- [ ] `CSRF_TRUSTED_ORIGINS` に本番URLを追加
- [ ] PostgreSQL への接続を `DB_*` 環境変数から読み込み確認

### パッケージ・依存関係
- [ ] `requirements.txt` が最新（`pip freeze > requirements.txt`）
- [ ] `psycopg2-binary` が含まれている
- [ ] `gunicorn` または `waitress` が含まれている
- [ ] `whitenoise` がインストール済み

### 静的ファイル・メディア
- [ ] `STATIC_ROOT` が設定されている（`staticfiles/` に集約）
- [ ] `python manage.py collectstatic --noinput` で実行可能
- [ ] `MEDIA_ROOT` が適切に設定（Azure Storage Blob 使用推奨）
- [ ] `.gitignore` に `db.sqlite3`, `.env`, `staticfiles/` を追加

### WSGI
- [ ] `wsgi.py` に WhiteNoise が統合されている
- [ ] `application` が正しく定義されている

---

## 2. Azure リソース設定

### App Service Plan
- [ ] **Linux** プラン（Django は Linux 推奨）
- [ ] SKU は開発環境なら **B1**, 本番なら **B2 以上** を推奨
- [ ] リージョンは **Japan East** （日本）

### Web App
- [ ] ランタイム: **Python 3.11**
- [ ] 起動ファイル: `gunicorn config.wsgi` または `waitress-serve --port 8000 config.wsgi:application`
- [ ] デプロイスロット: `staging` を作成して検証後に `production` へ swap

### PostgreSQL Flexible Server
- [ ] サーバー作成済み（SKU: **Standard_B1ms** 推奨）
- [ ] ファイアウォール設定: App Service IP を allow
- [ ] SSL: `require` に設定（Azure では自動）
- [ ] バックアップ: 7日以上の保持設定

### Network
- [ ] VNet 設定（オプション、セキュリティ重視の場合）
- [ ] DNS レコード: App Service のIPをカスタムドメインに割り当て

---

## 3. 環境変数・シークレット管理

### Azure Key Vault（推奨）
- [ ] Key Vault リソース作成
- [ ] 以下のシークレットを追加:
  - [ ] `SECRET-KEY` : f#zjx28!23@8esjhokxzg=x(q@@%d90_x*91x%))voa%@7ud3#
  - [ ] `DB-PASSWORD` :P@ssw0rd
  - [ ] `EMAIL-HOST-PASSWORD` : メール送信用パスワード

### App Service 設定
- [ ] Application Settings に以下を追加:
  - [ ] `DEBUG` = False
  - [ ] `ALLOWED_HOSTS` = `yourdomain.azurewebsites.net,yourdomain.com`
  - [ ] `CSRF_TRUSTED_ORIGINS` = `https://yourdomain.azurewebsites.net,https://yourdomain.com`
  - [ ] `DB_ENGINE` = `django.db.backends.postgresql`
  - [ ] `DB_NAME` = `nexusdb` ###移行後に作成
  - [ ] `DB_USER` = `nexusadmin`
  - [ ] `DB_HOST` = `nexus2026-psql`
  - [ ] `DB_PORT` = 5432
- [ ] `Database` タブで接続文字列を設定（オプション）

---

## 4. デプロイ方法

### 方法 A: ZIP Deploy（最もシンプル）
- [ ] `.venv`, `.git`, `db.sqlite3` を除外した ZIP ファイルを作成
- [ ] `az webapp deployment source config-zip` でアップロード
- [ ] App Service ログで デプロイ確認

### 方法 B: GitHub Actions（推奨、自動化）
- [ ] GitHub レポジトリを設定
- [ ] `.github/workflows/deploy.yml` を追加
- [ ] GitHub Secrets に以下を追加:
  - [ ] `AZURE_WEBAPP_PUBLISH_PROFILE` : App Service 発行プロファイル
- [ ] `main` ブランチへの push で自動デプロイ

### 方法 C: Local Git Deploy
- [ ] Azure App Service のデプロイユーザーを設定
- [ ] `git remote add azure` で Azure を追加
- [ ] `git push azure main` でデプロイ

---

## 5. データベース・データ移行

### マイグレーション実行
- [ ] `python manage.py migrate` を実行（Azure SSH コンソール）
- [ ] `python migrate_to_postgres.py` でデータ移行（SQLite → PostgreSQL）
- [ ] データ検証: `python manage.py dbshell`

### スーパーユーザー作成
- [ ] `python manage.py createsuperuser` 実行
- [ ] 管理者アカウントを作成

---

## 6. SSL/TLS・セキュリティ設定

### HTTPS
- [ ] App Service 設定で **HTTPS のみ** を有効化
- [ ] カスタルドメインの場合、SSL 証明書をバインド
  - [ ] Azure Key Vault から証明書フロントエンド
  - [ ] または Let's Encrypt で無料証明書取得

### セッション・CSRF
- [ ] `SESSION_COOKIE_SECURE = True` （settings.py）
- [ ] `CSRF_COOKIE_SECURE = True` （settings.py）
- [ ] `SECURE_SSL_REDIRECT = True` （settings.py - environments で制御）

### CORS・フレーム オプション
- [ ] `X-Frame-Options: DENY` （デフォルト）
- [ ] `X-Content-Type-Options: nosniff`
- [ ] 必要に応じて CORS ヘッダ設定

---

## 7. 監視・ロギング

### Application Insights
- [ ] Application Insights リソース作成
- [ ] App Service と統合（自動）
- [ ] 開始してから 30分以上の HTTP リクエスト収集

### ログ設定
- [ ] App Service ログを有効化:
  - [ ] アプリケーションログ（ファイルシステム）
  - [ ] ウェブサーバーログ
  - [ ] 詳細なエラーメッセージ
  - [ ] 失敗した要求のトレース

### アラート設定
- [ ] CPU 使用率 > 80% でアラート
- [ ] メモリ使用率 > 85% でアラート
- [ ] HTTP 5xx エラーが連続で発生
- [ ] レスポンス時間 > 2秒

---

## 8. バックアップ・復旧計画

### PostgreSQL
- [ ] Azure バックアップ設定: 7日以上の保持
- [ ] 定期的な pg_dump による外部バックアップ

### Web App
- [ ] Backup/Restore 設定
- [ ] リストア ポイント: 定期的に確認

### コンテンツ
- [ ] Git リモートリポジトリに main ブランチが存在
- [ ] 定期的な GitHub/Azure DevOps へのプッシュ

---

## 9. DNS・カスタムドメイン設定（必要な場合）

### Azure 側
- [ ] App Service にカスタムドメインを追加
- [ ] SSL 証明書をバインド

### DNS（Route53/Cloudflare/他）
- [ ] A レコード: `yourdomain.com` → `<app>.azurewebsites.net`
- [ ] CNAME: `www.yourdomain.com` → `yourdomain.com`
- [ ] MX レコード（メール使用時）を確認

---

## 10. 本番環境での動作確認

### Smoke Test
- [ ] `https://app.azurewebsites.net/` にアクセス可能
- [ ] ログイン画面が表示される
- [ ] ログイン機能が動作
- [ ] 知識ベース記事が表示される
- [ ] メール送信が動作（管理画面でテスト）
- [ ] ファイル添付が動作

### パフォーマンステスト
- [ ] レスポンス時間が 2秒以下
- [ ] CPU/メモリ使用率が正常範囲（< 70%）
- [ ] エラーログに異常がない

### セキュリティテスト
- [ ] データベース接続が暗号化（SSL）
- [ ] シークレット（DB パスワード等）がログに出力されていない
- [ ] CORS ポリシーが適切（外部サイトからのアクセス制御）

---

## 11. トラブルシューティング・参考命令

### ログ確認
```bash
az webapp log tail -g <resource-group> -n <app-name>
```

### SSH コンソール
```bash
az webapp ssh -g <resource-group> -n <app-name>
```

### PostgreSQL 接続テスト
```bash
psql -h <server>.postgres.database.azure.com -U pgadmin -d appdb
```

### 環境変数確認（App Service）
```bash
az webapp config appsettings list -g <resource-group> -n <app-name>
```

---

## 12. ロールバック計画

問題が発生した場合：

1. **Staging スロットで検証**  
   → スロット swap で本番切替（失敗時は swap back）

2. **Git で前のバージョンに戻す**  
   ```bash
   git revert <commit-hash>
   git push azure main
   ```

3. **データベース復旧**  
   ```bash
   az postgres flexible-server restore -n <server> -g <rg> --restore-point-in-time "2026-05-27T10:00:00"
   ```

---

## 完了！ 🎉

すべてのチェックボックスにチェックが入ったら、本番環境への移行が完了です。  
定期的にこのチェックリストを見直して、セキュリティと可用性を維持してください。
