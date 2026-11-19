"""顧客申請専用OAuth / Sheets読取 / Gmail下書き。送信APIは実装しない。

gmail.composeは送信権限も含むため、同意画面でその旨を説明すること。
呼出元はCSRF・HTTPメソッドを検証し、DB transactionの外から呼ぶこと。
OAuth stateの消費記録はDBに保存し、全worker共通で再利用を拒否する。
秘密・個人回答・外部応答はログ出力しない。SECRET_KEY変更時は再連携する。
"""

import base64
import hashlib
import json
import math
import re
import secrets
import time
import uuid
from datetime import timedelta
from email.message import EmailMessage
from email.policy import SMTP
from urllib.parse import urlencode, urlsplit

import jwt
import requests
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.db import connection as db_connection, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from .chat_schedule import current_chat_environment
from .customer_application_google_models import CustomerApplicationGoogleConnection, CustomerApplicationGoogleState
from .customer_application_models import CustomerApplication
from .drive_oauth import get_oauth_client

__all__ = ['CustomerGoogleError', 'make_auth_request', 'handle_callback',
           'get_connection', 'disconnect', 'get_access_token',
           'list_applications', 'create_draft']

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
USERINFO_URL = 'https://openidconnect.googleapis.com/v1/userinfo'
CERTS_URL = 'https://www.googleapis.com/oauth2/v3/certs'
SHEETS_URL = 'https://sheets.googleapis.com/v4/spreadsheets/'
GMAIL_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/drafts'
SPREADSHEET_ID = '1JaWW0c_CE9W5_egLvoqrWcnwuIbb6QCERBRrQvAE5Pg'
SHEET_ID = 1583871824
SCOPES = ('openid', 'email', 'https://www.googleapis.com/auth/spreadsheets.readonly',
          'https://www.googleapis.com/auth/gmail.compose')
HEADERS = ['タイムスタンプ', 'メールアドレス', '会社名', '部署名', '契約者氏名', 'メールアドレス']
SESSION_KEY = 'customer_application_google_pending'
ERROR = '顧客申請のGoogle連携を確認できません。設定を確認し、再連携してください。'
SAFE_ERROR = '下書きの作成結果を確認できません。再作成せず、承認者のGmailを確認してください。'


class CustomerGoogleError(Exception):
    """外部値を受け取らず、固定文言だけを公開する。"""

    def __init__(self, *, draft=False):
        super().__init__(SAFE_ERROR if draft else ERROR)


def _fail():
    raise CustomerGoogleError()


def _text(value, maximum=16384):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and not re.search(r'[\x00-\x20\x7f-\x9f]', value))


def _equal(left, right):
    return (isinstance(left, str) and isinstance(right, str)
            and secrets.compare_digest(left.encode(), right.encode()))


def _preview(value):
    return str(value or '').strip().lower() in {'demo', 'cs'}


def _actor(actor):
    model = get_user_model()
    if (not isinstance(actor, model) or actor.pk is None or actor._state.adding
            or not actor.is_authenticated or not actor.is_active
            or not (actor.is_staff or actor.is_superuser)
            or _preview(getattr(actor, '_view_mode_override', ''))):
        _fail()
    saved = model.objects.get(Q(is_staff=True) | Q(is_superuser=True), pk=actor.pk, is_active=True)
    if (not _email(actor.email) or not _email(saved.email)
            or actor.email.casefold() != saved.email.casefold()):
        _fail()
    return saved


def _request_actor(request):
    if _preview(request.session.get('account_view_mode')):
        _fail()
    return _actor(request.user)


def _domain():
    value = getattr(settings, 'GOOGLE_WORKSPACE_DOMAIN', '')
    if (not isinstance(value, str) or not 1 <= len(value) <= 253
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p)
                   for p in value.split('.'))):
        _fail()
    return value


def _email(value):
    if not _text(value, 254) or value.count('@') != 1:
        return False
    validate_email(value)
    return value.rsplit('@', 1)[1].casefold() == _domain()


def _callback_uri():
    origin = getattr(settings, 'CUSTOMER_APPLICATION_PUBLIC_URL', '')
    if not isinstance(origin, str) or re.search(r'[\s\\\x00-\x1f\x7f]', origin):
        _fail()
    parts = urlsplit(origin)
    if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path or parts.query or parts.fragment
            or parts.netloc.endswith(':') or origin != 'https://' + parts.netloc):
        _fail()
    # Force port validation and reject non-DNS/ambiguous host spellings.
    if (parts.port is not None and not 1 <= parts.port <= 65535
            or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', parts.hostname)
            or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', p)
                   for p in parts.hostname.split('.'))):
        _fail()
    return origin + reverse('customer_application_google_callback')


