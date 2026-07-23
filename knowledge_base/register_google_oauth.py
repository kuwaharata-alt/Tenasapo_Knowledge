#!/usr/bin/env python
import os
import django
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp, SocialAccount

# 設定値
client_id = (os.getenv('GOOGLE_OAUTH_CLIENT_ID') or '').strip()
secret = (os.getenv('GOOGLE_OAUTH_CLIENT_SECRET') or '').strip()
if not client_id or not secret:
    raise ValueError('GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET を環境変数に設定してください。')
try:
    site = Site.objects.get(id=1)
except Site.DoesNotExist:
    site = Site.objects.create(id=1, domain='tk-tenainfo.rikka.biz:8443', name='tk-tenainfo.rikka.biz:8443')

# 既に存在しないか確認
existing = SocialApp.objects.filter(provider='google').first()
if existing:
    print(f'✓ Already exists: {existing.name}')
    print(f'  Provider: {existing.provider}')
    print(f'  Client ID: {existing.client_id[:20]}...')
else:
    try:
        # 新規作成
        app = SocialApp.objects.create(
            provider='google',
            name='Google',
            client_id=client_id,
            secret=secret,
        )
        app.sites.add(site)
        print(f'✓ Created: {app.name} ({app.provider})')
        print(f'✓ Site: {site.domain}')
        print(f'✓ Client ID: {app.client_id[:20]}...')
    except Exception as e:
        print(f'✗ Error: {e}')

