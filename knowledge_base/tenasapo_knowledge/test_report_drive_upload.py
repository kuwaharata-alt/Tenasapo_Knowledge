"""Offline Drive upload protocol and real reservation-ledger regressions.

Only the HTTP boundary, PDF renderer and external Chat sender are replaced;
reservation saving, claiming, completion and report-card construction stay real.
All credentials and IDs below are dedicated, non-production test values.
"""

import base64
from datetime import date, datetime, timedelta
import hashlib
import hmac
from io import StringIO
import json
from unittest.mock import Mock, call, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import report_drive_upload as upload
from .chat_schedule import (
    ChatNotificationDelivery as Delivery,
    ChatNotificationSchedule as Schedule,
    JAPAN_TIMEZONE,
    save_reservation,
)
from .chat_schedule_delivery import claim_delivery, send_claimed_delivery
from .scheduled_chat_messages import build_report_message


SOURCE = 'tenasapo_knowledge.report_drive_upload'
DELIVERY = 'tenasapo_knowledge.chat_schedule_delivery'
PDF_BUILDER = 'tenasapo_knowledge.scheduled_report_pdf.build_scheduled_report_pdf'
COMMAND = 'tenasapo_knowledge.management.commands.send_scheduled_chat_notifications'
VIEW = 'tenasapo_knowledge.chat_schedule_settings'
UPLOAD_URL = 'https://script.google.com/macros/s/offline_test_deployment/exec'
UPLOAD_SECRET = 'offline-drive-only-secret-not-for-production-123456'
FOLDER_ID = 'offline_folder_0123456789'
FILE_ID = 'offline_file_0123456789'
PDF_URL = f'https://drive.google.com/file/d/{FILE_ID}/view'
PDF = b'%PDF-1.4\n% offline fixture\n%%EOF\n'
FILENAME = 'Nexus_月次レポート_2026-10.pdf'
LEAK = 'private-upstream-error-do-not-disclose'
NOW = datetime(2026, 11, 13, 12, tzinfo=JAPAN_TIMEZONE)
TEST_SETTINGS = dict(
    DEBUG=True,
    GOOGLE_CHAT_REPORT_PDF_ENABLED=True,
    GOOGLE_DRIVE_REPORT_BACKEND='gas',
    GOOGLE_CHAT_SCHEDULED_SEND_ENABLED=True,
    GOOGLE_DRIVE_REPORT_UPLOAD_URL=UPLOAD_URL,
    GOOGLE_DRIVE_REPORT_UPLOAD_SECRET=UPLOAD_SECRET,
    GOOGLE_DRIVE_REPORT_FOLDER_ID=FOLDER_ID,
    GOOGLE_CHAT_GAS_WEB_APP_URL='',
    GOOGLE_CHAT_WEBHOOK_URL='https://chat.example.test/offline-only',
    GOOGLE_CHAT_GAS_SECRET='separate-offline-chat-secret-1234567890',
    SITE_URL='https://nexus.example.test',
    ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False,
    SESSION_SAVE_EVERY_REQUEST=False,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)


class OfflineNetworkMixin:
    """Assert guard calls as well: production may catch AssertionError."""

    def setUp(self):
        super().setUp()
        self.network_guards = []
        for target in (
            'urllib.request.urlopen',
            SOURCE + '.urlopen',
            'tenasapo_knowledge.notifications.urlopen',
            'requests.sessions.Session.request',
            'http.client.HTTPConnection.connect',
            'http.client.HTTPSConnection.connect',
            'socket.create_connection',
            'socket.socket.connect',
            'socket.socket.connect_ex',
        ):
            guard = self.enterContext(patch(
                target, side_effect=AssertionError('live network forbidden')))
            self.network_guards.append(guard)
        # Cleanups run even when setUp or a test fails, before mocks are undone.
        self.addCleanup(self.assert_no_network)

    def assert_no_network(self):
        for guard in self.network_guards:
            guard.assert_not_called()

    def assert_no_leak(self, value):
        for private in (UPLOAD_SECRET, UPLOAD_URL, FOLDER_ID, LEAK, 'Traceback'):
            self.assertNotIn(private, value)


