#!/usr/bin/env python
"""
Google OAuth から取得されるデータを確認するスクリプト
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from allauth.socialaccount.models import SocialAccount

print("=" * 60)
print("Google SocialAccount 一覧")
print("=" * 60)

google_accounts = SocialAccount.objects.filter(provider='google')

for account in google_accounts:
    print(f"\nユーザー: {account.user.username} ({account.user.email})")
    print(f"Provider: {account.provider}")
    print(f"UID: {account.uid}")
    print(f"\nextra_data に含まれる情報:")
    
    extra_data = account.extra_data
    for key, value in sorted(extra_data.items()):
        # 長い値は省略
        if isinstance(value, str) and len(value) > 100:
            print(f"  {key}: {value[:100]}...")
        else:
            print(f"  {key}: {value}")

if not google_accounts.exists():
    print("\n⚠️ Google SocialAccount がまだ登録されていません")
    print("先にログインを完了してください")

print("\n" + "=" * 60)
print("\nUserProfile フィールド一覧:")
print("=" * 60)

from tenasapo_knowledge.models import UserProfile

for user_profile in UserProfile.objects.all():
    print(f"\nユーザー: {user_profile.user.username}")
    print(f"  uid: {user_profile.uid}")
    print(f"  display_name: {user_profile.display_name}")
    print(f"  company_name: {user_profile.company_name}")
    print(f"  user_type: {user_profile.user_type}")
    print(f"  email_addresses: {user_profile.email_addresses}")
    print(f"  note: {user_profile.note}")
