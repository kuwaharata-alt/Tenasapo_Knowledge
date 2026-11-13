"""Backend-only regressions; every HTTP entry point is blocked or mocked."""

from datetime import date, datetime, timedelta
from io import StringIO
import json
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase, override_settings

from .chat_schedule import (
    ChatNotificationDelivery as Delivery, ChatNotificationSchedule as Schedule,
    JAPAN_TIMEZONE, ScheduleConflict, notification_datetime, save_schedule,
)
from .models import KnowledgeArticle, TipsArticle
from .notifications import GoogleChatNotificationError, send_scheduled_google_chat_message
from .scheduled_chat_messages import MAX_PAYLOAD_BYTES, build_deadline_message, build_report_message


COMMAND = 'tenasapo_knowledge.management.commands.send_scheduled_chat_notifications'
NOW = datetime(2026, 11, 13, 12, tzinfo=JAPAN_TIMEZONE)


class ScheduledTransportTests(SimpleTestCase):
    def setUp(self):
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('live HTTP')))
        self.enterContext(patch('tenasapo_knowledge.notifications.urlopen', side_effect=AssertionError('live HTTP')))

    @override_settings(GOOGLE_CHAT_GAS_WEB_APP_URL='https://relay.example.test/exec',
                       GOOGLE_CHAT_WEBHOOK_URL='https://chat.example.test/')
    def test_gas_card_uses_dedicated_action_and_legacy_text_once(self):
        message = {'text': 'fallback list', 'cardsV2': [{'card': {'sections': []}}]}
        with patch('tenasapo_knowledge.notifications._request_json_post',
                   return_value=(200, '{"ok":true,"status":200}')) as post:
            self.assertTrue(send_scheduled_google_chat_message(message)['confirmed'])
        post.assert_called_once()
        data = json.loads(post.call_args.args[1])
        self.assertEqual(data, {'action': 'scheduled_card', 'payload': message, 'text': message['text']})

    @override_settings(GOOGLE_CHAT_GAS_WEB_APP_URL='', GOOGLE_CHAT_WEBHOOK_URL='https://chat.example.test/')
    def test_direct_card_and_text(self):
        for message in ('deadline', {'text': 'report', 'cardsV2': []}):
            with self.subTest(message=message), patch('tenasapo_knowledge.notifications._request_json_post',
                    return_value=(200, '{"name":"spaces/test/messages/test"}')) as post:
                send_scheduled_google_chat_message(message)
                self.assertEqual(json.loads(post.call_args.args[1]),
                                 {'text': message} if isinstance(message, str) else message)
                post.assert_called_once()

    @override_settings(GOOGLE_CHAT_GAS_WEB_APP_URL='https://relay.example.test/exec',
                       GOOGLE_CHAT_WEBHOOK_URL='https://chat.example.test/')
    def test_uncertain_responses_never_fall_back_or_expose_response(self):
        for response in ((200, 'secret body'), (200, '{"ok":true}'),
                         (200, '{"ok":false,"status":500}'), (503, '{}')):
            with self.subTest(response=response), patch('tenasapo_knowledge.notifications._request_json_post',
                                                       return_value=response) as post:
                with self.assertRaises(GoogleChatNotificationError) as error:
                    send_scheduled_google_chat_message('deadline')
                self.assertNotIn('secret', str(error.exception))
                post.assert_called_once()
        with patch('tenasapo_knowledge.notifications._request_json_post', side_effect=TimeoutError) as post:
            with self.assertRaises(GoogleChatNotificationError):
                send_scheduled_google_chat_message('deadline')
            post.assert_called_once()

    def test_migration_state_matches_schedule_model_without_database_access(self):
        loader = MigrationLoader(None)
        key = ('tenasapo_knowledge', '0082_chat_schedule_environment_revisions')
        historical = loader.project_state([key]).apps.get_model('tenasapo_knowledge', 'ChatNotificationSchedule')
        self.assertEqual({f.name for f in historical._meta.fields}, {f.name for f in Schedule._meta.fields})
        for field in Schedule._meta.fields:
            actual = field.deconstruct()[1:]
            expected = historical._meta.get_field(field.name).deconstruct()[1:]
            self.assertEqual(actual, expected, field.name)
        self.assertEqual(historical._meta.constraints, Schedule._meta.constraints)
        self.assertFalse(historical._meta.get_field('target_month').unique)
        self.assertIn(('tenasapo_knowledge', '0081_chat_notification_schedule'),
                      loader.disk_migrations[key].dependencies)

    def test_relay_does_not_log_raw_payload_and_has_size_guard(self):
        source = (Path(__file__).resolve().parent.parent / 'scripts/google_chat_notification_relay.gs').read_text(encoding='utf-8')
        self.assertIn("action === 'scheduled_card'", source)
        self.assertIn('getBytes().length >= 28000', source)
        self.assertNotIn("Logger.log('doPost start raw=", source)


