# Tenasapo_Knowledge
テナサポナレッジWebサイト

## 掲載期限の事前通知

FAQ/Tips には任意で掲載期限（`掲載期限`）を設定できます。

- 掲載期限の 1 週間前に、投稿者・承認者へメール通知する管理コマンド:
	- `python manage.py notify_expiring_articles`
- 動作確認のみ（メール送信しない）:
	- `python manage.py notify_expiring_articles --dry-run`

日次で上記コマンドを実行するようにタスクスケジューラ等へ登録してください。