@sensitive_variables()
def _json(method, url, **kwargs):
    # Every caller constructs URLs from constants; redirects and retries are disabled.
    response = getattr(requests, method)(url, timeout=30, allow_redirects=False, **kwargs)
    if not 200 <= response.status_code < 300:
        _fail()
    result = response.json()
    if not isinstance(result, dict):
        _fail()
    return result


def _scope(value):
    if not isinstance(value, str):
        _fail()
    granted = set(value.split())
    if 'https://www.googleapis.com/auth/userinfo.email' in granted:
        granted.remove('https://www.googleapis.com/auth/userinfo.email')
        granted.add('email')
    # 同じGoogle clientに以前許可したログイン/profile/Drive権限が応答に
    # 含まれる場合がある。要求は最小限にしつつ必須権限の欠落だけを拒否する。
    if not set(SCOPES).issubset(granted):
        _fail()


def _identity(actor, identity, expected_sub=None, expected_email=None):
    if (identity.get('email_verified') is not True or identity.get('hd') != _domain()
            or not _email(identity.get('email')) or not _text(identity.get('sub'), 255)
            or identity['email'].casefold() != actor.email.casefold()
            or expected_sub is not None and identity['sub'] != expected_sub
            or expected_email is not None and identity['email'].casefold() != expected_email.casefold()):
        _fail()
    _social(actor, identity['sub'])


def _social(actor, sub):
    from allauth.socialaccount.models import SocialAccount

    linked = list(SocialAccount.objects.filter(user_id=actor.pk, provider='google')
                  .values_list('uid', flat=True))
    if any(uid != sub for uid in linked):
        _fail()


@sensitive_variables()
def _id_token(raw, client, nonce):
    if not _text(raw, 32768):
        _fail()
    header = jwt.get_unverified_header(raw)
    if header.get('alg') != 'RS256' or not _text(header.get('kid'), 255):
        _fail()
    keys = _json('get', CERTS_URL).get('keys')
    if not isinstance(keys, list):
        _fail()
    matching = [key for key in keys if isinstance(key, dict) and key.get('kid') == header['kid']
                and key.get('kty') == 'RSA' and key.get('use', 'sig') == 'sig']
    if len(matching) != 1:
        _fail()
    claims = jwt.decode(raw, jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(matching[0])),
                        algorithms=['RS256'], audience=client.client_id,
                        options={'require': ['exp', 'iat', 'iss', 'aud', 'sub', 'nonce']})
    if (claims['iss'] not in {'https://accounts.google.com', 'accounts.google.com'}
            or not _equal(claims['nonce'], nonce)
            or 'azp' in claims and claims['azp'] != client.client_id):
        _fail()
    return claims


def _binding(conn):
    return dict(version=1, actor_id=str(conn.actor_id), environment=conn.environment,
                client_id=conn.client_id, google_sub=conn.google_sub, email=conn.email)


@sensitive_variables()
def _fernet():
    secret = settings.SECRET_KEY
    if not isinstance(secret, str) or not secret:
        _fail()
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=b'tenasapo-knowledge/customer-application-google/refresh/v1').derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(key))


@sensitive_variables()
def _encrypt(conn, token):
    return _fernet().encrypt(json.dumps(dict(_binding(conn), refresh_token=token),
                                       separators=(',', ':')).encode()).decode('ascii')


@sensitive_variables()
def _decrypt(conn):
    payload = json.loads(_fernet().decrypt(conn.encrypted_refresh_token.encode('ascii')))
    if (not isinstance(payload, dict) or not _text(payload.get('refresh_token'))
            or any(payload.get(k) != v for k, v in _binding(conn).items())):
        _fail()
    return payload['refresh_token']


@sensitive_variables()
def make_auth_request(request):
    try:
        actor = _request_actor(request)
        client = get_oauth_client()
        callback = _callback_uri()
        state, verifier, nonce = (secrets.token_urlsafe(48) for _ in range(3))
        request.session[SESSION_KEY] = dict(
            state=state, verifier=verifier, nonce=nonce, actor_id=str(actor.pk),
            environment=current_chat_environment(), client_id=client.client_id,
            callback_uri=callback, issued_at=time.time())
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        return AUTH_URL + '?' + urlencode(dict(
            client_id=client.client_id, redirect_uri=callback, response_type='code',
            scope=' '.join(SCOPES), state=state, nonce=nonce, code_challenge=challenge,
            code_challenge_method='S256', access_type='offline', prompt='consent',
            login_hint=actor.email, hd=_domain(), include_granted_scopes='false'))
    except Exception:
        raise CustomerGoogleError() from None


