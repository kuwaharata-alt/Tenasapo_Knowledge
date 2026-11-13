"""Separate Drive consent, never a login flow or a sharing-permission change.

Existing arbitrary destination folders require the full Drive scope, not
drive.file. Operators must disclose this broad consent and configure the OAuth
consent screen accordingly. SECRET_KEY rotation invalidates stored connections;
reconnect rather than falling back to plaintext or older signing keys.
"""

import base64
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
import secrets
import time
from urllib.parse import urlencode

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
import requests

from .chat_schedule import current_chat_environment
from .drive_oauth_models import DriveOAuthConnection


AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
USERINFO_URL = 'https://openidconnect.googleapis.com/v1/userinfo'
UPLOAD_URL = 'https://www.googleapis.com/upload/drive/v3/files'
DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive'
SCOPES = ('openid', 'email', DRIVE_SCOPE)
SESSION_KEY = 'drive_oauth_pending'
STATE_MAX_AGE = 600
ID_PATTERN = r'[A-Za-z0-9_-]{10,200}'
CONNECTION_ERROR = 'Drive連携を確認できません。設定を確認し、再連携してください。'
AUTH_ERROR = 'Drive認証を完了できません。連携を最初からやり直してください。'
REFRESH_ERROR = 'Drive認証を更新できません。再連携してください。'
UPLOAD_ERROR = 'PDFのDrive保存結果を確認できません。再試行せず、Driveを確認してください。'


class DriveOAuthError(Exception):
    """Only fixed, non-sensitive messages may cross this API boundary."""


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str = field(repr=False)


@dataclass(frozen=True)
class DriveUploadConfig:
    actor_id: int | str
    environment: str
    folder_id: str
    client_id: str
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)

    @property
    def env(self):
        return self.environment


def _text(value, maximum=16384):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and value == value.strip() and not re.search(r'[\x00-\x20\x7f-\x9f]', value))


def _equal(left, right):
    return (isinstance(left, str) and isinstance(right, str)
            and secrets.compare_digest(left.encode('utf-8'), right.encode('utf-8')))


def get_oauth_client():
    """Settings APP wins; never mix half-configured settings with SocialApp."""
    try:
        app = getattr(settings, 'SOCIALACCOUNT_PROVIDERS', {}).get('google', {}).get('APP', {})
        client_id, secret = app.get('client_id', ''), app.get('secret', '')
        if not client_id and not secret:
            from allauth.socialaccount.models import SocialApp
            from django.contrib.sites.models import Site

            # No global, unassigned, or other-Site apps are eligible.
            apps = list(SocialApp.objects.filter(
                provider='google', sites=Site.objects.get_current()).distinct()[:2])
            if len(apps) != 1:
                raise DriveOAuthError(CONNECTION_ERROR)
            client_id, secret = apps[0].client_id, apps[0].secret
        if not _text(client_id, 255) or not _text(secret):
            raise DriveOAuthError(CONNECTION_ERROR)
        return OAuthClient(client_id, secret)
    except Exception:
        raise DriveOAuthError(CONNECTION_ERROR) from None


def _actor(actor):
    model = get_user_model()
    if (not isinstance(actor, model) or actor.pk is None or actor._state.adding
            or not actor.is_authenticated or not actor.is_active or not actor.is_staff):
        raise DriveOAuthError(CONNECTION_ERROR)
    # Recheck persisted permissions rather than trusting a scheduler's stale user.
    if not model.objects.filter(pk=actor.pk, is_active=True, is_staff=True).exists():
        raise DriveOAuthError(CONNECTION_ERROR)
    return actor


def _domain():
    domain = getattr(settings, 'GOOGLE_WORKSPACE_DOMAIN', '')
    if (not isinstance(domain, str) or len(domain) > 253
            or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', domain)
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
                   for part in domain.split('.'))):
        raise DriveOAuthError(CONNECTION_ERROR)
    return domain


def _email_valid(email):
    return (_text(email, 254) and email.count('@') == 1
            and email.rsplit('@', 1)[1] == _domain())


def _callback_uri(request):
    return request.build_absolute_uri(reverse('drive_oauth_callback'))


def make_auth_request(request):
    """Return a consent URL and save one single-use state/PKCE binding."""
    try:
        actor = _actor(request.user)
        client = get_oauth_client()
        environment = current_chat_environment()
        callback_uri = _callback_uri(request)
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        domain = _domain()
        request.session[SESSION_KEY] = {
            'state': state, 'verifier': verifier, 'actor_id': str(actor.pk),
            'environment': environment, 'issued_at': time.time(),
            'callback_uri': callback_uri, 'client_id': client.client_id,
        }
        return AUTH_URL + '?' + urlencode({
            'client_id': client.client_id, 'redirect_uri': callback_uri,
            'response_type': 'code', 'scope': ' '.join(SCOPES), 'state': state,
            'code_challenge': challenge, 'code_challenge_method': 'S256',
            'access_type': 'offline', 'prompt': 'consent', 'hd': domain,
        })
    except Exception:
        raise DriveOAuthError(AUTH_ERROR) from None


