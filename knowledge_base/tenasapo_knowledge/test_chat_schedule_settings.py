"""Calendar integration tests; all outbound HTTP is blocked."""

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .chat_schedule import ChatNotificationSchedule as Schedule, JAPAN_TIMEZONE
from .chat_schedule_settings import ChatScheduleForm, ChatScheduleSettingsView


@override_settings(DEBUG=True, GOOGLE_CHAT_SCHEDULED_SEND_ENABLED=False)
class ChatScheduleSettingsTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user('calendar-staff', is_staff=True)
        self.url = reverse('chat_schedule_settings') + '?month=2026-11'
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('live HTTP')))
        self.enterContext(patch('requests.sessions.Session.request', side_effect=AssertionError('live HTTP')))

    def data(self, **overrides):
        return dict(expected_revision='0', deadline='2026-11-30',
                    deadline_notice_at='2026-11-25T09:00', report_notice_at='2026-12-01T10:30',
                    enabled='on', **overrides)

    def response(self, data=None, user=None, url=None):
        factory = RequestFactory()
        request = factory.get(url or self.url) if data is None else factory.post(url or self.url, data)
        request.user = user or self.staff
        request.session = {}
        request._messages = FallbackStorage(request)
        response = ChatScheduleSettingsView.as_view()(request)
        if hasattr(response, 'render'):
            response.render()
        return response

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

    def test_save_uses_jst_and_creates_delivery_history_without_send(self):
        response = self.response(self.data())
        self.assertEqual(response.status_code, 302)
        schedule = Schedule.objects.get()
        self.assertEqual(schedule.target_month, date(2026, 11, 1))
        self.assertEqual(timezone.localtime(schedule.deadline_notice_at, JAPAN_TIMEZONE).hour, 9)
        self.assertEqual(schedule.deliveries.count(), 2)
        self.assertTrue(all(d.attempts == 0 for d in schedule.deliveries.all()))
        response = self.response()
        self.assertContains(response, '第1版')
        self.assertContains(response, '未送信')
        self.assertContains(response, 'value="2026-12-01T10:30"')

    def test_edit_creates_revision_and_conflict_is_not_overwritten(self):
        self.response(self.data())
        stale = self.response(self.data())
        self.assertEqual(stale.status_code, 409)
        self.assertContains(stale, '再読込', status_code=409)
        data = self.data()
        data.update(expected_revision='1', enabled='')
        self.assertEqual(self.response(data).status_code, 302)
        self.assertEqual(Schedule.objects.count(), 2)
        self.assertFalse(Schedule.objects.filter(enabled=True).exists())

    def test_invalid_input_preserves_active_revision(self):
        self.response(self.data())
        data = self.data()
        data.update(expected_revision='1', report_notice_at='not-a-date')
        self.assertEqual(self.response(data).status_code, 200)
        self.assertEqual(Schedule.objects.count(), 1)
        self.assertTrue(Schedule.objects.get().enabled)

    def test_dates_are_jst_even_if_current_timezone_differs(self):
        with timezone.override('UTC'):
            form = ChatScheduleForm(self.data())
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data['deadline_notice_at'].utcoffset().total_seconds(), 9 * 3600)

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