@sensitive_variables()
def handle_callback(request):
    try:
        pending = request.session.pop(SESSION_KEY, None)
        actor = _request_actor(request)
        client = get_oauth_client()
        if not isinstance(pending, dict):
            _fail()
        issued = pending.get('issued_at')
        age = time.time() - issued if type(issued) in (int, float) else -1
        if (not _equal(pending.get('state'), request.GET.get('state'))
                or not math.isfinite(age) or not 0 <= age <= 600
                or pending.get('actor_id') != str(actor.pk)
                or pending.get('environment') != current_chat_environment()
                or pending.get('client_id') != client.client_id
                or pending.get('callback_uri') != _callback_uri()
                or not _text(pending.get('nonce'), 128)
                or not isinstance(pending.get('verifier'), str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', pending['verifier'])
                or 'error' in request.GET or not _text(request.GET.get('code'))):
            _fail()
        CustomerApplicationGoogleState.objects.filter(expires_at__lt=timezone.now()).delete()
        _, unused = CustomerApplicationGoogleState.objects.get_or_create(
            state_hash=hashlib.sha256(pending['state'].encode()).hexdigest(),
            defaults={'expires_at': timezone.now() + timedelta(seconds=601)},
        )
        if not unused:
            _fail()
        token = _json('post', TOKEN_URL, data=dict(
            grant_type='authorization_code', code=request.GET['code'],
            redirect_uri=pending['callback_uri'], client_id=client.client_id,
            client_secret=client.client_secret, code_verifier=pending['verifier']))
        _scope(token.get('scope'))
        if (not _text(token.get('access_token')) or not _text(token.get('refresh_token'))
                or str(token.get('token_type', '')).lower() != 'bearer'):
            _fail()
        claims = _id_token(token.get('id_token'), client, pending['nonce'])
        identity = _json('get', USERINFO_URL, headers=_headers(token['access_token']))
        _identity(actor, identity, expected_sub=claims['sub'])
        actor = _request_actor(request)
        _identity(actor, identity)
        if (get_oauth_client() != client or pending['environment'] != current_chat_environment()
                or pending['callback_uri'] != _callback_uri()):
            _fail()
        conn = CustomerApplicationGoogleConnection(
            actor=actor, environment=pending['environment'], client_id=client.client_id,
            google_sub=identity['sub'], email=identity['email'].casefold())
        conn.encrypted_refresh_token = _encrypt(conn, token['refresh_token'])
        saved, _ = CustomerApplicationGoogleConnection.objects.update_or_create(
            actor=actor, environment=conn.environment, defaults={
                'client_id': conn.client_id, 'google_sub': conn.google_sub,
                'email': conn.email, 'encrypted_refresh_token': conn.encrypted_refresh_token})
        return saved
    except Exception:
        raise CustomerGoogleError() from None


@sensitive_variables()
def get_connection(actor):
    try:
        saved = _actor(actor)
        client = get_oauth_client()
        conn = CustomerApplicationGoogleConnection.objects.filter(
            actor=saved, environment=current_chat_environment()).first()
        if conn is not None:
            if (conn.client_id != client.client_id or not _text(conn.google_sub, 255)
                    or not _email(conn.email) or conn.email.casefold() != saved.email.casefold()):
                _fail()
            _social(saved, conn.google_sub)
            _decrypt(conn)
        return conn
    except Exception:
        raise CustomerGoogleError() from None


def disconnect(request):
    try:
        actor = _request_actor(request)
        request.session.pop(SESSION_KEY, None)
        CustomerApplicationGoogleConnection.objects.filter(
            actor=actor, environment=current_chat_environment()).delete()
    except Exception:
        raise CustomerGoogleError() from None


def _headers(token):
    return {'Authorization': 'Bearer ' + token}


@sensitive_variables()
def get_access_token(actor):
    try:
        conn = get_connection(actor)
        client = get_oauth_client()
        if conn is None or conn.client_id != client.client_id:
            _fail()
        token = _json('post', TOKEN_URL, data=dict(
            grant_type='refresh_token', refresh_token=_decrypt(conn),
            client_id=client.client_id, client_secret=client.client_secret))
        if (not _text(token.get('access_token'))
                or str(token.get('token_type', '')).lower() != 'bearer'):
            _fail()
        if 'scope' in token:
            _scope(token['scope'])
        identity = _json('get', USERINFO_URL, headers=_headers(token['access_token']))
        _identity(_actor(actor), identity, conn.google_sub, conn.email)
        current = get_connection(actor)
        if (current is None or current.pk != conn.pk
                or current.encrypted_refresh_token != conn.encrypted_refresh_token
                or get_oauth_client() != client):
            _fail()
        # Google normally does not rotate here; preserve a returned rotation securely.
        if 'refresh_token' in token:
            if not _text(token['refresh_token']):
                _fail()
            encrypted = _encrypt(conn, token['refresh_token'])
            changed = CustomerApplicationGoogleConnection.objects.filter(
                pk=conn.pk, encrypted_refresh_token=conn.encrypted_refresh_token).update(
                    encrypted_refresh_token=encrypted, updated_at=timezone.now())
            if changed != 1:
                _fail()
            conn.encrypted_refresh_token = encrypted
        return conn, token['access_token']
    except Exception:
        raise CustomerGoogleError() from None


@sensitive_variables()
def list_applications(actor, start_row):
    try:
        if type(start_row) is not int or start_row < 2:
            _fail()
        _, token = get_access_token(actor)
        url = SHEETS_URL + SPREADSHEET_ID
        metadata = _json('get', url, headers=_headers(token), params={'fields': 'sheets.properties'})
        sheets = metadata.get('sheets')
        if not isinstance(sheets, list):
            _fail()
        matches = [s['properties'] for s in sheets if isinstance(s, dict)
                   and isinstance(s.get('properties'), dict)
                   and type(s['properties'].get('sheetId')) is int
                   and s['properties']['sheetId'] == SHEET_ID]
        if len(matches) != 1 or not isinstance(matches[0].get('title'), str) or not matches[0]['title']:
            _fail()
        title = "'" + matches[0]['title'].replace("'", "''") + "'!"
        data = _json('get', url + '/values:batchGet', headers=_headers(token), params={
            'ranges': [title + 'A1:F1', title + f'A{start_row}:F{start_row + 199}'],
            'valueRenderOption': 'FORMATTED_VALUE', 'majorDimension': 'ROWS'})
        ranges = data.get('valueRanges')
        if (not isinstance(ranges, list) or len(ranges) != 2
                or not all(isinstance(r, dict) for r in ranges)
                or ranges[0].get('values') != [HEADERS]):
            _fail()
        rows = ranges[1].get('values', [])
        if not isinstance(rows, list) or len(rows) > 200:
            _fail()
        applications = []
        for offset, row in enumerate(rows):
            if not isinstance(row, list) or len(row) > 6 or not all(isinstance(v, str) for v in row):
                _fail()
            row = row + [''] * (6 - len(row))
            if not any(v.strip() for v in row):
                continue
            applications.append(dict(zip(
                ('submitted_at', 'applicant_email', 'company_name', 'department', 'display_name', 'email'),
                row), source_key=f'{SPREADSHEET_ID}:{SHEET_ID}:{start_row + offset}'))
        _actor(actor)
        return {'ok': True, 'applications': applications,
                'next_row': start_row + 200 if len(rows) == 200 else None}
    except Exception:
        raise CustomerGoogleError() from None


def _remote_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,255}', value):
        _fail()
    return value


