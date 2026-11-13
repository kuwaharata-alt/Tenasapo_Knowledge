"""Shared Django message toast rendering contracts."""

from django.contrib.messages import constants, Message
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class NotificationLayoutTests(SimpleTestCase):
    def test_message_levels_are_rendered_once_outside_main(self):
        levels = (
            (constants.SUCCESS, '完了', 'status'),
            (constants.INFO, 'お知らせ', 'status'),
            (constants.WARNING, 'ご確認ください', 'status'),
            (constants.ERROR, 'エラー', 'alert'),
        )
        for level, label, role in levels:
            with self.subTest(level=level):
                html = render_to_string('base.html', {
                    'messages': [Message(level, '通知テスト <script>')],
                })
                self.assertEqual(html.count('通知テスト &lt;script&gt;'), 1)
                self.assertIn(f'role="{role}"', html)
                self.assertIn(f'>{label}</strong>', html)
                self.assertIn('data-bs-delay="5000"', html)
                self.assertIn('data-bs-dismiss="toast"', html)
                self.assertLess(html.index('aria-label="通知"'), html.index('<main'))

    def test_no_message_has_no_notification_container(self):
        html = render_to_string('base.html', {'messages': []})
        self.assertNotIn('aria-label="通知"', html)