@override_settings(DEBUG=True, GOOGLE_CHAT_SCHEDULED_SEND_ENABLED=True,
                   GOOGLE_CHAT_GAS_WEB_APP_URL='', GOOGLE_CHAT_WEBHOOK_URL='https://chat.example.test/')
class ScheduledBackendTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username='schedule-editor')
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('live HTTP')))
        self.enterContext(patch('requests.sessions.Session.request', side_effect=AssertionError('live HTTP')))
        self.http = self.enterContext(patch('tenasapo_knowledge.notifications._request_json_post',
                                          return_value=(200, '{"name":"spaces/test/messages/test"}')))
        self.enterContext(patch(COMMAND + '.timezone.now', return_value=NOW))

    def values(self, **overrides):
        return dict(target_month=date(2026, 11, 1), deadline=date(2026, 11, 30),
                    deadline_notice_at=NOW - timedelta(hours=1), report_notice_at=NOW + timedelta(days=1),
                    enabled=True, actor=self.actor, **overrides)

    def schedule(self, **overrides):
        values = self.values()
        values.update(overrides)
        return save_schedule(**values)

    def command(self, *args, **kwargs):
        output = StringIO()
        call_command('send_scheduled_chat_notifications', *args, stdout=output, **kwargs)
        return output.getvalue()

    def test_dry_run_json_has_no_writes_or_http(self):
        self.schedule(report_notice_at=NOW)
        before = (list(Schedule.objects.values()), list(Delivery.objects.values()))
        output = self.command('--json')
        self.assertIn('cardsV2', output)
        self.assertIn('dry-run', output)
        self.assertEqual(before, (list(Schedule.objects.values()), list(Delivery.objects.values())))
        self.http.assert_not_called()

    def test_send_requires_both_flags_and_disallows_payload_logging(self):
        self.schedule()
        with override_settings(GOOGLE_CHAT_SCHEDULED_SEND_ENABLED=False):
            self.command()
            with self.assertRaises(CommandError):
                self.command('--send')
        with self.assertRaises(CommandError):
            self.command('--send', '--json')
        self.assertFalse(Delivery.objects.exclude(state='pending').exists())
        self.http.assert_not_called()

    def test_duplicate_run_sends_once_and_actual_output_has_no_payload(self):
        self.schedule()
        output = self.command('--send') + self.command('--send')
        self.http.assert_called_once()
        delivery = Delivery.objects.get(kind='deadline')
        self.assertEqual((delivery.state, delivery.attempts), ('sent', 1))
        self.assertNotIn('投稿をお願いします', output)

    def test_dry_run_does_not_create_missing_delivery_rows(self):
        schedule = self.schedule()
        Delivery.objects.filter(schedule=schedule).delete()
        self.command('--dry-run')
        self.assertFalse(Delivery.objects.exists())
        self.http.assert_not_called()

    def test_sent_kind_remains_protected_after_date_edit(self):
        schedule = self.schedule()
        self.command('--send')
        revised = self.schedule(expected_revision=1, deadline_notice_at=NOW)
        self.command('--send')
        self.http.assert_called_once()
        self.assertEqual(Delivery.objects.get(schedule=revised, kind='deadline').state, 'sent')
        schedule.refresh_from_db()
        self.assertFalse(schedule.enabled)

    def test_stale_claim_becomes_unknown_and_no_retry(self):
        schedule = self.schedule()
        Delivery.objects.filter(schedule=schedule, kind='deadline').update(
            state='sending', attempts=1, claimed_at=NOW - timedelta(hours=1))
        self.command('--send')
        self.command('--send')
        self.assertEqual(Delivery.objects.get(schedule=schedule, kind='deadline').state, 'unknown')
        self.http.assert_not_called()

    def test_historical_attempt_blocks_bypassed_revision_without_copied_ledger(self):
        schedule = self.schedule()
        self.command('--send')
        revised = self.schedule(expected_revision=1)
        # Simulate another writer accidentally clearing the copied protection.
        Delivery.objects.filter(schedule=revised, kind='deadline').update(state='pending', attempts=0, sent_at=None)
        self.command('--send')
        self.http.assert_called_once()
        schedule.refresh_from_db()
        self.assertFalse(schedule.enabled)

    def test_environment_isolation_and_mismatched_flag(self):
        production = self.schedule()
        Schedule.objects.filter(pk=production.pk).update(environment='production')
        self.schedule()  # Same month may coexist in development.
        self.command('--send')
        self.http.assert_called_once()
        self.assertEqual(Delivery.objects.get(schedule=production, kind='deadline').attempts, 0)
        with self.assertRaises(CommandError):
            self.command('--send', '--environment', 'production')
        with self.assertRaises(ValidationError):
            self.schedule(environment='production')

    def test_expired_marked_failed_without_attempt_and_exact_48h_allowed(self):
        schedule = self.schedule(deadline_notice_at=NOW - timedelta(hours=48, seconds=1))
        before = list(Delivery.objects.values())
        self.assertIn('expired', self.command())
        self.assertEqual(list(Delivery.objects.values()), before)
        self.command('--send')
        delivery = Delivery.objects.get(schedule=schedule, kind='deadline')
        self.assertEqual((delivery.state, delivery.attempts), ('failed', 0))
        self.http.assert_not_called()
        other = self.schedule(target_month=date(2026, 12, 1), deadline_notice_at=NOW - timedelta(hours=48))
        self.command('--send')
        self.assertEqual(Delivery.objects.get(schedule=other, kind='deadline').state, 'sent')

    def test_timeout_unknown_never_retried_even_after_revision(self):
        schedule = self.schedule()
        self.http.side_effect = TimeoutError
        with self.assertRaises(CommandError):
            self.command('--send')
        next_version = self.schedule(expected_revision=schedule.revision)
        self.command('--send')
        self.http.assert_called_once()
        self.assertEqual(Delivery.objects.get(schedule=next_version, kind='deadline').state, 'unknown')

    def test_revision_actor_disables_old_and_preserves_every_terminal_kind(self):
        for index, state in enumerate(('sent', 'failed', 'unknown', 'pending')):
            with self.subTest(state=state):
                schedule = self.schedule(target_month=date(2027, index + 1, 1))
                Delivery.objects.filter(schedule=schedule, kind='deadline').update(state=state, attempts=1)
                newer = self.schedule(target_month=schedule.target_month, expected_revision=1,
                                      deadline_notice_at=NOW + timedelta(days=3))
                schedule.refresh_from_db()
                self.assertFalse(schedule.enabled)
                self.assertEqual((newer.revision, newer.updated_by), (2, self.actor))
                carried = Delivery.objects.get(schedule=newer, kind='deadline')
                self.assertEqual(carried.attempts, 1)
                self.assertEqual(carried.state, 'unknown' if state == 'pending' else state)
                self.assertEqual(Delivery.objects.get(schedule=newer, kind='report').attempts, 0)

    def test_stale_revision_inflight_and_database_unique_constraint(self):
        schedule = self.schedule()
        with self.assertRaises(ScheduleConflict):
            self.schedule()
        Delivery.objects.filter(schedule=schedule, kind='deadline').update(state='sending', attempts=1)
        with self.assertRaises(ScheduleConflict):
            self.schedule(expected_revision=1)
        self.assertEqual(Schedule.objects.count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Schedule.objects.create(environment='development', target_month=schedule.target_month,
                deadline=schedule.deadline, deadline_notice_at=NOW, report_notice_at=NOW,
                revision=2, enabled=True)

    def test_invalid_calendar_does_not_disable_previous_revision(self):
        schedule = self.schedule()
        with self.assertRaises(ValidationError):
            self.schedule(expected_revision=1, deadline_notice_at=NOW.replace(tzinfo=None))
        schedule.refresh_from_db()
        self.assertTrue(schedule.enabled)
        self.assertEqual(Schedule.objects.count(), 1)
        with self.assertRaises(ValidationError):
            self.schedule(target_month=date(2026, 12, 2))
        self.assertEqual(notification_datetime(date(2026, 11, 1)).hour, 9)

    def article(self, model=KnowledgeArticle, *, at=NOW, **overrides):
        values = dict(title='タイトル <script>& "quoted"', body='BODY_MUST_NEVER_BE_SENT',
                      created_by=self.actor, created_by_name='<b>投稿者</b>',
                      visible_to_customer=False, visible_to_systena=True)
        values.update(overrides)
        item = model.objects.create(**values)
        model.objects.filter(pk=item.pk).update(created_at=at)
        return item

    @override_settings(SITE_URL='https://nexus.example.test/base')
    def test_report_jst_boundaries_or_visibility_and_as_of(self):
        schedule = self.schedule(report_notice_at=NOW)
        start = datetime(2026, 11, 1, tzinfo=JAPAN_TIMEZONE)
        self.article(at=start, title='IN_MONTH_START')
        self.article(TipsArticle, at=NOW, title='IN_CUSTOMER', visible_to_customer=True,
                     visible_to_systena=False, is_approved=False, is_published=False)
        for at, title, overrides in (
            (start - timedelta(microseconds=1), 'EXCLUDED_PREVIOUS', {}),
            (NOW + timedelta(microseconds=1), 'EXCLUDED_FUTURE', {}),
            (NOW, 'EXCLUDED_HIDDEN', {'visible_to_systena': False}),
            (datetime(2026, 12, 1, tzinfo=JAPAN_TIMEZONE), 'EXCLUDED_NEXT', {}),
        ):
            self.article(at=at, title=title, **overrides)
        message = build_report_message(schedule, NOW + timedelta(days=40))
        wire = json.dumps(message, ensure_ascii=False)
        self.assertIn('全2件', message['text'])
        self.assertIn('IN_MONTH_START', wire)
        self.assertIn('IN_CUSTOMER', wire)
        self.assertNotIn('EXCLUDED', wire)
        self.assertNotIn('BODY_MUST_NEVER_BE_SENT', wire)
        self.assertIn('https://nexus.example.test/base/', wire)
        self.assertIn('投稿期限', build_deadline_message(schedule))

    def test_escaping_size_cap_and_omitted_count(self):
        schedule = self.schedule(report_notice_at=NOW)
        for index in range(45):
            self.article(title='<script>&"' * 20, created_by_name='<&"' * 50)
        message = build_report_message(schedule, NOW)
        wire = json.dumps(message).encode('utf-8')
        widgets = message['cardsV2'][0]['card']['sections'][0]['widgets']
        entries = [w['columns']['columnItems'] for w in widgets if 'columns' in w]
        self.assertLessEqual(len(entries), 40)
        self.assertLess(len(wire), MAX_PAYLOAD_BYTES)
        self.assertIn(f'省略{45 - len(entries)}件', message['text'])
        title_cell = entries[0][0]['widgets'][0]['decoratedText']
        author_cell = entries[0][1]['widgets'][0]['decoratedText']
        self.assertIn('&lt;script&gt;', title_cell['text'])
        self.assertNotIn('<script>', title_cell['text'])
        self.assertIn('&lt;', author_cell['text'])
        self.assertNotIn('BODY_MUST_NEVER_BE_SENT', message['text'])

    def test_invalid_or_missing_https_base_omits_links(self):
        schedule = self.schedule(report_notice_at=NOW)
        self.article()
        for base in ('', 'http://localhost:8000', 'https://user:pass@example.test',
                     'https://example.test/?q=x', 'https://example.test:bad', '//example.test'):
            with self.subTest(base=base), override_settings(SITE_URL=base):
                message = build_report_message(schedule, NOW)
                self.assertNotIn('<a href=', json.dumps(message))