@sensitive_variables()
def _scan(token, message_id):
    page_token = None
    seen = set()
    found = None
    for _ in range(10):
        params = {'maxResults': 100}
        if page_token:
            params['pageToken'] = page_token
        page = _json('get', GMAIL_URL, headers=_headers(token), params=params)
        drafts = page.get('drafts', [])
        if not isinstance(drafts, list) or len(drafts) > 100:
            _fail()
        for draft in drafts:
            if not isinstance(draft, dict):
                _fail()
            draft_id = _remote_id(draft.get('id'))
            full = _json('get', GMAIL_URL + '/' + draft_id,
                         headers=_headers(token), params={'format': 'full'})
            if full.get('id') != draft_id:
                _fail()
            headers = full['message']['payload']['headers']
            if not isinstance(headers, list):
                _fail()
            ids = []
            for header in headers:
                if (not isinstance(header, dict) or not isinstance(header.get('name'), str)
                        or not isinstance(header.get('value'), str)):
                    _fail()
                if header['name'].lower() == 'message-id':
                    ids.append(header['value'].strip())
            if message_id in ids:
                if len(ids) != 1 or found is not None:
                    _fail()
                found = draft_id
        page_token = page.get('nextPageToken')
        if not page_token:
            return found
        if not _text(page_token, 4096) or page_token in seen:
            _fail()
        seen.add(page_token)
    _fail()  # Incomplete scan must never authorize a new POST.


