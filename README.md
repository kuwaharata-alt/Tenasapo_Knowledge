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

## Windows: 定期再起動 + 再起動後の自動起動（タスクスケジューラ）

Windows サーバーで運用する場合は、次の 2 スクリプトをタスクスケジューラに登録します。

- 再起動スクリプト: `knowledge_base/scripts/windows/reboot_host.ps1`
- 起動スクリプト: `knowledge_base/scripts/windows/start_django_after_boot.ps1`
- 登録スクリプト: `knowledge_base/scripts/windows/register_reboot_and_startup_tasks.ps1`

管理者 PowerShell で次を実行すると、上記 2 タスクを自動登録できます。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\webapps\knowledge_base\scripts\windows\register_reboot_and_startup_tasks.ps1"
```

### 1) 事前確認

- 仮想環境 Python: `c:\webapps\knowledge_base\.venv\Scripts\python.exe`
- Django 管理スクリプト: `c:\webapps\knowledge_base\manage.py`

### 2) タスク A（定期再起動）

1. タスクスケジューラ → **タスクの作成**
2. 全般:
	- 名前: `KB-Periodic-Reboot`
	- **最上位の特権で実行する** にチェック
3. トリガー:
	- 例: 毎週 日曜 03:00
4. 操作:
	- プログラム/スクリプト: `powershell.exe`
	- 引数の追加:
	  `-NoProfile -ExecutionPolicy Bypass -File "C:\webapps\knowledge_base\scripts\windows\reboot_host.ps1" -DelaySeconds 30 -Reason "KB periodic reboot"`

### 3) タスク B（再起動後にアプリ起動）

1. タスクスケジューラ → **タスクの作成**
2. 全般:
	- 名前: `KB-Start-Django-After-Boot`
	- **最上位の特権で実行する** にチェック
3. トリガー:
	- **スタートアップ時**
	- （任意）遅延: 1 分
4. 操作:
	- プログラム/スクリプト: `powershell.exe`
	- 引数の追加:
	  `-NoProfile -ExecutionPolicy Bypass -File "C:\webapps\knowledge_base\scripts\windows\start_django_after_boot.ps1" -HostAddress "0.0.0.0" -Port 8000`

### 4) ログ

実行ログは以下に出力されます。

- `c:\webapps\knowledge_base\logs\django\scheduled_reboot.log`
- `c:\webapps\knowledge_base\logs\django\django_startup.log`
- `c:\webapps\knowledge_base\logs\django\django_stdout.log`
- `c:\webapps\knowledge_base\logs\django\django_stderr.log`
