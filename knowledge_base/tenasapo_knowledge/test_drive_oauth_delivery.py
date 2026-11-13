"""Offline OAuth delivery integration: real reservations, claims and completion."""

from datetime import date, datetime, timedelta
from io import StringIO
from importlib import import_module
import json
from unittest.mock import Mock, call, patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
import requests

from . import drive_oauth as oauth
from .chat_schedule import (
    ChatNotificationDelivery as Delivery, ChatNotificationSchedule as Schedule,
    JAPAN_TIMEZONE, save_reservation,
)
from .chat_schedule_delivery import claim_delivery, send_claimed_delivery
from .scheduled_chat_messages import build_report_message
from .test_drive_oauth_views import OfflineNetworkMixin


CORE = 'tenasapo_knowledge.chat_schedule_delivery'
SOURCE = 'tenasapo_knowledge.drive_oauth'
VIEW = 'tenasapo_knowledge.chat_schedule_settings'
COMMAND = 'tenasapo_knowledge.management.commands.send_scheduled_chat_notifications'
PDF_BUILDER = 'tenasapo_knowledge.scheduled_report_pdf.build_scheduled_report_pdf'
NOW = datetime(2026, 11, 13, 12, tzinfo=JAPAN_TIMEZONE)
PDF = b'%PDF-1.4\n% offline OAuth delivery fixture\n%%EOF\n'
PDF_URL = 'https://drive.google.com/file/d/offline_oauth_file_0123456789/view'
SECRET = 'offline-delivery-client-secret'
REFRESH = 'offline-delivery-refresh-token'


