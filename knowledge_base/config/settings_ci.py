"""Offline CI settings. Never import deployment settings or read .env files.

Keep the real request middleware/context processors, including the consent
gate. Only infrastructure is replaced with memory/local temporary resources.
"""

import atexit
from ipaddress import ip_address
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from test_knowledge_tabs_settings import *  # noqa: F403

_temporary = TemporaryDirectory(prefix='nexus-ci-')
atexit.register(_temporary.cleanup)
_root = Path(_temporary.name)
MEDIA_ROOT = _root / 'media'
MEDIA_ROOT.mkdir()
FILE_UPLOAD_TEMP_DIR = str(_root / 'uploads')
Path(FILE_UPLOAD_TEMP_DIR).mkdir()
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'logo']
# collectstatic must persist for the following packaging step, unlike test data.
# This is the build output directory, never a deployment/environment path.

INSTALLED_APPS = [*INSTALLED_APPS, 'django_extensions']
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'tenasapo_knowledge.middleware.SafeSessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'tenasapo_knowledge.middleware.LoginRequiredExceptAssetsMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
TEMPLATES[0]['OPTIONS']['context_processors'] = [
    *TEMPLATES[0]['OPTIONS']['context_processors'],
    'tenasapo_knowledge.context_processors.user_display_name',
]
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]
PASSWORD_HASHERS = ['django.contrib.auth.hashers.PBKDF2PasswordHasher']
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.' + name}
    for name in ('UserAttributeSimilarityValidator', 'MinimumLengthValidator',
                 'CommonPasswordValidator', 'NumericPasswordValidator')
]
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'login'
SESSION_COOKIE_AGE = 60 * 30
SESSION_SAVE_EVERY_REQUEST = True
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_ADAPTER = 'tenasapo_knowledge.adapters.ExistingUserGoogleAdapter'
SOCIALACCOUNT_PROVIDERS = {'google': {
    'SCOPE': ['profile', 'email'], 'AUTH_PARAMS': {'access_type': 'online'},
    'APP': {'client_id': '', 'secret': '', 'key': ''},
}}
ACCOUNT_DEFAULT_HTTP_PROTOCOL = 'https'
GOOGLE_AUTO_CREATE_USER = True
GOOGLE_DEFAULT_COMPANY_NAME = 'Googleログインユーザー'
USER_ROLES = ['投稿者', '承認者', 'demo']
USER_GROUPS = USER_ROLES
USER_GROUP_REVIEWER_NAME = '承認者'
GROUP_ROLE_PERMISSIONS = {
    '管理者': [f'tenasapo_knowledge.{action}_{model}'
           for model in ('knowledgearticle', 'tipsarticle', 'manual')
           for action in ('add', 'change', 'delete', 'view')],
    '承認者': [f'tenasapo_knowledge.{action}_{model}'
           for model in ('knowledgearticle', 'tipsarticle')
           for action in ('view', 'change')],
    'システナ': [f'tenasapo_knowledge.{action}_{model}'
             for model in ('knowledgearticle', 'tipsarticle')
             for action in ('view', 'add', 'change')] + ['tenasapo_knowledge.view_manual'],
    'カスタマー': ['tenasapo_knowledge.view_knowledgearticle', 'tenasapo_knowledge.view_tipsarticle'],
}
GOOGLE_CHAT_WEBHOOK_URL = ''
GOOGLE_CHAT_GAS_WEB_APP_URL = ''
GOOGLE_CHAT_DEV_WEBHOOK_URL = ''
GOOGLE_DRIVE_REPORT_BACKEND = 'gas'
GOOGLE_DRIVE_REPORT_UPLOAD_URL = ''
GOOGLE_DRIVE_REPORT_UPLOAD_SECRET = ''
GOOGLE_DRIVE_REPORT_FOLDER_ID = ''
GOOGLE_DRIVE_ROOT_FOLDER_ID = None
GOOGLE_SERVICE_ACCOUNT_JSON_PATH = None
GOOGLE_WORKSPACE_DOMAIN = 'example.invalid'
# 既存OAuth回帰テストの既定。GASテストは明示的にgasへ切り替える。
CUSTOMER_APPLICATION_BACKEND = 'oauth'
GOOGLE_WORKSPACE_ADMIN_EMAIL = None
DEFAULT_FROM_EMAIL = 'ci@example.invalid'


def _offline_audit(event, args):
    """Block real DB access and outbound sockets even if a test forgets a mock."""
    if event == 'sqlite3.connect':
        name = str(args[0])
        if name != ':memory:' and not (name.startswith('file:') and 'mode=memory' in name):
            raise RuntimeError('CI permits only in-memory SQLite databases')
    if event in {'socket.connect', 'socket.sendto', 'socket.getaddrinfo'}:
        address = args[1] if event != 'socket.getaddrinfo' else (args[0],)
        host = address[0] if isinstance(address, tuple) else address
        if host == 'localhost':
            return
        try:
            if ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise OSError('External networking is disabled by CI settings')


sys.addaudithook(_offline_audit)