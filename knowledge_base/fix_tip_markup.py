#!/usr/bin/env python
"""
修正: Tips 記事のマークアップ構造を正規化
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from tenasapo_knowledge.models import TipsArticle

# Get the article to fix
tip = TipsArticle.objects.filter(title__contains='Microsoft Entra Connect で発生するエラー').first()

if not tip:
    print("❌ 記事が見つかりません")
    exit(1)

print(f"修正対象: {tip.title}")
print(f"現在の body 長: {len(tip.body)} 文字")

# Fix the markup structure
old_body = tip.body

# This is the corrected text without unnecessary nested markup
new_body = """[b][u][color=blue][size=16]### 事象[/size][/color][/u][/b]

オンプレミス AD と同期済みのユーザーをクラウドユーザーに変換する際（同期対象外にした後、Microsoft Entra ID で削除済みユーザーを復元する際）に、Microsoft Entra Connect 上で `DeletingCloudOnlyObjectNotAllowed` というエラーが発生することがある。

[b][u][color=blue][size=16]### 原因[/size][/color][/u][/b]

オンプレ AD からユーザーを削除した後に、Microsoft Entra Connect にて **2 回の同期** を行っていない場合に発生する。
Microsoft Entra Connect は 1 度の同期処理で Microsoft Entra ID に対しユーザーの削除要求を出す。しかし、その次の同期処理が実行される前に Microsoft Entra ID 上で削除済みユーザーを復元してしまうと、Microsoft Entra Connect 側で「削除したはずのユーザーが復活している」と判断され、本エラーが出力される。

[b][u][color=blue][size=16]### 事前回避手順（エラーを発生させないための方法）[/size][/color][/u][/b]

以下の流れで処理を行うことで、エラーの発生を防ぐことができる。

1. ユーザーをオンプレ AD で削除、または同期の対象外とする。
2. Microsoft Entra Connect 上で PowerShell を起動し、以下のコマンドレットを実行して差分同期を行う。
`Start-ADSyncSyncCycle -PolicyType Delta`
3. Microsoft Entra Connect サーバー上の Synchronization Service Manager で、Export 処理まで完了することを確認する。
4. 再度 PowerShell コンソール上で以下を実行して、2回目の差分同期を行う。
`Start-ADSyncSyncCycle -PolicyType Delta`
5. Synchronization Service Manager で Export 処理まで完了することを確認する。
6. Microsoft Entra ID (Azure Portal や Entra 管理センター) にて、削除済みユーザーから対象ユーザーを復元する。

[b][u][color=blue][size=16]### 発生してしまった場合の対応手順[/size][/color][/u][/b]

すでにエラーが発生してしまった場合は、一時的に Microsoft Entra ID のユーザーを再度「削除済みユーザー」に移す必要がある。
※ 注意：この対応処理を行っている間は、対象ユーザーは Microsoft 365 のアプリケーションを利用できなくなる。

1. Microsoft Entra ID 側でユーザーを削除し、削除済みユーザーへ格納する。
2. Microsoft Entra Connect 上で PowerShell を起動し、以下のコマンドレットを実行して差分同期を行う。
`Start-ADSyncSyncCycle -PolicyType Delta`
3. Microsoft Entra Connect サーバー上の Synchronization Service Manager で、Export 処理まで完了することを確認する。
4. 再度 Microsoft Entra ID 上で削除済みユーザーからユーザーを復元する。

[b][u][color=blue][size=16]### 対象ユーザーの特定方法[/size][/color][/u][/b]

Microsoft Entra Connect Health などでは詳細が表示されない場合があるため、どのオブジェクトでエラーが発生しているか特定するには以下の手順を実施する。

1. Microsoft Entra Connect の Synchronization Service Manager 上で、`DeletingCloudOnlyObjectNotAllowed` のエラーをクリックする。
2. 表示された画面の「Detail」をクリックする。
3. 表示された画面内にある「Object ID」の「Value」の値を控える。
4. 控えた値を、Azure Portal や Entra 管理センターの [すべてのユーザー] 内上部にある検索ボックスに貼り付けると、該当のユーザーが表示される。

[b][u][color=blue][size=16]### 注意事項[/size][/color][/u][/b]

* 上記の手順によりエラーは解消可能だが、この方法で同期されたユーザーをクラウドユーザーに変換する操作自体が、Microsoft 社としては非推奨、あるいはサポート外の操作に該当する可能性があるため取り扱いには注意が必要。"""

tip.body = new_body
tip.save()

print(f"✅ 修正完了")
print(f"新しい body 長: {len(tip.body)} 文字")
