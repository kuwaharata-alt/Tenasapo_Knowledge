"""Manual-send regressions: real views/claims/transport, mocked outbound HTTP.

Overlapping workers are interleaved at the HTTP boundary deterministically;
these tests do not depend on SQLite thread locking or timing sleeps.
"""

from datetime import date, datetime, timedelta
from io import StringIO
import json
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .chat_schedule import (
    ChatNotificationDelivery as Delivery, ChatNotificationSchedule as Schedule,
    JAPAN_TIMEZONE, save_schedule,
)
from .chat_schedule_delivery import claim_delivery, finish_delivery
from .notifications import GoogleChatPayloadTooLarge


VIEW = 'tenasapo_knowledge.chat_schedule_settings'
COMMAND = 'tenasapo_knowledge.management.commands.send_scheduled_chat_notifications'
NOW = datetime(2026, 11, 13, 12, tzinfo=JAPAN_TIMEZONE)
CONFIRMED = (200, '{"name":"spaces/test/messages/manual"}')


@override_settings(
    DEBUG=True, GOOGLE_CHAT_SCHEDULED_SEND_ENABLED=True,
    GOOGLE_CHAT_GAS_WEB_APP_URL='', GOOGLE_CHAT_WEBHOOK_URL='https://chat.example.test/',
    ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
)
class ChatScheduleManualSendTests(TestCase):
    def setUp(self):
        # Block both imported aliases and lower-level escape routes. Assert in
        # tearDown too: production code may catch the guard's AssertionError.
        self.http_guards = []
        for target in (
            'urllib.request.urlopen',
            'tenasapo_knowledge.notifications.urlopen',
            'requests.sessions.Session.request',
            'http.client.HTTPConnection.connect',
            'http.client.HTTPSConnection.connect',
            'socket.create_connection',
            'socket.socket.connect',
        ):
            guard = self.enterContext(patch(target, side_effect=AssertionError('live HTTP blocked')))
            self.http_guards.append(guard)
        self.http = self.enterContext(patch(
            'tenasapo_knowledge.notifications._request_json_post', return_value=CONFIRMED))
        self.enterContext(patch(VIEW + '.timezone.now', return_value=NOW))
        self.staff = get_user_model().objects.create_user('manual-send-staff', is_staff=True)
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.staff)
        self.schedule = self.make_schedule()

    def tearDown(self):
        for guard in self.http_guards:
            guard.assert_not_called()
        super().tearDown()

    def make_schedule(self, **overrides):
        values = dict(
            target_month=date(2026, 11, 1), deadline=date(2026, 11, 30),
            deadline_notice_at=NOW + timedelta(days=2),
            report_notice_at=NOW + timedelta(days=3), enabled=True, actor=self.staff,
        )
        values.update(overrides)
        return save_schedule(**values)

    def url(self, schedule=None, kind='deadline'):
        schedule = self.schedule if schedule is None else schedule
        return reverse('chat_schedule_send', args=[schedule.pk, kind])

    def data(self, schedule=None, kind='deadline', **overrides):
        schedule = self.schedule if schedule is None else schedule
        values = dict(schedule_id=str(schedule.pk), expected_revision=str(schedule.revision),
                      kind=kind, environment=schedule.environment)
        values.update(overrides)
        return values

    def confirmation(self, schedule=None, kind='deadline', client=None, **query):
        schedule = self.schedule if schedule is None else schedule
        values = {'expected_revision': str(schedule.revision)}
        values.update(query)
        return (client or self.client).get(self.url(schedule, kind), values)

    def csrf_token(self, client=None):
        client = client or self.client
        # Obtain a real masked token via the confirmation template, not by
        # disabling middleware or forging request._dont_enforce_csrf_checks.
        response = self.confirmation(client=client)
        self.assertIn(response.status_code, (200, 409))
        return client.cookies[settings.CSRF_COOKIE_NAME].value

    def post(self, schedule=None, kind='deadline', client=None, token=None, **overrides):
        client = client or self.client
        if token is None:
            token = self.csrf_token(client)
        data = self.data(schedule, kind, **overrides)
        data['csrfmiddlewaretoken'] = token
        return client.post(self.url(schedule, kind), data)

    def snapshot(self):
        return (list(Schedule.objects.order_by('pk').values()),
                list(Delivery.objects.order_by('pk').values()))

    def delivery(self, kind='deadline', schedule=None):
        return Delivery.objects.get(schedule=schedule or self.schedule, kind=kind)

    def automatic_send(self):
        output = StringIO()
        call_command('send_scheduled_chat_notifications', '--send', stdout=output)
        return output.getvalue()

    def assert_rejected_without_writes(self, action, statuses=(302, 400, 404, 409)):
        before = self.snapshot()
        calls = self.http.call_count
        response = action()
        self.assertIn(response.status_code, statuses)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.http.call_count, calls)
        return response

    @override_settings(SESSION_SAVE_EVERY_REQUEST=False)
    def test_staff_get_and_head_are_read_only_even_without_delivery_rows(self):
        self.schedule.deliveries.all().delete()
        for kind in Delivery.Kind.values:
            with self.subTest(kind=kind):
                before = self.snapshot()
                with CaptureQueriesContext(connection) as queries:
                    response = self.confirmation(kind=kind)
                    head = self.client.head(self.url(kind=kind), {'expected_revision': '1'})
                # Disable routine middleware session refresh to isolate this
                # view's writes. Schedule/delivery snapshots remain checked too.
                writes = [' '.join(query['sql'].lstrip().split()[:2]) for query in queries
                          if query['sql'].lstrip().split()[0].upper()
                          in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'CREATE', 'ALTER', 'DROP'}]
                self.assertContains(response, '確認して今すぐ送信')
                self.assertContains(response, 'csrfmiddlewaretoken')
                self.assertTrue(response.context['available'])
                self.assertTrue(response.context['preview'])
                self.assertEqual(head.status_code, 200)
                self.assertEqual(self.snapshot(), before)
                self.http.assert_not_called()
                self.assertEqual(writes, [], 'Confirmation must not write to any database table')
        self.http.assert_not_called()

    def test_anonymous_cannot_confirm_or_send(self):
        anonymous = Client(enforce_csrf_checks=True)
        token = self.csrf_token()
        anonymous.cookies[settings.CSRF_COOKIE_NAME] = token
        self.assert_rejected_without_writes(
            lambda: self.confirmation(client=anonymous), statuses=(302, 403))
        self.assert_rejected_without_writes(
            lambda: self.post(client=anonymous, token=token), statuses=(302, 403))

    def test_member_and_nonstaff_superuser_cannot_confirm_or_send(self):
        token = self.csrf_token()
        for superuser in (False, True):
            with self.subTest(superuser=superuser):
                user = get_user_model().objects.create_user(
                    f'manual-member-{superuser}', is_staff=False, is_superuser=superuser)
                client = Client(enforce_csrf_checks=True)
                client.force_login(user)
                client.cookies[settings.CSRF_COOKIE_NAME] = token
                for action in (lambda: self.confirmation(client=client),
                               lambda: self.post(client=client, token=token)):
                    response = self.assert_rejected_without_writes(action, statuses=(302,))
                    self.assertEqual(response.url, reverse('article_list'))

    def test_post_requires_csrf_cookie_and_matching_token(self):
        self.assert_rejected_without_writes(
            lambda: self.client.post(self.url(), self.data()), statuses=(403,))
        token = self.csrf_token()
        self.assert_rejected_without_writes(
            lambda: self.client.post(self.url(), self.data()), statuses=(403,))
        self.assert_rejected_without_writes(
            lambda: self.post(token='invalid-token'), statuses=(403,))
        different = 'A' * 32 if token != 'A' * 32 else 'B' * 32
        self.assert_rejected_without_writes(
            lambda: self.post(token=different), statuses=(403,))
        self.assertEqual(self.post(token=token).status_code, 302)
        self.http.assert_called_once()

    def test_post_requires_send_setting_to_be_exactly_true(self):
        token = self.csrf_token()
        for value in (False, None, 1, 'True'):
            with self.subTest(value=value), override_settings(GOOGLE_CHAT_SCHEDULED_SEND_ENABLED=value):
                before = self.snapshot()
                response = self.confirmation()
                self.assertNotContains(response, '確認して今すぐ送信')
                self.assertEqual(self.snapshot(), before)
                self.assert_rejected_without_writes(lambda: self.post(token=token))
                self.assertIsNone(claim_delivery(
                    self.schedule.pk, 'deadline', NOW, manual=True, expected_revision=1))
                self.assertEqual(self.snapshot(), before)
        self.http.assert_not_called()

    def test_manual_can_send_each_kind_before_its_reserved_time(self):
        for index, kind in enumerate(Delivery.Kind.values, start=1):
            with self.subTest(kind=kind):
                self.assertEqual(self.post(kind=kind).status_code, 302)
                delivery = self.delivery(kind)
                self.assertEqual((delivery.state, delivery.attempts), ('sent', 1))
                self.assertEqual(delivery.claimed_at, NOW)
                self.assertEqual(delivery.sent_at, NOW)
                self.assertEqual(self.http.call_count, index)
                payload = json.loads(self.http.call_args.args[1])
                self.assertIn('text', payload)
                self.assertIn('cardsV2', payload)

    def test_manual_is_not_blocked_by_automatic_48_hour_limit(self):
        revised = self.make_schedule(expected_revision=1, deadline_notice_at=NOW - timedelta(days=3))
        self.assertEqual(self.post(schedule=revised).status_code, 302)
        self.assertEqual(self.delivery(schedule=revised).state, 'sent')
        self.http.assert_called_once()

    def test_only_latest_enabled_revision_can_be_sent(self):
        revised = self.make_schedule(expected_revision=1)
        self.assert_rejected_without_writes(lambda: self.confirmation(), statuses=(409,))
        self.assert_rejected_without_writes(lambda: self.post())
        self.assertEqual(self.post(schedule=revised).status_code, 302)
        self.assertEqual(self.delivery(schedule=revised).state, 'sent')
        self.http.assert_called_once()

    def test_disabled_latest_revision_cannot_be_sent(self):
        revised = self.make_schedule(expected_revision=1, enabled=False)
        self.assert_rejected_without_writes(
            lambda: self.confirmation(schedule=revised), statuses=(409,))
        self.assert_rejected_without_writes(lambda: self.post(schedule=revised))

    def test_higher_disabled_revision_blocks_even_an_enabled_older_row(self):
        revised = self.make_schedule(expected_revision=1, enabled=False)
        # Defensive fixture for a writer bypassing save_schedule's invariant.
        Schedule.objects.filter(pk=self.schedule.pk).update(enabled=True)
        self.assert_rejected_without_writes(lambda: self.confirmation(), statuses=(409,))
        self.assert_rejected_without_writes(lambda: self.post())
        self.assert_rejected_without_writes(lambda: self.post(schedule=revised))

    def test_foreign_and_unassigned_environment_rows_are_not_sendable(self):
        token = self.csrf_token()
        for environment in ('production', 'unassigned'):
            with self.subTest(environment=environment):
                foreign = Schedule.objects.create(
                    environment=environment, revision=99, target_month=self.schedule.target_month,
                    deadline=self.schedule.deadline, deadline_notice_at=NOW,
                    report_notice_at=NOW, enabled=True,
                )
                self.assert_rejected_without_writes(
                    lambda: self.confirmation(schedule=foreign), statuses=(404,))
                self.assert_rejected_without_writes(
                    lambda: self.post(schedule=foreign, token=token, environment='development'),
                    statuses=(404,))
                self.assert_rejected_without_writes(
                    lambda: self.post(schedule=foreign, token=token), statuses=(400,))
        self.assertEqual(self.post(token=token).status_code, 302)
        self.http.assert_called_once()

    def test_production_selects_only_production_even_if_development_revision_is_higher(self):
        self.make_schedule(expected_revision=1)
        with override_settings(DEBUG=False):
            production = self.make_schedule()
            self.assert_rejected_without_writes(
                lambda: self.confirmation(), statuses=(404,))
            self.assert_rejected_without_writes(
                lambda: self.post(token=self.csrf_token_for(production), environment='production'),
                statuses=(404,))
            self.assertEqual(self.post(schedule=production, token=self.csrf_token_for(production)).status_code, 302)
            self.assertEqual(self.delivery(schedule=production).state, 'sent')
        self.assertEqual(self.delivery().attempts, 0)
        self.http.assert_called_once()

    def csrf_token_for(self, schedule):
        self.assertEqual(self.confirmation(schedule=schedule).status_code, 200)
        return self.client.cookies[settings.CSRF_COOKIE_NAME].value

    def test_tampered_post_fields_are_rejected_without_claim(self):
        token = self.csrf_token()
        cases = (
            {'schedule_id': str(self.schedule.pk + 1000)}, {'schedule_id': '0'},
            {'expected_revision': '0'}, {'expected_revision': '2'},
            {'expected_revision': 'abc'}, {'expected_revision': ''},
            {'kind': 'report'}, {'kind': 'invalid'}, {'kind': ''},
            {'environment': 'production'}, {'environment': 'unassigned'}, {'environment': ''},
        )
        for values in cases:
            with self.subTest(values=values):
                # Keep the URL selection fixed while tampering only with the
                # submitted fields (post(kind=...) intentionally selects a URL).
                data = self.data()
                data.update(values, csrfmiddlewaretoken=token)
                self.assert_rejected_without_writes(lambda: self.client.post(self.url(), data))
        for field in ('schedule_id', 'expected_revision', 'kind', 'environment'):
            with self.subTest(missing=field):
                data = self.data()
                data.pop(field)
                data['csrfmiddlewaretoken'] = token
                self.assert_rejected_without_writes(
                    lambda: self.client.post(self.url(), data), statuses=(400,))

    def test_invalid_get_revision_or_kind_does_not_write(self):
        for revision in ('', '0', 'abc', '2'):
            with self.subTest(revision=revision):
                self.assert_rejected_without_writes(
                    lambda: self.confirmation(expected_revision=revision), statuses=(400, 409))
        self.assert_rejected_without_writes(
            lambda: self.confirmation(kind='invalid'), statuses=(400,))
        self.assert_rejected_without_writes(
            lambda: self.client.get(self.url()), statuses=(400,))

    def test_edit_between_confirmation_and_post_rejects_stale_form(self):
        token = self.csrf_token()
        revised = self.make_schedule(expected_revision=1)
        self.assert_rejected_without_writes(lambda: self.post(token=token))
        self.assertEqual(self.delivery(schedule=revised).attempts, 0)

    def test_disabled_after_confirmation_is_revalidated_by_claim(self):
        token = self.csrf_token()
        Schedule.objects.filter(pk=self.schedule.pk).update(enabled=False)
        self.assert_rejected_without_writes(lambda: self.post(token=token))

    def test_duplicate_post_sends_once_and_does_not_update_ledger_again(self):
        token = self.csrf_token()
        self.assertEqual(self.post(token=token).status_code, 302)
        self.assert_rejected_without_writes(lambda: self.post(token=token))
        self.assert_rejected_without_writes(lambda: self.confirmation(), statuses=(409,))
        self.http.assert_called_once()
        self.assertEqual(self.delivery().attempts, 1)

    def test_overlapping_manual_post_and_automatic_worker_cannot_claim_again(self):
        revised = self.make_schedule(expected_revision=1, deadline_notice_at=NOW)
        token = self.csrf_token()

        def while_sending(*args, **kwargs):
            delivery = self.delivery(schedule=revised)
            self.assertEqual((delivery.state, delivery.attempts), ('sending', 1))
            self.assertIsNone(delivery.sent_at)
            self.assert_rejected_without_writes(lambda: self.post(schedule=revised, token=token))
            before = self.snapshot()
            self.automatic_send()
            self.assertEqual(self.snapshot(), before)
            return CONFIRMED

        self.http.side_effect = while_sending
        self.assertEqual(self.post(schedule=revised, token=token).status_code, 302)
        self.automatic_send()
        self.http.assert_called_once()
        self.assertEqual(self.delivery(schedule=revised).state, 'sent')

    def test_automatic_claim_blocks_manual_post_before_and_after_completion(self):
        revised = self.make_schedule(expected_revision=1, deadline_notice_at=NOW)
        token = self.csrf_token()

        def while_sending(*args, **kwargs):
            self.assertEqual(self.delivery(schedule=revised).state, 'sending')
            self.assert_rejected_without_writes(lambda: self.post(schedule=revised, token=token))
            self.assertIsNone(claim_delivery(
                revised.pk, 'deadline', NOW, manual=True, expected_revision=revised.revision))
            return CONFIRMED

        self.http.side_effect = while_sending
        self.automatic_send()
        self.assert_rejected_without_writes(lambda: self.post(schedule=revised, token=token))
        self.http.assert_called_once()
        self.assertEqual((self.delivery(schedule=revised).state,
                          self.delivery(schedule=revised).attempts), ('sent', 1))

    def test_automatic_claim_respects_time_but_manual_claim_can_win_early(self):
        before = self.snapshot()
        self.assertIsNone(claim_delivery(self.schedule.pk, 'deadline', NOW))
        self.assertEqual(self.snapshot(), before)
        claimed = claim_delivery(self.schedule.pk, 'deadline', NOW, manual=True, expected_revision=1)
        self.assertIsNotNone(claimed)
        self.assertIsNone(claim_delivery(self.schedule.pk, 'deadline', NOW + timedelta(days=2)))
        self.assert_rejected_without_writes(lambda: self.post())
        self.assertEqual(self.delivery().attempts, 1)
        self.http.assert_not_called()

    def test_claim_rejects_invalid_revision_types_and_stale_revision(self):
        for revision in (None, True, 0, -1, '1', 2):
            with self.subTest(revision=revision):
                before = self.snapshot()
                self.assertIsNone(claim_delivery(
                    self.schedule.pk, 'deadline', NOW, manual=True, expected_revision=revision))
                self.assertEqual(self.snapshot(), before)
        self.http.assert_not_called()

    def test_unconfirmed_http_results_become_unknown_and_never_retry(self):
        cases = ((200, '{}'), (200, 'not json'), (200, '{"name":""}'),
                 (503, '{"name":"spaces/test/messages/no"}'), TimeoutError('timeout'))
        for index, result in enumerate(cases, start=1):
            with self.subTest(result=result):
                schedule = self.make_schedule(target_month=date(2027, index, 1), deadline_notice_at=NOW)
                self.http.reset_mock()
                self.http.side_effect = result if isinstance(result, Exception) else None
                if not isinstance(result, Exception):
                    self.http.return_value = result
                self.assertEqual(self.post(schedule=schedule).status_code, 302)
                delivery = self.delivery(schedule=schedule)
                self.assertEqual((delivery.state, delivery.attempts), ('unknown', 1))
                self.assertIsNone(delivery.sent_at)
                self.assertTrue(delivery.last_error)
                self.assert_rejected_without_writes(lambda: self.post(schedule=schedule))
                self.automatic_send()
                self.http.assert_called_once()

    def test_sender_must_return_boolean_true_confirmation(self):
        for index, result in enumerate((None, {}, {'confirmed': False},
                                        {'confirmed': 1}, {'confirmed': 'true'}), start=1):
            with self.subTest(result=result):
                schedule = self.make_schedule(target_month=date(2028, index, 1))
                with patch(VIEW + '.send_scheduled_google_chat_message', return_value=result) as sender:
                    self.assertEqual(self.post(schedule=schedule).status_code, 302)
                    self.assertEqual(self.delivery(schedule=schedule).state, 'unknown')
                    self.assertIsNone(self.delivery(schedule=schedule).sent_at)
                    self.assert_rejected_without_writes(lambda: self.post(schedule=schedule))
                    sender.assert_called_once()
        self.http.assert_not_called()

    def test_gas_http_200_without_chat_confirmation_is_unknown_without_fallback(self):
        with override_settings(GOOGLE_CHAT_GAS_WEB_APP_URL='https://relay.example.test/exec'):
            self.http.return_value = (200, '{"ok":true}')
            self.assertEqual(self.post(kind='report').status_code, 302)
            self.assertEqual(self.delivery('report').state, 'unknown')
            self.assertIsNone(self.delivery('report').sent_at)
            self.assert_rejected_without_writes(lambda: self.post(kind='report'))
            self.http.assert_called_once()
            self.assertEqual(self.http.call_args.args[0], 'https://relay.example.test/exec')

    def test_preflight_failure_is_failed_without_http_and_never_retried(self):
        Schedule.objects.filter(pk=self.schedule.pk).update(deadline_notice_at=NOW)
        token = self.csrf_token()
        with override_settings(GOOGLE_CHAT_WEBHOOK_URL='', GOOGLE_CHAT_GAS_WEB_APP_URL=''):
            self.assertEqual(self.post(token=token).status_code, 302)
        delivery = self.delivery()
        self.assertEqual((delivery.state, delivery.attempts), ('failed', 1))
        self.assertIsNone(delivery.sent_at)
        self.assertTrue(delivery.last_error)
        self.assert_rejected_without_writes(lambda: self.post(token=token))
        before = self.snapshot()
        self.automatic_send()
        self.assertEqual(self.snapshot(), before)
        self.http.assert_not_called()

    def test_builder_failure_and_oversize_are_failed_without_http(self):
        token = self.csrf_token()
        for index, error in enumerate((ValueError('bad data'), GoogleChatPayloadTooLarge('too large')), start=1):
            with self.subTest(error=type(error).__name__):
                schedule = self.make_schedule(target_month=date(2029, index, 1))
                with patch(VIEW + '.build_deadline_message', side_effect=error):
                    self.assertEqual(self.post(schedule=schedule, token=token).status_code, 302)
                self.assertEqual(self.delivery(schedule=schedule).state, 'failed')
                self.assertIsNone(self.delivery(schedule=schedule).sent_at)
                self.assert_rejected_without_writes(lambda: self.post(schedule=schedule, token=token))
        self.http.assert_not_called()

    def test_failed_unknown_and_dirty_pending_rows_are_not_claimed(self):
        cases = (
            dict(state='failed'), dict(state='unknown'), dict(state='sending'), dict(state='sent'),
            dict(attempts=1), dict(claimed_at=NOW), dict(sent_at=NOW),
        )
        token = self.csrf_token()
        for index, values in enumerate(cases, start=1):
            with self.subTest(values=values):
                schedule = self.make_schedule(target_month=date(2030, index, 1))
                schedule.deliveries.filter(kind='deadline').update(**values)
                self.assert_rejected_without_writes(lambda: self.post(schedule=schedule, token=token))
                self.assert_rejected_without_writes(
                    lambda: self.confirmation(schedule=schedule), statuses=(409,))
        self.http.assert_not_called()

    def test_historical_sent_failed_unknown_block_a_reset_ledger_in_new_revision(self):
        token = self.csrf_token()
        for index, state in enumerate(('sent', 'failed', 'unknown'), start=1):
            with self.subTest(state=state):
                original = self.make_schedule(target_month=date(2031, index, 1))
                original.deliveries.filter(kind='deadline').update(state=state, attempts=1)
                revised = self.make_schedule(target_month=original.target_month, expected_revision=1)
                self.assertEqual(self.delivery(schedule=revised).state, state)
                self.assert_rejected_without_writes(lambda: self.post(schedule=revised, token=token))
                # Even an accidental reset must not defeat cross-revision protection.
                revised.deliveries.filter(kind='deadline').update(
                    state='pending', attempts=0, claimed_at=None, sent_at=None)
                self.assert_rejected_without_writes(lambda: self.post(schedule=revised, token=token))
        self.http.assert_not_called()

    def test_one_sent_keeps_enabled_and_both_sent_archive_for_either_order(self):
        token = self.csrf_token()
        for index, kinds in enumerate((('deadline', 'report'), ('report', 'deadline')), start=1):
            with self.subTest(order=kinds):
                schedule = self.make_schedule(target_month=date(2032, index, 1))
                self.assertEqual(self.post(schedule=schedule, kind=kinds[0], token=token).status_code, 302)
                schedule.refresh_from_db()
                self.assertTrue(schedule.enabled)
                self.assertEqual(self.delivery(kinds[1], schedule).state, 'pending')
                self.assertEqual(self.post(schedule=schedule, kind=kinds[1], token=token).status_code, 302)
                schedule.refresh_from_db()
                self.assertFalse(schedule.enabled)
                self.assertEqual(set(schedule.deliveries.values_list('state', flat=True)), {'sent'})
                for kind in kinds:
                    self.assert_rejected_without_writes(
                        lambda: self.post(schedule=schedule, kind=kind, token=token))
        self.assertEqual(self.http.call_count, 4)

    def test_sent_plus_unknown_does_not_archive(self):
        self.assertEqual(self.post().status_code, 302)
        self.http.side_effect = TimeoutError
        self.assertEqual(self.post(kind='report').status_code, 302)
        self.schedule.refresh_from_db()
        self.assertTrue(self.schedule.enabled)
        self.assertEqual(self.delivery().state, 'sent')
        self.assertEqual(self.delivery('report').state, 'unknown')

    def test_stale_worker_success_cannot_disable_a_newer_revision(self):
        self.schedule.deliveries.filter(kind='report').update(state='sent', attempts=1, sent_at=NOW)
        claimed = claim_delivery(self.schedule.pk, 'deadline', NOW, manual=True, expected_revision=1)
        self.assertIsNotNone(claimed)
        # Normal saves reject in-flight workers. Construct a competing revision
        # directly to exercise finish_delivery's independent stale-version guard.
        Schedule.objects.filter(pk=self.schedule.pk).update(enabled=False)
        newer = Schedule.objects.create(
            environment='development', target_month=self.schedule.target_month, revision=2,
            deadline=self.schedule.deadline, deadline_notice_at=NOW, report_notice_at=NOW,
            enabled=True, updated_by=self.staff,
        )
        for kind in Delivery.Kind.values:
            Delivery.objects.create(schedule=newer, kind=kind, state='sent', attempts=1, sent_at=NOW)
        newer_before = Schedule.objects.filter(pk=newer.pk).values().get()
        self.assertEqual(finish_delivery(claimed, 'sent'), 'sent')
        self.assertEqual(self.delivery().state, 'sent')
        self.assertEqual(Schedule.objects.filter(pk=newer.pk).values().get(), newer_before)
        self.assertTrue(Schedule.objects.get(pk=newer.pk).enabled)
        self.http.assert_not_called()

    def test_timed_out_old_worker_cannot_overwrite_unknown_or_archive_new_revision(self):
        claimed = claim_delivery(self.schedule.pk, 'deadline', NOW, manual=True, expected_revision=1)
        self.assertIsNotNone(claimed)
        with patch(COMMAND + '.timezone.now', return_value=NOW + timedelta(minutes=31)):
            self.automatic_send()
        self.assertEqual(self.delivery().state, 'unknown')
        newer = self.make_schedule(expected_revision=1)
        before = self.snapshot()
        self.assertEqual(finish_delivery(claimed, 'sent'), 'unknown')
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(Schedule.objects.get(pk=newer.pk).enabled)
        self.assertEqual(self.delivery(schedule=newer).state, 'unknown')
        self.assertIsNone(self.delivery().sent_at)
        self.http.assert_not_called()