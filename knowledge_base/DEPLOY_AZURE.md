# Azure App Service へのデプロイ手順

## 前提条件
- Azure サブスクリプションがアクティブ
- Azure CLI がインストール済み（`az --version`）
- PostgreSQL Flexible Server を作成済み

---

## ステップ 1: Azure にログイン

```powershell
az login
az account set --subscription "<SUBSCRIPTION_ID>"
```

---

## ステップ 2: リソースグループ・リソース取得/作成

```powershell
$rg = "rg-tenasapo-prod"
$app = "app-tenasapo-prod"
$plan = "asp-tenasapo-prod"
$pg_server = "pg-tenasapo-prod"
$db_name = "appdb"

# リソースグループ作成（既存の場合はスキップ）
az group create -n $rg -l japaneast

# App Service Plan 作成（Linux, Django 推奨）
az appservice plan create `
  -g $rg -n $plan `
  --sku B1 --is-linux true

# Web App 作成
az webapp create `
  -g $rg -p $plan -n $app `
  --runtime "PYTHON|3.11" `
  --startup-file "gunicorn config.wsgi"
```

---

## ステップ 3: 環境変数を App Service に設定

```powershell
# Azure Key Vault に秘密を保存（推奨）
$vault_name = "kv-tenasapo-prod"
az keyvault create -g $rg -n $vault_name -l japaneast

# Secret 作成例
az keyvault secret set --vault-name $vault_name `
  --name "SECRET-KEY" `
  --value "your-secure-secret-key"

az keyvault secret set --vault-name $vault_name `
  --name "DB-PASSWORD" `
  --value "your-postgres-password"

# または直接設定（開発・テスト環境のみ）
az webapp config appsettings set -g $rg -n $app --settings `
  DEBUG="False" `
  SECRET_KEY="your-secret-key" `
  ALLOWED_HOSTS="$app.azurewebsites.net,yourdomain.com" `
  CSRF_TRUSTED_ORIGINS="https://$app.azurewebsites.net,https://yourdomain.com" `
  DB_ENGINE="django.db.backends.postgresql" `
  DB_NAME="$db_name" `
  DB_USER="pgadmin" `
  DB_PASSWORD="YourPostgresPassword123!" `
  DB_HOST="$pg_server.postgres.database.azure.com" `
  DB_PORT="5432"
```

---

## ステップ 4: アプリケーションをデプロイ

### 方法 A: ZIP Deploy（推奨）

```powershell
# デプロイ用 ZIP を作成（.venv と .git 除外）
cd c:\webapps\knowledge_base

# 本番用 static ファイルを準備
.\.venv\Scripts\python.exe manage.py collectstatic --noinput

# ZIP 作成
Compress-Archive -Path . -DestinationPath ..\app.zip -Exclude .venv, .git, db.sqlite3, sent_emails

# アップロード
az webapp deployment source config-zip `
  -g $rg -n $app `
  --src ..\app.zip
```

### 方法 B: Local Git Deploy

```powershell
# App Service 用 Git リモート設定
az webapp deployment user set --user-name <username> --password <password>

# Local Git からデプロイ
git remote add azure `
  https://<username>@$app.scm.azurewebsites.net/$app.git

git push azure main
```

---

## ステップ 5: データベースマイグレーション実行

```powershell
# App Service コンソール（Kudu）や SSH でマイグレーション実行
az webapp ssh --resource-group $rg --name $app

# SSH 接続後
python manage.py migrate
python manage.py createsuperuser  # (オプション)
```

または Azure CLI から直接実行：

```powershell
az webapp remote-debugging on -g $rg -n $app
# 上記でURLが表示される → ブラウザで確認して実行

# または App Service の「高度なツール」(Kudu) で実行
https://$app.scm.azurewebsites.net/api/command
```

---

## ステップ 6: 動作確認

```powershell
# アプリケーションにアクセス
Start-Process "https://$app.azurewebsites.net"

# ログ確認
az webapp log tail -g $rg -n $app

# Application Insights でモニタリング
az monitor log-analytics workspace create -g $rg -n "law-tenasapo"
az webapp config monitoring set -g $rg -n $app `
  --web-server-logging filesystem `
  --detailed-error-logging true `
  --failed-request-tracing true
```

---

## ステップ 7: 本番用セキュリティ設定

```powershell
# HTTPS Only を有効化
az webapp update -g $rg -n $app --https-only true

# Identity（マネージド ID）を有効化
az webapp identity assign -g $rg -n $app

# SSL/TLS 証明書の設定（カスタムドメインの場合）
# Azure Key Vault から App Service へ証明書をバインド
az webapp config ssl bind `
  -g $rg -n $app `
  --certificate-thumbprint <THUMBPRINT> `
  --ssl-type SNI
```

---

## SQLite → PostgreSQL データ移行

### 既存データがある場合

```bash
# PostgreSQL dump ツール使用
pg_dump -h $pg_server.postgres.database.azure.com \
  -U pgadmin \
  -d $db_name \
  --clean --if-exists > backup.sql

# または Django ORM を使用
python manage.py dumpdata > data.json
python manage.py loaddata data.json  # (新しい PostgreSQL で)
```

---

## トラブルシューティング

### アプリが起動しない場合
```powershell
# ログ確認
az webapp log tail -g $rg -n $app --follow

# または Kudu コンソール
https://$app.scm.azurewebsites.net/DebugConsole
```

### DATABASE接続エラー
- PostgreSQL ファイアウォール設定確認（0.0.0.0/0 許可か確認）
- 接続文字列の `sslmode=require` 確認
- DB ユーザーのパスワード確認

### 静的ファイル 404 エラー
```powershell
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
# 再度 ZIP Deploy
```

---

## バックアップ・復旧

```powershell
# PostgreSQL バックアップ
az postgres flexible-server backup create `
  -g $rg -s $pg_server `
  --backup-name "backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

# アプリケーションバックアップ
az webapp backup create -g $rg -n $app
```

---

## 監視・アラート

```powershell
# Application Insights メトリクス確認
az monitor metrics list-definitions -g $rg `
  --resource-type "Microsoft.Web/sites" `
  --query "[].name"

# アラート作成例（CPU > 80%）
az monitor metrics alert create `
  -g $rg -n "HighCPU-$app" `
  --scopes "/subscriptions/<SUBSCRIPTION>/resourceGroups/$rg/providers/Microsoft.Web/sites/$app" `
  --condition "avg Percentage CPU > 80" `
  --window-size 5m `
  --evaluation-frequency 1m
```

---

## 参考リンク
- [Azure App Service on Linux - Django!](https://docs.microsoft.com/en-us/azure/app-service/containers/app-service-linux-python)
- [Azure Database for PostgreSQL - Flexible Server](https://docs.microsoft.com/en-us/azure/postgresql/flexible-server/overview)
- [WhiteNoise Documentation](http://whitenoise.evans.io/)
