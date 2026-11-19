"""Offline service/API contracts; run with config.settings_ci and memory DB only."""

import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from . import customer_applications as service
from .customer_application_models import CustomerApplication
from .models import Customer, UserProfile


def row(number=2, **changes):
    value = {
        'source_key': f'{service.SPREADSHEET_ID}:{service.SHEET_ID}:{number}',
        'submitted_at': '2026-11-19T01:02:03Z',
        'applicant_email': 'respondent@example.com',
        'email': f'invite{number}@example.com',
        'company_name': '申請会社', 'department': '開発部', 'display_name': '申請者',
    }
    value.update(changes)
    return value


def application(number=2, **changes):
    value = row(number)
    value['submitted_at'] = timezone.now()
    value.update(changes)
    return CustomerApplication.objects.create(**value)


def state_model():
    return apps.get_model('tenasapo_knowledge', 'CustomerApplicationImportState')


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   CUSTOMER_APPLICATION_PUBLIC_URL='https://nexus.example.com')
class ApprovalTests(TestCase):
    def setUp(self):
        self.reviewer = get_user_model().objects.create_user('reviewer', is_staff=True)
        self.google = self.enterContext(patch.object(service, '_call_google', return_value={
            'ok': True, 'draft_id': 'draft_123',
        }))

    def approve(self, **changes):
        return service.approve_application(application(**changes).pk, self.reviewer)

    def failed_draft(self):
        item = application()
        self.google.side_effect = TimeoutError('PRIVATE REMOTE DETAIL')
        with self.assertRaises(service.ApplicationError) as caught:
            service.approve_application(item.pk, self.reviewer)
        self.assertNotIn('PRIVATE', str(caught.exception))
        item.refresh_from_db()
        self.google.side_effect = None
        return item

    def test_customer_privileges_company_uid_and_f_recipient(self):
        existing = Customer.objects.create(name='申請会社')
        get_user_model().objects.create_user('csuser800001')
        occupied = get_user_model().objects.create_user('occupied')
        UserProfile.objects.create(user=occupied, uid='800002', company_name='別会社')
        result = self.approve()
        user = result.created_user
        profile = user.knowledge_profile
        self.assertEqual(user.username, 'invite2@example.com')
        self.assertEqual(user.email, result.email)
        self.assertTrue(user.is_active)
        self.assertTrue(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.groups.exists())
        self.assertFalse(user.user_permissions.exists())
        self.assertEqual(profile.user_type, 'customer')
        self.assertTrue(profile.exclude_from_analysis)
        self.assertTrue(profile.google_first_login_done)
        self.assertEqual(profile.uid, '800003')
        self.assertEqual(profile.email_addresses, result.email)
        self.assertEqual(profile.department, result.department)
        self.assertEqual(profile.display_name, result.display_name)
        self.assertEqual(list(user.accessible_customers.all()), [existing])
        self.assertEqual(result.reviewed_by, self.reviewer)
        self.assertIsNotNone(result.reviewed_at)
        self.assertTrue(default_token_generator.check_token(user, result.invitation_token))
        payload, actor = self.google.call_args.args
        self.assertIs(actor, self.reviewer)
        self.assertEqual(payload['to'], result.email)
        self.assertNotIn(result.applicant_email, json.dumps(payload))
        self.assertIn('https://nexus.example.com/', payload['body'])
        self.assertIn(result.invitation_token, payload['body'])
        self.assertTrue(payload['body'].endswith(f'Nexus申請番号: {result.pk}'))
        self.assertEqual(payload['application_id'], str(result.pk))

    def test_retry_expired_token_never_reissues(self):
        item = self.failed_draft()
        user = item.created_user
        user.set_password('changed-to-invalidate-existing-token')
        user.save(update_fields=['password'])
        old_token = item.invitation_token
        self.google.reset_mock()
        with patch.object(default_token_generator, 'make_token') as make:
            with self.assertRaises(service.ApplicationError):
                service.create_invitation_draft(item.pk, self.reviewer)
            make.assert_not_called()
        item.refresh_from_db()
        self.assertEqual(item.invitation_token, old_token)
        self.google.assert_not_called()

    def test_timeout_expired_token_never_reissues(self):
        item = self.failed_draft()
        with patch.object(default_token_generator, '_now', return_value=(
            default_token_generator._now() + timedelta(days=10)
        )), patch.object(default_token_generator, 'make_token') as make:
            with self.assertRaises(service.ApplicationError):
                service.create_invitation_draft(item.pk, self.reviewer)
            make.assert_not_called()

    def test_imported_privilege_fields_and_existing_b_address_are_not_used(self):
        get_user_model().objects.create_user('respondent@example.com', is_staff=True)
        self.google.return_value = {
            'applications': [row(is_staff=True, is_superuser=True, user_type='systena',
                                 uid='000001', exclude_from_analysis=False)],
            'next_row': None,
        }
        self.assertEqual(service.import_applications(self.reviewer), 1)
        self.google.return_value = {'draft_id': 'draft_safe'}
        item = service.approve_application(CustomerApplication.objects.get().pk, self.reviewer)
        user = item.created_user
        self.assertEqual(user.username, item.email)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.knowledge_profile.user_type, 'customer')
        self.assertEqual(user.knowledge_profile.uid, '800001')
        self.assertTrue(user.knowledge_profile.exclude_from_analysis)
        self.assertEqual(self.google.call_args.args[0]['to'], item.email)

    def test_missing_token_can_be_created(self):
        item = self.failed_draft()
        CustomerApplication.objects.filter(pk=item.pk).update(invitation_token='')
        result = service.create_invitation_draft(item.pk, self.reviewer)
        self.assertTrue(default_token_generator.check_token(result.created_user, result.invitation_token))

    def test_draft_failure_retry_preserves_user_token_and_uuid(self):
        item = self.failed_draft()
        self.assertEqual(item.status, 'approved')
        self.assertEqual(item.draft_state, 'failed')
        first_payload = self.google.call_args.args[0]
        token, user_id = item.invitation_token, item.created_user_id
        result = service.create_invitation_draft(item.pk, self.reviewer)
        self.assertEqual(result.created_user_id, user_id)
        self.assertEqual(result.invitation_token, token)
        self.assertEqual(self.google.call_args.args[0], first_payload)
        self.assertIs(self.google.call_args.args[1], self.reviewer)
        self.assertEqual(result.draft_state, 'created')
        self.google.reset_mock()
        self.assertEqual(service.create_invitation_draft(item.pk, self.reviewer).draft_id, 'draft_123')
        service.approve_application(item.pk, self.reviewer)
        self.google.assert_not_called()
        self.assertEqual(get_user_model().objects.filter(email=item.email).count(), 1)

    def test_case_insensitive_duplicate_sources(self):
        for field in ('username', 'email', 'profile', 'approved'):
            with self.subTest(field=field), transaction.atomic():
                item = application(email=f'{field}@example.com')
                address = item.email.upper()
                if field in ('username', 'email'):
                    get_user_model().objects.create_user(**{
                        'username': address if field == 'username' else 'existing',
                        'email': address if field == 'email' else '',
                    })
                elif field == 'profile':
                    user = get_user_model().objects.create_user('existing')
                    UserProfile.objects.create(user=user, company_name='既存',
                                               email_addresses=f'other@example.com; {address}')
                else:
                    application(3, email=address, status='approved')
                with self.assertRaises(service.ApplicationError):
                    service.approve_application(item.pk, self.reviewer)
                item.refresh_from_db()
                self.assertEqual(item.status, 'pending')
                transaction.set_rollback(True)
        self.google.assert_not_called()

    def test_approved_email_database_constraint_is_case_insensitive(self):
        application(email='Same@example.com', status='approved')
        with self.assertRaises(IntegrityError), transaction.atomic():
            application(3, email='same@EXAMPLE.COM', status='approved')

    def test_approval_rolls_back_all_objects_on_save_failure(self):
        item = application()
        with patch.object(CustomerApplication, 'save', side_effect=IntegrityError('private')):
            with self.assertRaises(service.ApplicationError):
                service.approve_application(item.pk, self.reviewer)
        item.refresh_from_db()
        self.assertEqual(item.status, 'pending')
        self.assertIsNone(item.created_user_id)
        self.assertFalse(get_user_model().objects.filter(email=item.email).exists())
        self.assertFalse(UserProfile.objects.exists())
        self.assertFalse(Customer.objects.exists())
        self.google.assert_not_called()

    def test_uid_collision_retries_transaction(self):
        occupied = get_user_model().objects.create_user('existing')
        UserProfile.objects.create(user=occupied, uid='800001', company_name='既存')
        with patch.object(service, '_next_uid', side_effect=['800001', '800002']):
            result = self.approve()
        self.assertEqual(result.created_user.knowledge_profile.uid, '800002')
        self.assertEqual(get_user_model().objects.filter(email=result.email).count(), 1)

    def test_rejected_pending_and_missing_applications_are_refused(self):
        rejected = application(status='rejected')
        with self.assertRaises(service.ApplicationError):
            service.approve_application(rejected.pk, self.reviewer)
        for pk in (rejected.pk, application(3).pk, uuid.uuid4(), 'not-a-uuid'):
            with self.subTest(pk=pk), self.assertRaises(service.ApplicationError):
                service.create_invitation_draft(pk, self.reviewer)
        self.google.assert_not_called()

    def test_five_minute_lease(self):
        item = self.failed_draft()
        now = timezone.now()
        CustomerApplication.objects.filter(pk=item.pk).update(
            draft_state='creating', draft_started_at=now - timedelta(seconds=299),
        )
        self.google.reset_mock()
        with patch.object(service.timezone, 'now', return_value=now):
            with self.assertRaises(service.ApplicationError):
                service.create_invitation_draft(item.pk, self.reviewer)
            self.google.assert_not_called()
            CustomerApplication.objects.filter(pk=item.pk).update(
                draft_started_at=now - timedelta(minutes=5),
            )
            self.assertEqual(service.create_invitation_draft(item.pk, self.reviewer).draft_state, 'created')

    def test_stale_attempt_cannot_overwrite_newer_success_or_failure(self):
        item = self.failed_draft()
        for succeeds in (False, True):
            with self.subTest(succeeds=succeeds):
                newer_attempt = uuid.uuid4()

                def takeover(payload, actor):
                    self.assertIs(actor, self.reviewer)
                    CustomerApplication.objects.filter(pk=item.pk).update(
                        draft_attempt_id=newer_attempt, draft_state='creating',
                    )
                    if not succeeds:
                        raise TimeoutError('private')
                    return {'ok': True, 'draft_id': 'stale_draft'}

                CustomerApplication.objects.filter(pk=item.pk).update(
                    draft_state='failed', draft_started_at=None,
                )
                self.google.side_effect = takeover
                with self.assertRaises(service.ApplicationError):
                    service.create_invitation_draft(item.pk, self.reviewer)
                item.refresh_from_db()
                self.assertEqual(item.draft_attempt_id, newer_attempt)
                self.assertEqual(item.draft_state, 'creating')
                self.assertEqual(item.draft_id, '')

    def test_stale_worker_never_overwrites_already_saved_draft(self):
        item = self.failed_draft()
        for succeeds in (False, True):
            with self.subTest(succeeds=succeeds):
                def newer_success(payload, actor):
                    self.assertIs(actor, self.reviewer)
                    CustomerApplication.objects.filter(pk=item.pk).update(
                        draft_attempt_id=uuid.uuid4(), draft_state='created', draft_id='newer_draft',
                    )
                    if not succeeds:
                        raise TimeoutError('private')
                    return {'draft_id': 'stale_draft'}

                CustomerApplication.objects.filter(pk=item.pk).update(
                    draft_state='failed', draft_id='', draft_started_at=None,
                )
                self.google.side_effect = newer_success
                if succeeds:
                    self.assertEqual(service.create_invitation_draft(item.pk, self.reviewer).draft_id, 'newer_draft')
                else:
                    with self.assertRaises(service.ApplicationError):
                        service.create_invitation_draft(item.pk, self.reviewer)
                item.refresh_from_db()
                self.assertEqual(item.draft_state, 'created')
                self.assertEqual(item.draft_id, 'newer_draft')
                self.assertEqual(item.draft_error, '')

    def test_draft_persistence_failure_recovers_after_lease_with_same_link(self):
        item = self.failed_draft()
        save = CustomerApplication.save

        def fail_final_save(instance, *args, **kwargs):
            if instance.draft_id:
                raise IntegrityError('simulated persistence failure')
            return save(instance, *args, **kwargs)

        with patch.object(CustomerApplication, 'save', new=fail_final_save):
            with self.assertRaises(IntegrityError):
                service.create_invitation_draft(item.pk, self.reviewer)
        first_payload = self.google.call_args.args[0]
        item.refresh_from_db()
        self.assertEqual(item.draft_state, 'creating')
        self.assertEqual(item.draft_id, '')
        CustomerApplication.objects.filter(pk=item.pk).update(
            draft_started_at=timezone.now() - timedelta(minutes=6),
        )
        result = service.create_invitation_draft(item.pk, self.reviewer)
        self.assertEqual(result.draft_id, 'draft_123')
        self.assertEqual(self.google.call_args.args[0], first_payload)

    def test_deleted_or_inactive_created_user_is_refused_without_communication(self):
        item = self.failed_draft()
        item.created_user.is_active = False
        item.created_user.save(update_fields=['is_active'])
        self.google.reset_mock()
        with self.assertRaises(service.ApplicationError):
            service.create_invitation_draft(item.pk, self.reviewer)
        item.created_user.delete()
        with self.assertRaises(service.ApplicationError):
            service.create_invitation_draft(item.pk, self.reviewer)
        self.google.assert_not_called()

    def test_bad_public_origin_and_bad_draft_id_fail_safely(self):
        item = self.failed_draft()
        with self.settings(CUSTOMER_APPLICATION_PUBLIC_URL='https://evil.test/path'):
            self.google.reset_mock()
            with self.assertRaises(service.ApplicationError):
                service.create_invitation_draft(item.pk, self.reviewer)
            self.google.assert_not_called()
        self.google.return_value = {'draft_id': '../private\nvalue'}
        with self.assertRaises(service.ApplicationError):
            service.create_invitation_draft(item.pk, self.reviewer)
        item.refresh_from_db()
        self.assertEqual(item.draft_state, 'failed')
        self.assertEqual(item.draft_id, '')

    def test_only_original_reviewer_can_retry_even_after_draft_created(self):
        item = self.failed_draft()
        other = get_user_model().objects.create_user('other-reviewer', is_staff=True)
        self.google.reset_mock()
        with self.assertRaises(service.ApplicationError):
            service.create_invitation_draft(item.pk, other)
        self.google.assert_not_called()
        item.refresh_from_db()
        self.assertEqual(item.draft_state, 'failed')
        self.assertEqual(item.reviewed_by, self.reviewer)
        service.create_invitation_draft(item.pk, self.reviewer)
        self.google.reset_mock()
        with self.assertRaises(service.ApplicationError):
            service.create_invitation_draft(item.pk, other)
        self.google.assert_not_called()