def _fernet():
    secret = settings.SECRET_KEY
    if not isinstance(secret, str) or not secret:
        raise DriveOAuthError(CONNECTION_ERROR)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=b'tenasapo-knowledge/drive-oauth/refresh-token/v1').derive(secret.encode('utf-8'))
    return Fernet(base64.urlsafe_b64encode(key))


def _binding(connection):
    return {'version': 1, 'actor_id': str(connection.actor_id),
            'environment': connection.environment, 'client_id': connection.client_id,
            'google_sub': connection.google_sub, 'email': connection.email}


def _encrypt(connection, refresh_token):
    payload = dict(_binding(connection), refresh_token=refresh_token)
    return _fernet().encrypt(json.dumps(payload, separators=(',', ':')).encode('utf-8')).decode('ascii')


def _decrypt(connection):
    try:
        payload = json.loads(_fernet().decrypt(
            connection.encrypted_refresh_token.encode('ascii')).decode('utf-8'))
        if (not isinstance(payload, dict)
                or any(payload.get(key) != value for key, value in _binding(connection).items())
                or not _text(payload.get('refresh_token'))
                or not _text(connection.google_sub, 255) or not _email_valid(connection.email)):
            raise DriveOAuthError(CONNECTION_ERROR)
        return payload['refresh_token']
    except Exception:
        raise DriveOAuthError(CONNECTION_ERROR) from None


def _response_json(response):
    # Reject redirects without following them, even if they contain valid JSON.
    if not 200 <= response.status_code < 300:
        raise DriveOAuthError(AUTH_ERROR)
    data = response.json()
    if not isinstance(data, dict):
        raise DriveOAuthError(AUTH_ERROR)
    return data


