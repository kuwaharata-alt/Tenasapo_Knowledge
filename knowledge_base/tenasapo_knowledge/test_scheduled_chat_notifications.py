"""Backend-only regressions; every HTTP entry point is blocked or mocked."""

from datetime import date, datetime, timedelta
from io import StringIO
import json
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase, override_settings

from . import scheduled_chat_messages
from .chat_schedule import (
    ChatNotificationDelivery as Delivery, ChatNotificationSchedule as Schedule,
    JAPAN_TIMEZONE, ScheduleConflict, notification_datetime, save_schedule,
)
from .models import KnowledgeArticle, PostingQuota, TipsArticle, UserProfile
from .notifications import GoogleChatNotificationError, send_scheduled_google_chat_message
from .scheduled_chat_messages import MAX_PAYLOAD_BYTES, build_deadline_message, build_report_message
from .views import CONTRIBUTOR_GROUP_NAME, SummaryView


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
        key = ('tenasapo_knowledge', '0083_independent_chat_reservations')
        historical = loader.project_state([key]).apps.get_model('tenasapo_knowledge', 'ChatNotificationSchedule')
        self.assertEqual({f.name for f in historical._meta.fields}, {f.name for f in Schedule._meta.fields})
        for field in Schedule._meta.fields:
            actual = field.deconstruct()[1:]
            expected = historical._meta.get_field(field.name).deconstruct()[1:]
            self.assertEqual(actual, expected, field.name)
        self.assertEqual(historical._meta.constraints, Schedule._meta.constraints)
        self.assertFalse(historical._meta.get_field('target_month').unique)
        self.assertIn(('tenasapo_knowledge', '0082_chat_schedule_environment_revisions'),
                      loader.disk_migrations[key].dependencies)

    def test_relay_does_not_log_raw_payload_and_has_size_guard(self):
        source = (Path(__file__).resolve().parent.parent / 'scripts/google_chat_notification_relay.gs').read_text(encoding='utf-8')
        self.assertIn("action === 'scheduled_card'", source)
        self.assertIn('getBytes().length >= 28000', source)
        self.assertNotIn("Logger.log('doPost start raw=", source)


