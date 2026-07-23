#!/usr/bin/env python
"""
ログイン時にadminに変わる問題をデバッグするスクリプト
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from allauth.socialaccount.models import SocialAccount, SocialApp

User = get_user_model()

print("=" * 70)
print("kuwaharata@systena.co.jp に関するユーザー・SocialAccount 状態")
print("=" * 70)

email = 'kuwaharata@systena.co.jp'
users = User.objects.filter(email__iexact=email)

print(f"\n【メール: {email} のユーザー一覧】")
for user in users:
    print(f"  ID: {user.id}")
    print(f"  Username: {user.username}")
    print(f"  Email: {user.email}")
    print(f"  Is Active: {user.is_active}")
    print(f"  Is Staff: {user.is_staff}")
    print(f"  Is Superuser: {user.is_superuser}")

print(f"\n【Google SocialAccount 一覧】")
google_accounts = SocialAccount.objects.filter(provider='google')
for account in google_accounts:
    print(f"  ID: {account.id}")
    print(f"  UID: {account.uid}")
    print(f"  Email: {account.extra_data.get('email', 'N/A')}")
    print(f"  Linked User ID: {account.user_id}")
    print(f"  Linked User: {account.user.username if account.user else 'None'}")

print(f"\n【adapters.py の _resolve_existing_user() ロジック検証】")
from tenasapo_knowledge.adapters import ExistingUserGoogleAdapter

adapter = ExistingUserGoogleAdapter()
resolved_user = adapter._resolve_existing_user(email)

if resolved_user:
    print(f"  解決したユーザー: {resolved_user.username} (ID: {resolved_user.id})")
else:
    print(f"  解決したユーザー: None")

print("\n" + "=" * 70)
print("【問題判定】")
print("=" * 70)

# 問題1: 同一メールで複数ユーザー
if users.count() > 1:
    print("⚠️  同一メール複数ユーザー存在！")
    for u in users:
        print(f"   - {u.username} (ID {u.id})")

# 問題2: Google SocialAccount が admin に紐付いている  
admin_google = google_accounts.filter(user__username='admin')
if admin_google.exists():
    print("⚠️  Google SocialAccount が admin に紐付いている！")
    for acc in admin_google:
        print(f"   - {acc.extra_data.get('email')} → admin (ID {acc.user_id})")

# 問題3: _resolve_existing_user() が正しく動作しているか
if resolved_user and resolved_user.username == 'admin':
    print("❌ _resolve_existing_user() が admin を選択している！")
    print("   → adapters.py のロジックを見直す必要があります")
elif resolved_user and resolved_user.username == 'kuwaharata':
    print("✅ _resolve_existing_user() が kuwaharata を正しく選択しています")
