#!/usr/bin/env python
"""
kuwaharata 削除確認
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from tenasapo_knowledge.models import UserProfile
from allauth.socialaccount.models import SocialAccount

User = get_user_model()

print('=' * 70)
print('kuwaharata ユーザー確認')
print('=' * 70)

kuwaharata = User.objects.filter(username='kuwaharata').first()
if kuwaharata:
    print(f'❌ まだ存在：ID {kuwaharata.id}, Email: {kuwaharata.email}')
else:
    print('✅ DB から削除されています')

print()
print('=' * 70)
print('kuwaharata UserProfile 確認')
print('=' * 70)

profile = UserProfile.objects.filter(user__username='kuwaharata').first()
if profile:
    print(f'⚠️  まだ存在：uid={profile.uid}')
else:
    print('✅ UserProfile も削除されています')

print()
print('=' * 70)
print('Google SocialAccount 確認')
print('=' * 70)

google_accounts = SocialAccount.objects.filter(provider='google')
print(f'Google SocialAccounts: {google_accounts.count()} 件')
for acc in google_accounts:
    email = acc.extra_data.get('email', 'N/A')
    username = acc.user.username if acc.user else 'N/A'
    print(f'  - {email} -> User ID {acc.user_id} ({username})')