def handle_callback(request):
    """Consume state before validation; return the saved connection, not a login."""
    try:
        pending = request.session.pop(SESSION_KEY, None)
        actor = _actor(request.user)
        client = get_oauth_client()
        environment = current_chat_environment()
        callback_uri = _callback_uri(request)
        if not isinstance(pending, dict):
            raise DriveOAuthError(AUTH_ERROR)
        issued = pending.get('issued_at')
        age = time.time() - issued if type(issued) in (int, float) else -1
        if (not _equal(pending.get('state'), request.GET.get('state'))
                or not math.isfinite(age) or not 0 <= age <= STATE_MAX_AGE
                or pending.get('actor_id') != str(actor.pk)
                or pending.get('environment') != environment
                or pending.get('client_id') != client.client_id
                or pending.get('callback_uri') != callback_uri
                or not isinstance(pending.get('verifier'), str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', pending['verifier'])
                or 'error' in request.GET or not _text(request.GET.get('code'))):
            raise DriveOAuthError(AUTH_ERROR)
        token = _response_json(requests.post(TOKEN_URL, data={
            'grant_type': 'authorization_code', 'code': request.GET['code'],
            'redirect_uri': callback_uri, 'client_id': client.client_id,
            'client_secret': client.client_secret, 'code_verifier': pending['verifier'],
        }, timeout=30, allow_redirects=False))
        scope = token.get('scope')
        if (not _text(token.get('access_token')) or not _text(token.get('refresh_token'))
                or not isinstance(scope, str) or DRIVE_SCOPE not in scope.split()
                or str(token.get('token_type', '')).lower() != 'bearer'):
            raise DriveOAuthError(AUTH_ERROR)
        identity = _response_json(requests.get(USERINFO_URL,
            headers={'Authorization': 'Bearer ' + token['access_token']},
            timeout=30, allow_redirects=False))
        if (identity.get('email_verified') is not True or identity.get('hd') != _domain()
                or not _email_valid(identity.get('email')) or not _text(identity.get('sub'), 255)):
            raise DriveOAuthError(AUTH_ERROR)
        # All remote and local checks finish before the only credential write.
        _actor(actor)
        if (current_chat_environment() != environment
                or get_oauth_client() != client or _callback_uri(request) != callback_uri):
            raise DriveOAuthError(AUTH_ERROR)
        connection = DriveOAuthConnection(
            actor=actor, environment=environment, client_id=client.client_id,
            google_sub=identity['sub'], email=identity['email'])
        ciphertext = _encrypt(connection, token['refresh_token'])
        saved, _ = DriveOAuthConnection.objects.update_or_create(
            environment=environment, actor=actor, defaults={
                'client_id': client.client_id, 'google_sub': connection.google_sub,
                'email': connection.email, 'encrypted_refresh_token': ciphertext,
            })
        return saved
    except Exception:
        raise DriveOAuthError(AUTH_ERROR) from None


def get_connection(actor):
    """Return None only when absent; stale/invalid connections fail closed."""
    try:
        actor = _actor(actor)
        client = get_oauth_client()
        connection = DriveOAuthConnection.objects.filter(
            actor=actor, environment=current_chat_environment()).first()
        if connection is not None:
            if connection.client_id != client.client_id:
                raise DriveOAuthError(CONNECTION_ERROR)
            _decrypt(connection)
        return connection
    except Exception:
        raise DriveOAuthError(CONNECTION_ERROR) from None


def _folder_id():
    folder = getattr(settings, 'GOOGLE_DRIVE_REPORT_FOLDER_ID', '')
    if not isinstance(folder, str) or not re.fullmatch(ID_PATTERN, folder):
        raise DriveOAuthError(CONNECTION_ERROR)
    return folder


def get_drive_upload_config(actor):
    """Read-only preflight; plaintext exists only in this immutable memory value."""
    try:
        connection = get_connection(actor)
        client = get_oauth_client()
        if connection is None or connection.client_id != client.client_id:
            raise DriveOAuthError(CONNECTION_ERROR)
        return DriveUploadConfig(actor.pk, connection.environment, _folder_id(),
                                 client.client_id, client.client_secret, _decrypt(connection))
    except Exception:
        raise DriveOAuthError(CONNECTION_ERROR) from None


def _validate_config(config):
    """Revalidate actor, environment, client, folder and current ciphertext."""
    try:
        if not isinstance(config, DriveUploadConfig):
            raise DriveOAuthError(CONNECTION_ERROR)
        actor = get_user_model().objects.get(pk=config.actor_id)
        current = get_drive_upload_config(actor)
        if (current.actor_id != config.actor_id or current.environment != config.environment
                or current.folder_id != config.folder_id or current.client_id != config.client_id
                or not _equal(current.client_secret, config.client_secret)
                or not _equal(current.refresh_token, config.refresh_token)):
            raise DriveOAuthError(CONNECTION_ERROR)
    except Exception:
        raise DriveOAuthError(CONNECTION_ERROR) from None


def upload_pdf_direct(pdf, filename, config):
    """One refresh and one file-create attempt; no retry or permissions calls."""
    from .report_drive_upload import validate_pdf

    try:
        validate_pdf(pdf)
        if (not isinstance(filename, str) or not 5 <= len(filename) <= 180
                or not filename.endswith('.pdf') or filename != filename.strip()
                or re.search(r'[\\/\x00-\x1f\x7f-\x9f<>:"|?*]', filename)):
            raise ValueError
    except Exception:
        raise DriveOAuthError('PDFの形式・サイズまたはファイル名が不正です。') from None
    _validate_config(config)
    try:
        token = _response_json(requests.post(TOKEN_URL, data={
            'grant_type': 'refresh_token', 'refresh_token': config.refresh_token,
            'client_id': config.client_id, 'client_secret': config.client_secret,
        }, timeout=30, allow_redirects=False))
        if (not _text(token.get('access_token'))
                or str(token.get('token_type', '')).lower() != 'bearer'
                or ('scope' in token and (not isinstance(token['scope'], str)
                                         or DRIVE_SCOPE not in token['scope'].split()))):
            raise DriveOAuthError(REFRESH_ERROR)
        access_token = token['access_token']
    except Exception:
        raise DriveOAuthError(REFRESH_ERROR) from None
    # No Drive side effect yet: reject a revoked actor/config after the refresh.
    _validate_config(config)
    try:
        boundary = 'nexus_' + secrets.token_hex(24)
        metadata = json.dumps({'name': filename, 'parents': [config.folder_id]}, ensure_ascii=True)
        body = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'
                f'{metadata}\r\n--{boundary}\r\nContent-Type: application/pdf\r\n\r\n').encode('utf-8')
        body += pdf + f'\r\n--{boundary}--\r\n'.encode('ascii')
        result = _response_json(requests.post(UPLOAD_URL,
            params={'uploadType': 'multipart', 'supportsAllDrives': 'true', 'fields': 'id'},
            headers={'Authorization': 'Bearer ' + access_token,
                     'Content-Type': f'multipart/related; boundary={boundary}'},
            data=body, timeout=60, allow_redirects=False))
        file_id = result.get('id')
        if not isinstance(file_id, str) or not re.fullmatch(ID_PATTERN, file_id):
            raise DriveOAuthError(UPLOAD_ERROR)
    except Exception:
        raise DriveOAuthError(UPLOAD_ERROR) from None
    return f'https://drive.google.com/file/d/{file_id}/view'