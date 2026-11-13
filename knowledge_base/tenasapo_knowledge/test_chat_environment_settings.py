"""Evaluate routing with dummy endpoints only; never send external requests."""

import os
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .notifications import send_google_chat_message


class ChatEnvironmentSettingsTests(SimpleTestCase):
    def evaluate(self, debug, dev='https://dev.invalid/chat', testing=False):
        env = {
            'DEBUG': debug,
            'DB_ENGINE': 'django.db.backends.postgresql',
            'DB_NAME': 'test',
            'DB_USER': 'test',
            'DB_PASSWORD': 'dummy',
            'DB_HOST': 'localhost',
            'DB_PORT': '5432',
            'GOOGLE_CHAT_DEV_WEBHOOK_URL': dev,
            'GOOGLE_CHAT_WEBHOOK_URL': 'https://production.invalid/chat',
            'GOOGLE_CHAT_GAS_WEB_APP_URL': 'https://production.invalid/relay',
        }
        with patch.dict(os.environ, env), patch.object(sys, 'argv', ['manage.py', 'test'] if testing else ['server']), \
                patch('dotenv.load_dotenv'), patch('os.makedirs'):
            return runpy.run_path(str(Path(__file__).resolve().parent.parent / 'config/settings.py'))

    def test_development_uses_only_development_webhook_even_with_production_overrides(self):
        values = self.evaluate('True')
        self.assertEqual(values['GOOGLE_CHAT_WEBHOOK_URL'], 'https://dev.invalid/chat')
        self.assertEqual(values['GOOGLE_CHAT_GAS_WEB_APP_URL'], '')

    def test_missing_or_blank_development_url_never_falls_back_to_production(self):
        for dev in ('', '   '):
            with self.subTest(dev=dev):
                values = self.evaluate('True', dev)
                self.assertEqual(values['GOOGLE_CHAT_WEBHOOK_URL'], '')
                self.assertEqual(values['GOOGLE_CHAT_GAS_WEB_APP_URL'], '')

    def test_production_and_testing_keep_existing_environment_overrides(self):
        for debug, testing in (('False', False), ('True', True)):
            values = self.evaluate(debug, testing=testing)
            self.assertEqual(values['GOOGLE_CHAT_WEBHOOK_URL'], 'https://production.invalid/chat')
            self.assertEqual(values['GOOGLE_CHAT_GAS_WEB_APP_URL'], 'https://production.invalid/relay')

    @override_settings(GOOGLE_CHAT_WEBHOOK_URL='https://dev.invalid/chat', GOOGLE_CHAT_GAS_WEB_APP_URL='')
    def test_message_routes_directly_to_development_webhook(self):
        with patch('tenasapo_knowledge.notifications._request_json_post', return_value=(200, '{}')) as request:
            send_google_chat_message('routing test')
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], 'https://dev.invalid/chat')