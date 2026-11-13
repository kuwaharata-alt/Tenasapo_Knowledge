"""Calendar integration tests; all outbound HTTP is blocked."""

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .chat_schedule import ChatNotificationSchedule as Schedule, JAPAN_TIMEZONE, current_chat_environment
from .chat_schedule_settings import ChatScheduleForm, ChatScheduleSettingsView


@override_settings(DEBUG=True, GOOGLE_CHAT_SCHEDULED_SEND_ENABLED=False)
class ChatScheduleSettingsTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user('calendar-staff', is_staff=True)
        self.url = reverse('chat_schedule_settings') + '?month=2026-11'
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('live HTTP')))
        self.enterContext(patch('requests.sessions.Session.request', side_effect=AssertionError('live HTTP')))

    def data(self, **overrides):
        values = dict(kind='deadline', environment=current_chat_environment(), expected_revision='0',
                      deadline='2026-11-30', notice_at='2026-11-25T09:00', enabled='on')
        values.update(overrides)
        return values

    def response(self, data=None, user=None, url=None):
        factory = RequestFactory()
        request = factory.get(url or self.url) if data is None else factory.post(url or self.url, data)
        # Exercise the real decorator with a matching CSRF cookie/header.
        seed = factory.get(self.url)
        request.META['HTTP_X_CSRFTOKEN'] = get_token(seed)
        request.COOKIES['csrftoken'] = seed.META['CSRF_COOKIE']
        request.user = user or self.staff
        request.session = {}
        request._messages = FallbackStorage(request)
        response = ChatScheduleSettingsView.as_view()(request)
        if hasattr(response, 'render'):
            response.render()
        return response

    def test_monthly_report_has_no_deadline_and_can_save_without_one(self):
        response = self.response()
        forms = dict(response.context_data['reservation_forms'])
        report_form = next(form for form in forms.values() if form.initial.get('kind') == 'report')
        self.assertNotIn('deadline', report_form.fields)
        values = self.data(kind='report', notice_at='2026-12-01T10:30')
        del values['deadline']
        self.assertEqual(self.response(values).status_code, 302)
        schedule = Schedule.objects.get()
        self.assertEqual(schedule.kind, 'report')
        self.assertEqual(schedule.target_month, date(2026, 11, 1))
        self.assertContains(self.response(), '対象外')

    def test_calendar_get_is_read_only(self):
        response = self.response()
        self.assertContains(response, 'type="month"')
        self.assertContains(response, 'type="date"')
        self.assertContains(response, 'type="datetime-local"', count=2)
        self.assertContains(response, '無効（予約の保存のみ可能）')
        self.assertContains(response, '日時はすべて日本時間です。')
        self.assertContains(response, '本番（Azure Linux）')
        self.assertNotContains(response, 'この環境のWindows定期実行タスクも必要です')
        self.assertEqual(Schedule.objects.count(), 0)

    def test_reservation_explanation_is_inside_info_popover(self):
        response = self.response()
        self.assertContains(response, 'aria-label="通知予約の説明を表示"')
        self.assertContains(response, 'id="reservation-help" popover="auto"')
        html = response.content.decode()
        before, help_content = html.split('<div id="reservation-help"', 1)
        help_content, after = help_content.split('<div class="row g-4">', 1)
        for explanation in ('対象月と通知日時は独立しています。',
                            '期限とレポートは別々に保存・送信・アーカイブされます。'):
            self.assertIn(explanation, help_content)
            self.assertNotIn(explanation, before)
            self.assertNotIn(explanation, after)

    def test_save_uses_jst_and_creates_delivery_history_without_send(self):
        response = self.response(self.data())
        self.assertEqual(response.status_code, 302)
        schedule = Schedule.objects.get()
        self.assertEqual(schedule.target_month, date(2026, 11, 1))
        self.assertEqual(timezone.localtime(schedule.deadline_notice_at, JAPAN_TIMEZONE).hour, 9)
        self.assertEqual(schedule.deliveries.count(), 1)
        self.assertTrue(all(d.attempts == 0 for d in schedule.deliveries.all()))
        response = self.response()
        self.assertContains(response, '第1版')
        self.assertContains(response, '未送信')
        edit = self.response(url=self.url + f'&edit={schedule.pk}&kind=deadline')
        self.assertContains(edit, 'value="2026-11-25T09:00"')

    def test_edit_creates_revision_and_conflict_is_not_overwritten(self):
        self.response(self.data())
        stale = self.response(self.data(schedule_id=str(Schedule.objects.get().pk), expected_revision='0'))
        self.assertEqual(stale.status_code, 409)
        self.assertContains(stale, '再読込', status_code=409)
        data = self.data()
        data.update(schedule_id=str(Schedule.objects.get().pk), expected_revision='1', enabled='')
        self.assertEqual(self.response(data).status_code, 302)
        self.assertEqual(Schedule.objects.count(), 2)
        self.assertFalse(Schedule.objects.filter(enabled=True).exists())

    def test_history_separates_active_schedule_and_archived_revisions_without_writes(self):
        self.response(self.data())
        data = self.data()
        data['schedule_id'] = str(Schedule.objects.get().pk)
        data['expected_revision'] = '1'
        self.response(data)
        active = self.response()
        self.assertContains(active, '第2版')
        self.assertNotContains(active, '第1版')
        self.assertEqual(active.context_data['active_schedule_count'], 1)
        self.assertEqual(active.context_data['archived_schedule_count'], 1)
        archived = self.response(url=self.url + '&history=archive')
        self.assertContains(archived, '第1版')
        self.assertNotContains(archived, '第2版')
        self.assertContains(archived, '無効／旧版')
        self.assertContains(archived, 'name="history" value="archive"')
        self.assertEqual(Schedule.objects.count(), 2)
        self.assertEqual(sum(row.deliveries.count() for row in Schedule.objects.all()), 2)

    def test_disabled_latest_schedule_is_archived_too(self):
        data = self.data()
        data['enabled'] = ''
        self.response(data)
        self.assertContains(self.response(), 'この月の有効な予約はありません。')
        archived = self.response(url=self.url + '&history=archive')
        self.assertContains(archived, '第1版')
        self.assertEqual(archived.context_data['archived_schedule_count'], 1)

    def test_invalid_input_preserves_active_revision(self):
        self.response(self.data())
        data = self.data()
        data.update(expected_revision='1', notice_at='not-a-date')
        self.assertEqual(self.response(data).status_code, 200)
        self.assertEqual(Schedule.objects.count(), 1)
        self.assertTrue(Schedule.objects.get().enabled)

    def test_dates_are_jst_even_if_current_timezone_differs(self):
        with timezone.override('UTC'):
            form = ChatScheduleForm(self.data())
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data['notice_at'].utcoffset().total_seconds(), 9 * 3600)

    def test_other_environment_is_not_visible_and_can_save_independently(self):
        self.response(self.data())
        with override_settings(DEBUG=False):
            response = self.response()
            self.assertNotContains(response, '第1版')
            self.assertContains(response, 'Azure側の定期ジョブ')
            self.assertContains(response, '本番には不要です')
            self.assertEqual(self.response(self.data()).status_code, 302)
        self.assertEqual(set(Schedule.objects.values_list('environment', flat=True)), {'production', 'development'})

    def test_nonstaff_cannot_read_or_save(self):
        user = get_user_model().objects.create_user('calendar-member')
        for data in (None, self.data()):
            response = self.response(data, user=user)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, reverse('article_list'))
        self.assertEqual(Schedule.objects.count(), 0)

    def test_csrf_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        self.assertEqual(client.post(self.url, self.data()).status_code, 403)
        self.assertEqual(Schedule.objects.count(), 0)

    def test_invalid_month_is_safe_and_does_not_write(self):
        for raw in ('2026-1', '9999-12', 'no', '//example.test'):
            response = self.response(url=reverse('chat_schedule_settings') + '?month=' + raw)
            self.assertEqual(response.status_code, 200)
        self.assertFalse(Schedule.objects.exists())