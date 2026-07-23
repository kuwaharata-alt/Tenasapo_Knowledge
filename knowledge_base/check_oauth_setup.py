#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site

# 全 SocialApp を確認
print('All SocialApps:')
for app in SocialApp.objects.all():
    sites = app.sites.all()
    print(f'  ID={app.id}, Provider={app.provider}, Name={app.name}')
    print(f'    Sites: {list(sites.values_list("domain", flat=True))}')

# Site 1 に関連付けられたアプリを確認
print('\nApps for Site ID=1:')
site = Site.objects.get(id=1)
apps_for_site = site.socialapp_set.all()
print(f'  Count: {apps_for_site.count()}')
for app in apps_for_site:
    print(f'  {app.provider}: {app.name}')

# すべてのサイトを確認
print('\nAll Sites:')
for s in Site.objects.all():
    print(f'  ID={s.id}, Domain={s.domain}, Apps={s.socialapp_set.count()}')