@override_settings(DEBUG=True, GOOGLE_CHAT_SCHEDULED_SEND_ENABLED=True,
                   GOOGLE_DRIVE_REPORT_FOLDER_ID='',
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

    def member(self, name, uid, *, excluded=False, contributor=True):
        user = get_user_model().objects.create_user(username=f'member-{uid}')
        UserProfile.objects.create(user=user, display_name=name, uid=f'{uid:06}',
                                   exclude_from_analysis=excluded)
        if contributor:
            group, _ = Group.objects.get_or_create(name=CONTRIBUTOR_GROUP_NAME)
            user.groups.add(group)
        return user

    def quota(self, month=date(2026, 11, 1), *, faq=1, tips=1, alpha=1):
        return PostingQuota.objects.create(effective_month=month, faq_count=faq,
                                           tips_count=tips, alpha_count=alpha)

    def test_deadline_lists_only_deficient_names_one_per_line(self):
        schedule = self.schedule()
        self.quota()
        zero = self.member('ゼロ', 10)
        one_kind = self.member('種別不足', 20)
        alpha = self.member('共通不足', 30)
        achieved = self.member('達成者', 40)
        for user, faq, tips in ((one_kind, 3, 0), (alpha, 1, 1), (achieved, 1, 2)):
            for model, count in ((KnowledgeArticle, faq), (TipsArticle, tips)):
                for _ in range(count):
                    self.article(model, created_by=user, created_by_name='')
        with patch.object(scheduled_chat_messages, 'quota_progress',
                          wraps=scheduled_chat_messages.quota_progress) as progress:
            message = build_deadline_message(schedule, NOW)
        self.assertCountEqual([call.args[:2] for call in progress.call_args_list],
                              [(0, 0), (3, 0), (1, 1), (1, 2)])
        self.assertTrue(message.startswith('【Nexus】2026年11月 投稿期限のお知らせ\n'))
        self.assertIn('投稿数不足者: 3人', message)
        self.assertEqual(message.split('投稿数不足者: 3人\n', 1)[1].splitlines(),
                         [f'・{zero.knowledge_profile.display_name}', '・種別不足', '・共通不足'])
        for detail in ('不足:', '実績', '合計', ' | '):
            self.assertNotIn(detail, message)
        self.assertNotIn('達成者', message)
        self.assertNotIn('BODY_MUST_NEVER_BE_SENT', message)

    def test_deadline_target_month_deadline_jst_and_future_cutoff(self):
        from django.utils import timezone

        self.quota(faq=20, tips=0, alpha=0)
        user = self.member('境界', 10)
        schedule = self.schedule(deadline=date(2026, 11, 10))
        start = datetime(2026, 11, 1, tzinfo=JAPAN_TIMEZONE)
        end = datetime(2026, 11, 11, tzinfo=JAPAN_TIMEZONE)
        for at in (start - timedelta(microseconds=1), start, end - timedelta(microseconds=1),
                   end, NOW, NOW + timedelta(microseconds=1)):
            self.article(at=at, created_by=user, created_by_name='')
        # Current active timezone must not move the JST date boundaries.
        with timezone.override('UTC'), patch.object(
                scheduled_chat_messages, 'quota_progress',
                wraps=scheduled_chat_messages.quota_progress) as progress:
            message = build_deadline_message(schedule, NOW)
            self.assertEqual(message.split('投稿数不足者: 1人\n', 1)[1].splitlines(), ['・境界'])
            progress.assert_called_once_with(2, 0, {'faq': 20, 'tips': 0, 'alpha': 0, 'total': 20})
            progress.reset_mock()
            schedule.deadline = date(2026, 12, 5)  # Month end, not the later deadline.
            message = build_deadline_message(schedule, NOW)
            self.assertEqual(message.split('投稿数不足者: 1人\n', 1)[1].splitlines(), ['・境界'])
            progress.assert_called_once_with(4, 0, {'faq': 20, 'tips': 0, 'alpha': 0, 'total': 20})
            progress.reset_mock()
            self.article(at=datetime(2026, 12, 1, tzinfo=JAPAN_TIMEZONE),
                         created_by=user, created_by_name='')
            message = build_deadline_message(schedule, NOW + timedelta(days=40))
            self.assertEqual(message.split('投稿数不足者: 1人\n', 1)[1].splitlines(), ['・境界'])
            progress.assert_called_once_with(5, 0, {'faq': 20, 'tips': 0, 'alpha': 0, 'total': 20})
        self.assertIn('集計時点: 2026/11/13 12:00 JST', build_deadline_message(schedule, NOW))

    def test_deadline_uses_scheduled_month_not_execution_month_or_cumulative(self):
        self.quota(faq=9, tips=9, alpha=9)
        self.quota(date(2027, 1, 1), faq=1, tips=0, alpha=0)
        user = self.member('対象月', 10)
        self.article(created_by=user, created_by_name='')  # November, not January.
        schedule = self.schedule(target_month=date(2027, 1, 1), deadline=date(2027, 1, 31))
        with patch.object(scheduled_chat_messages, 'quota_progress',
                          wraps=scheduled_chat_messages.quota_progress) as progress:
            message = build_deadline_message(schedule, NOW)
        progress.assert_called_once_with(0, 0, {'faq': 1, 'tips': 0, 'alpha': 0, 'total': 1})
        self.assertTrue(message.startswith('【Nexus】2027年1月 投稿期限のお知らせ\n'))
        self.assertEqual(message.split('投稿数不足者: 1人\n', 1)[1].splitlines(), ['・対象月'])

    def test_deadline_reuses_member_visibility_saved_names_zero_and_order(self):
        schedule = self.schedule()
        self.quota(faq=10, tips=0, alpha=0)
        first = self.member('先頭', 10)
        second = self.member('保存名', 20)
        self.member('ゼロ', 30)
        self.member('保存名', 21)  # Existing same-name merging is preserved.
        for uid, name, options in ((40, '非表示', {'excluded': True}),
                                   (50, 'グループ外', {'contributor': False}),
                                   (60, 'AdMiN', {})):
            self.member(name, uid, **options)
        for customer, systena, saved in ((True, False, '保存名'), (False, True, first.username),
                                         (False, False, '保存名')):
            self.article(created_by=first, created_by_name=saved,
                         visible_to_customer=customer, visible_to_systena=systena,
                         visible_to_demo=True, is_published=False, is_approved=False)
        self.article(created_by=second, created_by_name='以前の名前')
        with patch.object(scheduled_chat_messages, 'quota_progress',
                          wraps=scheduled_chat_messages.quota_progress) as progress:
            message = build_deadline_message(schedule, NOW)
        self.assertEqual([call.args for call in progress.call_args_list],
                         [(1, 0, {'faq': 10, 'tips': 0, 'alpha': 0, 'total': 10}),
                          (1, 0, {'faq': 10, 'tips': 0, 'alpha': 0, 'total': 10}),
                          (0, 0, {'faq': 10, 'tips': 0, 'alpha': 0, 'total': 10})])
        self.assertIn('投稿数不足者: 3人', message)
        self.assertEqual(message.split('投稿数不足者: 3人\n', 1)[1].splitlines(),
                         ['・先頭', '・保存名', '・ゼロ'])
        for excluded in ('非表示', 'グループ外', 'AdMiN', '以前の名前'):
            self.assertNotIn(excluded, message)

    def test_deadline_distinguishes_unconfigured_no_members_and_all_achieved(self):
        schedule = self.schedule()
        self.assertIn('投稿目標が未設定', build_deadline_message(schedule, NOW))
        self.quota(faq=0, tips=0, alpha=0)
        self.assertIn('対象メンバーがいません', build_deadline_message(schedule, NOW))
        self.member('目標ゼロ', 10)
        self.assertIn('全員が投稿目標を達成', build_deadline_message(schedule, NOW))

    def test_deadline_card_has_bulleted_names_and_escaped_user_content(self):
        from .scheduled_chat_messages import build_deadline_notification

        schedule = self.schedule()
        self.quota()
        self.member('<b>氏名 & 確認</b>', 10)
        payload = build_deadline_notification(schedule, NOW)
        self.assertEqual(payload['text'], '\n'.join([
            '【Nexus】2026年11月 投稿期限のお知らせ',
            'Q&A・Tipsの投稿をお願いします。',
        ]))
        self.assertEqual(len(payload['cardsV2']), 1)
        card = payload['cardsV2'][0]['card']
        self.assertNotIn('header', card)
        self.assertEqual(len(card['sections']), 1)
        blocks = [widget['textParagraph']['text'] for widget in card['sections'][0]['widgets']]
        end = min(schedule.deadline, date(2026, 11, 30))
        self.assertEqual(blocks[0], f'投稿期限<br>　{schedule.deadline:%Y/%m/%d}')
        self.assertEqual(blocks[1], f'集計期間<br>　2026/11/1~{end.year}/{end.month}/{end.day}')
        self.assertEqual(blocks[2], '集計時点<br>　2026/11/13 12:00 JST')
        self.assertTrue(blocks[3].startswith('月間目標<br>　'))
        self.assertIn('件 ＋ α ', blocks[3])
        self.assertNotIn('基本目標超過分', blocks[3])
        self.assertEqual(blocks[-2], '不足者（1人）')
        self.assertIn('・&lt;b&gt;氏名 &amp; 確認&lt;/b&gt;', blocks[-1])
        self.assertNotIn('<b>', blocks[-1])
        self.assertNotIn('実績:', payload['text'])

    def test_deadline_default_now_and_naive_rejection(self):
        schedule = self.schedule()
        self.assertEqual(build_deadline_message(schedule), build_deadline_message(schedule, NOW))
        with self.assertRaisesRegex(ValueError, 'timezone aware'):
            build_deadline_message(schedule, NOW.replace(tzinfo=None))

    def test_deadline_enumerates_more_than_forty_without_truncating(self):
        schedule = self.schedule()
        self.quota()
        for uid in range(1, 46):
            self.member(f'対象{uid:02}', uid)
        message = build_deadline_message(schedule, NOW)
        self.assertIn('投稿数不足者: 45人', message)
        names = message.split('投稿数不足者: 45人\n', 1)[1].splitlines()
        self.assertEqual(len(names), 45)
        self.assertEqual(names[-1], '・対象45')
        for detail in ('不足:', '実績', '合計', ' | '):
            self.assertNotIn(detail, message)
        self.assertNotIn('省略', message)

    def test_deadline_command_passes_same_now_for_dry_run_and_send(self):
        schedule = self.schedule()
        with patch(COMMAND + '.build_deadline_message', return_value='test') as build:
            self.command('--dry-run')
            build.assert_called_once_with(schedule, NOW)
            build.reset_mock()
            self.command('--send')
            build.assert_called_once_with(schedule, NOW)

    def test_deadline_helper_explicit_filters_are_read_only_and_three_queries(self):
        user = self.member('集計', 10)
        self.article(created_by=user, created_by_name='')
        self.article(at=NOW + timedelta(microseconds=1), created_by=user, created_by_name='')
        with self.assertNumQueries(3):
            rows, totals = SummaryView.member_post_counts(
                from_date=date(2026, 11, 1), to_date=date(2026, 11, 30), as_of=NOW)
        self.assertEqual(rows[0]['faq_post_count'], 1)
        self.assertEqual(totals['post_count_total'], 1)

    def assert_report_layout(self, message, month, monthly, half, *, pdf_url=None):
        heading = f'【Nexus】{month.year}年{month.month}月 投稿状況'
        self.assertEqual(message['text'], heading + (
            f'\n<{pdf_url}|月次レポート>' if pdf_url is not None else ''))
        self.assertEqual(len(message['cardsV2']), 1)
        card = message['cardsV2'][0]['card']
        self.assertEqual(set(card), {'sections'})
        progress = sum(half) / 300 * 100
        self.assertEqual(card['sections'], [
            {'widgets': [{'textParagraph': {'text': (
                f'{month.month}月投稿数<br>　Q&amp;A：{monthly[0]}件<br>　Tips：{monthly[1]}件')}}]},
            {'widgets': [{'textParagraph': {'text': (
                f'半期投稿数<br>　進捗：{progress:.1f}％'
                f'<br>　Q&amp;A：{half[0]}件<br>　Tips：{half[1]}件')}}]},
        ])
        card_wire = json.dumps(card, ensure_ascii=False)
        for forbidden in ('http', 'buttonList', 'openLink', 'columns', 'decoratedText', '<a '):
            self.assertNotIn(forbidden, card_wire)
        wire = json.dumps(message, ensure_ascii=False)
        for forbidden in ('省略', 'タイトル', '投稿者', 'BODY_MUST_NEVER_BE_SENT'):
            self.assertNotIn(forbidden, wire)
        self.assertLess(len(json.dumps(message).encode('utf-8')), MAX_PAYLOAD_BYTES)

    def test_report_jst_boundaries_or_visibility_and_earlier_execution_or_notice(self):
        from django.utils import timezone

        schedule = self.schedule(report_notice_at=NOW)
        user = self.member('境界メンバー', 10)
        start = datetime(2026, 11, 1, tzinfo=JAPAN_TIMEZONE)
        self.article(at=start, created_by=user, created_by_name='')
        self.article(TipsArticle, at=NOW, created_by=user, created_by_name='',
                     visible_to_customer=True, visible_to_systena=False,
                     is_approved=False, is_published=False)
        for at, overrides in (
            (start - timedelta(microseconds=1), {}),
            (NOW + timedelta(microseconds=1), {}),
            (NOW, {'visible_to_systena': False, 'visible_to_demo': True}),
            (datetime(2026, 12, 1, tzinfo=JAPAN_TIMEZONE), {}),
        ):
            self.article(at=at, created_by=user, created_by_name='', **overrides)
        for execution, cutoff, expected in (
            (NOW + timedelta(days=40), NOW, (1, 1)),
            (start, start, (1, 0)),
        ):
            with self.subTest(execution=execution), timezone.override('UTC'), patch.object(
                    SummaryView, 'member_post_counts', wraps=SummaryView.member_post_counts) as counts:
                message = build_report_message(schedule, execution)
                self.assertEqual(timezone.get_current_timezone_name(), 'UTC')
                self.assertCountEqual([call.kwargs for call in counts.call_args_list], [
                    dict(from_date=date(2026, 11, 1), to_date=date(2026, 11, 30), as_of=cutoff),
                    dict(from_date=date(2026, 11, 1), to_date=date(2027, 3, 31), as_of=cutoff),
                ])
            self.assert_report_layout(message, schedule.target_month, expected, expected)
            self.assertNotIn('境界メンバー', json.dumps(message, ensure_ascii=False))

    def test_report_uses_pdf_member_roster_and_saved_name_scope(self):
        schedule = self.schedule(report_notice_at=NOW)
        first = self.member('集計メンバー', 10)
        second = self.member('保存名メンバー', 20)
        self.member('保存名メンバー', 21)  # Same-name members must not double count.
        self.member('ゼロメンバー', 30)
        hidden = self.member('非表示メンバー', 40, excluded=True)
        outsider = self.member('グループ外メンバー', 50, contributor=False)
        admin = self.member('AdMiN', 60)
        self.article(created_by=first, created_by_name='')
        self.article(created_by=first, created_by_name=first.username)
        self.article(TipsArticle, created_by=self.actor, created_by_name='保存名メンバー')
        self.article(created_by=second, created_by_name='以前の名前')
        for user in (hidden, outsider, admin, self.actor):
            self.article(created_by=user, created_by_name='')
        message = build_report_message(schedule, NOW)
        self.assert_report_layout(message, schedule.target_month, (2, 1), (2, 1))
        wire = json.dumps(message, ensure_ascii=False)
        for private in ('集計メンバー', '保存名メンバー', 'ゼロメンバー', '非表示メンバー',
                        'グループ外メンバー', 'AdMiN', '以前の名前', first.username, self.actor.username):
            self.assertNotIn(private, wire)

    def test_report_half_period_boundaries_and_later_months_before_cutoff(self):
        from django.utils import timezone

        schedule = self.schedule(report_notice_at=NOW)
        user = self.member('半期メンバー', 10)
        for model, at in (
            (KnowledgeArticle, datetime(2026, 4, 1, tzinfo=JAPAN_TIMEZONE) - timedelta(microseconds=1)),
            (KnowledgeArticle, datetime(2026, 4, 1, tzinfo=JAPAN_TIMEZONE)),
            (TipsArticle, datetime(2026, 11, 1, tzinfo=JAPAN_TIMEZONE) - timedelta(microseconds=1)),
            (KnowledgeArticle, datetime(2026, 11, 1, tzinfo=JAPAN_TIMEZONE)),
            (TipsArticle, NOW),
            (KnowledgeArticle, NOW + timedelta(microseconds=1)),
            (KnowledgeArticle, datetime(2027, 3, 31, 23, 59, 59, 999999, tzinfo=JAPAN_TIMEZONE)),
            (TipsArticle, datetime(2027, 4, 1, tzinfo=JAPAN_TIMEZONE)),
        ):
            self.article(model, at=at, created_by=user, created_by_name='')
        for month, cutoff, monthly, half in (
            # October counts toward April's half even though it is a later month.
            (date(2026, 4, 1), NOW, (1, 0), (1, 1)),
            (date(2026, 10, 1), NOW, (0, 1), (1, 1)),
            (date(2026, 11, 1), NOW, (1, 1), (1, 1)),
            # A future target month is empty monthly, not empty for the whole half.
            (date(2027, 1, 1), NOW, (0, 0), (1, 1)),
            (date(2027, 3, 1), datetime(2027, 4, 2, tzinfo=JAPAN_TIMEZONE), (1, 0), (3, 1)),
        ):
            with self.subTest(month=month), timezone.override('UTC'):
                schedule.target_month = month
                schedule.report_notice_at = cutoff
                message = build_report_message(schedule, cutoff + timedelta(days=1))
            self.assert_report_layout(message, month, monthly, half)

    def test_report_forty_five_member_posts_have_actual_totals_without_lists_or_truncation(self):
        schedule = self.schedule(report_notice_at=NOW)
        user = self.member('PRIVATE_MEMBER<&>' * 30, 10)
        for index in range(45):
            self.article(created_by=user, created_by_name='', title='PRIVATE_TITLE<script>&"' * 30)
        pdf_url = 'https://drive.google.com/file/d/' + 'a' * 200 + '/view'
        for url in (None, pdf_url):
            with self.subTest(pdf_url=url):
                message = build_report_message(schedule, NOW, pdf_url=url)
                self.assert_report_layout(message, schedule.target_month, (45, 0), (45, 0), pdf_url=url)
                wire = json.dumps(message, ensure_ascii=False)
                for private in ('PRIVATE_TITLE', 'PRIVATE_MEMBER', user.username, '<script>'):
                    self.assertNotIn(private, wire)

    def test_report_canonical_pdf_link_is_named_in_text_only(self):
        schedule = self.schedule(report_notice_at=NOW)
        for file_id in ('a' * 10, 'Ab_09-' * 20, 'Z' * 200):
            with self.subTest(file_id=file_id):
                pdf_url = f'https://drive.google.com/file/d/{file_id}/view'
                message = build_report_message(schedule, NOW, pdf_url=pdf_url)
                self.assert_report_layout(message, schedule.target_month, (0, 0), (0, 0), pdf_url=pdf_url)
                self.assertEqual(json.dumps(message).count(pdf_url), 1)
        message = build_report_message(schedule, NOW)
        self.assert_report_layout(message, schedule.target_month, (0, 0), (0, 0))
        self.assertNotIn('月次レポート', json.dumps(message, ensure_ascii=False))

    @override_settings(GOOGLE_CHAT_REPORT_PDF_ENABLED=False,
                       GOOGLE_DRIVE_REPORT_FOLDER_ID='offline_folder_0123456789')
    def test_report_links_drive_folder_without_uploading_pdf(self):
        schedule = self.schedule(report_notice_at=NOW)
        message = build_report_message(schedule, NOW)
        self.assertEqual(message['text'], '【Nexus】2026年11月 投稿状況\n'
                         '<https://drive.google.com/drive/folders/offline_folder_0123456789|月次レポート>')
        pdf_url = 'https://drive.google.com/file/d/offline_file_0123456789/view'
        uploaded = build_report_message(schedule, NOW, pdf_url=pdf_url)
        self.assertIn(f'<{pdf_url}|月次レポート>', uploaded['text'])
        self.assertNotIn('/folders/', uploaded['text'])

    def test_report_rejects_noncanonical_or_unsafe_pdf_urls(self):
        schedule = self.schedule(report_notice_at=NOW)
        valid = 'https://drive.google.com/file/d/offline_file_0123456789/view'
        for url in (
            '', 123, False, 'javascript:alert(1)', '//drive.google.com/file/d/offline_file_0123456789/view',
            valid.replace('https:', 'http:'), valid.replace('drive.google.com', 'evil.example'),
            valid.replace('drive.google.com', 'drive.google.com.evil.example'),
            valid.replace('drive.google.com', 'user:pass@drive.google.com'),
            valid.replace('drive.google.com', 'drive.google.com:443'),
            valid.replace('/offline_file_0123456789/', '/' + 'a' * 9 + '/'),
            valid.replace('/offline_file_0123456789/', '/' + 'a' * 201 + '/'),
            valid.replace('offline_file_0123456789', 'invalid%2Ffile_0123456789'),
            valid.replace('offline_file_0123456789', 'invalid.file_0123456789'),
            valid.replace('/view', '/edit'), valid + '/', valid + '?usp=sharing', valid + '#fragment',
            ' ' + valid, valid + ' ', valid + '\n', valid + '\r\n', valid + '\t',
            valid + '|偽リンク>', valid + '"><script>',
        ):
            with self.subTest(pdf_url=url), self.assertRaises(ValueError):
                build_report_message(schedule, NOW, pdf_url=url)