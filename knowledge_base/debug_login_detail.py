#!/usr/bin/env python
"""
より詳細なデバッグ
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from allauth.socialaccount.models import SocialAccount

User = get_user_model()

print("=" * 70)
print("全ユーザー一覧（メールアドレス付き）")
print("=" * 70)

# 全ユーザーのメールアドレスを確認
for user in User.objects.all().order_by('id'):
    print(f"ID: {user.id:2d} | Username: {user.username:20s} | Email: {user.email}")

print("\n" + "=" * 70)
print("admin ユーザーの詳細")
print("=" * 70)

admin = User.objects.get(username='admin')
print(f"Username: {admin.username}")
print(f"Email: '{admin.email}'")
print(f"Email is empty: {not admin.email}")

print("\n" + "=" * 70)
print("kuwaharata ユーザーの詳細")
print("=" * 70)

kuwaharata = User.objects.filter(username='kuwaharata').first()
if kuwaharata:
    print(f"Username: {kuwaharata.username}")
    print(f"Email: '{kuwaharata.email}'")
else:
    print("kuwaharata ユーザーが存在しません！")

print("\n" + "=" * 70)
print("Google SocialAccount の詳細")
print("=" * 70)

google_accounts = SocialAccount.objects.filter(provider='google')
for acc in google_accounts:
    print(f"\nID: {acc.id}")
    print(f"  UID: {acc.uid}")
    print(f"  Email (from OAuth): {acc.extra_data.get('email')}")
    print(f"  Linked To User: {acc.user.username} (ID {acc.user_id})")
    print(f"  Linked User Email: '{acc.user.email}'")

print("\n" + "=" * 70)
print("【根本原因推定】")
print("=" * 70)

if admin.email and 'kuwaharata' in admin.email:
    print("❌ admin ユーザーに kuwaharata のメールが登録されている")
    print("   → これが原因！adapters.py は admin のメールで kuwaharata を探す")
    print("   → でも admin にメールがあるので、別の user_id で重複判定される")
elif not admin.email:
    print("⚠️  admin ユーザーにメールアドレスが登録されていない")
else:
    print(f"⚠️  admin のメール: {admin.email}")
    print(f"     別のユーザーとの競合はなさそう")
