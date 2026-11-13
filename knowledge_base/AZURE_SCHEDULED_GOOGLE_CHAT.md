# Azure Linux の Google Chat 予約通知

## 対象と現状

- 本番はAzure Linuxです。既存の[配備手順](DEPLOY_AZURE.md)はApp Serviceの組み込みPythonランタイムを使用しています。
- Djangoの`send_scheduled_chat_notifications`管理コマンドはOS非依存です。Windowsのタスク スケジューラやPowerShellスクリプトは本番では使用しません。
- 本資料は導入手順です。資料追加や予約の保存だけではAzureのジョブは作成されません。Azure実リソースの構成、ジョブ登録・稼働は別途確認が必要です。

## App Serviceの定期WebJobを使う

Linuxの組み込みPythonランタイムでは、シェル／PythonのTriggered WebJobが利用できます。まず実際のApp ServiceにWebJobs機能があることを確認します。独自コンテナ等の場合は対応条件を確認してください。

1. Basic以上のプランで **Always Onを有効**にします。Webプロセス内のスレッドやコンテナ内の常駐cronには依存させません。
2. WebJobがWebアプリと同じ配備コード、Python依存関係、App Settings／Key Vault、PostgreSQLを利用できることを確認します。Oryx配備ではコードの実体が一時ディレクトリになる場合があるため、Windowsのパスや固定の仮想環境パスは流用しません。実行環境の`APP_PATH`などを確認し、管理コマンドを実行する作業ディレクトリとPythonを決定します。
3. そのPythonから`manage.py send_scheduled_chat_notifications --environment production --dry-run --max-count 20 --no-color`を呼ぶ起動スクリプトを用意します。**初回は送信しない手動Triggered WebJob**として登録します。`DEBUG=False`、`GOOGLE_CHAT_SCHEDULED_SEND_ENABLED=False`を確認します。`--environment`は環境の切替ではなく一致確認です。
4. 管理コマンドはdry-runでも通知内容を標準出力へ出すため、起動スクリプトでは標準出力・標準エラーを破棄し、終了コードだけを返します。本文・投稿者・Webhook URL・トークンをWebJobログに保存しないでください。重複実行を防止し、実行上限（例：240秒）も設定します。異常終了時の無条件な即時再実行は避けます。
5. 手動dry-runの終了コード、対象環境、予約・DB接続を確認してから定期化します。NCRONTABは`0 */5 * * * *`（秒を含む6フィールド、5分間隔）を指定します。ソース管理連携中はWebJobも配備パイプラインで管理します。ジョブの追加・更新で既存のアプリ配備を置き換えないようにしてください。
6. 定期起動は通常UTCです。5分間隔の確認処理なのでタイムゾーン変更は不要です。画面の予約日時・期限は日本時間のまま、Django側が時刻を比較します。実際の送信は予約時刻以降の次の正常な実行時となり、秒単位での配信保証ではありません。

dry-runの終了コード0は今回の処理成功のみを示します。対象予約がなくても0になり、実送信先・認証・到達性を検証したことにはなりません。

## 実送信への切替（運用担当者が確認後に実施）

- 配備先のマイグレーション、GASリレーを使う場合のデプロイ版と`scheduled_card`対応、通知先スペースを確認します。
- `GOOGLE_CHAT_SCHEDULED_SEND_ENABLED=True`と管理コマンドの`--send`の**両方**が必要です。起動スクリプトから自動で送信許可を有効化しないでください。
- `--dry-run`を`--send`に変更すると、到来済みの有効予約すべてが最大件数まで対象です。特定月を選ぶオプションはなく、`--max-count 1`も対象月の限定にはなりません。切替前に不要な予約を無効化してください。
- スケジューラーは対象DBに対して一系統にします。Windows開発タスクを本番DBへ向けたり、Azure側で二重登録したりしないでください。
- 非本番スロットでは`WEBJOBS_STOPPED=1`または`WEBJOBS_DISABLE_SCHEDULE=1`をスロット固定設定にし、送信許可もFalseにします。`DEBUG=False`のステージングもアプリ内ではproductionと判定される点に注意してください。
- 48時間を超過した通知は送信しません。失敗・結果不明は自動再送せず、Chat実物と配信履歴を照合します。
- 緊急停止時は定期ジョブを停止し、実行中ジョブも確認して送信許可をFalseに戻します。既に受理されたHTTP送信は取り消せません。

本変更ではAzureへの接続・ジョブ登録・実Chat送信は行っていません。

## 参考

- [Microsoft Learn: WebJobs（Linuxコードを含む）](https://learn.microsoft.com/en-us/azure/app-service/webjobs-create?tabs=linuxcode)
- [Microsoft Learn: Pythonのビルド・配備](https://learn.microsoft.com/en-us/azure/app-service/configure-language-python#customize-build-automation)
- [Windows開発環境専用の手順](WINDOWS_SCHEDULED_GOOGLE_CHAT.md)