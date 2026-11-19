"""Offline GAS protocol tests; use config.settings_ci (temporary test DB only)."""

import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.utils import timezone

from . import customer_application_gas as gas
from . import customer_applications as service
from .customer_application_gas_models import CustomerApplicationGASJob as Job
from .customer_application_gas_models import CustomerApplicationGASNonce as Nonce
from .customer_application_models import CustomerApplication, CustomerApplicationImportState


@override_settings(
    CUSTOMER_APPLICATION_BACKEND='gas', CUSTOMER_APPLICATION_GAS_SECRET='s' * 48,
    CUSTOMER_APPLICATION_GAS_MAILBOX='reviewer@company.example',
    GOOGLE_WORKSPACE_DOMAIN='company.example',
    CUSTOMER_APPLICATION_PUBLIC_URL='https://nexus.example.com',
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class GasTests(TransactionTestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            'gas-reviewer', email='reviewer@company.example', is_staff=True,
        )
        self.enterContext(override_settings(CUSTOMER_APPLICATION_GAS_ACTOR_ID=str(self.actor.pk)))
        self.enterContext(patch.object(gas, 'current_chat_environment', return_value='development'))
        self.factory = RequestFactory()
        self.builder = self.enterContext(patch.object(
            service, 'build_invitation_payload', create=True,
            side_effect=lambda app: {'to': app.email, 'subject': 'Invitation', 'body': 'Existing token link'},
        ))

    def request(self, action='ping', payload=None, *, timestamp=None, nonce=None, path=None):
        body = json.dumps({} if payload is None else payload).encode()
        timestamp = str(int(time.time()) if timestamp is None else timestamp)
        nonce = nonce or uuid.uuid4().hex
        path = path or gas.PATHS[action]
        canonical = '\n'.join([
            'nexus-gas-v1', 'development', 'POST', path, timestamp, nonce,
            hashlib.sha256(body).hexdigest(),
        ])
        signature = hmac.new(b's' * 48, canonical.encode(), hashlib.sha256).hexdigest()
        return self.factory.post(
            path, data=body, content_type='application/json',
            HTTP_X_NEXUS_TIMESTAMP=timestamp, HTTP_X_NEXUS_NONCE=nonce,
            HTTP_X_NEXUS_SIGNATURE=signature,
        )

    def row(self, number=2):
        return {
            'source_key': f'{service.SPREADSHEET_ID}:{service.SHEET_ID}:{number}',
            'submitted_at': '2026-11-19T01:02:03Z',
            'applicant_email': 'respondent@example.com', 'email': f'invite{number}@example.com',
            'company_name': 'Company', 'department': '', 'display_name': 'Applicant',
        }

    def page(self, start=2, end=3, rows=None):
        return {
            'start_row': start, 'next_row': end,
            'spreadsheet_id': service.SPREADSHEET_ID, 'sheet_id': service.SHEET_ID,
            'headers': list(gas.HEADERS), 'applications': [self.row(start)] if rows is None else rows,
        }

    def approved(self, number=2):
        user = get_user_model().objects.create_user(
            f'invite{number}@example.com', email=f'invite{number}@example.com', password='not-shared',
        )
        values = self.row(number)
        values['submitted_at'] = timezone.now()
        return CustomerApplication.objects.create(
            **values, status='approved', created_user=user, reviewed_by=self.actor,
            reviewed_at=timezone.now(), invitation_token=default_token_generator.make_token(user),
        )

    def claim(self, **payload):
        return gas.dispatch('claim', payload, self.actor)['job']

    def ack(self, job, status='created', draft_id='draft_safe'):
        return gas.dispatch('ack', {
            'application_id': job['application_id'], 'attempt_id': job['attempt_id'],
            'status': status, 'draft_id': draft_id,
        }, self.actor)

    def test_authorize_ping_and_secret_not_in_configuration(self):
        self.assertEqual(gas.authorize(self.request()).pk, self.actor.pk)
        self.assertEqual(Nonce.objects.count(), 1)
        self.assertNotIn('secret', gas.gas_configuration())
        result = gas.dispatch('ping', {}, self.actor)
        self.assertEqual(result, {
            'ok': True, 'service': 'nexus-customer-application-gas-v1',
            'environment': 'development', 'next_row': 2, 'mailbox': self.actor.email,
        })

    def test_unsigned_body_writes_no_nonce(self):
        request = self.factory.post(gas.PATHS['import'], data=b'{}', content_type='application/json')
        with self.assertRaises(gas.GasError):
            gas.authorize(request)
        self.assertFalse(Nonce.objects.exists())

    def test_signature_tamper_and_wrong_environment(self):
        request = self.request()
        request._body = b'{"tampered":true}'
        with self.assertRaises(gas.GasError):
            gas.authorize(request)
        with patch.object(gas, 'current_chat_environment', return_value='production'):
            with self.assertRaises(gas.GasError):
                gas.authorize(self.request())
        self.assertFalse(Nonce.objects.exists())

    def test_replay_and_timestamp_windows(self):
        request = self.request()
        gas.authorize(request)
        with self.assertRaises(gas.GasError) as caught:
            gas.authorize(request)
        self.assertEqual(caught.exception.code, 'replay')
        for timestamp in (int(time.time()) - 301, int(time.time()) + 301, '-1', '1e10'):
            with self.subTest(timestamp=timestamp), self.assertRaises(gas.GasError):
                gas.authorize(self.request(timestamp=timestamp))
        self.assertEqual(Nonce.objects.count(), 1)

    def test_nonce_survives_business_rollback(self):
        request = self.request()
        gas.authorize(request)
        with self.assertRaises(gas.GasError):
            with transaction.atomic():
                gas.dispatch('import', {}, self.actor)
        self.assertEqual(Nonce.objects.count(), 1)
        with self.assertRaises(gas.GasError):
            gas.authorize(request)

    def test_authorize_requires_independent_transaction(self):
        with transaction.atomic(), self.assertRaises(gas.GasError) as caught:
            gas.authorize(self.request())
        self.assertEqual(caught.exception.code, 'transaction_not_supported')
        self.assertFalse(Nonce.objects.exists())

    def test_protocol_rejects_bad_headers_path_method_query_and_oversize(self):
        for header, value in (
            ('HTTP_X_NEXUS_NONCE', 'A' * 32), ('HTTP_X_NEXUS_SIGNATURE', 'A' * 64),
            ('HTTP_X_NEXUS_TIMESTAMP', '+123'), ('CONTENT_LENGTH', str(gas.MAX_BODY + 1)),
            ('QUERY_STRING', 'action=ack'), ('REQUEST_METHOD', 'GET'),
        ):
            request = self.request()
            request.META[header] = value
            if header == 'REQUEST_METHOD':
                request.method = value
            with self.subTest(header=header), self.assertRaises(gas.GasError):
                gas.authorize(request)
        with self.assertRaises(gas.GasError):
            gas.authorize(self.request(path='/arbitrary/'))
        request = self.request()
        request._body = b' ' * (gas.MAX_BODY + 1)
        with self.assertRaises(gas.GasError):
            gas.authorize(request)
        self.assertFalse(Nonce.objects.exists())

    def test_configuration_rejects_disabled_short_secret_bad_actor_and_mailbox(self):
        for values in (
            {'CUSTOMER_APPLICATION_BACKEND': 'oauth'},
            {'CUSTOMER_APPLICATION_GAS_SECRET': 'short'},
            {'CUSTOMER_APPLICATION_GAS_ACTOR_ID': True},
            {'CUSTOMER_APPLICATION_GAS_ACTOR_ID': '999999999'},
            {'CUSTOMER_APPLICATION_GAS_MAILBOX': 'Reviewer@company.example'},
            {'GOOGLE_WORKSPACE_DOMAIN': 'other.example'},
        ):
            with self.subTest(values=list(values)), override_settings(**values):
                with self.assertRaises(gas.GasError):
                    gas.gas_configuration()
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        with self.assertRaises(gas.GasError):
            gas.gas_configuration()

    def test_import_idempotent_old_page_and_cursor_no_users_created(self):
        before = get_user_model().objects.count()
        page = self.page()
        result = gas.dispatch('import', page, self.actor)
        self.assertEqual(result, {'ok': True, 'next_row': 3, 'imported': 1})
        self.assertEqual(gas.dispatch('import', page, self.actor), {
            'ok': True, 'next_row': 3, 'imported': 0,
        })
        self.assertEqual(CustomerApplication.objects.count(), 1)
        self.assertEqual(get_user_model().objects.count(), before)
        self.assertEqual(gas.dispatch('import', self.page(3, 3, []), self.actor), {
            'ok': True, 'next_row': 3, 'imported': 0,
        })

    def test_invalid_source_headers_duplicate_and_future_pages_have_no_writes(self):
        variants = [
            {'spreadsheet_id': 'other'}, {'sheet_id': 1583871824}, {'headers': ['wrong']},
            {'start_row': True}, {'next_row': 203}, {'next_row': 2},
            {'applications': [self.row(), self.row()]},
            {'applications': [dict(self.row(), email='bad\n@example.com')]},
            {'start_row': 3, 'next_row': 4, 'applications': [self.row(3)]},
        ]
        for changes in variants:
            page = self.page()
            page.update(changes)
            with self.subTest(changes=list(changes)), self.assertRaises(service.ApplicationError):
                gas.dispatch('import', page, self.actor)
            self.assertFalse(CustomerApplication.objects.exists())
            self.assertFalse(CustomerApplicationImportState.objects.exists())
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_cursor_cas_failure_rolls_back_page(self):
        CustomerApplicationImportState.objects.create(source_key=gas._source_key())
        with patch('django.db.models.query.QuerySet.update', return_value=0):
            with self.assertRaises(gas.GasError) as caught:
                gas.dispatch('import', self.page(), self.actor)
        self.assertEqual(caught.exception.code, 'cursor_conflict')
        self.assertEqual(gas._cursor(), 2)
        self.assertFalse(CustomerApplication.objects.exists())

    def test_queue_is_idempotent_pins_owner_and_does_not_regenerate_token(self):
        item = self.approved()
        token = item.invitation_token
        with patch.object(default_token_generator, 'make_token') as make:
            gas.queue_draft(item.pk, self.actor)
            gas.queue_draft(item.pk, self.actor)
            make.assert_not_called()
        job = Job.objects.get()
        self.assertFalse(job.creation_authorized)
        self.assertEqual(job.mailbox, self.actor.email)
        self.assertEqual(job.message_id, f'<nexus-application-{item.pk}@customer-applications.invalid>')
        item.refresh_from_db()
        self.assertEqual(item.invitation_token, token)
        self.assertEqual(item.draft_state, 'pending')
        self.assertFalse(item.draft_remote_attempted)
        job.mailbox = 'other@company.example'
        with self.assertRaises(ValidationError):
            job.save()

    def test_queue_rejects_other_actor_and_all_oauth_markers(self):
        item = self.approved()
        other = get_user_model().objects.create_user('other', email='other@company.example', is_staff=True)
        with self.assertRaises(gas.GasError):
            gas.queue_draft(item.pk, other)
        for field, value in (
            ('draft_google_sub', 'google-sub'), ('draft_remote_attempted', True),
            ('draft_id', 'existing'), ('draft_message_id', '<existing>'),
            ('draft_mailbox_email', self.actor.email), ('draft_attempt_id', uuid.uuid4()),
            ('draft_state', 'creating'),
        ):
            old = getattr(item, field)
            CustomerApplication.objects.filter(pk=item.pk).update(**{field: value})
            with self.subTest(field=field), self.assertRaises(gas.GasError):
                gas.queue_draft(item.pk, self.actor)
            CustomerApplication.objects.filter(pk=item.pk).update(**{field: old})
        self.assertFalse(Job.objects.exists())

    def test_queue_rejects_invalid_token_origin_or_inactive_user(self):
        item = self.approved()
        for values in ({'invitation_token': ''}, {'invitation_token': 'expired'}):
            CustomerApplication.objects.filter(pk=item.pk).update(**values)
            with self.assertRaises(gas.GasError):
                gas.queue_draft(item.pk, self.actor)
        CustomerApplication.objects.filter(pk=item.pk).update(invitation_token=item.invitation_token)
        with override_settings(CUSTOMER_APPLICATION_PUBLIC_URL='http://unsafe.example'):
            with self.assertRaises(service.ApplicationError):
                gas.queue_draft(item.pk, self.actor)
        item.created_user.is_active = False
        item.created_user.save(update_fields=['is_active'])
        with self.assertRaises(gas.GasError):
            gas.queue_draft(item.pk, self.actor)
        self.assertFalse(Job.objects.exists())

    def test_first_claim_authorizes_once_then_recovery_has_no_body(self):
        item = self.approved()
        gas.queue_draft(item.pk, self.actor)
        first, second = self.claim(), self.claim()
        self.assertTrue(first['allow_create'])
        self.assertFalse(second['allow_create'])
        self.assertEqual(first['attempt_id'], second['attempt_id'])
        self.assertNotIn('body', second)
        self.builder.assert_called_once()
        item.refresh_from_db()
        self.assertTrue(item.draft_remote_attempted)
        self.assertEqual(item.draft_state, 'creating')
        self.assertEqual(str(item.draft_attempt_id), first['attempt_id'])
        self.assertEqual(item.draft_mailbox_email, self.actor.email)
        self.assertEqual(item.draft_google_sub, '')
        self.assertTrue(Job.objects.get().creation_authorized)

    def test_expired_before_first_claim_is_not_authorized_and_does_not_block(self):
        expired, active = self.approved(), self.approved(3)
        gas.queue_draft(expired.pk, self.actor)
        gas.queue_draft(active.pk, self.actor)
        expired.created_user.set_password('invalidate')
        expired.created_user.save(update_fields=['password'])
        claimed = self.claim()
        self.assertEqual(claimed['application_id'], str(active.pk))
        self.assertFalse(Job.objects.get(application=expired).creation_authorized)

    def test_recovery_and_success_ack_allow_expired_token(self):
        item = self.approved()
        gas.queue_draft(item.pk, self.actor)
        first = self.claim()
        with patch.object(default_token_generator, '_now', return_value=(
            default_token_generator._now() + timedelta(days=10)
        )):
            second = self.claim()
            self.assertFalse(second['allow_create'])
            self.assertEqual(self.ack(first)['status'], 'created')
        self.assertIsNone(self.claim())

    def test_ack_stale_attempt_cross_actor_and_reconfigured_mailbox(self):
        item = self.approved()
        gas.queue_draft(item.pk, self.actor)
        claimed = self.claim()
        stale = dict(claimed, attempt_id=str(uuid.uuid4()))
        with self.assertRaises(gas.GasError):
            self.ack(stale)
        other = get_user_model().objects.create_user('other', email='other@company.example', is_staff=True)
        with self.assertRaises(gas.GasError):
            gas.dispatch('ack', {'application_id': str(item.pk), 'attempt_id': claimed['attempt_id'],
                                 'status': 'created', 'draft_id': 'safe'}, other)
        with override_settings(CUSTOMER_APPLICATION_GAS_ACTOR_ID=other.pk,
                               CUSTOMER_APPLICATION_GAS_MAILBOX=other.email):
            with self.assertRaises(gas.GasError):
                gas.queue_draft(item.pk, other)
        self.assertFalse(CustomerApplication.objects.get(pk=item.pk).draft_id)

    def test_duplicate_ack_cannot_change_success(self):
        item = self.approved()
        gas.queue_draft(item.pk, self.actor)
        claimed = self.claim()
        self.assertEqual(self.ack(claimed), self.ack(claimed))
        self.assertEqual(self.ack(claimed, 'uncertain', '')['status'], 'created')
        with self.assertRaises(gas.GasError):
            self.ack(claimed, draft_id='different')
        item.refresh_from_db()
        self.assertEqual(item.draft_id, 'draft_safe')
        self.assertEqual(Job.objects.get().state, 'created')

    def test_uncertain_ack_never_reauthorizes_and_queue_remains_idempotent(self):
        item = self.approved()
        gas.queue_draft(item.pk, self.actor)
        claimed = self.claim()
        self.ack(claimed, 'uncertain', '')
        self.ack(claimed, 'uncertain', '')
        gas.queue_draft(item.pk, self.actor)
        self.assertFalse(self.claim()['allow_create'])
        self.ack(claimed)
        self.assertIsNone(self.claim())

    def test_claim_fairness_excludes_and_single_batch(self):
        first, second = self.approved(), self.approved(3)
        gas.queue_draft(first.pk, self.actor)
        gas.queue_draft(second.pk, self.actor)
        one, two = self.claim(), self.claim()
        self.assertNotEqual(one['application_id'], two['application_id'])
        self.assertIsNone(self.claim(exclude_ids=[str(first.pk), str(second.pk)]))
        self.assertEqual(self.claim(application_id=str(first.pk))['application_id'], str(first.pk))
        for excluded in (['not-a-uuid'], [str(first.pk)] * 21, 'wrong'):
            with self.assertRaises(gas.GasError):
                self.claim(exclude_ids=excluded)

    def test_ack_before_claim_or_invalid_draft_id_is_rejected(self):
        item = self.approved()
        gas.queue_draft(item.pk, self.actor)
        job = Job.objects.get()
        payload = {'application_id': str(item.pk), 'attempt_id': str(job.attempt_id),
                   'status': 'created', 'draft_id': 'draft_safe'}
        with self.assertRaises(gas.GasError):
            gas.dispatch('ack', payload, self.actor)
        self.claim()
        for draft_id in ('bad\nvalue', 'a' * 256, '', None):
            with self.subTest(draft_id=draft_id), self.assertRaises(gas.GasError):
                gas.dispatch('ack', dict(payload, draft_id=draft_id), self.actor)

    def test_dispatch_invalid_payload_action_and_claim_oauth_injection(self):
        for action, payload in (('other', {}), ('ping', []), (None, {})):
            with self.assertRaises(gas.GasError):
                gas.dispatch(action, payload, self.actor)
        item = self.approved()
        gas.queue_draft(item.pk, self.actor)
        CustomerApplication.objects.filter(pk=item.pk).update(draft_google_sub='other-backend')
        self.assertIsNone(self.claim())
        self.assertFalse(Job.objects.get().creation_authorized)