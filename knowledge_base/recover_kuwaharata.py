#!/usr/bin/env python
"""
kuwaharata ユーザーを復旧し、Google SocialAccount を削除
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from allauth.socialaccount.models import SocialAccount
from tenasapo_knowledge.models import UserProfile, Customer

User = get_user_model()

print("=" * 70)
print("Step 1: Google SocialAccount の削除")
print("=" * 70)

deleted, _ = SocialAccount.objects.filter(provider='google').delete()
print(f"✅ {deleted} 件の Google SocialAccount を削除")

print("\n" + "=" * 70)
print("Step 2: kuwaharata ユーザーの作成")
print("=" * 70)

# kuwaharata ユーザーを作成
kuwaharata_user, created = User.objects.get_or_create(
    username='kuwaharata',
    defaults={
        'email': 'kuwaharata@systena.co.jp',
        'is_active': True,
        'is_staff': True,
        'is_superuser': False,
    }
)

if created:
    print(f"✅ kuwaharata ユーザーを新規作成")
    kuwaharata_user.set_unusable_password()
    kuwaharata_user.save()
else:
    # 既存なら email を更新
    if not kuwaharata_user.email:
        kuwaharata_user.email = 'kuwaharata@systena.co.jp'
        kuwaharata_user.save()
        print(f"✅ kuwaharata ユーザーにメールアドレスを設定")

print("\n" + "=" * 70)
print("Step 3: UserProfile の確認・作成")
print("=" * 70)

profile, created = UserProfile.objects.get_or_create(
    user=kuwaharata_user,
    defaults={
        'uid': '001801',
        'display_name': '桑原拓也',
        'company_name': 'システナ',
        'user_type': UserProfile.USER_TYPE_SYSTENA,
        'email_addresses': 'kuwaharata@systena.co.jp',
    }
)

if created:
    print(f"✅ UserProfile を新規作成")
else:
    print(f"✅ UserProfile (既存): uid={profile.uid}")

print("\n" + "=" * 70)
print("Step 4: Customer グループ設定")
print("=" * 70)

customer, _ = Customer.objects.get_or_create(name='システナ')
if kuwaharata_user in customer.users.all():
    print(f"✅ Already in Customer group")
else:
    customer.users.add(kuwaharata_user)
    print(f"✅ Customer グループに追加")

print("\n" + "=" * 70)
print("✅ 完了！次にGoogle ログインを試してください")
print("=" * 70)
