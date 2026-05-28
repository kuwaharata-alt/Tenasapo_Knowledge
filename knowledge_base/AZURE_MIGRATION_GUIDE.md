# Azure App Service 移行ガイド

## 概要
このプロジェクトを **Azure App Service** と **PostgreSQL** で運用するための設定・ツール類をまとめました。

---

## 📦 作成されたファイル一覧

| ファイル名 | 説明 |
|-----------|------|
| **requirements.txt** | Python 依存関係（PostgreSQL対応版） |
| **config/settings.py** | Django 本番環境対応設定 |
| **config/wsgi.py** | WhiteNoise 統合済みの WSGI 設定 |
| **.env.example** | 環境変数テンプレート |
| **.gitignore** | Git 無視設定（秘密情報保護） |
| **DEPLOY_AZURE.md** | Azure へのデプロイ詳細手順 |
| **migrate_to_postgres.py** | SQLite → PostgreSQL データ移行スクリプト |
| **.github/workflows/deploy.yml** | GitHub Actions CI/CD パイプライン |
| **AZURE_MIGRATION_CHECKLIST.md** | 本番環境への移行チェックリスト |

---

## 🚀 クイックスタート（開発環境）

### 1. ローカル環境で PostgreSQL に接続テスト

```bash
# PostgreSQL ドライバ確認
python -m pip list | grep psycopg

# 環境変数設定（テスト用）
SET DB_ENGINE=django.db.backends.postgresql
SET DB_NAME=testdb
SET DB_USER=pgadmin
SET DB_PASSWORD=your_password
SET DB_HOST=localhost
SET DB_PORT=5432

# マイグレーション実行
cd knowledge_base
python manage.py migrate
```

### 2. SQLite → PostgreSQL データ移行

```bash
# スクリプト実行（PostgreSQL 接続情報を環境変数で設定してから）
python migrate_to_postgres.py
```

### 3. 動作確認

```bash
cd knowledge_base
python manage.py runserver
# http://localhost:8000 にアクセス
```

---

## ☁️ Azure へのデプロイ（本番環境）

### 方法 A: CLI コマンド（最短）

```powershell
# 1. リソース作成
$rg = "rg-tenasapo-prod"
$app = "app-tenasapo-prod"

az group create -n $rg -l japaneast
az appservice plan create -g $rg -n "asp-tenasapo" --sku B1 --is-linux
az webapp create -g $rg -p "asp-tenasapo" -n $app --runtime "PYTHON|3.11"

# 2. 環境変数設定
az webapp config appsettings set -g $rg -n $app --settings `
  DEBUG="False" `
  DB_ENGINE="django.db.backends.postgresql" `
  DB_NAME="appdb" `
  DB_USER="pgadmin" `
  DB_PASSWORD="YourPassword" `
  DB_HOST="yourserver.postgres.database.azure.com" `
  DB_PORT="5432"

# 3. デプロイ
Compress-Archive -Path . -DestinationPath app.zip -Exclude .venv, .git
az webapp deployment source config-zip -g $rg -n $app --src app.zip

# 4. マイグレーション
az webapp ssh -g $rg -n $app
python manage.py migrate
exit
```

### 方法 B: GitHub Actions（自動デプロイ）

```bash
git add .
git commit -m "Prepare for Azure migration"
git push origin main
# → .github/workflows/deploy.yml が自動実行
```

---

## 🔧 重要な設定情報

### Django 設定（config/settings.py）

| 項目 | 開発環境 | 本番環境 |
|------|---------|---------|
| `DEBUG` | True | False |
| `DB_ENGINE` | sqlite3 | postgresql |
| `STATIC_ROOT` | 不要 | `staticfiles/` |
| `ALLOWED_HOSTS` | localhost | yourdomain.azurewebsites.net |

### 環境変数（.env）

```env
DEBUG=False
SECRET_KEY=your-secret-key
ALLOWED_HOSTS=yourdomain.azurewebsites.net
DB_ENGINE=django.db.backends.postgresql
DB_NAME=appdb
DB_USER=pgadmin
DB_PASSWORD=YourPassword
DB_HOST=server.postgres.database.azure.com
```

---

## ⚠️ 注意点・トラブルシューティング

### 1. PostgreSQL に接続できない
```bash
# ファイアウォール設定確認（Azure Portal）
# PostgreSQL > ネットワーク > ファイアウォール規則
# App Service の IP を許可する（0.0.0.0/0 はセキュリティ上 OK）

# または psql で接続テスト
psql -h server.postgres.database.azure.com -U pgadmin -d appdb
```

### 2. 静的ファイルが表示されない
```bash
python manage.py collectstatic --noinput
# App Service に再度デプロイ
```

### 3. SECRET_KEY エラー
```env
# .env に必ず設定（ランダムな長い文字列）
SECRET_KEY=django-insecure-生成したキーをここに貼り付け
```

### 4. メール送信エラー
```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.sendgrid.net  # または使用するメールサービス
EMAIL_PORT=587
EMAIL_HOST_USER=apikey
EMAIL_HOST_PASSWORD=SG.your-sendgrid-api-key
```

---

## 📚 参考リンク

- [Django デプロイメント チェックリスト](https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/)
- [Azure App Service on Linux - Python](https://docs.microsoft.com/en-us/azure/app-service/containers/app-service-linux-python)
- [Azure Database for PostgreSQL](https://docs.microsoft.com/en-us/azure/postgresql/single-server/overview)
- [WhiteNoise ドキュメント](http://whitenoise.evans.io/)
- [Gunicorn デプロイメント](https://gunicorn.org/)

---

## 📋 チェックリスト

本番環境へのデプロイ前に、[AZURE_MIGRATION_CHECKLIST.md](./AZURE_MIGRATION_CHECKLIST.md) をご確認ください。

---

## 💬 サポート

問題が発生した場合は、以下をご確認ください：

1. **ログ確認**
   ```bash
   az webapp log tail -g <resource-group> -n <app-name>
   ```

2. **Kudu コンソール**
   ```
   https://<app-name>.scm.azurewebsites.net/DebugConsole
   ```

3. **Application Insights**
   Azure Portal → Application Insights → ライブメトリクス

---

**最後に、本運用前に十分なテストを実施してください！** ✅
