# メニュー別の実装

大きな既存モジュールを、URL・権限・画面動作を変えずに段階的に分割しています。

| パッケージ | 分割済みの担当 | 主要ファイル |
| --- | --- | --- |
| knowledge | 便利機能一覧・検索・お気に入り | [views.py](knowledge/views.py) |
| input | INPUT ハブ・便利機能登録／編集・カテゴリフォーム | [views.py](input/views.py)、[forms.py](input/forms.py) |
| users | ユーザー新規作成・契約／デモ発行、ユーザー編集等のフォーム | [views.py](users/views.py)、[forms.py](users/forms.py) |
| manuals | マニュアルの一覧・詳細・登録・編集・削除 | [views.py](manuals/views.py)、[forms.py](manuals/forms.py) |
| reports | 投稿上限設定 | [views.py](reports/views.py)、[forms.py](reports/forms.py) |
| shared | 共通権限・遷移先検証・閲覧履歴・便利機能カテゴリ | [access.py](shared/access.py)、[history.py](shared/history.py)、[qr_categories.py](shared/qr_categories.py) |

## 互換性と依存方向

- 新規の機能実装・URL 登録は原則このパッケージを直接参照します。
- [従来の views.py](../views.py) と [forms.py](../forms.py) は、移動済みのクラス・関数を明示的に再公開します。旧 import と新 import は同じ実装オブジェクトを返します。
- 各機能から従来の巨大 views/forms を逆 import しないこと。機能間共通処理は shared 等へ分離します。
- クラスの実装モジュールは変わります。テストの patch 対象は、その依存先を実際に参照する新モジュールに合わせてください。
- [URL 定義](../urls.py) のパスと名前、および [プロジェクト側のマニュアル別名](../../config/urls.py) は維持します。
- [境界テスト](../test_feature_boundaries.py) が互換再公開・独立 import・URL とフォームの対応を検証します。

## 未分割の範囲

- Knowledge 本体の FAQ／Tips 一覧・詳細、記事作成／編集・承認、添付ファイル等。
- ユーザー一覧・詳細・更新・削除、初回登録、パスワード・ログイン関連。
- 集計本体・PDF 出力・プロジェクト文書・その他管理画面。

上記の多くは [従来の views.py](../views.py) と [forms.py](../forms.py) にあります。顧客申請は既存の専用 [views](../customer_application_views.py)、[サービス](../customer_applications.py)、[Google API](../customer_application_google.py) に分離済みで、今回は移動していません。

モデル・マイグレーション・テンプレート・テストは既存配置を維持します。フォルダー名だけで完全分割済みと判断せず、この表と URL 定義から担当実装を確認してください。