@override_settings(
    DEBUG=True, GOOGLE_CHAT_REPORT_PDF_ENABLED=True,
    GOOGLE_CHAT_SCHEDULED_SEND_ENABLED=True, GOOGLE_DRIVE_REPORT_BACKEND='oauth',
    GOOGLE_CHAT_GAS_WEB_APP_URL='', GOOGLE_CHAT_WEBHOOK_URL='https://chat.example.test/offline',
    GOOGLE_DRIVE_REPORT_UPLOAD_URL='', GOOGLE_DRIVE_REPORT_UPLOAD_SECRET='',
    GOOGLE_DRIVE_REPORT_FOLDER_ID='offline_oauth_folder_012345',
    SOCIALACCOUNT_PROVIDERS={'google': {'APP': {
        'client_id': 'offline-delivery-client', 'secret': SECRET}}},
    GOOGLE_WORKSPACE_DOMAIN='systena.co.jp', SITE_URL='https://nexus.example.test',
    ROOT_URLCONF='config.urls', ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False,
    SESSION_SAVE_EVERY_REQUEST=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class DriveOAuthDeliveryTests(OfflineNetworkMixin, TestCase):
    def setUp(self):
        super().setUp()
        # Load the command before patching its source sender. Otherwise its
        # imported alias would retain a guard after this test restores mocks.
        import_module(COMMAND)
        self.actor = get_user_model().objects.create_user('oauth-delivery-staff', is_staff=True)
        self.config = oauth.DriveUploadConfig(
            self.actor.pk, 'development', 'offline_oauth_folder_012345',
            'offline-delivery-client', SECRET, REFRESH)
        # Patch where the core actually looks up its imported names, not just
        # the source module (which would leave live aliases in the core).
        self.config_lookup = self.enterContext(patch(CORE + '.get_drive_upload_config', return_value=self.config))
        self.upload = self.enterContext(patch(CORE + '.upload_pdf_direct', return_value=PDF_URL))
        self.pdf_builder = self.enterContext(patch(PDF_BUILDER, return_value=PDF))
        self.forbidden_aliases = [self.enterContext(patch(
            target, side_effect=AssertionError('unexpected transport/backend alias')))
            for target in (
                CORE + '.get_upload_config', CORE + '.upload_report_pdf',
                SOURCE + '.upload_pdf_direct',
                'tenasapo_knowledge.report_drive_upload.get_upload_config',
                'tenasapo_knowledge.report_drive_upload.upload_report_pdf',
                'tenasapo_knowledge.notifications.send_scheduled_google_chat_message',
            )]
        self.addCleanup(self.assert_no_forbidden_aliases)
        self.target_url = Mock(return_value='https://chat.example.test/offline')
        self.sender = Mock(return_value={'confirmed': True})
        self.deadline_builder = Mock(return_value={'text': 'offline deadline'})
        self.report_builder = Mock(wraps=build_report_message)
        self.enterContext(patch(CORE + '.timezone.now', return_value=NOW))

    def assert_no_forbidden_aliases(self):
        for guard in self.forbidden_aliases:
            guard.assert_not_called()

    def reserve(self, kind='report'):
        return save_reservation(
            target_month=date(2026, 10, 1), kind=kind, deadline=date(2026, 10, 31),
            notice_at=NOW - timedelta(minutes=1), enabled=True, actor=self.actor)

    def snapshot(self):
        return (list(Schedule.objects.order_by('pk').values()),
                list(Delivery.objects.order_by('pk').values()))

    def send(self, schedule):
        delivery = claim_delivery(schedule.pk, schedule.kind, NOW)
        self.assertIsNotNone(delivery)
        self.assertEqual((delivery.state, delivery.attempts), ('sending', 1))
        self.assertEqual(delivery.claimed_at, NOW)
        state = send_claimed_delivery(
            delivery, NOW, target_url=self.target_url, deadline_builder=self.deadline_builder,
            report_builder=self.report_builder, sender=self.sender)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, state)
        return delivery

    def assert_terminal(self, schedule, delivery, state):
        self.assertEqual((delivery.state, delivery.attempts), (state, 1))
        self.assertEqual(delivery.claimed_at, NOW)
        self.assertEqual(delivery.sent_at, NOW if state == 'sent' else None)
        schedule.refresh_from_db()
        self.assertEqual(schedule.enabled, state != 'sent')
        self.assertEqual(schedule.updated_by_id, self.actor.pk)
        before = self.snapshot()
        self.assertIsNone(claim_delivery(schedule.pk, schedule.kind, NOW))
        self.assertIsNone(claim_delivery(schedule.pk, schedule.kind, NOW,
                                        manual=True, expected_revision=schedule.revision))
        self.assertEqual(self.snapshot(), before)
        for private in (SECRET, REFRESH, 'Traceback', 'private-timeout'):
            self.assertNotIn(private, delivery.last_error)

    def assert_no_pdf(self):
        self.pdf_builder.assert_not_called()
        self.upload.assert_not_called()

    def command(self, *arguments):
        output = StringIO()
        with patch(COMMAND + '.get_google_chat_target_url', self.target_url), patch(
                COMMAND + '.send_scheduled_google_chat_message', self.sender):
            call_command('send_scheduled_chat_notifications', *arguments, stdout=output)
        return output.getvalue()

    def test_oauth_report_uses_saved_actor_uploads_then_sends_real_message_link(self):
        schedule = self.reserve()
        self.assertEqual(schedule.updated_by_id, self.actor.pk)
        events = Mock()
        for name, mock in (('config', self.config_lookup), ('pdf', self.pdf_builder),
                           ('upload', self.upload), ('chat', self.sender)):
            events.attach_mock(mock, name)
        delivery = self.send(schedule)
        self.assert_terminal(schedule, delivery, 'sent')
        self.config_lookup.assert_called_once_with(self.actor)
        self.pdf_builder.assert_called_once_with(delivery.schedule, NOW)
        filename = (f'Nexus_月次レポート_2026-10_development_'
                    f'{schedule.event_id.hex}_r{schedule.revision}.pdf')
        self.upload.assert_called_once_with(PDF, filename, self.config)
        self.assertEqual(self.report_builder.call_args_list, [
            call(delivery.schedule, NOW), call(delivery.schedule, NOW, pdf_url=PDF_URL)])
        self.sender.assert_called_once()
        payload = self.sender.call_args.args[0]
        self.assertEqual(payload['text'], f'【Nexus】2026年10月 投稿状況\n<{PDF_URL}|月次レポート>')
        self.assertIn('cardsV2', payload)
        self.assertNotIn(PDF_URL, json.dumps(payload['cardsV2']))
        self.assertEqual([entry[0] for entry in events.mock_calls], ['config', 'pdf', 'upload', 'chat'])
        self.deadline_builder.assert_not_called()

    def test_manual_missing_connection_stops_before_claim_without_ledger_changes(self):
        schedule = self.reserve()
        client = Client()  # Normal client CSRF behavior is intentional here.
        client.force_login(self.actor)
        before = self.snapshot()
        with patch(VIEW + '.claim_delivery', wraps=claim_delivery) as claim, patch(
                VIEW + '.send_scheduled_google_chat_message', self.sender), patch(
                VIEW + '.get_drive_upload_config', wraps=oauth.get_drive_upload_config) as preflight:
            response = client.post(reverse('chat_schedule_send', args=[schedule.pk, 'report']), {
                'schedule_id': schedule.pk, 'expected_revision': schedule.revision,
                'kind': 'report', 'environment': 'development',
            }, secure=True)
        self.assertRedirects(response, reverse('chat_schedule_settings') + '?month=2026-10',
                             fetch_redirect_response=False)
        preflight.assert_called_once_with(self.actor)
        claim.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(any('今回は送信を試行していません' in str(m)
                            for m in get_messages(response.wsgi_request)))
        self.config_lookup.assert_not_called()
        self.assert_no_pdf()
        self.sender.assert_not_called()

    def test_automatic_missing_connection_is_failed_without_pdf_or_network_or_retry(self):
        schedule = self.reserve()
        # Real local OAuth preflight sees no connection. Keep the claim/send/
        # finish path real, including command failure reporting and skip logic.
        self.config_lookup.side_effect = oauth.get_drive_upload_config
        with self.assertRaisesMessage(CommandError, '自動再送は行いません'):
            self.command('--send')
        delivery = Delivery.objects.get(schedule=schedule, kind='report')
        self.assert_terminal(schedule, delivery, 'failed')
        self.assertTrue(delivery.last_error)
        self.config_lookup.assert_called_once_with(self.actor)
        before = self.snapshot()
        self.assertIn('skip 2026-10 report: failed', self.command('--send'))
        self.assertEqual(self.snapshot(), before)
        self.config_lookup.assert_called_once()
        self.assert_no_pdf()
        self.sender.assert_not_called()

    def test_dry_runs_do_not_claim_access_oauth_render_upload_or_send(self):
        self.reserve()
        before = self.snapshot()
        with patch(COMMAND + '.claim_delivery', wraps=claim_delivery) as claim:
            for arguments in ((), ('--dry-run',), ('--dry-run', '--json')):
                with self.subTest(arguments=arguments):
                    output = self.command(*arguments)
                    self.assertIn('mode=dry-run', output)
                    self.assertIn('2026-10', output)
                    self.assertEqual(self.snapshot(), before)
            claim.assert_not_called()
        self.config_lookup.assert_not_called()
        self.assert_no_pdf()
        self.sender.assert_not_called()

    def test_deadline_never_accesses_oauth_even_when_pdf_feature_is_enabled(self):
        schedule = self.reserve('deadline')
        delivery = self.send(schedule)
        self.assert_terminal(schedule, delivery, 'sent')
        self.config_lookup.assert_not_called()
        self.assert_no_pdf()
        self.report_builder.assert_not_called()
        self.deadline_builder.assert_called_once_with(delivery.schedule, NOW)
        self.sender.assert_called_once_with({'text': 'offline deadline'})

    def test_upload_timeout_is_unknown_no_chat_and_no_automatic_or_manual_reclaim(self):
        schedule = self.reserve()
        self.upload.side_effect = requests.Timeout('private-timeout ' + SECRET + REFRESH)
        with self.assertRaisesMessage(CommandError, '自動再送は行いません'):
            self.command('--send')
        delivery = Delivery.objects.get(schedule=schedule, kind='report')
        self.assert_terminal(schedule, delivery, 'unknown')
        self.assertTrue(delivery.last_error)
        self.pdf_builder.assert_called_once()
        self.upload.assert_called_once()
        self.config_lookup.assert_called_once_with(self.actor)
        before = self.snapshot()
        self.assertIn('skip 2026-10 report: unknown', self.command('--send'))
        self.assertEqual(self.snapshot(), before)
        self.upload.assert_called_once()
        self.sender.assert_not_called()

    @override_settings(GOOGLE_CHAT_REPORT_PDF_ENABLED=False)
    def test_disabled_feature_sends_report_without_oauth_or_pdf_link(self):
        schedule = self.reserve()
        delivery = self.send(schedule)
        self.assert_terminal(schedule, delivery, 'sent')
        self.config_lookup.assert_not_called()
        self.assert_no_pdf()
        self.sender.assert_called_once()
        self.assertIn('<https://drive.google.com/drive/folders/offline_oauth_folder_012345|月次レポート>',
                  self.sender.call_args.args[0]['text'])

    def test_pdf_build_failure_is_failed_before_upload_and_chat(self):
        schedule = self.reserve()
        self.pdf_builder.side_effect = ValueError('private-timeout ' + SECRET)
        delivery = self.send(schedule)
        self.assert_terminal(schedule, delivery, 'failed')
        self.config_lookup.assert_called_once_with(self.actor)
        self.pdf_builder.assert_called_once()
        self.upload.assert_not_called()
        self.sender.assert_not_called()