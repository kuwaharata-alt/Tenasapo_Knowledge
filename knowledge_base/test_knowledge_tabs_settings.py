"""Isolated offline test settings: never import config.settings or load dotenv.

Run with the venv interpreter and ``-B -m django test
--settings=test_knowledge_tabs_settings tenasapo_knowledge.test_knowledge_tabs``.
Only an in-memory SQLite test database is used; no server is started.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SECRET_KEY = 'offline-tabs-tests-only-not-a-production-key'
DEBUG = False
ALLOWED_HOSTS = ['testserver']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.sites', 'django.contrib.messages',
    'django.contrib.staticfiles', 'allauth', 'allauth.account',
    'allauth.socialaccount', 'allauth.socialaccount.providers.google',
    'tenasapo_knowledge',
]
MIDDLEWARE = [
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend']
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
STATIC_URL = '/static/'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'unused-offline-test-media'
SITE_ID = 1
USE_TZ = True
TIME_ZONE = 'Asia/Tokyo'
LANGUAGE_CODE = 'ja'
FAQ_APPROVAL_ENABLED = True
GOOGLE_CHAT_SCHEDULED_SEND_ENABLED = False
GOOGLE_CHAT_REPORT_PDF_ENABLED = False