class ImportTests(TestCase):
    def setUp(self):
        self.reviewer = get_user_model().objects.create_user('import-reviewer', is_staff=True)
        self.google = self.enterContext(patch.object(service, '_call_google'))

    def cursor(self):
        return state_model().objects.get(pk=f'{service.SPREADSHEET_ID}:{service.SHEET_ID}').next_row

    def test_idempotent_existing_row_not_overwritten_and_tail_only(self):
        original = application()
        self.google.return_value = {'applications': [row(company_name='変更禁止')], 'next_row': None}
        self.assertEqual(service.import_applications(self.reviewer), 0)
        original.refresh_from_db()
        self.assertEqual(original.company_name, '申請会社')
        self.assertEqual(self.cursor(), 3)
        self.google.return_value = {'applications': [], 'next_row': None}
        self.assertEqual(service.import_applications(self.reviewer), 0)
        self.assertEqual(self.google.call_args.args[0]['start_row'], 3)
        self.assertEqual(self.cursor(), 3)
        self.google.return_value = {'applications': [row(3)], 'next_row': None}
        self.assertEqual(service.import_applications(self.reviewer), 1)
        self.assertEqual(self.cursor(), 4)
        for call in self.google.call_args_list:
            self.assertIs(call.args[1], self.reviewer)

    def test_more_than_2000_rows_resume_on_next_call(self):
        def page(payload, actor):
            self.assertIs(actor, self.reviewer)
            start = payload['start_row']
            stop = min(start + 200, 2003)
            return {'applications': [row(i) for i in range(start, stop)],
                    'next_row': stop if stop < 2003 else None}

        self.google.side_effect = page
        self.assertEqual(service.import_applications(self.reviewer), 2000)
        self.assertEqual(self.google.call_count, 10)
        self.assertEqual(self.cursor(), 2002)
        self.assertEqual(service.import_applications(self.reviewer), 1)
        self.assertEqual(self.google.call_args.args[0]['start_row'], 2002)
        self.assertEqual(self.cursor(), 2003)
        self.assertEqual(CustomerApplication.objects.count(), 2001)

    def test_empty_page_with_next_continues_and_empty_tail_preserves_start(self):
        self.google.side_effect = [
            {'applications': [], 'next_row': 202},
            {'applications': [row(205)], 'next_row': 402},
            {'applications': [], 'next_row': None},
        ]
        self.assertEqual(service.import_applications(self.reviewer), 1)
        self.assertEqual([call.args[0]['start_row'] for call in self.google.call_args_list], [2, 202, 402])
        self.assertEqual(self.cursor(), 402)

    def test_empty_initial_sheet_does_not_skip_first_response(self):
        self.google.return_value = {'applications': [], 'next_row': None}
        self.assertEqual(service.import_applications(self.reviewer), 0)
        self.assertEqual(self.cursor(), 2)

    def test_invalid_page_is_atomic_and_cursor_unchanged(self):
        bad_rows = [
            None, row(email=''), row(email='a@example.com,b@example.com'),
            row(email='Name <a@example.com>'), row(company_name=''),
            row(department='x\nBcc: other@example.com'), row(display_name='x' * 151),
            row(submitted_at='invalid'), row(source_key='other:sheet:2'),
            row(202), row(applicant_email='invalid'), row(email=None),
        ]
        for invalid in bad_rows:
            with self.subTest(invalid=invalid):
                self.google.return_value = {'applications': [row(3), invalid], 'next_row': None}
                with self.assertRaises(service.ApplicationError):
                    service.import_applications(self.reviewer)
                self.assertFalse(CustomerApplication.objects.exists())
                self.assertEqual(self.cursor(), 2)

    def test_invalid_page_shapes_and_duplicate_source_keys(self):
        pages = [None, {}, {'applications': [], 'next_row': True},
                 {'applications': [], 'next_row': 2}, {'applications': [], 'next_row': 203},
                 {'applications': {}, 'next_row': None},
                 {'applications': [row()] * 201, 'next_row': None},
                 {'applications': [row(), row()], 'next_row': None}]
        for page in pages:
            with self.subTest(page=page):
                self.google.return_value = page
                with self.assertRaises(service.ApplicationError):
                    service.import_applications(self.reviewer)
                self.assertEqual(self.cursor(), 2)

    def test_successful_page_persists_when_later_page_fails(self):
        self.google.side_effect = [
            {'applications': [row()], 'next_row': 202},
            service.ApplicationError('remote failed'),
        ]
        with self.assertRaises(service.ApplicationError):
            service.import_applications(self.reviewer)
        self.assertEqual(CustomerApplication.objects.count(), 1)
        self.assertEqual(self.cursor(), 202)

    def test_save_failure_rolls_back_rows_and_cursor(self):
        self.google.return_value = {'applications': [row(), row(3)], 'next_row': None}
        original = CustomerApplication.objects.get_or_create

        def save(**kwargs):
            if kwargs['source_key'].endswith(':3'):
                raise IntegrityError('private')
            return original(**kwargs)

        with patch.object(CustomerApplication.objects, 'get_or_create', side_effect=save):
            with self.assertRaises(service.ApplicationError):
                service.import_applications(self.reviewer)
        self.assertFalse(CustomerApplication.objects.exists())
        self.assertEqual(self.cursor(), 2)

    def test_concurrent_cursor_change_rejects_stale_page(self):
        def other_import(payload, actor):
            self.assertIs(actor, self.reviewer)
            state_model().objects.all().update(next_row=202)
            return {'applications': [row()], 'next_row': 202}

        self.google.side_effect = other_import
        with self.assertRaises(service.ApplicationError):
            service.import_applications(self.reviewer)
        self.assertEqual(self.cursor(), 202)
        self.assertFalse(CustomerApplication.objects.exists())

    def test_cursor_database_constraint_rejects_header_row(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            state_model().objects.create(source_key='invalid-cursor', next_row=1)


class GoogleDispatcherTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user('dispatcher-reviewer', is_staff=True)
        self.list_applications = self.enterContext(patch.object(service.google, 'list_applications'))
        self.create_draft = self.enterContext(patch.object(service.google, 'create_draft'))

    def test_list_passes_actor_and_start_row_and_returns_result(self):
        self.list_applications.return_value = {'applications': [], 'next_row': None}
        result = service._call_google({'action': 'list_applications', 'start_row': 202}, self.actor)
        self.assertIs(result, self.list_applications.return_value)
        self.list_applications.assert_called_once_with(self.actor, 202)
        self.create_draft.assert_not_called()

    def test_create_draft_passes_actor_application_subject_and_body(self):
        item = application(status='approved', reviewed_by=self.actor)
        self.create_draft.return_value = {'draft_id': 'draft_dispatcher'}
        result = service._call_google({
            'action': 'create_invitation_draft', 'application_id': str(item.pk),
            'to': item.email, 'subject': '招待', 'body': '合成本文',
        }, self.actor)
        self.assertIs(result, self.create_draft.return_value)
        self.create_draft.assert_called_once_with(self.actor, item, '招待', '合成本文')
        self.list_applications.assert_not_called()

    def test_google_errors_hide_private_causes_for_both_actions(self):
        item = application(status='approved', reviewed_by=self.actor)

        def fail(*args):
            try:
                raise TimeoutError('PRIVATE access_token=synthetic-secret')
            except TimeoutError as exc:
                raise service.google.CustomerGoogleError() from exc

        self.list_applications.side_effect = fail
        self.create_draft.side_effect = fail
        for payload in (
            {'action': 'list_applications', 'start_row': 2},
            {'action': 'create_invitation_draft', 'application_id': str(item.pk),
             'subject': '招待', 'body': '合成本文'},
        ):
            with self.subTest(action=payload['action']):
                with self.assertRaises(service.ApplicationError) as caught:
                    service._call_google(payload, self.actor)
                self.assertEqual(str(caught.exception), service.google.ERROR)
                self.assertNotIn('PRIVATE', str(caught.exception))
                self.assertNotIn('synthetic-secret', str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertTrue(caught.exception.__suppress_context__)

    def test_unknown_action_does_not_call_google(self):
        with self.assertRaises(service.ApplicationError) as caught:
            service._call_google({'action': 'PRIVATE-unknown-action'}, self.actor)
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.list_applications.assert_not_called()
        self.create_draft.assert_not_called()


class PublicOriginTests(SimpleTestCase):
    def test_public_origin_canonicalization_and_malicious_origins(self):
        with self.settings(CUSTOMER_APPLICATION_PUBLIC_URL='https://NEXUS.example.com:443'):
            self.assertEqual(service._public_origin(), 'https://nexus.example.com')
        for url in ('http://nexus.example.com', '//evil.test', 'https://user@evil.test',
                    'https://nexus.example.com/', 'https://nexus.example.com/path',
                    'https://nexus.example.com?', 'https://nexus.example.com#',
                    'https://nexus.example.com:', 'https://nexus.example.com:bad',
                    'https://nexus.example.com\\@evil.test', 'https://nexus.example.com\n',
                    'https://nexus.example.com:99999', None):
            with self.subTest(url=url), self.settings(CUSTOMER_APPLICATION_PUBLIC_URL=url):
                with self.assertRaises(service.ApplicationError):
                    service._public_origin()


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   CUSTOMER_APPLICATION_PUBLIC_URL='https://nexus.example.com')
class TransactionBoundaryTests(TransactionTestCase):
    def test_external_calls_outside_transaction(self):
        self.assertIn('mode=memory', connection.settings_dict['NAME'])
        reviewer = get_user_model().objects.create_user('reviewer')

        def google(payload, actor):
            self.assertIs(actor, reviewer)
            self.assertFalse(connection.in_atomic_block)
            if payload['action'] == 'list_applications':
                return {'applications': [row()], 'next_row': None}
            self.assertTrue(CustomerApplication.objects.filter(status='approved').exists())
            return {'draft_id': 'draft_boundary'}

        with patch.object(service, '_call_google', side_effect=google):
            self.assertEqual(service.import_applications(reviewer), 1)
            service.approve_application(CustomerApplication.objects.get().pk, reviewer)