# 月次レポートPDFのDrive保存

## 組織内限定の場合：Drive API直接保存（推奨）

GASは使用しません。Nexusにログインしたスタッフが「Chat通知予約」の「Google Driveと連携する」から別途Driveへの初回認可を行います。以後は予約保存者の暗号化したrefresh tokenで自動・手動送信に対応します。

1. Google Cloudの既存Googleログイン用プロジェクトで **Google Drive APIを有効化**します。
2. OAuth同意画面へ `openid`、`email`、`https://www.googleapis.com/auth/drive` を設定します。任意の既存フォルダへ保存するためDrive全体のスコープを要求します。組織ポリシーによる管理者承認が必要な場合があります。Nexusの通常ログインのスコープは変更しません。
3. OAuthクライアントの「承認済みのリダイレクトURI」へ、予約画面のDrive連携欄に表示されるURLを**完全一致**で追加します。例：`https://tk-tenainfo.rikka.biz:8443/summary/drive/callback/`。既存のGoogleログインcallbackは削除しません。Googleが許可しないプライベートIPではなく、既存の認可済みHTTPSホスト名でアクセスしてください。
4. `GOOGLE_DRIVE_REPORT_BACKEND=oauth`、`GOOGLE_CHAT_REPORT_PDF_ENABLED=True` を設定します。保存先は従来と同じ環境別の `GOOGLE_DRIVE_REPORT_DEV_FOLDER_ID`（開発）または `GOOGLE_DRIVE_REPORT_FOLDER_ID`（本番）です。GAS URL・専用秘密鍵はこの方式では使いません。
5. マイグレーション0084を適用し、再起動します。本番適用は別途実施してください。
6. 予約保存者としてNexusへログインし、「Google Driveと連携する」を押して、保存先フォルダに書き込める社内Googleアカウントで許可します。Google側で検証済みメール・Workspaceドメインを確認します。他のNexusユーザーや開発／本番と接続は共有しません。
7. 接続済みの保存者による新規予約で検証します。未接続の場合、手動送信は予約を消費せず停止します。自動送信を予約する前に必ず連携してください。

refresh tokenはDBに暗号化して保存し、画面に表示しません。Djangoの `SECRET_KEY` を変更すると復号できなくなるため、変更後は再連携が必要です。DBバックアップも機密情報として管理してください。「連携を解除」はこの環境の接続情報だけを削除します。Google側の認可自体はGoogleアカウントの接続アプリ設定で取り消してください。

以下は旧GAS方式（`GOOGLE_DRIVE_REPORT_BACKEND=gas`）の設定手順です。

## 動作

- 予約の対象月のPDFを実送信時に生成し、Driveに保存してChat本文へ「月次レポート」の名前付きリンクを追加します。カードはPDFと同じ分析対象メンバーの月別Q&A・Tips件数、半期進捗・Q&A・Tips件数を表示します。
- 例：11月に10月分を通知する場合、対象月は10月、通知日時は11月に指定します。実行日の前月へ自動変更はしません。
- PDFは既存の「Nexus 月次レポート」と同じ描画処理を使用します。JSTの対象月および実行時刻と予約通知日時の早い方を上限として集計します。カードには記事一覧を載せず、PDFに対象の記事を収録します。
- 予約保存者が現在も有効なスタッフ／管理者であることを確認し、そのユーザーが閲覧できる記事本文・画像・参考リンクを収録します。フォルダの利用者にこれらの内容を配布してよいか確認してください。Driveの権限はDjangoの閲覧権限とは連動しません。
- 指定フォルダ内へ新規作成し、既存ファイルの上書き・公開共有への変更・閲覧者追加は行いません。
- 期限通知、確認画面GET、dry-runではPDF生成・保存を行いません。
- 保存失敗／結果不明ではChatを送らず、同じ予約を再試行しません。Drive保存後にChat送信が失敗した場合、ファイルは残ります。ファイル名の対象月・環境・イベントID・予約版で確認してください。
- PDF上限は10MiBです。超過時は外部保存せず中止します。

## Google側の設定（外部操作が必要）

1. Apps Scriptで**専用の新規プロジェクト**を作成し、[scripts/google_drive_report_upload.gs](scripts/google_drive_report_upload.gs) を貼り付けます。既存のChat通知リレーへ混ぜないでください。この専用アプリはChatへは送信しません。
2. プロジェクトの設定 → スクリプトプロパティに次を登録します。
   - `REPORT_FOLDER_ID`：`1UXh2fpq72dVVLnCR8MhBF-A9Q3zLays0`
   - `REPORT_UPLOAD_SECRET`：専用のランダムな32文字以上の秘密鍵。Django側にも同じ値を設定します。DjangoのSECRET_KEYやWebhookのトークンは再利用しません。
3. GASの実行ユーザーへフォルダ内のファイル作成権限を付与し、Driveの認可を行います。共有ドライブの場合はその環境でDriveAppによる作成が許可されることも確認してください。
4. ウェブアプリとして「自分として実行」でデプロイします。サーバーからの対話なしPOSTを受け取れるアクセス範囲が必要です。Workspaceポリシーで禁止されている場合は管理者と調整してください。URLを知るだけではアップロードできず、本文のHMAC署名・時刻・リプレイ・PDF形式を検証します。
5. 発行された `/exec` URLを下記Django設定へ登録します。既存のChatリレーURLを登録しないでください。
6. 保存先フォルダの共有設定を確認します。継承元フォルダ自体が広く公開されている場合、PDFにもそのアクセスが及びます。

## Django側の設定

既定はPDF保存のみOFFです。**既存Chat通知の送信許可は変更しません。**

| 用途 | 本番 `DEBUG=False` | 開発 `DEBUG=True` |
| --- | --- | --- |
| 専用GAS URL | `GOOGLE_DRIVE_REPORT_UPLOAD_URL` | `GOOGLE_DRIVE_REPORT_DEV_UPLOAD_URL` |
| 専用秘密鍵 | `GOOGLE_DRIVE_REPORT_UPLOAD_SECRET` | `GOOGLE_DRIVE_REPORT_DEV_UPLOAD_SECRET` |
| フォルダID | `GOOGLE_DRIVE_REPORT_FOLDER_ID` | `GOOGLE_DRIVE_REPORT_DEV_FOLDER_ID` |

- 本番のフォルダIDの既定値は指定フォルダです。開発の既定値は空で、本番設定へ補完しません。
- 開発検証には別のフォルダと別のGASデプロイ・秘密鍵を使用してください。GAS側 `REPORT_FOLDER_ID` とDjango側のIDは一致が必要です。
- 設定完了後に `GOOGLE_CHAT_REPORT_PDF_ENABLED=True` を設定して再起動します。未設定のまま有効化すると、レポート予約の試行が失敗扱いになるため、設定を先に完了させてください。
- Azureではアプリ設定／秘密情報管理へ登録してください。秘密鍵をGit、チャット、スクリーンショットへ記載しないでください。
- 設定後、画面の「PDFのDrive保存：有効」を確認し、新規予約で実送信を検証します。保存済み・失敗・結果不明の予約は再利用しません。

## 検証範囲

PythonテストとGASのオフラインスタブテストで、PDFの月境界・閲覧制限・署名・固定フォルダ・共有設定を変更しないこと・重複送信防止を検証します。実際のDrive認可・組織ポリシー・フォルダ権限・GASデプロイ・Chatの表示は実環境で別途確認が必要です。