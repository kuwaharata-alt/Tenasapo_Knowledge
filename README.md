# Tenasapo_Knowledge
テナサポナレッジWebサイト

## 掲載期限の事前通知

FAQ/Tips には任意で掲載期限（`掲載期限`）を設定できます。

- 掲載期限の 1 週間前に、投稿者・承認者へメール通知する管理コマンド:
	- `python manage.py notify_expiring_articles`
- 動作確認のみ（メール送信しない）:
	- `python manage.py notify_expiring_articles --dry-run`

日次で上記コマンドを実行するようにタスクスケジューラ等へ登録してください。

## パスワード自動更新（Linux cron）

`cs-demo` のパスワードを定期更新し、通知メール送信まで行う実行スクリプトを追加しています。

- 実行スクリプト: `knowledge_base/scripts/rotate_password_cron.sh`
- 実処理: `python manage.py rotate_user_password`
	- パスワード更新
	- 変更日時+パスワードを備考（`UserProfile.note`）へ追記
	- メール送信

### 1) 初回準備

```bash
cd /path/to/webapps/knowledge_base
chmod +x scripts/rotate_password_cron.sh
```

### 2) 手動確認（本番更新）

```bash
ROTATE_USERNAME="cs-demo" \
ROTATE_RECIPIENT_EMAIL="your-team@example.com" \
./scripts/rotate_password_cron.sh
```

### 3) cron 設定例（毎月1日 03:00）

```bash
crontab -e
```

```cron
0 3 1 * * cd /path/to/webapps/knowledge_base && ROTATE_USERNAME="cs-demo" ROTATE_RECIPIENT_EMAIL="your-team@example.com" ./scripts/rotate_password_cron.sh >> /var/log/rotate_user_password.log 2>&1
```

### 4) dry-run で動作確認したい場合

```cron
0 3 1 * * cd /path/to/webapps/knowledge_base && ROTATE_USERNAME="cs-demo" ROTATE_RECIPIENT_EMAIL="your-team@example.com" ROTATE_DRY_RUN="true" ./scripts/rotate_password_cron.sh >> /var/log/rotate_user_password.log 2>&1
```