@override_settings(**TEST_SETTINGS)
class ReportDriveUploadTests(OfflineNetworkMixin, SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.config = upload.get_upload_config()
        # Replace the imported alias, not urllib.request.urlopen. The lower
        # network guards remain armed during every actual uploader invocation.
        self.http = self.enterContext(patch(SOURCE + '.urlopen'))
        self.http.side_effect = self.confirmed_response
        self.responses = []

    def confirmed_response(self, request, *, timeout):
        envelope = json.loads(request.data)
        payload = json.loads(envelope['payload'])
        data = dict(ok=True, action=upload.ACTION,
                    request_id=payload['request_id'], folder_id=FOLDER_ID,
                    file_id=FILE_ID)
        return self.response(data)

    def response(self, data, status=200, *, raw=None):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.getcode.return_value = status
        response.read.return_value = (
            json.dumps(data).encode('utf-8') if raw is None else raw)
        self.responses.append(response)
        return response

    def test_hmac_envelope_posts_exact_pdf_and_returns_canonical_drive_link(self):
        with patch(SOURCE + '.time.time', return_value=1794538800.75):
            result = upload.upload_report_pdf(PDF, FILENAME, self.config)
        self.assertEqual(result, PDF_URL)
        self.http.assert_called_once()
        request = self.http.call_args.args[0]
        self.assertEqual(self.http.call_args.kwargs, {'timeout': 60})
        self.assertEqual(request.full_url, UPLOAD_URL)
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Content-type'),
                         'application/json; charset=UTF-8')
        envelope = json.loads(request.data.decode('utf-8'))
        self.assertEqual(set(envelope), {'payload', 'signature'})
        self.assertIsInstance(envelope['payload'], str)
        serialized = envelope['payload']
        payload = json.loads(serialized)
        self.assertEqual(set(payload), {
            'action', 'timestamp', 'request_id', 'filename', 'folder_id', 'pdf_base64'})
        self.assertEqual(payload['action'], 'upload_reserved_report_pdf')
        self.assertEqual(payload['timestamp'], 1794538800)
        self.assertRegex(payload['request_id'], r'^[0-9a-f]{32}$')
        self.assertEqual(payload['filename'], FILENAME)
        self.assertEqual(payload['folder_id'], FOLDER_ID)
        self.assertEqual(base64.b64decode(payload['pdf_base64'], validate=True), PDF)
        self.assertEqual(payload['pdf_base64'], base64.b64encode(PDF).decode('ascii'))
        self.assertEqual(serialized, json.dumps(payload, ensure_ascii=True, separators=(',', ':')))
        expected = hmac.new(UPLOAD_SECRET.encode(), serialized.encode(), hashlib.sha256).hexdigest()
        self.assertTrue(hmac.compare_digest(envelope['signature'], expected))
        self.assertNotIn(UPLOAD_SECRET, request.data.decode())
        self.responses[0].read.assert_called_once_with(4096)
        self.responses[0].__exit__.assert_called_once()

    def test_each_invocation_uses_a_fresh_request_id(self):
        for _ in range(2):
            self.assertEqual(upload.upload_report_pdf(PDF, FILENAME, self.config), PDF_URL)
        ids = [json.loads(json.loads(args.args[0].data)['payload'])['request_id']
               for args in self.http.call_args_list]
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(self.http.call_count, 2)

    def test_invalid_pdf_is_rejected_before_network(self):
        for pdf in (None, '', '%PDF-1.4', bytearray(PDF), b'', b'not a PDF',
                    b'\n%PDF-1.4', b'%PDF-' + b'x' * upload.MAX_PDF_BYTES):
            with self.subTest(type=type(pdf).__name__, size=len(pdf) if pdf is not None else None):
                with self.assertRaises(ValueError) as caught:
                    upload.upload_report_pdf(pdf, FILENAME, self.config)
                self.assert_no_leak(str(caught.exception))
        self.http.assert_not_called()

    def test_pdf_size_limit_is_inclusive(self):
        pdf = b'%PDF-' + b'x' * (upload.MAX_PDF_BYTES - 5)
        self.assertIsNone(upload.validate_pdf(pdf))
        with self.assertRaises(ValueError):
            upload.validate_pdf(pdf + b'x')
        self.http.assert_not_called()

    def test_invalid_filenames_are_rejected_before_network(self):
        filenames = (None, 123, '', '.pdf', 'report.PDF', 'report.txt',
                     ' report.pdf', 'report.pdf ', '../report.pdf', 'dir/report.pdf',
                     'dir\\report.pdf', 'a' * 177 + '.pdf')
        filenames += tuple('report' + char + '.pdf' for char in (
            '\x00', '\n', '\r', '\t', '\x1f', '\x7f', '\x85', '\x9f', '<', '>', ':', '"', '|', '?', '*'))
        for filename in filenames:
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                upload.upload_report_pdf(PDF, filename, self.config)
        self.http.assert_not_called()

    def test_filename_length_boundaries_and_unicode_are_accepted(self):
        for filename in ('a.pdf', 'a' * 176 + '.pdf', FILENAME):
            with self.subTest(filename=filename):
                self.http.reset_mock()
                self.assertEqual(upload.upload_report_pdf(PDF, filename, self.config), PDF_URL)
                self.http.assert_called_once()

    def test_unconfirmed_responses_are_rejected_without_leaks_or_retries(self):
        cases = [
            ('http', status, {}) for status in (199, 300, 400, 403, 500, 503)
        ] + [
            ('ok', 200, {'ok': value}) for value in (False, None, 1, 'true')
        ] + [
            ('action', 200, {'action': value}) for value in (None, '', 'send_chat', LEAK)
        ] + [
            ('folder', 200, {'folder_id': value}) for value in (None, '', 'other_folder_012345')
        ] + [
            ('request', 200, {'request_id': value}) for value in (None, '', '0' * 32)
        ] + [
            ('file', 200, {'file_id': value}) for value in (
                None, 123, '', 'short', 'x' * 201, 'contains/slash',
                'contains space', 'https://drive.google.com/file/d/forged/view',
                'file_0123456789\n', '<script>private</script>')
        ]
        for label, status, changes in cases:
            with self.subTest(label=label, status=status, changes=changes):
                self.http.reset_mock()

                def rejected(request, *, timeout):
                    payload = json.loads(json.loads(request.data)['payload'])
                    data = dict(ok=True, action=upload.ACTION, request_id=payload['request_id'],
                                folder_id=FOLDER_ID, file_id=FILE_ID,
                                error=LEAK + UPLOAD_SECRET)
                    data.update(changes)
                    return self.response(data, status)

                self.http.side_effect = rejected
                self.assert_upload_unconfirmed()

    def assert_upload_unconfirmed(self):
        with self.assertRaises(RuntimeError) as caught:
            upload.upload_report_pdf(PDF, FILENAME, self.config)
        self.http.assert_called_once()
        self.assertEqual(str(caught.exception), 'PDFのDrive保存結果を確認できません。再試行しません。')
        self.assert_no_leak(str(caught.exception))
        self.assert_no_leak(repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_malformed_and_nonobject_responses_never_retry(self):
        for raw in (b'not-json', b'', b'\xff', b'null', b'[]', b'1', b'"ok"', b'{}'):
            with self.subTest(raw=raw):
                self.http.reset_mock()
                self.http.side_effect = lambda request, timeout: self.response(None, raw=raw)
                self.assert_upload_unconfirmed()

    def test_transport_and_response_read_errors_never_retry_or_leak(self):
        for stage in ('open', 'read', 'status', 'enter', 'exit'):
            with self.subTest(stage=stage):
                self.http.reset_mock()
                error = TimeoutError(LEAK + UPLOAD_SECRET + UPLOAD_URL)
                if stage == 'open':
                    self.http.side_effect = error
                else:
                    response = self.response({})
                    method = {'read': response.read, 'status': response.getcode,
                              'enter': response.__enter__, 'exit': response.__exit__}[stage]
                    method.side_effect = error
                    self.http.side_effect = None
                    self.http.return_value = response
                self.assert_upload_unconfirmed()

    def test_invalid_dedicated_configuration_never_calls_network(self):
        cases = [
            {'GOOGLE_DRIVE_REPORT_UPLOAD_URL': value} for value in (
                '', 'http://script.google.com/macros/s/test/exec',
                'https://evil.example/macros/s/test/exec',
                'https://script.google.com.evil.example/macros/s/test/exec',
                'https://user:pass@script.google.com/macros/s/test/exec',
                UPLOAD_URL + '?token=private', UPLOAD_URL + '#fragment',
                UPLOAD_URL.replace('/exec', '/dev'),
                'https://script.google.com/macros/s//exec')
        ] + [
            {'GOOGLE_DRIVE_REPORT_UPLOAD_SECRET': value} for value in ('', 'x' * 31)
        ] + [
            {'GOOGLE_DRIVE_REPORT_FOLDER_ID': value} for value in (
                '', 'short', 'x' * 201, 'folder/0123456789',
                'https://drive.google.com/drive/folders/offline_folder', ' folder_0123456789')
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides), override_settings(**overrides):
                with self.assertRaises(ImproperlyConfigured) as caught:
                    upload.get_upload_config()
                self.assert_no_leak(str(caught.exception))
        self.http.assert_not_called()

    def test_configuration_uses_only_dedicated_drive_settings(self):
        self.assertEqual((self.config.url, self.config.secret, self.config.folder_id),
                         (UPLOAD_URL, UPLOAD_SECRET, FOLDER_ID))
        with override_settings(GOOGLE_DRIVE_REPORT_UPLOAD_SECRET=''):
            # A configured Chat transport must not supply a fallback secret.
            with self.assertRaises(ImproperlyConfigured):
                upload.get_upload_config()
        self.http.assert_not_called()

    def test_pdf_feature_flag_requires_literal_true(self):
        self.assertTrue(upload.report_pdf_enabled())
        for value in (False, None, 0, 1, 'True', 'true'):
            with self.subTest(value=value), override_settings(GOOGLE_CHAT_REPORT_PDF_ENABLED=value):
                self.assertFalse(upload.report_pdf_enabled())
        self.http.assert_not_called()


@override_settings(**TEST_SETTINGS)
class ReportDriveDeliveryTests(OfflineNetworkMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.actor = get_user_model().objects.create_user('offline-drive-staff', is_staff=True)
        self.pdf_builder = self.enterContext(patch(PDF_BUILDER, return_value=PDF))
        # Cover both imported aliases. Delivery currently calls its own alias;
        # neither path may escape to the real uploader in integration tests.
        self.upload = self.enterContext(patch(DELIVERY + '.upload_report_pdf', return_value=PDF_URL))
        self.source_upload = self.enterContext(patch(SOURCE + '.upload_report_pdf', return_value=PDF_URL))
        self.target_url = Mock(return_value='https://chat.example.test/offline-only')
        self.sender = Mock(return_value={'confirmed': True})
        self.deadline_builder = Mock(return_value={'text': 'offline deadline'})
        self.report_builder = Mock(wraps=build_report_message)

    def reserve(self, kind='report', **overrides):
        values = dict(target_month=date(2026, 10, 1), kind=kind,
                      deadline=date(2026, 10, 31), notice_at=NOW - timedelta(minutes=1),
                      enabled=True, actor=self.actor)
        values.update(overrides)
        return save_reservation(**values)

    def send(self, schedule):
        delivery = claim_delivery(schedule.pk, schedule.kind, NOW)
        self.assertIsNotNone(delivery)
        self.assertEqual((delivery.state, delivery.attempts), ('sending', 1))
        state = send_claimed_delivery(
            delivery, NOW, target_url=self.target_url,
            deadline_builder=self.deadline_builder, report_builder=self.report_builder,
            sender=self.sender)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, state)
        self.assertEqual(delivery.attempts, 1)
        return delivery

    def assert_terminal(self, schedule, delivery, state):
        self.assertEqual(delivery.state, state)
        self.assertIsNotNone(delivery.claimed_at)
        self.assertEqual(delivery.sent_at is not None, state == 'sent')
        self.assertIsNone(claim_delivery(schedule.pk, schedule.kind, NOW))
        self.assertIsNone(claim_delivery(schedule.pk, schedule.kind, NOW,
                                        manual=True, expected_revision=schedule.revision))
        schedule.refresh_from_db()
        self.assertEqual(schedule.enabled, state != 'sent')
        self.assertEqual(schedule.updated_by_id, self.actor.pk)
        self.assert_no_leak(delivery.last_error)
        self.source_upload.assert_not_called()

    def assert_no_pdf_or_upload(self):
        self.pdf_builder.assert_not_called()
        self.upload.assert_not_called()
        self.source_upload.assert_not_called()

    def snapshot(self):
        return (list(Schedule.objects.order_by('pk').values()),
                list(Delivery.objects.order_by('pk').values()))

    def test_enabled_report_uploads_previous_month_pdf_then_sends_real_link_card(self):
        schedule = self.reserve()
        self.assertEqual(schedule.updated_by_id, self.actor.pk)
        self.assertEqual(schedule.target_month, date(2026, 10, 1))
        events = Mock()
        events.attach_mock(self.pdf_builder, 'pdf')
        events.attach_mock(self.upload, 'upload')
        events.attach_mock(self.sender, 'chat')
        delivery = self.send(schedule)
        self.assert_terminal(schedule, delivery, 'sent')
        self.pdf_builder.assert_called_once_with(delivery.schedule, NOW)
        built_schedule = self.pdf_builder.call_args.args[0]
        self.assertEqual(built_schedule.updated_by, self.actor)
        self.assertEqual(built_schedule.target_month, date(2026, 10, 1))
        filename = (f'Nexus_月次レポート_2026-10_development_'
                    f'{schedule.event_id.hex}_r{schedule.revision}.pdf')
        self.upload.assert_called_once_with(PDF, filename, upload.get_upload_config())
        self.assertNotIn('2026-11', filename)
        self.assertEqual(self.report_builder.call_args_list, [
            call(delivery.schedule, NOW), call(delivery.schedule, NOW, pdf_url=PDF_URL)])
        self.sender.assert_called_once()
        message = self.sender.call_args.args[0]
        self.assertEqual(message['text'],
                 f'【Nexus】2026年10月 投稿状況\n<{PDF_URL}|月次レポート>')
        card_wire = json.dumps(message['cardsV2'], ensure_ascii=False)
        self.assertNotIn(PDF_URL, card_wire)
        self.assertNotIn('buttonList', card_wire)
        self.assertNotIn('openLink', card_wire)
        self.assertEqual([entry[0] for entry in events.mock_calls], ['pdf', 'upload', 'chat'])
        self.target_url.assert_called_once_with()
        self.deadline_builder.assert_not_called()

    @override_settings(GOOGLE_CHAT_REPORT_PDF_ENABLED=False,
                       GOOGLE_DRIVE_REPORT_UPLOAD_URL='', GOOGLE_DRIVE_REPORT_UPLOAD_SECRET='')
    def test_disabled_report_sends_without_pdf_upload_or_link(self):
        schedule = self.reserve()
        delivery = self.send(schedule)
        self.assert_terminal(schedule, delivery, 'sent')
        self.assert_no_pdf_or_upload()
        self.report_builder.assert_called_once_with(delivery.schedule, NOW)
        self.sender.assert_called_once()
        self.assertIn(f'<https://drive.google.com/drive/folders/{FOLDER_ID}|月次レポート>',
                  self.sender.call_args.args[0]['text'])

    @override_settings(GOOGLE_DRIVE_REPORT_UPLOAD_URL='', GOOGLE_DRIVE_REPORT_UPLOAD_SECRET='')
    def test_deadline_never_builds_pdf_or_uploads_even_with_feature_enabled(self):
        schedule = self.reserve('deadline')
        delivery = self.send(schedule)
        self.assert_terminal(schedule, delivery, 'sent')
        self.assert_no_pdf_or_upload()
        self.report_builder.assert_not_called()
        self.deadline_builder.assert_called_once_with(delivery.schedule, NOW)
        self.sender.assert_called_once_with({'text': 'offline deadline'})

    def test_upload_failure_is_unknown_without_chat_and_cannot_be_claimed_again(self):
        schedule = self.reserve()
        self.upload.side_effect = RuntimeError(LEAK + UPLOAD_SECRET + UPLOAD_URL)
        delivery = self.send(schedule)
        self.assert_terminal(schedule, delivery, 'unknown')
        self.pdf_builder.assert_called_once()
        self.upload.assert_called_once()
        self.sender.assert_not_called()
        self.report_builder.assert_called_once_with(delivery.schedule, NOW)
        self.assertTrue(delivery.last_error)

    @override_settings(GOOGLE_DRIVE_REPORT_UPLOAD_SECRET='')
    def test_invalid_configuration_fails_before_pdf_upload_or_chat(self):
        schedule = self.reserve()
        delivery = self.send(schedule)
        self.assert_terminal(schedule, delivery, 'failed')
        self.assert_no_pdf_or_upload()
        self.sender.assert_not_called()
        self.assertTrue(delivery.last_error)

    def test_pdf_build_exception_fails_without_upload_or_chat(self):
        schedule = self.reserve()
        self.pdf_builder.side_effect = ValueError(LEAK + UPLOAD_SECRET)
        delivery = self.send(schedule)
        self.assert_terminal(schedule, delivery, 'failed')
        self.pdf_builder.assert_called_once()
        self.upload.assert_not_called()
        self.sender.assert_not_called()

    def test_invalid_rendered_pdf_fails_without_upload_or_chat(self):
        for index, pdf in enumerate((None, b'', b'not-pdf', '%PDF-1.4')):
            with self.subTest(pdf=pdf):
                self.pdf_builder.reset_mock()
                self.pdf_builder.return_value = pdf
                schedule = self.reserve(notice_at=NOW - timedelta(minutes=index + 1))
                delivery = self.send(schedule)
                self.assert_terminal(schedule, delivery, 'failed')
                self.pdf_builder.assert_called_once()
                self.upload.assert_not_called()
                self.sender.assert_not_called()

    def test_chat_must_explicitly_confirm_even_after_successful_upload(self):
        for index, result in enumerate((None, {}, {'confirmed': False}, {'confirmed': 1})):
            with self.subTest(result=result):
                self.upload.reset_mock()
                self.sender.reset_mock()
                self.sender.return_value = result
                schedule = self.reserve(notice_at=NOW - timedelta(minutes=index + 1))
                delivery = self.send(schedule)
                self.assert_terminal(schedule, delivery, 'unknown')
                self.upload.assert_called_once()
                self.sender.assert_called_once()

    def test_dry_run_and_default_command_never_build_pdf_upload_or_send(self):
        self.reserve()
        before = self.snapshot()
        with patch(COMMAND + '.timezone.now', return_value=NOW), patch(
                COMMAND + '.send_scheduled_google_chat_message') as sender:
            for arguments in ((), ('--dry-run',), ('--dry-run', '--json')):
                with self.subTest(arguments=arguments):
                    output = StringIO()
                    call_command('send_scheduled_chat_notifications', *arguments, stdout=output)
                    self.assertIn('mode=dry-run', output.getvalue())
                    self.assertIn('2026-10', output.getvalue())
                    self.assertEqual(self.snapshot(), before)
                    self.assert_no_pdf_or_upload()
                    sender.assert_not_called()
                    self.sender.assert_not_called()

    def test_staff_manual_confirmation_get_never_builds_pdf_uploads_or_claims(self):
        schedule = self.reserve()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        url = reverse('chat_schedule_send', args=[schedule.pk, 'report'])
        before = self.snapshot()
        with patch(VIEW + '.timezone.now', return_value=NOW), patch(
                VIEW + '.send_scheduled_google_chat_message') as sender:
            response = client.get(url, {'expected_revision': str(schedule.revision)})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['available'])
        self.assertIn('2026年10月', response.context['preview'])
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertNotIn(PDF_URL, response.context['preview'])
        self.assertEqual(self.snapshot(), before)
        self.assert_no_pdf_or_upload()
        sender.assert_not_called()
        self.sender.assert_not_called()