"""Offline OAuth/security regressions; only HTTP responses are simulated.

The small URLconf keeps core tests independent of the optional settings views.
All credentials, accounts, identifiers and PDF contents are test-only fixtures.
"""

import base64
from dataclasses import replace
from email import policy
from email.parser import BytesParser
import hashlib
import json
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import path, reverse
import requests

from . import drive_oauth as oauth
from .drive_oauth_models import DriveOAuthConnection
from .report_drive_upload import MAX_PDF_BYTES


urlpatterns = [
    path('offline/drive/callback/', lambda request: HttpResponse(),
         name='drive_oauth_callback'),
]

SOURCE = 'tenasapo_knowledge.drive_oauth'
CLIENT_ID = 'offline-drive-client.apps.googleusercontent.com'
CLIENT_SECRET = 'offline-drive-client-secret-not-for-production'
REFRESH = 'offline-refresh-token-private'
ACCESS = 'offline-access-token-private'
SECRET_KEY = 'offline-django-secret-key-not-for-production'
FOLDER_ID = 'offline_folder_0123456789'
FILE_ID = 'offline_file_0123456789'
EMAIL = 'drive-test@systena.co.jp'
SUB = 'offline-google-subject'
CODE = 'offline-single-use-code'
LEAK = 'private-upstream-error-do-not-disclose'
PDF = b'%PDF-1.4\n% offline binary fixture \x00\xff\n%%EOF\n'
FILENAME = '月次レポート_2026-11.pdf'
APP = {'google': {'APP': {'client_id': CLIENT_ID, 'secret': CLIENT_SECRET}}}