def _owned(actor, application, conn):
    if (application.reviewed_by_id != actor.pk or application.status != 'approved'
            or application.draft_google_sub and application.draft_google_sub != conn.google_sub
            or application.draft_mailbox_email
            and application.draft_mailbox_email.casefold() != conn.email.casefold()
            or bool(application.draft_google_sub) != bool(application.draft_mailbox_email)):
        _fail()
    if application.draft_remote_attempted and (
            not application.draft_google_sub or not application.draft_message_id):
        _fail()


@sensitive_variables()
def create_draft(actor, application, subject, body):
    """単一POSTのみ。返却IDの保存は既存サービスの責務（復旧はMessage-ID照合）。"""
    try:
        if (not isinstance(application, CustomerApplication) or application._state.adding
                or application.pk is None or db_connection.in_atomic_block):
            _fail()
        _actor(actor)
        item = CustomerApplication.objects.get(pk=application.pk)
        # Reject another reviewer before token refresh or any mailbox access.
        if item.reviewed_by_id != actor.pk:
            _fail()
        conn, token = get_access_token(actor)
        _owned(actor, item, conn)
        if item.draft_id:
            return {'ok': True, 'draft_id': _remote_id(item.draft_id)}
        if (not isinstance(subject, str) or not subject.strip() or len(subject) > 998
                or re.search(r'[\x00-\x1f\x7f]', subject)
                or not isinstance(body, str)
                or not body.rstrip().splitlines()
                or body.rstrip().splitlines()[-1] != f'Nexus申請番号: {item.pk}'):
            _fail()
        validate_email(item.email)
        if not _text(item.email, 254):
            _fail()
        message_id = item.draft_message_id or f'<{uuid.uuid4()}@nexus.invalid>'
        if not re.fullmatch(r'<[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}@nexus\.invalid>', message_id):
            _fail()
        found = _scan(token, message_id)
        current = get_connection(actor)
        if (current is None or current.pk != conn.pk
                or current.encrypted_refresh_token != conn.encrypted_refresh_token):
            _fail()
        with transaction.atomic():
            locked = CustomerApplication.objects.select_for_update().get(pk=item.pk)
            _actor(actor)
            _owned(actor, locked, current)
            if locked.draft_id:
                return {'ok': True, 'draft_id': _remote_id(locked.draft_id)}
            if found:
                if (locked.draft_message_id != message_id or not locked.draft_google_sub
                        or not locked.draft_remote_attempted):
                    _fail()
                return {'ok': True, 'draft_id': found}
            if locked.draft_remote_attempted or locked.draft_message_id != item.draft_message_id:
                _fail()
            message = EmailMessage(policy=SMTP)
            message['To'] = locked.email  # F列だけ。回答者(B列)には送らない。
            message['Subject'] = subject
            message['Message-ID'] = message_id
            message.set_content(body)
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode('ascii')
            # Conditional UPDATE also protects SQLite, where row locks are unavailable.
            changed = CustomerApplication.objects.filter(
                pk=locked.pk, reviewed_by_id=actor.pk, draft_remote_attempted=False,
                draft_message_id=locked.draft_message_id,
                draft_google_sub=locked.draft_google_sub,
                draft_mailbox_email=locked.draft_mailbox_email,
            ).update(draft_google_sub=conn.google_sub, draft_mailbox_email=conn.email,
                     draft_message_id=message_id, draft_remote_attempted=True)
            if changed != 1:
                _fail()
        # Durable attempt precedes the ONLY create request; no retry on any failure.
        current = get_connection(actor)
        if (current is None or current.pk != conn.pk
                or current.encrypted_refresh_token != conn.encrypted_refresh_token):
            _fail()
        result = _json('post', GMAIL_URL, headers=_headers(token), json={'message': {'raw': raw}})
        return {'ok': True, 'draft_id': _remote_id(result.get('id'))}
    except Exception:
        raise CustomerGoogleError(draft=True) from None