"""合成データとmock HTTPのみ。実行にはモデル登録・0090適用可能な状態が必要。"""

import base64
import copy
import json
import time
import uuid
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import jwt
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TransactionTestCase, override_settings

from . import customer_application_google as google
from .customer_application_google_models import CustomerApplicationGoogleConnection as Connection
from .customer_application_models import CustomerApplication


def response(data, status=200):
    result = Mock(status_code=status)
    result.json.return_value = data
    return result


@override_settings(
    SECRET_KEY='synthetic-customer-google-test-key',
    DEBUG=False,
    GOOGLE_WORKSPACE_DOMAIN='example.test',
    CUSTOMER_APPLICATION_PUBLIC_URL='https://tk-tenainfo.rikka.biz:8443/',
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class CustomerGoogleTests(TransactionTestCase):
    """TransactionTestCase: 下書きの試行記録をHTTPより前にcommitできる。"""

    def setUp(self):
        cache.clear()
        self.actor = get_user_model().objects.create_user(
            username='synthetic-reviewer', email='Reviewer@example.test',
            is_staff=True, is_active=True,
        )
        self.client_config = SimpleNamespace(client_id='synthetic-client', client_secret='synthetic-secret')
        self.patches = [
            patch.object(google, 'get_oauth_client', return_value=self.client_config),
            patch.object(google, 'reverse', return_value='/customer/google/callback/'),
            patch.object(google.requests, 'get'),
            patch.object(google.requests, 'post'),
        ]
        self.oauth_client, self.reverse, self.http_get, self.http_post = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.identity = {'sub': 'synthetic-sub', 'email': 'reviewer@EXAMPLE.TEST',
                         'email_verified': True, 'hd': 'example.test'}
        self.factory = RequestFactory()

    def request(self, params=None):
        req = self.factory.get('/callback/', params or {}, HTTP_HOST='untrusted.invalid')
        req.user = self.actor
        req.session = {}
        return req

    def connect(self):
        conn = Connection(actor=self.actor, environment='production',
                          client_id=self.client_config.client_id, google_sub='synthetic-sub',
                          email='reviewer@example.test')
        conn.encrypted_refresh_token = google._encrypt(conn, 'synthetic-refresh')
        conn.save()
        return conn

    def pending(self):
        req = self.request()
        url = google.make_auth_request(req)
        pending = copy.deepcopy(req.session[google.SESSION_KEY])
        req.GET = self.factory.get('/', {'state': pending['state'], 'code': 'synthetic-code'}).GET
        return req, pending, parse_qs(urlsplit(url).query)

    def token(self):
        return dict(access_token='synthetic-access', refresh_token='synthetic-refresh',
                    token_type='Bearer', scope=' '.join(google.SCOPES), id_token='synthetic-id-token')

    def callback(self, req):
        self.http_post.return_value = response(self.token())
        self.http_get.return_value = response(self.identity)
        with patch.object(google, '_id_token', return_value={'sub': 'synthetic-sub'}):
            return google.handle_callback(req)

    def application(self, **changes):
        fields = dict(source_key='synthetic:' + str(uuid.uuid4()),
                      submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                      applicant_email='respondent@example.test', email='invitee@example.test',
                      company_name='合成会社', department='', display_name='合成利用者',
                      status='approved', reviewed_by=self.actor)
        fields.update(changes)
        return CustomerApplication.objects.create(**fields)

    def draft(self, app):
        return google.create_draft(self.actor, app, '招待', f'合成本文\nNexus申請番号: {app.pk}')

    def mock_draft_token(self, conn):
        p = patch.object(google, 'get_access_token', return_value=(conn, 'synthetic-access'))
        p.start()
        self.addCleanup(p.stop)

    def assert_http_options(self):
        for mock in (self.http_get, self.http_post):
            for call in mock.call_args_list:
                self.assertEqual(call.kwargs['timeout'], 30)
                self.assertIs(call.kwargs['allow_redirects'], False)

    def test_auth_request_scopes_pkce_nonce_and_trusted_origin(self):
        req, pending, params = self.pending()
        self.assertEqual(set(params['scope'][0].split()), set(google.SCOPES))
        self.assertEqual(params['redirect_uri'], ['https://nexus.example.test/customer/google/callback/'])
        self.assertEqual(params['prompt'], ['consent'])
        self.assertEqual(params['access_type'], ['offline'])
        self.assertEqual(params['login_hint'], [self.actor.email])
        self.assertEqual(params['nonce'], [pending['nonce']])
        self.assertEqual(params['code_challenge_method'], ['S256'])
        self.assertNotEqual(params['code_challenge'][0], pending['verifier'])
        self.assertNotIn('drive', params['scope'][0])

    def test_callback_success_encrypted_and_replay_rejected(self):
        req, pending, _ = self.pending()
        conn = self.callback(req)
        self.assertEqual(conn.email, 'reviewer@example.test')
        self.assertNotIn('synthetic-refresh', conn.encrypted_refresh_token)
        self.assertNotIn('reviewer', repr(conn))
        self.assertEqual(google._decrypt(conn), 'synthetic-refresh')
        self.assertNotIn(google.SESSION_KEY, req.session)
        with self.assertRaises(google.CustomerGoogleError):
            self.callback(req)
        # A concurrent request with an old session snapshot also cannot replay.
        cache.clear()  # 消費済みstateはworker-local cacheでなくDBで共有される。
        req.session[google.SESSION_KEY] = pending
        with self.assertRaises(google.CustomerGoogleError):
            self.callback(req)
        self.assertEqual(self.http_post.call_count, 1)
        self.assert_http_options()

    def test_state_all_bindings_age_and_single_use_on_failure(self):
        changes = [dict(state='wrong'), dict(actor_id='different'),
                   dict(environment='development'), dict(client_id='different'),
                   dict(callback_uri='https://other.invalid/callback'),
                   dict(issued_at=time.time() - 601), dict(issued_at=float('nan')),
                   dict(issued_at=time.time() + 60), dict(verifier='invalid'), dict(nonce='')]
        for change in changes:
            with self.subTest(change=tuple(change)):
                req, _, _ = self.pending()
                req.session[google.SESSION_KEY].update(change)
                with self.assertRaises(google.CustomerGoogleError):
                    google.handle_callback(req)
                self.assertNotIn(google.SESSION_KEY, req.session)
        self.http_post.assert_not_called()

    def test_public_url_is_strict_https_origin(self):
        for origin in ('http://nexus.example.test', 'https://nexus.example.test/',
                       'https://nexus.example.test/path', 'https://user@nexus.example.test',
                       'https://nexus.example.test?x=1', 'https://nexus.example.test#x',
                       'https://nexus.example.test:bad', 'https://nexus.example.test\\@evil.invalid'):
            with self.subTest(origin=origin), self.settings(CUSTOMER_APPLICATION_PUBLIC_URL=origin):
                with self.assertRaises(google.CustomerGoogleError):
                    google.make_auth_request(self.request())

    def test_preview_in_session_and_actor_denied(self):
        for mode in ('demo', 'cs'):
            req = self.request()
            req.session['account_view_mode'] = mode
            with self.assertRaises(google.CustomerGoogleError):
                google.make_auth_request(req)
            self.actor._view_mode_override = mode
            with self.assertRaises(google.CustomerGoogleError):
                google.get_connection(self.actor)
        self.http_get.assert_not_called()
        self.http_post.assert_not_called()

    def test_effective_and_persisted_privileges_rechecked(self):
        self.connect()
        self.actor.is_staff = False
        with self.assertRaises(google.CustomerGoogleError):
            google.get_connection(self.actor)
        self.actor.is_staff = True
        get_user_model().objects.filter(pk=self.actor.pk).update(is_staff=False)
        with self.assertRaises(google.CustomerGoogleError):
            google.get_connection(self.actor)
        get_user_model().objects.filter(pk=self.actor.pk).update(is_superuser=True)
        self.actor.is_superuser = True
        self.assertIsNotNone(google.get_connection(self.actor))
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        with self.assertRaises(google.CustomerGoogleError):
            google.get_connection(self.actor)

    def test_callback_identity_and_scope_rejection(self):
        bad_identities = [dict(email_verified=False), dict(hd='other.test'),
                          dict(email='other@example.test'), dict(email='reviewer@other.test'),
                          dict(sub='other-sub'), dict(sub='')]
        for change in bad_identities:
            with self.subTest(change=tuple(change)):
                req, _, _ = self.pending()
                identity = dict(self.identity, **change)
                self.http_get.return_value = response(identity)
                self.http_post.return_value = response(self.token())
                with patch.object(google, '_id_token', return_value={'sub': 'synthetic-sub'}):
                    with self.assertRaises(google.CustomerGoogleError):
                        google.handle_callback(req)
        for scope in ('openid email', None):
            req, _, _ = self.pending()
            token = self.token()
            token['scope'] = scope
            self.http_post.return_value = response(token)
            with self.assertRaises(google.CustomerGoogleError):
                google.handle_callback(req)
        self.assertFalse(Connection.objects.exists())

    def test_existing_client_grants_do_not_break_additional_consent(self):
        req, _, _ = self.pending()
        token = self.token()
        token['scope'] += ' https://www.googleapis.com/auth/drive profile'
        self.http_post.return_value = response(token)
        self.http_get.return_value = response(self.identity)
        with patch.object(google, '_id_token', return_value={'sub': 'synthetic-sub'}):
            self.assertEqual(google.handle_callback(req).actor_id, self.actor.pk)

    def test_existing_google_social_account_sub_must_match(self):
        from allauth.socialaccount.models import SocialAccount

        SocialAccount.objects.create(user=self.actor, provider='google', uid='other-sub')
        req, _, _ = self.pending()
        with self.assertRaises(google.CustomerGoogleError):
            self.callback(req)
        self.assertFalse(Connection.objects.exists())

    def test_signed_id_token_nonce_audience_and_signature(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
        jwk.update(kid='synthetic-key', use='sig')
        self.http_get.return_value = response({'keys': [jwk]})
        claims = dict(iss='https://accounts.google.com', aud=self.client_config.client_id,
                      sub='synthetic-sub', nonce='synthetic-nonce', iat=int(time.time()),
                      exp=int(time.time()) + 600)
        raw = jwt.encode(claims, private, algorithm='RS256', headers={'kid': 'synthetic-key'})
        self.assertEqual(google._id_token(raw, self.client_config, 'synthetic-nonce')['sub'], 'synthetic-sub')
        with self.assertRaises(google.CustomerGoogleError):
            google._id_token(raw, self.client_config, 'different-nonce')
        for change in (dict(aud='other-client'), dict(iss='https://evil.invalid'),
                       dict(exp=int(time.time()) - 10), dict(azp='other-client')):
            raw = jwt.encode(dict(claims, **change), private, algorithm='RS256', headers={'kid': 'synthetic-key'})
            # Private helper may raise PyJWT errors; public callback masks them.
            with self.assertRaises(Exception):
                google._id_token(raw, self.client_config, 'synthetic-nonce')
        self.assert_http_options()

    def test_cipher_binds_all_fields_secret_and_separate_context(self):
        conn = self.connect()
        for name, value in (('actor_id', self.actor.pk + 100), ('environment', 'development'),
                            ('client_id', 'other'), ('google_sub', 'other'), ('email', 'other@example.test')):
            altered = copy.copy(conn)
            setattr(altered, name, value)
            with self.subTest(field=name), self.assertRaises(Exception):
                google._decrypt(altered)
        with self.settings(SECRET_KEY='rotated-synthetic-key'), self.assertRaises(Exception):
            google._decrypt(conn)
        from .drive_oauth import _fernet as drive_fernet

        with self.assertRaises(Exception):
            drive_fernet().decrypt(conn.encrypted_refresh_token.encode())

    def test_invalid_connection_never_returned_as_absent(self):
        self.assertIsNone(google.get_connection(self.actor))
        conn = self.connect()
        Connection.objects.filter(pk=conn.pk).update(encrypted_refresh_token='broken')
        with self.assertRaises(google.CustomerGoogleError):
            google.get_connection(self.actor)

    def test_refresh_validates_userinfo_every_time(self):
        self.connect()
        self.http_post.return_value = response(self.token())
        self.http_get.return_value = response(self.identity)
        conn, token = google.get_access_token(self.actor)
        self.assertEqual(token, 'synthetic-access')
        self.assertEqual(conn.google_sub, 'synthetic-sub')
        self.assertEqual(self.http_get.call_args.args[0], google.USERINFO_URL)
        for change in (dict(sub='different'), dict(email='different@example.test'),
                       dict(hd='different.test'), dict(email_verified=False)):
            self.http_get.return_value = response(dict(self.identity, **change))
            with self.assertRaises(google.CustomerGoogleError):
                google.get_access_token(self.actor)
        self.assert_http_options()

    def test_refresh_failures_are_fixed_and_no_secret_leaks(self):
        self.connect()
        for data, status in (({'error': 'synthetic-secret'}, 400), ([], 200),
                             ({'access_token': 'synthetic-access', 'token_type': 'Bearer', 'scope': 'openid'}, 200),
                             (self.token(), 302)):
            self.http_post.return_value = response(data, status)
            with self.assertRaises(google.CustomerGoogleError) as caught:
                google.get_access_token(self.actor)
            self.assertEqual(str(caught.exception), google.ERROR)
            self.assertIsNone(caught.exception.__cause__)
        self.http_post.side_effect = requests.Timeout('synthetic-secret')
        with self.assertRaises(google.CustomerGoogleError) as caught:
            google.get_access_token(self.actor)
        self.assertNotIn('synthetic-secret', repr(caught.exception))

    def test_callback_json_parse_error_is_masked_and_consumes_state(self):
        req, _, _ = self.pending()
        self.http_post.return_value = response({})
        self.http_post.return_value.json.side_effect = ValueError('synthetic-secret')
        with self.assertRaises(google.CustomerGoogleError) as caught:
            google.handle_callback(req)
        self.assertEqual(str(caught.exception), google.ERROR)
        self.assertNotIn(google.SESSION_KEY, req.session)
        self.assertFalse(Connection.objects.exists())

    def test_callback_nonce_verification_failure_never_persists(self):
        req, _, _ = self.pending()
        self.http_post.return_value = response(self.token())
        with patch.object(google, '_id_token', side_effect=ValueError('synthetic-secret')):
            with self.assertRaises(google.CustomerGoogleError) as caught:
                google.handle_callback(req)
        self.assertEqual(str(caught.exception), google.ERROR)
        self.assertFalse(Connection.objects.exists())
        self.http_get.assert_not_called()

    def test_connection_client_change_and_actor_email_change_fail_closed(self):
        conn = self.connect()
        self.client_config.client_id = 'different-client'
        with self.assertRaises(google.CustomerGoogleError):
            google.get_connection(self.actor)
        self.client_config.client_id = conn.client_id
        get_user_model().objects.filter(pk=self.actor.pk).update(email='different@example.test')
        with self.assertRaises(google.CustomerGoogleError):
            google.get_connection(self.actor)

    def test_disconnect_only_current_actor_environment(self):
        self.connect()
        req = self.request()
        req.session[google.SESSION_KEY] = {'synthetic': True}
        google.disconnect(req)
        self.assertFalse(Connection.objects.exists())
        self.assertNotIn(google.SESSION_KEY, req.session)
        self.http_post.assert_not_called()

    def sheet_responses(self, rows, headers=None):
        self.http_get.side_effect = [
            response({'sheets': [{'properties': {'sheetId': google.SHEET_ID, 'title': "合成'回答"}}]}),
            response({'valueRanges': [{'values': [google.HEADERS if headers is None else headers]}, {'values': rows}]}),
        ]

    def test_sheets_mapping_f_column_escaping_padding_blank_and_japanese_date(self):
        conn = self.connect()
        self.mock_draft_token(conn)
        self.sheet_responses([
            ['2026/11/19 12:34:56', 'respondent@example.test', '合成会社', '', '合成氏名', 'invitee@example.test'],
            [], ['2026/11/19 12:35:00', 'respondent@example.test', '合成会社'],
        ])
        result = google.list_applications(self.actor, 2)
        self.assertTrue(result['ok'])
        self.assertIsNone(result['next_row'])
        first, padded = result['applications']
        self.assertEqual(first['submitted_at'], '2026/11/19 12:34:56')
        self.assertEqual(first['applicant_email'], 'respondent@example.test')
        self.assertEqual(first['email'], 'invitee@example.test')
        self.assertEqual(first['source_key'], f'{google.SPREADSHEET_ID}:{google.SHEET_ID}:2')
        self.assertTrue(padded['source_key'].endswith(':4'))
        self.assertEqual(padded['email'], '')
        params = self.http_get.call_args.kwargs['params']
        self.assertEqual(params['ranges'], ["'合成''回答'!A1:F1", "'合成''回答'!A2:F201"])
        self.assertEqual(params['valueRenderOption'], 'FORMATTED_VALUE')
        self.assert_http_options()

    def test_sheets_headers_exact_and_gid_required(self):
        self.mock_draft_token(self.connect())
        self.sheet_responses([], headers=list(reversed(google.HEADERS)))
        with self.assertRaises(google.CustomerGoogleError):
            google.list_applications(self.actor, 2)
        self.http_get.side_effect = None
        self.http_get.return_value = response({'sheets': [{'properties': {'sheetId': 0, 'title': 'wrong'}}]})
        with self.assertRaises(google.CustomerGoogleError):
            google.list_applications(self.actor, 2)

    def test_sheets_paging_counts_raw_rows_and_maximum_200(self):
        self.mock_draft_token(self.connect())
        self.sheet_responses([[] for _ in range(200)])
        result = google.list_applications(self.actor, 202)
        self.assertEqual(result, {'ok': True, 'applications': [], 'next_row': 402})
        self.sheet_responses([[] for _ in range(201)])
        with self.assertRaises(google.CustomerGoogleError):
            google.list_applications(self.actor, 2)
        for start in (True, '2', 1, -1, 2.0):
            with self.subTest(start=start), self.assertRaises(google.CustomerGoogleError):
                google.list_applications(self.actor, start)

    def test_draft_single_post_durable_pin_and_mime_to_f_only(self):
        conn = self.connect()
        self.mock_draft_token(conn)
        app = self.application()
        self.http_get.return_value = response({'drafts': []})

        def create(url, **kwargs):
            self.assertEqual(url, google.GMAIL_URL)
            saved = CustomerApplication.objects.get(pk=app.pk)
            self.assertTrue(saved.draft_remote_attempted)
            self.assertFalse(google.db_connection.in_atomic_block)
            self.assertEqual(saved.draft_google_sub, conn.google_sub)
            raw = base64.urlsafe_b64decode(kwargs['json']['message']['raw'])
            message = BytesParser(policy=policy.default).parsebytes(raw)
            self.assertEqual(str(message['To']), app.email)
            self.assertIsNone(message['Cc'])
            self.assertIsNone(message['Bcc'])
            self.assertEqual(str(message['Message-ID']), saved.draft_message_id)
            self.assertTrue(message.get_content().rstrip().endswith(f'Nexus申請番号: {app.pk}'))
            self.assertNotIn(app.applicant_email, raw.decode())
            return response({'id': 'synthetic-draft'})

        self.http_post.side_effect = create
        self.assertEqual(self.draft(app), {'ok': True, 'draft_id': 'synthetic-draft'})
        self.assertEqual(self.http_post.call_count, 1)
        self.assert_http_options()

    def test_draft_timeout_retry_never_creates_duplicate(self):
        self.mock_draft_token(self.connect())
        app = self.application()
        self.http_get.return_value = response({'drafts': []})
        self.http_post.side_effect = requests.Timeout('synthetic-secret')
        with self.assertRaises(google.CustomerGoogleError) as caught:
            self.draft(app)
        self.assertEqual(str(caught.exception), google.SAFE_ERROR)
        app.refresh_from_db()
        original_message_id = app.draft_message_id
        self.assertTrue(app.draft_remote_attempted)
        with self.assertRaises(google.CustomerGoogleError):
            self.draft(app)
        self.assertEqual(self.http_post.call_count, 1)
        app.refresh_from_db()
        self.assertEqual(app.draft_message_id, original_message_id)

    def attempted(self):
        return self.application(draft_google_sub='synthetic-sub',
                                draft_mailbox_email='reviewer@example.test',
                                draft_remote_attempted=True,
                                draft_message_id=f'<{uuid.uuid4()}@nexus.invalid>')

    def test_draft_recovery_scans_all_pages_full_message_id(self):
        self.mock_draft_token(self.connect())
        app = self.attempted()
        self.http_get.side_effect = [
            response({'drafts': [{'id': 'other'}], 'nextPageToken': 'page2'}),
            response({'id': 'other', 'message': {'payload': {'headers': [{'name': 'Message-ID', 'value': '<other@nexus.invalid>'}]}}}),
            response({'drafts': [{'id': 'recovered'}]}),
            response({'id': 'recovered', 'message': {'payload': {'headers': [{'name': 'Message-ID', 'value': app.draft_message_id}]}}}),
        ]
        self.assertEqual(self.draft(app), {'ok': True, 'draft_id': 'recovered'})
        self.http_post.assert_not_called()
        self.assertEqual(self.http_get.call_args_list[2].kwargs['params']['pageToken'], 'page2')
        self.assertEqual(self.http_get.call_args_list[3].kwargs['params'], {'format': 'full'})

    def test_attempted_but_deleted_or_sent_draft_never_reposted(self):
        self.mock_draft_token(self.connect())
        app = self.attempted()
        self.http_get.return_value = response({'drafts': []})
        with self.assertRaises(google.CustomerGoogleError):
            self.draft(app)
        self.http_post.assert_not_called()

    def test_scan_failure_and_token_failure_leave_attempt_false(self):
        conn = self.connect()
        app = self.application()
        with patch.object(google, 'get_access_token', side_effect=google.CustomerGoogleError):
            with self.assertRaises(google.CustomerGoogleError):
                self.draft(app)
        self.mock_draft_token(conn)
        self.http_get.side_effect = requests.Timeout('synthetic-secret')
        with self.assertRaises(google.CustomerGoogleError):
            self.draft(app)
        app.refresh_from_db()
        self.assertFalse(app.draft_remote_attempted)
        self.http_post.assert_not_called()

    def test_scan_cap_is_ten_pages_and_never_posts_if_incomplete(self):
        self.mock_draft_token(self.connect())
        app = self.application()
        self.http_get.side_effect = [response({'drafts': [], 'nextPageToken': f'p{i}'}) for i in range(10)]
        with self.assertRaises(google.CustomerGoogleError):
            self.draft(app)
        self.assertEqual(self.http_get.call_count, 10)
        self.http_post.assert_not_called()
        app.refresh_from_db()
        self.assertFalse(app.draft_remote_attempted)

    def test_existing_id_still_validates_connection_and_owner(self):
        conn = self.connect()
        app = self.application(draft_id='existing-draft')
        with patch.object(google, 'get_access_token', return_value=(conn, 'synthetic-access')) as refresh:
            self.assertEqual(self.draft(app)['draft_id'], 'existing-draft')
            refresh.assert_called_once()
        with patch.object(google, 'get_access_token', side_effect=google.CustomerGoogleError):
            with self.assertRaises(google.CustomerGoogleError):
                self.draft(app)
        self.http_get.assert_not_called()
        self.http_post.assert_not_called()

    def test_other_actor_and_changed_mailbox_are_rejected(self):
        conn = self.connect()
        self.mock_draft_token(conn)
        app = self.attempted()
        for change in ({'draft_google_sub': 'different-sub'},
                       {'draft_mailbox_email': 'different@example.test'},
                       {'reviewed_by': None}):
            CustomerApplication.objects.filter(pk=app.pk).update(
                draft_google_sub='synthetic-sub', draft_mailbox_email='reviewer@example.test',
                reviewed_by=self.actor)
            CustomerApplication.objects.filter(pk=app.pk).update(**change)
            with self.assertRaises(google.CustomerGoogleError):
                self.draft(app)
        self.http_get.assert_not_called()
        self.http_post.assert_not_called()

    def test_different_reviewer_rejected_before_refresh(self):
        other = get_user_model().objects.create_user(
            username='synthetic-other', email='other@example.test', is_staff=True)
        app = self.application(reviewed_by=other)
        with patch.object(google, 'get_access_token') as refresh:
            with self.assertRaises(google.CustomerGoogleError):
                self.draft(app)
            refresh.assert_not_called()
        self.http_post.assert_not_called()

    def test_concurrent_attempt_claimed_while_scanning_cannot_post(self):
        self.mock_draft_token(self.connect())
        app = self.application()

        def claim(*args, **kwargs):
            CustomerApplication.objects.filter(pk=app.pk).update(
                draft_google_sub='synthetic-sub', draft_mailbox_email='reviewer@example.test',
                draft_message_id=f'<{uuid.uuid4()}@nexus.invalid>', draft_remote_attempted=True)
            return response({'drafts': []})

        self.http_get.side_effect = claim
        with self.assertRaises(google.CustomerGoogleError):
            self.draft(app)
        self.http_post.assert_not_called()

    def test_duplicate_message_ids_and_scan_redirect_fail_closed(self):
        self.mock_draft_token(self.connect())
        app = self.attempted()
        full = lambda identifier: response({
            'id': identifier, 'message': {'payload': {'headers': [
                {'name': 'Message-ID', 'value': app.draft_message_id}]}}})
        self.http_get.side_effect = [
            response({'drafts': [{'id': 'duplicate1'}, {'id': 'duplicate2'}]}),
            full('duplicate1'), full('duplicate2'),
        ]
        with self.assertRaises(google.CustomerGoogleError):
            self.draft(app)
        self.http_get.side_effect = None
        self.http_get.return_value = response({'drafts': []}, 302)
        with self.assertRaises(google.CustomerGoogleError):
            self.draft(app)
        self.http_post.assert_not_called()

    def test_invalid_create_response_marks_attempt_and_does_not_retry(self):
        self.mock_draft_token(self.connect())
        app = self.application()
        self.http_get.return_value = response({'drafts': []})
        self.http_post.return_value = response({'id': '../invalid'})
        with self.assertRaises(google.CustomerGoogleError):
            self.draft(app)
        app.refresh_from_db()
        self.assertTrue(app.draft_remote_attempted)
        with self.assertRaises(google.CustomerGoogleError):
            self.draft(app)
        self.assertEqual(self.http_post.call_count, 1)

    def test_body_marker_and_header_injection_rejected_before_attempt(self):
        self.mock_draft_token(self.connect())
        app = self.application()
        for subject, body in [('招待', 'marker missing'),
                              ('招待\r\nBcc: other@example.test', f'Nexus申請番号: {app.pk}')]:
            with self.assertRaises(google.CustomerGoogleError):
                google.create_draft(self.actor, app, subject, body)
        self.http_get.assert_not_called()
        self.http_post.assert_not_called()
        app.refresh_from_db()
        self.assertFalse(app.draft_remote_attempted)