@override_settings(
    DEBUG=True, SECRET_KEY=SECRET_KEY, SECRET_KEY_FALLBACKS=[],
    GOOGLE_WORKSPACE_DOMAIN='systena.co.jp',
    GOOGLE_DRIVE_REPORT_FOLDER_ID=FOLDER_ID, SOCIALACCOUNT_PROVIDERS=APP,
    ROOT_URLCONF=__name__, ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class DriveOAuthTests(TestCase):
    def setUp(self):
        super().setUp()
        self.guards = [self.enterContext(patch(
            target, side_effect=AssertionError('live network forbidden')))
            for target in (
                'requests.sessions.Session.request', 'urllib.request.urlopen',
                'http.client.HTTPConnection.connect', 'http.client.HTTPSConnection.connect',
                'socket.create_connection', 'socket.socket.connect', 'socket.socket.connect_ex',
            )]
        self.addCleanup(self.assert_no_network)
        self.post = self.enterContext(patch(
            SOURCE + '.requests.post', side_effect=AssertionError('unexpected POST')))
        self.get = self.enterContext(patch(
            SOURCE + '.requests.get', side_effect=AssertionError('unexpected GET')))
        self.actor = get_user_model().objects.create_user('offline-drive-staff', is_staff=True)
        self.other = get_user_model().objects.create_user('offline-other-staff', is_staff=True)
        self.factory = RequestFactory()

    def assert_no_network(self):
        # Core intentionally catches transport exceptions, so assert the guards
        # were untouched even when a test expected a sanitized OAuth exception.
        for guard in self.guards:
            guard.assert_not_called()

    @staticmethod
    def response(data, status=200):
        return Mock(status_code=status, json=Mock(return_value=data))

    @staticmethod
    def token(**changes):
        data = dict(access_token=ACCESS, refresh_token=REFRESH,
                    token_type='Bearer', scope='openid email ' + oauth.DRIVE_SCOPE)
        data.update(changes)
        return data

    @staticmethod
    def identity(**changes):
        data = dict(sub=SUB, email=EMAIL, email_verified=True, hd='systena.co.jp')
        data.update(changes)
        return data

    def begin(self, actor=None):
        request = self.factory.post('/offline/drive/connect/')
        request.user = self.actor if actor is None else actor
        request.session = SessionStore()
        url = oauth.make_auth_request(request)
        return request, url

    def callback_request(self, started, **query):
        values = dict(state=started.session[oauth.SESSION_KEY]['state'], code=CODE)
        values.update(query)
        request = self.factory.get(reverse('drive_oauth_callback'), values)
        request.user = started.user
        request.session = started.session
        return request

    def connect(self, actor=None):
        started, _ = self.begin(actor)
        request = self.callback_request(started)
        self.post.side_effect = None
        self.post.return_value = self.response(self.token())
        self.get.side_effect = None
        self.get.return_value = self.response(self.identity())
        saved = oauth.handle_callback(request)
        self.post.reset_mock()
        self.get.reset_mock()
        return saved

    def assert_safe_error(self, operation, message):
        with self.assertRaises(oauth.DriveOAuthError) as caught:
            operation()
        self.assertEqual(str(caught.exception), message)
        for private in (CLIENT_SECRET, REFRESH, ACCESS, CODE, LEAK, SECRET_KEY, 'Traceback'):
            self.assertNotIn(private, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def assert_callback_rejected(self, request, *, posts=0, gets=0):
        self.assert_safe_error(lambda: oauth.handle_callback(request), oauth.AUTH_ERROR)
        self.assertNotIn(oauth.SESSION_KEY, request.session)
        self.assertEqual(self.post.call_count, posts)
        self.assertEqual(self.get.call_count, gets)
        self.assertFalse(DriveOAuthConnection.objects.exists())

    def prepare_upload(self):
        self.connect()
        config = oauth.get_drive_upload_config(self.actor)
        self.post.side_effect = [self.response(self.token()), self.response({'id': FILE_ID})]
        return config

    def test_consent_url_has_single_use_state_pkce_offline_and_broad_drive_scope(self):
        request, url = self.begin()
        parsed = urlsplit(url)
        self.assertEqual(parsed.scheme + '://' + parsed.netloc + parsed.path, oauth.AUTH_URL)
        query = parse_qs(parsed.query)
        pending = request.session[oauth.SESSION_KEY].copy()
        for key, value in {
            'client_id': CLIENT_ID, 'response_type': 'code', 'access_type': 'offline',
            'prompt': 'consent', 'hd': 'systena.co.jp', 'code_challenge_method': 'S256',
            'state': pending['state'], 'redirect_uri': request.build_absolute_uri(
                reverse('drive_oauth_callback')),
        }.items():
            self.assertEqual(query[key], [value])
        self.assertEqual(set(query['scope'][0].split()), {'openid', 'email', oauth.DRIVE_SCOPE})
        self.assertNotIn('https://www.googleapis.com/auth/drive.file', query['scope'][0].split())
        verifier = pending['verifier']
        self.assertRegex(verifier, r'^[A-Za-z0-9_-]{43,128}$')
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest())
        self.assertEqual(query['code_challenge'], [challenge.rstrip(b'=').decode('ascii')])
        self.assertEqual(pending['actor_id'], str(self.actor.pk))
        self.assertEqual(pending['environment'], 'development')
        self.assertEqual(pending['client_id'], CLIENT_ID)
        self.assertNotIn(verifier, url)
        self.assertNotIn(CLIENT_SECRET, url)
        second = oauth.make_auth_request(request)
        self.assertNotEqual(second, url)
        self.assertNotEqual(request.session[oauth.SESSION_KEY]['state'], pending['state'])
        self.assertNotEqual(request.session[oauth.SESSION_KEY]['verifier'], verifier)
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_successful_callback_uses_code_and_same_session_and_consumes_state(self):
        started, _ = self.begin()
        pending = started.session[oauth.SESSION_KEY].copy()
        request = self.callback_request(started)
        self.assertIs(request.session, started.session)
        self.post.side_effect = None
        self.post.return_value = self.response(self.token())
        self.get.side_effect = None
        self.get.return_value = self.response(self.identity())
        saved = oauth.handle_callback(request)
        self.post.assert_called_once_with(oauth.TOKEN_URL, data={
            'grant_type': 'authorization_code', 'code': CODE,
            'redirect_uri': pending['callback_uri'], 'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET, 'code_verifier': pending['verifier'],
        }, timeout=30, allow_redirects=False)
        self.get.assert_called_once_with(oauth.USERINFO_URL,
            headers={'Authorization': 'Bearer ' + ACCESS}, timeout=30, allow_redirects=False)
        self.assertNotIn(oauth.SESSION_KEY, started.session)
        saved.refresh_from_db()
        self.assertEqual((saved.actor_id, saved.environment, saved.client_id, saved.google_sub,
                          saved.email), (self.actor.pk, 'development', CLIENT_ID, SUB, EMAIL))
        self.assertEqual(oauth.get_connection(self.actor).pk, saved.pk)
        self.assert_safe_error(lambda: oauth.handle_callback(request), oauth.AUTH_ERROR)
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(self.get.call_count, 1)

    def test_callback_rejects_bad_state_expiry_and_pkce_before_http(self):
        cases = [
            {'state': 'wrong'}, {'issued_at': -1000}, {'issued_at': 2001},
            {'issued_at': float('nan')}, {'issued_at': float('inf')},
            {'issued_at': '2000'}, {'issued_at': True},
            {'verifier': ''}, {'verifier': 'x' * 42}, {'verifier': 'x' * 129},
            {'verifier': '!' * 64}, {'callback_uri': 'https://other.example.test/callback'},
        ]
        with patch(SOURCE + '.time.time', return_value=2000):
            for changes in cases:
                with self.subTest(changes=changes):
                    started, _ = self.begin()
                    request = self.callback_request(started)
                    started.session[oauth.SESSION_KEY].update(changes)
                    self.assert_callback_rejected(request)

    def test_callback_rejects_missing_state_code_error_and_different_session_before_http(self):
        for changes in ({'state': ''}, {'code': ''}, {'code': ' code '}, {'error': LEAK}):
            with self.subTest(changes=changes):
                started, _ = self.begin()
                self.assert_callback_rejected(self.callback_request(started, **changes))
        started, _ = self.begin()
        request = self.callback_request(started)
        request.session = SessionStore()
        self.assert_callback_rejected(request)

    def test_callback_rejects_changed_environment_actor_or_client_before_http(self):
        for kind in ('environment', 'actor', 'client'):
            with self.subTest(kind=kind):
                started, _ = self.begin()
                request = self.callback_request(started)
                if kind == 'actor':
                    request.user = self.other
                settings = ({'DEBUG': False} if kind == 'environment' else
                            {'SOCIALACCOUNT_PROVIDERS': {'google': {'APP': {
                                'client_id': 'other-offline-client', 'secret': CLIENT_SECRET}}}}
                            if kind == 'client' else {})
                with override_settings(**settings):
                    self.assert_callback_rejected(request)

    def test_callback_rejects_missing_refresh_scope_or_access_token_before_userinfo(self):
        for changes in (
            {'refresh_token': None}, {'refresh_token': ''}, {'access_token': ''},
            {'scope': None}, {'scope': 'openid email'},
            {'scope': 'https://www.googleapis.com/auth/drive.file'},
            {'scope': oauth.DRIVE_SCOPE + '.readonly'}, {'token_type': 'Basic'},
        ):
            with self.subTest(changes=changes):
                started, _ = self.begin()
                self.post.reset_mock()
                self.post.side_effect = None
                self.post.return_value = self.response(self.token(**changes))
                self.assert_callback_rejected(self.callback_request(started), posts=1)

    def test_callback_rejects_unverified_wrong_hd_domain_and_malformed_identity(self):
        for changes in (
            {'email_verified': False}, {'email_verified': 'true'}, {'email_verified': 1},
            {'email_verified': None}, {'hd': 'other.example'}, {'hd': None},
            {'hd': 'SYSTENA.CO.JP'}, {'email': 'user@other.example'},
            {'email': 'user@systena.co.jp.evil.example'}, {'email': 'user@sub.systena.co.jp'},
            {'email': 'user@@systena.co.jp'}, {'email': None},
            {'email': ' user@systena.co.jp'}, {'sub': ''}, {'sub': None},
        ):
            with self.subTest(changes=changes):
                started, _ = self.begin()
                self.post.reset_mock()
                self.get.reset_mock()
                self.post.side_effect = self.get.side_effect = None
                self.post.return_value = self.response(self.token())
                self.get.return_value = self.response(self.identity(**changes))
                self.assert_callback_rejected(self.callback_request(started), posts=1, gets=1)

    def test_invalid_configured_domain_rejects_even_in_debug_before_http(self):
        for domain in ('', 'SYSTENA.CO.JP', 'systena.co.jp/', 'systena..co.jp', '*.systena.co.jp'):
            with self.subTest(domain=domain), override_settings(GOOGLE_WORKSPACE_DOMAIN=domain):
                self.assert_safe_error(lambda: self.begin(), oauth.AUTH_ERROR)
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_callback_redirects_http_errors_and_transport_failures_are_safe(self):
        for stage in ('token', 'userinfo'):
            for failure in (self.response(self.token(), 302), self.response({}, 500),
                            self.response([]), requests.Timeout(LEAK + CLIENT_SECRET + CODE),
                            Mock(status_code=200, json=Mock(side_effect=ValueError(LEAK)))):
                with self.subTest(stage=stage, failure=type(failure).__name__):
                    started, _ = self.begin()
                    self.post.reset_mock()
                    self.get.reset_mock()
                    self.post.side_effect = self.get.side_effect = None
                    self.post.return_value = self.response(self.token())
                    self.get.return_value = self.response(self.identity())
                    target = self.post if stage == 'token' else self.get
                    if isinstance(failure, Exception):
                        target.side_effect = failure
                    else:
                        target.return_value = failure
                    self.assert_callback_rejected(self.callback_request(started),
                                                  posts=1, gets=int(stage == 'userinfo'))
                    for call in self.post.call_args_list + self.get.call_args_list:
                        self.assertIs(call.kwargs['allow_redirects'], False)

    def test_revocation_during_callback_does_not_persist_credentials(self):
        started, _ = self.begin()
        self.post.side_effect = None
        self.post.return_value = self.response(self.token())

        def revoke(*args, **kwargs):
            get_user_model().objects.filter(pk=self.actor.pk).update(is_staff=False)
            return self.response(self.identity())

        self.get.side_effect = revoke
        self.assert_callback_rejected(self.callback_request(started), posts=1, gets=1)

    def test_persistence_is_ciphertext_and_refresh_is_only_exposed_in_config(self):
        saved = self.connect()
        row = DriveOAuthConnection.objects.values().get(pk=saved.pk)
        serialized = json.dumps(row, default=str)
        for private in (REFRESH, ACCESS, CLIENT_SECRET, CODE):
            self.assertNotIn(private, serialized)
        self.assertTrue(saved.encrypted_refresh_token)
        config = oauth.get_drive_upload_config(self.actor)
        self.assertEqual(config.refresh_token, REFRESH)
        self.assertEqual(config.client_secret, CLIENT_SECRET)
        self.assertEqual(config.folder_id, FOLDER_ID)
        self.assertNotIn(REFRESH, repr(config))
        self.assertNotIn(CLIENT_SECRET, repr(config))
        self.assertNotIn(CLIENT_SECRET, repr(oauth.get_oauth_client()))
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_ciphertext_binding_tamper_fails_closed_before_http(self):
        saved = self.connect()
        original = DriveOAuthConnection.objects.values().get(pk=saved.pk)
        for changes in (
            {'actor_id': self.other.pk}, {'environment': 'production'},
            {'client_id': 'other-offline-client'}, {'google_sub': 'different-sub'},
            {'email': 'different@systena.co.jp'},
            {'encrypted_refresh_token': REFRESH}, {'encrypted_refresh_token': 'broken-ciphertext'},
        ):
            with self.subTest(field=next(iter(changes))):
                DriveOAuthConnection.objects.filter(pk=saved.pk).update(**changes)
                actor = self.other if 'actor_id' in changes else self.actor
                with override_settings(DEBUG='environment' not in changes):
                    self.assert_safe_error(lambda: oauth.get_drive_upload_config(actor),
                                           oauth.CONNECTION_ERROR)
                DriveOAuthConnection.objects.filter(pk=saved.pk).update(**{
                    key: original[key] for key in changes})
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_secret_key_rotation_does_not_decrypt_using_fallback_keys(self):
        saved = self.connect()
        ciphertext = saved.encrypted_refresh_token
        with override_settings(SECRET_KEY='rotated-offline-key', SECRET_KEY_FALLBACKS=[SECRET_KEY]):
            self.assert_safe_error(lambda: oauth.get_drive_upload_config(self.actor),
                                   oauth.CONNECTION_ERROR)
        saved.refresh_from_db()
        self.assertEqual(saved.encrypted_refresh_token, ciphertext)
        self.assertEqual(oauth.get_drive_upload_config(self.actor).refresh_token, REFRESH)
        self.post.assert_not_called()

    def test_environment_and_actor_isolation_and_reconnect_updates_only_own_row(self):
        development = self.connect()
        self.assertIsNone(oauth.get_connection(self.other))
        with override_settings(DEBUG=False):
            self.assertIsNone(oauth.get_connection(self.actor))
            production = self.connect()
            self.assertEqual(oauth.get_connection(self.actor).pk, production.pk)
        other = self.connect(self.other)
        reconnected = self.connect()
        self.assertEqual(development.pk, reconnected.pk)
        self.assertNotEqual(development.encrypted_refresh_token, reconnected.encrypted_refresh_token)
        self.assertEqual(oauth.get_connection(self.actor).pk, development.pk)
        self.assertEqual(oauth.get_connection(self.other).pk, other.pk)
        self.assertEqual(DriveOAuthConnection.objects.count(), 3)

    def test_model_enforces_unique_actor_environment(self):
        saved = self.connect()
        with self.assertRaises(IntegrityError), transaction.atomic():
            DriveOAuthConnection.objects.create(
                actor=self.actor, environment=saved.environment, client_id=CLIENT_ID,
                google_sub=SUB, email=EMAIL, encrypted_refresh_token=saved.encrypted_refresh_token)

    def test_persisted_staff_and_active_revocation_reject_stale_actor_and_config(self):
        config = self.prepare_upload()
        for field in ('is_staff', 'is_active'):
            with self.subTest(field=field):
                get_user_model().objects.filter(pk=self.actor.pk).update(**{field: False})
                self.assertTrue(getattr(self.actor, field))
                self.assert_safe_error(lambda: oauth.get_drive_upload_config(self.actor),
                                       oauth.CONNECTION_ERROR)
                self.assert_safe_error(lambda: oauth.upload_pdf_direct(PDF, FILENAME, config),
                                       oauth.CONNECTION_ERROR)
                get_user_model().objects.filter(pk=self.actor.pk).update(**{field: True})
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_anonymous_nonstaff_inactive_and_unsaved_actors_cannot_connect(self):
        model = get_user_model()
        actors = [AnonymousUser(), model(username='unsaved', is_staff=True),
                  model.objects.create_user('nonstaff'),
                  model.objects.create_user('inactive-staff', is_staff=True, is_active=False)]
        for actor in actors:
            with self.subTest(actor=type(actor).__name__):
                self.assert_safe_error(lambda: self.begin(actor), oauth.AUTH_ERROR)
        self.post.assert_not_called()

    def test_upload_posts_one_refresh_one_related_create_and_returns_canonical_url(self):
        config = self.prepare_upload()
        result = oauth.upload_pdf_direct(PDF, FILENAME, config)
        self.assertEqual(result, f'https://drive.google.com/file/d/{FILE_ID}/view')
        self.assertEqual(self.post.call_count, 2)
        refresh, create = self.post.call_args_list
        self.assertEqual(refresh.args, (oauth.TOKEN_URL,))
        self.assertEqual(refresh.kwargs, {
            'data': {'grant_type': 'refresh_token', 'refresh_token': REFRESH,
                     'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET},
            'timeout': 30, 'allow_redirects': False,
        })
        self.assertEqual(create.args, (oauth.UPLOAD_URL,))
        self.assertEqual(create.kwargs['params'], {
            'uploadType': 'multipart', 'supportsAllDrives': 'true', 'fields': 'id'})
        self.assertEqual(create.kwargs['timeout'], 60)
        self.assertIs(create.kwargs['allow_redirects'], False)
        self.assertEqual(create.kwargs['headers']['Authorization'], 'Bearer ' + ACCESS)
        self.assertNotIn('files', create.kwargs)
        body = create.kwargs['data']
        self.assertIsInstance(body, bytes)
        content_type = create.kwargs['headers']['Content-Type']
        message = BytesParser(policy=policy.default).parsebytes(
            b'Content-Type: ' + content_type.encode('ascii') + b'\r\nMIME-Version: 1.0\r\n\r\n' + body)
        self.assertEqual(message.get_content_type(), 'multipart/related')
        boundary = message.get_boundary()
        self.assertTrue(boundary)
        self.assertIn(b'--' + boundary.encode('ascii') + b'--', body)
        parts = list(message.iter_parts())
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].get_content_type(), 'application/json')
        metadata = parts[0].get_payload(decode=True).decode('ascii')
        self.assertEqual(json.loads(metadata), {'name': FILENAME, 'parents': [FOLDER_ID]})
        self.assertNotIn(FILENAME.encode('utf-8'), body)
        self.assertEqual(parts[1].get_content_type(), 'application/pdf')
        self.assertEqual(parts[1].get_payload(decode=True), PDF)
        self.assertIn(PDF, body)
        self.assertNotIn(REFRESH.encode(), body)
        self.assertNotIn(CLIENT_SECRET.encode(), body)
        self.get.assert_not_called()  # No metadata lookup or sharing/permissions request.

    def test_upload_bad_file_ids_fail_without_retry_or_secret_leak(self):
        config = self.prepare_upload()
        for file_id in (None, '', 123, 'short', 'x' * 201, 'contains/slash',
                        'contains space', FILE_ID + '\n', 'https://evil.example/forged'):
            with self.subTest(file_id=file_id):
                self.post.reset_mock()
                self.post.side_effect = [self.response(self.token()), self.response({
                    'id': file_id, 'error': LEAK + CLIENT_SECRET + REFRESH})]
                self.assert_safe_error(lambda: oauth.upload_pdf_direct(PDF, FILENAME, config),
                                       oauth.UPLOAD_ERROR)
                self.assertEqual(self.post.call_count, 2)
        self.get.assert_not_called()

    def test_upload_timeout_redirect_and_http_failure_never_retry(self):
        config = self.prepare_upload()
        for failure in (requests.Timeout(LEAK + REFRESH + ACCESS + CLIENT_SECRET),
                        self.response({'id': FILE_ID}, 302), self.response({}, 403),
                        self.response({}, 503), self.response([]),
                        Mock(status_code=200, json=Mock(side_effect=ValueError(LEAK)))):
            with self.subTest(failure=type(failure).__name__):
                self.post.reset_mock()
                self.post.side_effect = [self.response(self.token()), failure]
                self.assert_safe_error(lambda: oauth.upload_pdf_direct(PDF, FILENAME, config),
                                       oauth.UPLOAD_ERROR)
                self.assertEqual(self.post.call_count, 2)
                for call in self.post.call_args_list:
                    self.assertIs(call.kwargs['allow_redirects'], False)
        self.get.assert_not_called()

    def test_refresh_failure_or_reduced_scope_prevents_drive_create(self):
        config = self.prepare_upload()
        failures = [requests.Timeout(LEAK + REFRESH + CLIENT_SECRET),
                    self.response(self.token(), 302), self.response({}, 401),
                    self.response(self.token(access_token='')),
                    self.response(self.token(token_type='Basic')),
                    self.response(self.token(scope='openid email')),
                    self.response(self.token(scope=None))]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.post.reset_mock()
                self.post.side_effect = [failure]
                self.assert_safe_error(lambda: oauth.upload_pdf_direct(PDF, FILENAME, config),
                                       oauth.REFRESH_ERROR)
                self.post.assert_called_once()
                self.assertEqual(self.post.call_args.args, (oauth.TOKEN_URL,))
                self.assertIs(self.post.call_args.kwargs['allow_redirects'], False)
        self.get.assert_not_called()

    def test_stale_or_tampered_upload_config_rejected_before_refresh(self):
        config = self.prepare_upload()
        for changes in (
            {'actor_id': self.other.pk}, {'environment': 'production'},
            {'folder_id': 'other_folder_0123456789'}, {'client_id': 'other-client'},
            {'client_secret': 'other-secret'}, {'refresh_token': 'other-refresh'},
        ):
            with self.subTest(field=next(iter(changes))):
                self.assert_safe_error(lambda: oauth.upload_pdf_direct(
                    PDF, FILENAME, replace(config, **changes)), oauth.CONNECTION_ERROR)
        with override_settings(DEBUG=False):
            self.assert_safe_error(lambda: oauth.upload_pdf_direct(PDF, FILENAME, config),
                                   oauth.CONNECTION_ERROR)
        DriveOAuthConnection.objects.filter(actor=self.actor).delete()
        self.assert_safe_error(lambda: oauth.upload_pdf_direct(PDF, FILENAME, config),
                               oauth.CONNECTION_ERROR)
        self.post.assert_not_called()

    def test_staff_revoked_during_refresh_prevents_drive_side_effect(self):
        config = self.prepare_upload()

        def revoke(*args, **kwargs):
            get_user_model().objects.filter(pk=self.actor.pk).update(is_staff=False)
            return self.response(self.token())

        self.post.side_effect = revoke
        self.assert_safe_error(lambda: oauth.upload_pdf_direct(PDF, FILENAME, config),
                               oauth.CONNECTION_ERROR)
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.args, (oauth.TOKEN_URL,))
        self.get.assert_not_called()

    def test_invalid_pdf_filename_or_folder_rejected_before_http(self):
        config = self.prepare_upload()
        for pdf in (None, b'', b'not PDF', bytearray(PDF), b'%PDF-' + b'x' * MAX_PDF_BYTES):
            with self.subTest(pdf_type=type(pdf).__name__), self.assertRaises(oauth.DriveOAuthError):
                oauth.upload_pdf_direct(pdf, FILENAME, config)
        for filename in ('../report.pdf', 'report.PDF', 'report.pdf\n', 'report\x00.pdf', '.pdf'):
            with self.subTest(filename=filename), self.assertRaises(oauth.DriveOAuthError):
                oauth.upload_pdf_direct(PDF, filename, config)
        for folder in ('', 'short', 'https://drive.google.com/folders/forged', 'folder/0123456789'):
            with self.subTest(folder=folder), override_settings(GOOGLE_DRIVE_REPORT_FOLDER_ID=folder):
                self.assert_safe_error(lambda: oauth.get_drive_upload_config(self.actor),
                                       oauth.CONNECTION_ERROR)
        self.post.assert_not_called()
        self.get.assert_not_called()