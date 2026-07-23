#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from allauth.socialaccount.models import SocialApp

print('All Google SocialApps:')
google_apps = SocialApp.objects.filter(provider='google')
print(f'Total count: {google_apps.count()}')

for app in google_apps:
    print(f'\n  ID={app.id}')
    print(f'  Name: {app.name}')
    print(f'  Client ID: {app.client_id[:30]}...')
    print(f'  Sites: {list(app.sites.values_list("domain", flat=True))}')

# 最初のものだけを残して、他は削除
if google_apps.count() > 1:
    first_app = google_apps.first()
    print(f'\nKeeping ID={first_app.id}, deleting others...')
    for app in google_apps.exclude(id=first_app.id):
        print(f'  Deleting ID={app.id}')
        app.delete()
    print('✓ Duplicates removed')
else:
    print('\n✓ No duplicates found')
