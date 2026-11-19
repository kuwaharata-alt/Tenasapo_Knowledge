"""GAS HTTP/browser integration tests using the real config.settings_ci middleware.

Run with config.settings_ci: no deployment settings, credentials or network.
TransactionTestCase is intentional: HMAC nonces must commit outside requests'
business transactions, including when ATOMIC_REQUESTS is enabled.
"""

import hashlib
import hmac
import json
import time
import uuid
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.db import connection
from django.test import Client, TransactionTestCase, override_settings
from django.urls import resolve, reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from . import customer_application_gas as gas
from . import customer_applications as service
from .customer_application_gas_models import CustomerApplicationGASJob as Job
from .customer_application_gas_models import CustomerApplicationGASNonce as Nonce
from .customer_application_models import CustomerApplication as Application
from .customer_application_models import CustomerApplicationImportState as ImportState
from .models import Customer, UserProfile


@override_settings(
    DEBUG=False,
    CUSTOMER_APPLICATION_BACKEND='gas',
    GOOGLE_WORKSPACE_DOMAIN='example.test',
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class GasViewTests(TransactionTestCase):
    secret = 'offline-gas-http-tests-only-' + 's' * 40
    origin = 'https://nexus.example.test:8443'
    spreadsheet_id = '1JaWW0c_CE9W5_egLvoqrWcnwuIbb6QCERBRrQvAE5Pg'
    sheet_id = '1583871824'
    headers = ['タイムスタンプ', 'メールアドレス', '会社名', '部署名', '契約者氏名', '契約メールアドレス']

    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            'gas-http-operator', email='gas-operator@example.test',
            password='Offline-operator-password!42', is_staff=True,
        )
        self.enterContext(override_settings(
            CUSTOMER_APPLICATION_GAS_ACTOR_ID=str(self.actor.pk),
            CUSTOMER_APPLICATION_GAS_SECRET=self.secret,
            CUSTOMER_APPLICATION_GAS_MAILBOX=self.actor.email,
            CUSTOMER_APPLICATION_PUBLIC_URL=self.origin,
        ))
        # Leave CI's security, session, CSRF, login and consent middleware intact.
        self.client = Client(enforce_csrf_checks=True)
        self.browser = Client(enforce_csrf_checks=True)
        self.browser.force_login(self.actor)
        # GAS must not call OAuth/Sheets/Gmail APIs, even on browser approval.
        for owner, name in (
            (service, '_call_google'), (service.google, '_json'),
            (service.google, 'get_connection'), (service.google, 'create_draft'),
            (service.google, 'list_applications'),
        ):
            mock = self.enterContext(patch.object(owner, name))
            self.addCleanup(mock.assert_not_called)

    def api_url(self, action):
        name = 'status' if action == 'ping' else action
        return reverse('customer_application_gas_' + name)

    def browser_url(self, action='list', item=None):
        return reverse('customer_application_' + action,
                       kwargs={'pk': item.pk} if item is not None else None)

    def signed(self, action='ping', payload=None, *, body=None, nonce=None,
               timestamp=None, signing_method='POST', signing_path=None,
               environment='production'):
        """Sign exact transmitted bytes, independently of authorize()."""
        if body is None:
            body = json.dumps({} if payload is None else payload, ensure_ascii=False,
                              separators=(',', ':')).encode('utf-8')
        timestamp = str(int(time.time()) if timestamp is None else timestamp)
        nonce = nonce or uuid.uuid4().hex
        canonical = '\n'.join([
            'nexus-gas-v1', environment, signing_method,
            signing_path or self.api_url(action), timestamp, nonce,
            hashlib.sha256(body).hexdigest(),
        ])
        signature = hmac.new(self.secret.encode('utf-8'), canonical.encode('utf-8'),
                             hashlib.sha256).hexdigest()
        return body, {
            'HTTP_X_NEXUS_TIMESTAMP': timestamp, 'HTTP_X_NEXUS_NONCE': nonce,
            'HTTP_X_NEXUS_SIGNATURE': signature,
        }

    def send(self, action='ping', payload=None, **signing):
        body, headers = self.signed(action, payload, **signing)
        return self.client.post(self.api_url(action), data=body,
                                content_type='application/json', secure=True, **headers)

    def assert_json(self, response, status, expected):
        self.assertEqual(response.status_code, status, response.content[:1000])
        self.assertEqual(response.json(), expected)
        self.assertNotIn('Location', response)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def row(self, number=2):
        return {
            'source_key': f'{self.spreadsheet_id}:{self.sheet_id}:{number}',
            'submitted_at': '2026-11-19T01:02:03Z',
            'applicant_email': f'record-only-b{number}@example.net',
            'email': f'invitation-f{number}@example.net',
            'company_name': 'HTTP検証株式会社', 'department': '営業部',
            'display_name': '申請 太郎',
        }

    def page(self, number=2):
        return {
            'spreadsheet_id': self.spreadsheet_id, 'sheet_id': self.sheet_id,
            'headers': list(self.headers), 'start_row': number, 'next_row': number + 1,
            'applications': [self.row(number)],
        }

    def imported(self, number=2):
        self.assert_json(self.send('import', self.page(number)), 200,
                         {'ok': True, 'next_row': number + 1, 'imported': 1})
        return Application.objects.get(source_key=self.row(number)['source_key'])

    def browser_post(self, action, item=None, *, browser=None):
        browser = browser or self.browser
        # Obtain the real middleware cookie, then send it with a same-origin POST.
        response = browser.get(self.browser_url('detail', item) if item else self.browser_url(),
                               secure=True)
        self.assertEqual(response.status_code, 200)
        token = browser.cookies[settings.CSRF_COOKIE_NAME].value
        return browser.post(self.browser_url(action, item), secure=True,
                            HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN='https://testserver')

    def approved(self):
        item = self.imported()
        response = self.browser_post('approve', item)
        self.assertRedirects(response, self.browser_url('detail', item),
                             fetch_redirect_response=False)
        item.refresh_from_db()
        self.assertEqual(item.status, Application.Status.APPROVED)
        return item

    def test_real_ci_middleware_is_present(self):
        for middleware in (
            'django.middleware.security.SecurityMiddleware',
            'django.middleware.csrf.CsrfViewMiddleware',
            'tenasapo_knowledge.middleware.SafeSessionMiddleware',
            'tenasapo_knowledge.middleware.LoginRequiredExceptAssetsMiddleware',
        ):
            self.assertIn(middleware, settings.MIDDLEWARE)

    def test_unsigned_posts_are_401_not_login_redirects_and_do_not_consume_nonce(self):
        for action in ('ping', 'import', 'claim', 'ack'):
            with self.subTest(action=action):
                response = self.client.post(self.api_url(action), data=b'{}',
                                            content_type='application/json', secure=True)
                self.assert_json(response, 401, {'ok': False, 'error': 'unauthorized'})
        self.assertFalse(Nonce.objects.exists())
        self.assertFalse(Application.objects.exists())
        self.assertFalse(ImportState.objects.exists())
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_get_status_is_unauthorized_even_with_post_signature(self):
        for headers in ({}, self.signed()[1]):
            with self.subTest(signed=bool(headers)):
                response = self.client.get(self.api_url('ping'), secure=True, **headers)
                self.assert_json(response, 401, {'ok': False, 'error': 'unauthorized'})
        self.assertFalse(Nonce.objects.exists())

    def test_http_is_rejected_without_consuming_nonce(self):
        body, headers = self.signed()
        response = self.client.post(self.api_url('ping'), data=body,
                                    content_type='application/json', **headers)
        self.assert_json(response, 400, {'ok': False, 'error': 'https_required'})
        self.assertFalse(Nonce.objects.exists())

    def test_signed_ping_identifies_service_without_user_or_application_writes(self):
        self.assert_json(self.send(), 200, {
            'ok': True, 'service': 'nexus-customer-application-gas-v1',
            'environment': 'production', 'next_row': 2, 'mailbox': self.actor.email,
        })
        self.assertEqual(Nonce.objects.count(), 1)
        self.assertEqual(get_user_model().objects.count(), 1)
        for model in (Application, ImportState, Job, Customer, UserProfile):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_import_only_stages_fixed_source_pending_and_is_idempotent(self):
        item = self.imported()
        self.assertEqual(item.status, Application.Status.PENDING)
        self.assertIsNone(item.created_user_id)
        self.assertIsNone(item.reviewed_by_id)
        self.assertIsNone(item.reviewed_at)
        self.assertEqual(item.invitation_token, '')
        self.assertEqual(item.draft_id, '')
        self.assertFalse(item.draft_remote_attempted)
        for field in ('applicant_email', 'email', 'company_name', 'department', 'display_name'):
            self.assertEqual(getattr(item, field), self.row()[field])
        self.assertEqual(ImportState.objects.get().source_key,
                         f'{self.spreadsheet_id}:{self.sheet_id}')
        self.assertEqual(ImportState.objects.get().next_row, 3)
        self.assert_json(self.send('import', self.page()), 200,
                         {'ok': True, 'next_row': 3, 'imported': 0})
        self.assertEqual(Application.objects.count(), 1)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertFalse(Job.objects.exists())
        self.assertFalse(Customer.objects.exists())
        self.assertFalse(UserProfile.objects.exists())

    def test_import_wrong_source_or_headers_fails_without_partial_writes(self):
        for changes in ({'spreadsheet_id': 'other'}, {'sheet_id': 1583871824},
                        {'headers': ['wrong']}, {'applications': [self.row(), self.row()]}):
            with self.subTest(fields=list(changes)):
                self.assert_json(self.send('import', dict(self.page(), **changes)), 400,
                                 {'ok': False, 'error': 'invalid_page'})
                self.assertFalse(Application.objects.exists())
                self.assertFalse(ImportState.objects.exists())
        self.assertEqual(Nonce.objects.count(), 4)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_import_approve_claim_recovery_and_repeated_ack_full_flow(self):
        item = self.approved()
        user = item.created_user
        self.assertEqual(get_user_model().objects.count(), 2)
        self.assertEqual(user.username, item.email)
        self.assertEqual(user.email, self.row()['email'])
        self.assertTrue(user.is_active)
        self.assertTrue(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.groups.exists())
        self.assertFalse(user.user_permissions.exists())
        self.assertEqual(item.reviewed_by_id, self.actor.pk)
        self.assertTrue(default_token_generator.check_token(user, item.invitation_token))
        self.assertEqual(user.knowledge_profile.user_type, UserProfile.USER_TYPE_CUSTOMER)
        self.assertEqual(user.knowledge_profile.email_addresses, item.email)
        self.assertTrue(Customer.objects.get(name=item.company_name).users.filter(pk=user.pk).exists())
        queued = Job.objects.get(application=item)
        self.assertEqual(queued.actor_id, self.actor.pk)
        self.assertEqual(queued.mailbox, self.actor.email)
        self.assertEqual(queued.environment, 'production')
        self.assertFalse(queued.creation_authorized)
        self.assertFalse(item.draft_remote_attempted)
        self.assertEqual(item.draft_state, Application.DraftState.PENDING)

        response = self.send('claim')
        self.assertEqual(response.status_code, 200)
        first = response.json()['job']
        self.assertIs(first['allow_create'], True)
        self.assertEqual(first['application_id'], str(item.pk))
        self.assertEqual(first['to'], item.email)  # F, never the recorded B address.
        self.assertEqual(first['mailbox'], self.actor.email)
        self.assertEqual(first['message_id'], queued.message_id)
        self.assertEqual(first['attempt_id'], str(queued.attempt_id))
        self.assertEqual(set(first), {
            'application_id', 'attempt_id', 'mailbox', 'message_id',
            'allow_create', 'to', 'subject', 'body',
        })
        self.assertNotIn(item.applicant_email, json.dumps(first, ensure_ascii=False))
        invitation_path = reverse('password_reset_confirm', kwargs={
            'uidb64': urlsafe_base64_encode(force_bytes(user.pk)), 'token': item.invitation_token,
        })
        self.assertIn(self.origin + invitation_path, first['body'])
        recovery_response = self.send('claim')
        self.assertEqual(recovery_response.status_code, 200)
        recovery = recovery_response.json()['job']
        self.assertIs(recovery['allow_create'], False)
        self.assertEqual(recovery['attempt_id'], first['attempt_id'])
        self.assertEqual(recovery['to'], item.email)
        self.assertNotIn('body', recovery)
        self.assertNotIn('subject', recovery)

        ack = {'application_id': str(item.pk), 'attempt_id': first['attempt_id'],
               'status': 'created', 'draft_id': 'gas_draft_http_123'}
        expected = {'ok': True, 'application_id': str(item.pk), 'status': 'created'}
        self.assert_json(self.send('ack', ack), 200, expected)
        item.refresh_from_db()
        queued.refresh_from_db()
        completed_at = queued.completed_at
        token = item.invitation_token
        self.assertEqual(item.draft_id, ack['draft_id'])
        self.assertEqual(item.draft_state, Application.DraftState.CREATED)
        self.assertTrue(item.draft_remote_attempted)
        self.assertEqual(item.draft_mailbox_email, self.actor.email)
        self.assertEqual(item.draft_google_sub, '')
        self.assertEqual(queued.state, Job.State.CREATED)
        self.assertIsNotNone(completed_at)
        self.assert_json(self.send('ack', ack), 200, expected)
        self.assert_json(self.send('ack', dict(ack, status='uncertain', draft_id='')), 200, expected)
        self.assert_json(self.send('ack', dict(ack, draft_id='different')), 409,
                         {'ok': False, 'error': 'ack_conflict'})
        self.assert_json(self.send('claim'), 200, {'ok': True, 'job': None})
        # Repeated browser approval cannot create a user, job, token or draft again.
        self.assertEqual(self.browser_post('approve', item).status_code, 302)
        self.assertEqual(self.browser_post('retry_draft', item).status_code, 302)
        item.refresh_from_db()
        queued.refresh_from_db()
        self.assertEqual(item.invitation_token, token)
        self.assertEqual(queued.completed_at, completed_at)
        self.assertEqual(item.draft_id, ack['draft_id'])
        self.assertEqual(get_user_model().objects.count(), 2)
        self.assertEqual(Job.objects.count(), 1)

    def test_other_staff_approval_is_rejected_before_user_creation(self):
        item = self.imported()
        other = get_user_model().objects.create_user(
            'other-gas-staff', email='other@example.test', is_staff=True,
        )
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(other)
        with patch.object(get_user_model().objects, 'create_user') as create_user:
            response = self.browser_post('approve', item, browser=browser)
            create_user.assert_not_called()
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, Application.Status.PENDING)
        self.assertIsNone(item.created_user_id)
        self.assertIsNone(item.reviewed_by_id)
        self.assertEqual(get_user_model().objects.count(), 2)
        self.assertFalse(Job.objects.exists())
        self.assertFalse(UserProfile.objects.exists())
        self.assertFalse(Customer.objects.exists())

    def test_browser_approval_requires_csrf_even_when_staff_authenticated(self):
        item = self.imported()
        for headers in ({}, {'HTTP_X_CSRFTOKEN': 'a' * 32}):
            with self.subTest(headers=list(headers)):
                response = self.browser.post(self.browser_url('approve', item), secure=True,
                                             HTTP_ORIGIN='https://testserver', **headers)
                self.assertEqual(response.status_code, 403)
        self.browser.get(self.browser_url('detail', item), secure=True)
        response = self.browser.post(
            self.browser_url('approve', item), secure=True,
            HTTP_X_CSRFTOKEN=self.browser.cookies[settings.CSRF_COOKIE_NAME].value,
            HTTP_ORIGIN='https://attacker.example',
        )
        self.assertEqual(response.status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.status, Application.Status.PENDING)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertFalse(Job.objects.exists())

    def test_gas_list_has_no_oauth_controls_errors_or_manual_import(self):
        response = self.browser.get(self.browser_url(), secure=True)
        self.assertContains(response, 'GAS定期連携')
        self.assertContains(response, '準備済み（実疎通は未確認）')
        self.assertContains(response, self.actor.email)
        self.assertContains(response, '表示を更新')
        for text in ('Googleアクセスを許可', 'Googleアクセスを再許可',
                     service.google.ERROR, '回答を取込',
                     'action="' + self.browser_url('import') + '"'):
            self.assertNotContains(response, text)
        self.assertEqual(response.context['application_backend'], 'gas')
        self.assertIs(response.context['gas_ready'], True)
        self.assertIs(response.context['gas_is_operator'], True)
        self.assertEqual(response.context['gas_actor_id'], self.actor.pk)
        self.assertEqual(response.context['gas_mailbox'], self.actor.email)
        self.assertEqual(response.context['gas_error'], '')
        self.assertEqual(response.context['google_connection_error'], '')
        self.assertIsNone(response.context['google_connection'])
        self.assertIs(response.context['configured'], True)

    def test_browser_manual_import_does_not_invoke_oauth_or_write(self):
        response = self.browser_post('import')
        self.assertRedirects(response, self.browser_url(), fetch_redirect_response=False)
        self.assertFalse(Application.objects.exists())
        self.assertFalse(ImportState.objects.exists())
        self.assertFalse(Nonce.objects.exists())
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_detail_context_contains_operator_and_valid_public_configuration(self):
        item = self.imported()
        response = self.browser.get(self.browser_url('detail', item), secure=True)
        self.assertContains(response, 'GASによる非同期の下書き作成待ち')
        self.assertContains(response, item.applicant_email)
        self.assertContains(response, item.email)
        self.assertEqual(response.context['application'].pk, item.pk)
        self.assertEqual(response.context['application_backend'], 'gas')
        self.assertIs(response.context['gas_ready'], True)
        self.assertIs(response.context['gas_is_operator'], True)
        self.assertIs(response.context['is_draft_owner'], False)
        self.assertEqual(response.context['gas_actor_id'], self.actor.pk)
        self.assertEqual(response.context['gas_mailbox'], self.actor.email)
        # A valid origin must not silently disable the configured operator's button.
        self.assertIs(response.context.get('configured'), True)
        self.assertNotContains(response, '公開URLまたはGAS連携設定が未完了')
        self.assertNotContains(response, 'type="submit" disabled')

    def test_detail_nonoperator_context_and_approved_token_not_exposed(self):
        item = self.approved()
        response = self.browser.get(self.browser_url('detail', item), secure=True)
        self.assertIs(response.context['is_draft_owner'], True)
        self.assertContains(response, '下書き処理を再キュー・照合')
        self.assertNotContains(response, item.invitation_token)
        self.assertIn('invitation_token', response.context['application'].get_deferred_fields())
        other = get_user_model().objects.create_user('gas-observer', is_staff=True)
        self.browser.force_login(other)
        response = self.browser.get(self.browser_url('detail', item), secure=True)
        self.assertIs(response.context['gas_is_operator'], False)
        self.assertIs(response.context['is_draft_owner'], False)
        self.assertNotContains(response, 'action="' + self.browser_url('retry_draft', item) + '"')
        self.assertNotContains(response, item.invitation_token)

    def test_invalid_configuration_ui_shows_fixed_gas_error_without_secret(self):
        with override_settings(CUSTOMER_APPLICATION_GAS_SECRET='invalid-private-secret'):
            response = self.browser.get(self.browser_url(), secure=True)
        self.assertContains(response, 'サーバー設定：未完了')
        self.assertContains(response, 'GAS連携の環境変数・固定実行ユーザー・公開URLを確認してください。')
        self.assertIs(response.context['gas_ready'], False)
        self.assertNotContains(response, 'invalid-private-secret')
        self.assertNotContains(response, service.google.ERROR)
        self.assertNotContains(response, 'Googleアクセスを許可')

    def test_method_path_environment_and_body_signature_tampering_is_rejected(self):
        variants = [
            ('ping', self.signed(signing_method='GET')),
            ('claim', self.signed('ping')),
            ('ping', self.signed(environment='development')),
            ('ping', self.signed(body=b'{"signed":true}')),
        ]
        for index, (action, (body, headers)) in enumerate(variants):
            if index == 3:
                body = b'{"signed":false}'
            with self.subTest(variant=index):
                response = self.client.post(self.api_url(action), data=body,
                                            content_type='application/json', secure=True, **headers)
                self.assert_json(response, 401, {'ok': False, 'error': 'unauthorized'})
        self.assertFalse(Nonce.objects.exists())

    def test_signature_is_over_raw_bytes_not_reserialized_json(self):
        body = ' { "日本語" : "疎通確認" }\n'.encode('utf-8')
        self.assertEqual(self.send(body=body).status_code, 200)
        original, headers = self.signed(body=body)
        self.assertEqual(original, body)
        response = self.client.post(
            self.api_url('ping'), data=json.dumps({'日本語': '疎通確認'}).encode('utf-8'),
            content_type='application/json', secure=True, **headers,
        )
        self.assert_json(response, 401, {'ok': False, 'error': 'unauthorized'})
        self.assertEqual(Nonce.objects.count(), 1)

    def test_signed_malformed_json_consumes_nonce_and_replay_stays_rejected(self):
        for body in (b'{', b'\xff', b'{"same":1,"same":2}', b'{"n":NaN}', b'[]'):
            with self.subTest(body=body):
                nonce = uuid.uuid4().hex
                self.assert_json(self.send(body=body, nonce=nonce), 400,
                                 {'ok': False, 'error': 'invalid_request'})
                digest = hashlib.sha256(('production' + nonce).encode('utf-8')).hexdigest()
                self.assertTrue(Nonce.objects.filter(pk=digest).exists())
                self.assert_json(self.send(body=body, nonce=nonce), 409,
                                 {'ok': False, 'error': 'replay'})
        self.assertEqual(Nonce.objects.count(), 5)
        self.assertFalse(Application.objects.exists())

    def test_failures_have_fixed_json_and_never_echo_exception_or_request_secrets(self):
        private = f'{self.secret} record-only@example.net {self.origin}/private-token'
        for exception, status, code in (
            (ValueError(private), 400, 'invalid_request'),
            (service.ApplicationError(private), 400, 'invalid_request'),
            (RuntimeError(private), 503, 'service_unavailable'),
        ):
            with self.subTest(exception=type(exception).__name__):
                with patch.object(gas, 'dispatch', side_effect=exception):
                    response = self.send(payload={'private': private})
                self.assert_json(response, status, {'ok': False, 'error': code})
                self.assertNotIn(private.encode('utf-8'), response.content)
                self.assertNotIn(self.secret.encode('utf-8'), response.content)
        self.assertEqual(Nonce.objects.count(), 3)

    def test_non_atomic_http_endpoints_work_with_atomic_requests_enabled(self):
        original_dispatch = gas.dispatch

        def dispatch_outside_transaction(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            return original_dispatch(*args, **kwargs)

        # Modify the existing test connection only; do not replace DATABASES or open a real DB.
        with patch.dict(connection.settings_dict, {'ATOMIC_REQUESTS': True}):
            with patch.object(gas, 'dispatch', side_effect=dispatch_outside_transaction) as dispatch:
                for action, status in (('ping', 200), ('import', 400), ('claim', 200), ('ack', 400)):
                    with self.subTest(action=action):
                        self.assertIn('default', resolve(self.api_url(action)).func._non_atomic_requests)
                        nonce = uuid.uuid4().hex
                        self.assertEqual(self.send(action, nonce=nonce).status_code, status)
                        self.assert_json(self.send(action, nonce=nonce), 409,
                                         {'ok': False, 'error': 'replay'})
                self.assertEqual(dispatch.call_count, 4)
        self.assertEqual(Nonce.objects.count(), 4)
        self.assertFalse(Application.objects.exists())

    def test_gas_job_blocks_switch_to_oauth_via_service_without_remote_calls(self):
        item = self.approved()
        token = item.invitation_token
        with override_settings(CUSTOMER_APPLICATION_BACKEND='oauth'):
            with self.assertRaisesMessage(service.ApplicationError, 'GASで処理中'):
                service.create_invitation_draft(item.pk, self.actor)
        item.refresh_from_db()
        self.assertEqual(item.invitation_token, token)
        self.assertEqual(item.draft_state, Application.DraftState.PENDING)
        self.assertFalse(item.draft_remote_attempted)
        self.assertEqual(Job.objects.count(), 1)
        self.assertEqual(get_user_model().objects.count(), 2)

    def test_existing_oauth_draft_blocks_switch_to_gas_via_service(self):
        item = self.approved()
        Job.objects.filter(application=item).delete()
        Application.objects.filter(pk=item.pk).update(
            draft_google_sub='existing-oauth-sub', draft_mailbox_email=self.actor.email,
            draft_id='existing_oauth_draft', draft_state=Application.DraftState.CREATED,
        )
        with self.assertRaises(gas.GasError) as caught:
            service.create_invitation_draft(item.pk, self.actor)
        self.assertEqual(caught.exception.code, 'backend_conflict')
        self.assertFalse(Job.objects.exists())
        item.refresh_from_db()
        self.assertEqual(item.draft_id, 'existing_oauth_draft')
        self.assertEqual(item.draft_google_sub, 'existing-oauth-sub')
        self.assertEqual(get_user_model().objects.count(), 2)