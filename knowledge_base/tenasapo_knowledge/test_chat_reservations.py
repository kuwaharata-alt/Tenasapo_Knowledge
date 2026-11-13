"""Independent event, migration and security regressions. No live transports."""
from datetime import date, datetime, timedelta
from importlib import import_module
from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .chat_schedule import (
    ChatNotificationDelivery as Delivery, ChatNotificationSchedule as Schedule,
    JAPAN_TIMEZONE, ScheduleConflict, save_reservation, save_schedule,
)
from .chat_schedule_delivery import can_attempt, claim_delivery, finish_delivery, send_claimed_delivery


NOW = datetime(2026, 11, 13, 12, tzinfo=JAPAN_TIMEZONE)


@override_settings(DEBUG=True, GOOGLE_CHAT_SCHEDULED_SEND_ENABLED=True,
                   ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
                   SESSION_SAVE_EVERY_REQUEST=False)
class IndependentReservationTests(TestCase):
    def setUp(self):
        self.guards = [self.enterContext(patch(name, side_effect=AssertionError('live network forbidden')))
                       for name in ('urllib.request.urlopen', 'tenasapo_knowledge.notifications.urlopen',
                                    'requests.sessions.Session.request', 'socket.socket.connect')]
        self.actor = get_user_model().objects.create_user('independent-staff', is_staff=True)
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.actor)
        self.url = reverse('chat_schedule_settings') + '?month=2026-11'

    def tearDown(self):
        for guard in self.guards:
            guard.assert_not_called()
        super().tearDown()

    def reserve(self, kind='deadline', at=NOW, **overrides):
        values = dict(target_month=date(2026, 11, 1), kind=kind, deadline=date(2026, 11, 30),
                      notice_at=at, enabled=True, actor=self.actor)
        values.update(overrides)
        return save_reservation(**values)

    def claim(self, schedule, **kwargs):
        return claim_delivery(schedule.pk, schedule.kind, NOW, manual=True,
                              expected_revision=schedule.revision, **kwargs)

    def send(self, schedule, *, sender=None):
        delivery = self.claim(schedule)
        self.assertIsNotNone(delivery)
        sender = sender or (lambda message: {'confirmed': True})
        return send_claimed_delivery(delivery, NOW, target_url=lambda: None,
                                     deadline_builder=lambda s, now: 'deadline',
                                     report_builder=lambda s, now: 'report', sender=sender)

    def snapshot(self):
        return list(Schedule.objects.order_by('pk').values()), list(Delivery.objects.order_by('pk').values())

    def test_multiple_deadlines_and_report_are_independent_in_either_send_order(self):
        for index, kinds in enumerate((('deadline', 'report'), ('report', 'deadline'))):
            month = date(2027, index + 1, 1)
            schedules = {kind: self.reserve(kind, target_month=month) for kind in kinds}
            for kind in kinds:
                original_other = Schedule.objects.filter(pk=schedules[kinds[1] if kind == kinds[0] else kinds[0]].pk).values().get()
                self.assertEqual(self.send(schedules[kind]), 'sent')
                self.assertFalse(Schedule.objects.get(pk=schedules[kind].pk).enabled)
                other = schedules[kinds[1] if kind == kinds[0] else kinds[0]]
                self.assertEqual(Schedule.objects.filter(pk=other.pk).values().get(), original_other)
                self.assertIsNone(self.claim(schedules[kind]))
            second = self.reserve(at=NOW + timedelta(days=1), target_month=month)
            self.assertEqual(self.send(second), 'sent')
            self.assertEqual(Delivery.objects.filter(schedule__target_month=month, state='sent').count(), 3)

    def test_sent_date_edit_does_not_replace_history_or_allow_duplicate_new_timestamp(self):
        first = self.reserve()
        self.send(first)
        before = self.snapshot()
        with self.assertRaises(ValidationError):
            self.reserve(at=NOW + timedelta(days=1), schedule_id=first.pk, expected_revision=first.revision)
        with self.assertRaises(ValidationError):
            self.reserve()
        self.assertEqual(self.snapshot(), before)
        second = self.reserve(at=NOW + timedelta(days=1))
        self.assertNotEqual(first.event_id, second.event_id)
        self.assertEqual(self.send(second), 'sent')
        self.assertEqual(Delivery.objects.get(schedule=first).attempts, 1)

    def test_pending_edit_and_archive_only_change_selected_event(self):
        first, other = self.reserve(), self.reserve('report')
        revised = self.reserve(at=NOW + timedelta(hours=1), schedule_id=first.pk,
                               expected_revision=first.revision, enabled=False)
        self.assertEqual(revised.event_id, first.event_id)
        self.assertFalse(Schedule.objects.get(pk=first.pk).enabled)
        self.assertTrue(Schedule.objects.get(pk=other.pk).enabled)
        self.assertIsNone(self.claim(first))
        self.assertIsNone(self.claim(revised))
        with self.assertRaises(ScheduleConflict):
            self.reserve(schedule_id=first.pk, expected_revision=first.revision)
        active = self.reserve(at=revised.notice_at, schedule_id=revised.pk, expected_revision=revised.revision)
        self.assertEqual(self.send(active), 'sent')

    def test_failed_unknown_and_dirty_rows_never_retry_same_event(self):
        for index, fields in enumerate((dict(state='failed'), dict(state='unknown'), dict(attempts=1),
                                        dict(claimed_at=NOW), dict(sent_at=NOW))):
            schedule = self.reserve(at=NOW + timedelta(minutes=index))
            schedule.deliveries.update(**fields)
            self.assertFalse(can_attempt(schedule, 'deadline'))
            self.assertIsNone(self.claim(schedule))
            with self.assertRaises(ValidationError):
                self.reserve(at=NOW + timedelta(days=2), schedule_id=schedule.pk,
                             expected_revision=schedule.revision)
            with self.assertRaises(ValidationError):
                self.reserve(at=schedule.notice_at)
        fresh = self.reserve(at=NOW + timedelta(days=3))
        self.assertIsNotNone(self.claim(fresh))

    def test_inflight_event_rejects_edit_but_does_not_block_other_event(self):
        deadline, report = self.reserve(), self.reserve('report')
        claimed = self.claim(deadline)
        self.assertIsNotNone(claimed)
        self.assertIsNone(self.claim(deadline))
        with self.assertRaises(ScheduleConflict):
            self.reserve(schedule_id=deadline.pk, expected_revision=deadline.revision)
        self.assertEqual(self.send(report), 'sent')
        self.assertEqual(finish_delivery(claimed, 'sent'), 'sent')

    def test_reset_latest_ledger_cannot_bypass_historical_guard(self):
        first = self.reserve()
        first.deliveries.update(state='unknown', attempts=1, claimed_at=NOW)
        revised = self.reserve(schedule_id=first.pk, expected_revision=first.revision, enabled=False)
        Schedule.objects.filter(pk=revised.pk).update(enabled=True)
        revised.deliveries.update(state='pending', attempts=0, claimed_at=None)
        self.assertIsNone(self.claim(revised))
        self.assertFalse(can_attempt(Schedule.objects.get(pk=revised.pk), 'deadline'))

    def test_stale_worker_cannot_overwrite_unknown_or_archive_other_event(self):
        first, report = self.reserve(), self.reserve('report')
        claim = self.claim(first)
        Delivery.objects.filter(pk=claim.pk).update(state='unknown')
        before = self.snapshot()
        self.assertEqual(finish_delivery(claim, 'sent'), 'unknown')
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(Schedule.objects.get(pk=report.pk).enabled)

    def test_scheduled_command_uses_only_matching_kind_and_shared_claim(self):
        first, second, report = self.reserve(), self.reserve(at=NOW - timedelta(hours=1)), self.reserve('report')
        command = 'tenasapo_knowledge.management.commands.send_scheduled_chat_notifications'
        with patch(command + '.timezone.now', return_value=NOW), patch(
                command + '.get_google_chat_target_url', return_value='mock'), patch(
                command + '.build_deadline_message', return_value='deadline') as deadline_builder, patch(
                command + '.build_report_message', return_value='report') as report_builder, patch(
                command + '.send_scheduled_google_chat_message', return_value={'confirmed': True}) as sender:
            def overlap(message):
                # Automatic and manual callers share the exact same CAS guard.
                current = first if message == 'report' else report
                if message == 'report':
                    self.assertIsNone(self.claim(current))
                return {'confirmed': True}
            sender.side_effect = overlap
            call_command('send_scheduled_chat_notifications', '--send', stdout=StringIO())
            call_command('send_scheduled_chat_notifications', '--send', stdout=StringIO())
            self.assertEqual(sender.call_count, 3)
            self.assertEqual(deadline_builder.call_count, 2)
            report_builder.assert_called_once()
        self.assertFalse(Schedule.objects.filter(pk__in=[first.pk, second.pk, report.pk], enabled=True).exists())

    def test_form_get_has_separate_forms_and_no_writes_and_posts_one_kind(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)
        self.assertContains(response, 'name="notice_at"', count=2)
        self.assertContains(response, 'value="deadline"')
        self.assertContains(response, 'value="report"')
        self.assertFalse(any(q['sql'].lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'} for q in queries))
        token = self.client.cookies['csrftoken'].value
        for kind in ('deadline', 'report'):
            response = self.client.post(self.url, dict(kind=kind, expected_revision=0, deadline='2026-11-30',
                                                     environment='development', notice_at='2026-11-13T12:00',
                                                     enabled='on', csrfmiddlewaretoken=token))
            self.assertEqual(response.status_code, 302)
        self.assertEqual(Schedule.objects.filter(enabled=True).count(), 2)
        self.assertEqual(Delivery.objects.count(), 2)

    def test_staff_csrf_environment_kind_revision_and_permission_are_required(self):
        schedule = self.reserve()
        url = reverse('chat_schedule_send', args=[schedule.pk, 'deadline'])
        self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.assertEqual(self.client.post(self.url, {}).status_code, 403)
        self.client.get(self.url)
        token = self.client.cookies['csrftoken'].value
        base = dict(schedule_id=schedule.pk, expected_revision=schedule.revision, kind='deadline',
                    environment='development', csrfmiddlewaretoken=token)
        before = self.snapshot()
        for field, value in (('environment', 'production'), ('kind', 'report'), ('expected_revision', 99)):
            data = dict(base, **{field: value})
            self.assertIn(self.client.post(url, data).status_code, (302, 400))
        for flag in (False, None, 1, 'True'):
            with override_settings(GOOGLE_CHAT_SCHEDULED_SEND_ENABLED=flag):
                self.assertIsNone(self.claim(schedule))
        with override_settings(DEBUG=False):
            self.assertIsNone(self.claim(schedule))
        self.assertIsNone(claim_delivery(schedule.pk, 'report', NOW, manual=True, expected_revision=schedule.revision))
        self.assertEqual(self.snapshot(), before)
        nonstaff = get_user_model().objects.create_user('nonstaff-super', is_superuser=True, is_staff=False)
        self.client.force_login(nonstaff)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        with self.assertRaises(ValidationError):
            self.reserve(at=NOW + timedelta(days=1), actor=nonstaff)

    def test_month_revision_uniqueness_and_save_conflicts_do_not_retry(self):
        first = self.reserve()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Schedule.objects.create(environment=first.environment, target_month=first.target_month,
                                    revision=first.revision, kind='report', deadline=first.deadline,
                                    deadline_notice_at=NOW, report_notice_at=NOW)
        before = self.snapshot()
        with patch.object(Schedule, 'save', side_effect=IntegrityError('conflict')):
            with self.assertRaises(ScheduleConflict):
                self.reserve(at=NOW + timedelta(days=1))
        self.assertEqual(self.snapshot(), before)

    def test_save_rejects_foreign_environment_invalid_selection_and_nonstaff_without_writes(self):
        self.client.get(self.url)
        token = self.client.cookies['csrftoken'].value
        data = dict(kind='deadline', environment='development', expected_revision=0,
                    deadline='2026-11-30', notice_at='2026-11-13T12:00', enabled='on',
                    csrfmiddlewaretoken=token)
        before = self.snapshot()
        for value in ('production', 'unassigned', ''):
            self.assertEqual(self.client.post(self.url, dict(data, environment=value)).status_code, 200)
            self.assertEqual(self.snapshot(), before)
        missing = dict(data)
        missing.pop('environment')
        self.assertEqual(self.client.post(self.url, missing).status_code, 200)
        with override_settings(DEBUG=False):
            self.assertEqual(self.client.post(self.url, data).status_code, 200)
        for month in ('bad', '2026-1', '9999-12', ''):
            response = self.client.post(reverse('chat_schedule_settings') + '?month=' + month, data)
            self.assertContains(response, '対象月が不正')
        response = self.client.post(self.url, dict(data, kind='invalid'))
        self.assertContains(response, '正しく選択してください')
        self.assertEqual(self.client.get(self.url + '&edit=invalid').status_code, 404)
        self.assertEqual(self.snapshot(), before)
        for superuser in (False, True):
            user = get_user_model().objects.create_user(f'save-nonstaff-{superuser}', is_superuser=superuser)
            self.client.force_login(user)
            self.assertEqual(self.client.post(self.url, data).status_code, 302)
            self.assertEqual(self.snapshot(), before)

    def test_new_reservation_cannot_reuse_any_historical_timestamp_after_pending_edit(self):
        original = self.reserve()
        revised = self.reserve(at=NOW + timedelta(hours=1), schedule_id=original.pk,
                               expected_revision=original.revision)
        before = self.snapshot()
        for at in (original.notice_at, revised.notice_at):
            with self.assertRaises(ValidationError):
                self.reserve(at=at)
        self.assertEqual(self.snapshot(), before)
        fresh = self.reserve(at=NOW + timedelta(hours=2))
        self.assertNotEqual(fresh.event_id, revised.event_id)
        self.assertTrue(can_attempt(fresh, 'deadline'))
        self.assertTrue(can_attempt(revised, 'deadline'))

    def test_new_and_combined_writers_cannot_mix_or_reset_existing_ledgers(self):
        combined = save_schedule(target_month=date(2026, 11, 1), deadline=date(2026, 11, 30),
                                 deadline_notice_at=NOW, report_notice_at=NOW, enabled=True, actor=self.actor)
        before = self.snapshot()
        with self.assertRaises(ScheduleConflict):
            self.reserve()
        self.assertEqual(self.snapshot(), before)
        independent = self.reserve(target_month=date(2026, 12, 1))
        before = self.snapshot()
        with self.assertRaises(ScheduleConflict):
            save_schedule(target_month=independent.target_month, deadline=combined.deadline,
                          deadline_notice_at=NOW, report_notice_at=NOW, enabled=True,
                          actor=self.actor, expected_revision=independent.revision)
        self.assertEqual(self.snapshot(), before)

    def test_data_migration_splits_without_losing_ledger_or_rearming_terminal_states(self):
        old_rows = []
        for month, state in enumerate(('sent', 'unknown', 'failed', 'sending', 'pending'), start=1):
            row = save_schedule(target_month=date(2026, month, 1), deadline=date(2026, month, 28),
                                deadline_notice_at=NOW, report_notice_at=NOW + timedelta(days=1),
                                enabled=True, actor=self.actor)
            row.deliveries.filter(kind='deadline').update(state=state, attempts=0 if state == 'pending' else 1)
            old_rows.append((row, state))
        ledger_before = {d['id']: d for d in Delivery.objects.values()}
        migration = import_module('tenasapo_knowledge.migrations.0083_independent_chat_reservations')
        class Editor:
            connection = connection
        migration.split_reservations(apps, Editor())
        self.assertEqual(Schedule.objects.count(), 10)
        self.assertEqual(Delivery.objects.count(), len(ledger_before))
        for d in Delivery.objects.values():
            old = ledger_before[d['id']]
            self.assertEqual({k: v for k, v in d.items() if k != 'schedule_id'},
                             {k: v for k, v in old.items() if k != 'schedule_id'})
            self.assertEqual(Schedule.objects.get(pk=d['schedule_id']).kind, d['kind'])
        for row, state in old_rows:
            deadline = Schedule.objects.get(pk=row.pk)
            report = Schedule.objects.get(target_month=row.target_month, kind='report')
            self.assertEqual(deadline.enabled, state == 'pending')
            self.assertTrue(report.enabled)
            self.assertEqual(report.created_at, row.created_at)
            if state != 'pending':
                self.assertIsNone(self.claim(deadline))
            self.assertTrue(can_attempt(report, 'report'))
            self.assertNotEqual(deadline.event_id, report.event_id)


class IndependentReservationMigrationTests(TransactionTestCase):
    def test_real_0082_to_0083_schema_and_history_on_isolated_memory_database(self):
        # Never roll back the workspace database (0083 is intentionally irreversible).
        # The complete historical schema is built in a separate in-memory database.
        original = connections['default']
        isolated = original.copy(alias='default')
        isolated.settings_dict['NAME'] = ':memory:'
        connections['default'] = isolated
        try:
            self.assertEqual(connections['default'].settings_dict['NAME'], ':memory:')
            executor = MigrationExecutor(isolated)
            old_target = [('tenasapo_knowledge', '0082_chat_schedule_environment_revisions')]
            executor.migrate(old_target)
            old_apps = executor.loader.project_state(old_target).apps
            OldSchedule = old_apps.get_model('tenasapo_knowledge', 'ChatNotificationSchedule')
            OldDelivery = old_apps.get_model('tenasapo_knowledge', 'ChatNotificationDelivery')
            expected = []
            for index, state in enumerate(('sent', 'failed', 'unknown', 'sending', 'pending', 'dirty'), 1):
                month = date(2026, index, 1)
                for revision in (1, 4):
                    row = OldSchedule.objects.create(
                        environment='development', target_month=month, revision=revision,
                        deadline=month.replace(day=28), deadline_notice_at=NOW, report_notice_at=NOW,
                        enabled=revision == 4)
                    for kind in ('deadline', 'report'):
                        values = dict(state='pending', attempts=0)
                        if kind == 'deadline' and revision == 1:
                            values.update(state='pending' if state == 'dirty' else state,
                                          attempts=0 if state == 'pending' else 1,
                                          claimed_at=None if state == 'pending' else NOW,
                                          sent_at=NOW if state == 'sent' else None,
                                          last_error='retained error' if state != 'pending' else '')
                        OldDelivery.objects.create(schedule=row, kind=kind, **values)
                    expected.append((row.pk, month, revision, row.created_at, row.updated_at, state))
            unassigned = OldSchedule.objects.create(
                environment='unassigned', target_month=date(2026, 1, 1), revision=1,
                deadline=date(2026, 1, 28), deadline_notice_at=NOW, report_notice_at=NOW, enabled=True)
            # Missing ledger rows must not invent a successful/attempted delivery.
            ledger_before = {row['id']: row for row in OldDelivery.objects.values()}
            new_target = [('tenasapo_knowledge', '0083_independent_chat_reservations')]
            executor = MigrationExecutor(isolated)
            executor.migrate(new_target)
            new_apps = executor.loader.project_state(new_target).apps
            NewSchedule = new_apps.get_model('tenasapo_knowledge', 'ChatNotificationSchedule')
            NewDelivery = new_apps.get_model('tenasapo_knowledge', 'ChatNotificationDelivery')
            self.assertEqual(NewSchedule.objects.count(), 26)
            self.assertEqual(NewDelivery.objects.count(), len(ledger_before))
            for ledger in NewDelivery.objects.values():
                prior = ledger_before[ledger['id']]
                self.assertEqual({k: v for k, v in ledger.items() if k != 'schedule_id'},
                                 {k: v for k, v in prior.items() if k != 'schedule_id'})
                owner = NewSchedule.objects.get(pk=ledger['schedule_id'])
                self.assertEqual(owner.kind, ledger['kind'])
                self.assertEqual(owner.target_month,
                                 next(item[1] for item in expected if item[0] == prior['schedule_id']))
            for pk, month, revision, created, updated, state in expected:
                deadline = NewSchedule.objects.get(pk=pk)
                report = NewSchedule.objects.get(environment='development', target_month=month,
                                                 kind='report', revision=4 + revision)
                self.assertEqual(deadline.enabled, revision == 4 and state == 'pending')
                self.assertEqual(report.enabled, revision == 4)
                for row in (deadline, report):
                    self.assertEqual((row.created_at, row.updated_at), (created, updated))
                self.assertNotEqual(deadline.event_id, report.event_id)
            self.assertFalse(NewSchedule.objects.filter(environment='unassigned', enabled=True).exists())
            self.assertEqual(NewSchedule.objects.get(pk=unassigned.pk).kind, 'deadline')
            self.assertEqual(NewSchedule.objects.values('event_id').distinct().count(), 14)
            self.assertEqual(NewSchedule.objects.filter(kind='').count(), 0)
            # Exercise the installed constraints, not just migration state metadata.
            enabled = NewSchedule.objects.filter(enabled=True).first()
            with self.assertRaises(IntegrityError), transaction.atomic():
                NewSchedule.objects.create(
                    environment=enabled.environment, target_month=enabled.target_month, revision=99,
                    event_id=enabled.event_id, kind=enabled.kind, enabled=True,
                    deadline=enabled.deadline, deadline_notice_at=NOW, report_notice_at=NOW)
        finally:
            isolated.close()
            connections['default'] = original
