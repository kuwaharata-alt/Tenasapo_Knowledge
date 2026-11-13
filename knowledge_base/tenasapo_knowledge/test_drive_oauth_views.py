"""Offline URL/view contracts; OAuth protocol details live in test_drive_oauth."""

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth import SESSION_KEY as AUTH_SESSION_KEY, get_user_model
from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings
from django.urls import resolve, reverse

from . import drive_oauth as oauth, drive_oauth_views as views
from .drive_oauth_models import DriveOAuthConnection


VIEW = 'tenasapo_knowledge.drive_oauth_views'
CLIENT_ID = 'offline-view-client.apps.googleusercontent.com'
CLIENT_SECRET = 'offline-view-client-secret'
REFRESH = 'offline-view-refresh-token'
ACCESS = 'offline-view-access-token'
CODE = 'offline-view-authorization-code'
APP = {'google': {'APP': {'client_id': CLIENT_ID, 'secret': CLIENT_SECRET}}}
TEST_SETTINGS = dict(
    DEBUG=True, ROOT_URLCONF='config.urls', ALLOWED_HOSTS=['testserver'],
    SECURE_SSL_REDIRECT=False, SESSION_SAVE_EVERY_REQUEST=False,
    GOOGLE_DRIVE_REPORT_BACKEND='oauth', GOOGLE_CHAT_REPORT_PDF_ENABLED=True,
    SOCIALACCOUNT_PROVIDERS=APP, GOOGLE_WORKSPACE_DOMAIN='systena.co.jp',
    SECRET_KEY='offline-drive-view-django-secret', SECRET_KEY_FALLBACKS=[],
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)


class OfflineNetworkMixin:
    """Block imported HTTP aliases and transports, including swallowed errors."""

    def setUp(self):
        super().setUp()
        self.network_guards = [self.enterContext(patch(
            target, side_effect=AssertionError('live network forbidden')))
            for target in (
                'requests.get', 'requests.post', 'requests.api.request',
                'requests.sessions.Session.request', 'requests.sessions.Session.send',
                'urllib.request.urlopen', 'urllib.request.OpenerDirector.open',
                'tenasapo_knowledge.notifications.urlopen',
                'tenasapo_knowledge.report_drive_upload.urlopen',
                'http.client.HTTPConnection.connect', 'http.client.HTTPSConnection.connect',
                'socket.create_connection', 'socket.socket.connect', 'socket.socket.connect_ex',
            )]
        # Registered after patches: inspect calls before their cleanup runs.
        self.addCleanup(self.assert_no_network)

    def assert_no_network(self):
        for guard in self.network_guards:
            guard.assert_not_called()


@override_settings(**TEST_SETTINGS)
class DriveOAuthViewTests(OfflineNetworkMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.actor = get_user_model().objects.create_user('oauth-view-staff', is_staff=True)
        self.other = get_user_model().objects.create_user('oauth-view-other', is_staff=True)
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.actor)
        self.settings_url = reverse('chat_schedule_settings')

    def connection(self, actor=None, environment='development'):
        row = DriveOAuthConnection(
            actor=self.actor if actor is None else actor, environment=environment,
            client_id=CLIENT_ID, google_sub='offline-view-subject',
            email='offline-view@systena.co.jp',
        )
        row.encrypted_refresh_token = oauth._encrypt(row, REFRESH)
        row.save()
        return row

    def snapshot(self):
        return list(DriveOAuthConnection.objects.order_by('pk').values())

    def csrf_token(self):
        response = self.client.get(self.settings_url, secure=True)
        self.assertContains(response, 'csrfmiddlewaretoken')
        return self.client.cookies[settings.CSRF_COOKIE_NAME].value

    def post(self, name, token):
        return self.client.post(reverse(name), {'csrfmiddlewaretoken': token},
                                secure=True, HTTP_REFERER='https://testserver' + self.settings_url)

    def assert_no_store(self, response):
        directives = {item.strip() for item in response['Cache-Control'].split(',')}
        self.assertTrue({'no-store', 'no-cache', 'private'} <= directives)

    def assert_no_credentials(self, response):
        wire = response.content.decode() + str(dict(response.items()))
        for secret in (CLIENT_SECRET, REFRESH, ACCESS, CODE):
            self.assertNotIn(secret, wire)

    def test_real_urls_resolve_to_decorated_views(self):
        for name, view, route in (
            ('drive_oauth_connect', views.drive_oauth_connect, '/summary/drive/connect/'),
            ('drive_oauth_callback', views.drive_oauth_callback, '/summary/drive/callback/'),
            ('drive_oauth_disconnect', views.drive_oauth_disconnect, '/summary/drive/disconnect/'),
        ):
            with self.subTest(name=name):
                self.assertEqual(reverse(name), route)
                self.assertIs(resolve(route).func, view)

    def test_staff_connect_only_post_with_csrf(self):
        before = self.snapshot()
        token = self.csrf_token()
        session_before = dict(self.client.session)
        with patch(VIEW + '.make_auth_request', wraps=oauth.make_auth_request) as begin:
            for method in ('get', 'head', 'put', 'delete'):
                with self.subTest(method=method):
                    response = getattr(self.client, method)(
                        reverse('drive_oauth_connect'), secure=True,
                        HTTP_X_CSRFTOKEN=token, HTTP_REFERER='https://testserver/')
                    self.assertEqual(response.status_code, 405)
            self.assertEqual(self.client.post(reverse('drive_oauth_connect'), secure=True,
                                             HTTP_REFERER='https://testserver/').status_code, 403)
            self.assertEqual(self.post('drive_oauth_connect', 'invalid').status_code, 403)
            different = 'A' * 32 if token != 'A' * 32 else 'B' * 32
            self.assertEqual(self.post('drive_oauth_connect', different).status_code, 403)
            begin.assert_not_called()
            self.assertEqual(dict(self.client.session), session_before)
            response = self.post('drive_oauth_connect', token)
            begin.assert_called_once()
        self.assertEqual(response.status_code, 302)
        self.assert_no_store(response)
        parsed = urlsplit(response.url)
        self.assertEqual(parsed.scheme + '://' + parsed.netloc + parsed.path, oauth.AUTH_URL)
        self.assertEqual(parse_qs(parsed.query)['redirect_uri'],
                         ['https://testserver' + reverse('drive_oauth_callback')])
        self.assertEqual(self.client.session[oauth.SESSION_KEY]['actor_id'], str(self.actor.pk))
        self.assertEqual(self.snapshot(), before)
        self.assert_no_credentials(response)

    def test_anonymous_and_nonstaff_cannot_connect_callback_or_disconnect(self):
        self.connection()
        token = self.csrf_token()
        users = [None, get_user_model().objects.create_user('oauth-view-member'),
                 get_user_model().objects.create_user('oauth-view-super', is_superuser=True)]
        with patch(VIEW + '.make_auth_request') as begin, patch(VIEW + '.handle_callback') as handle:
            for user in users:
                client = Client(enforce_csrf_checks=True)
                if user is not None:
                    client.force_login(user)
                client.cookies[settings.CSRF_COOKIE_NAME] = token
                before = self.snapshot()
                for name in ('drive_oauth_connect', 'drive_oauth_disconnect', 'drive_oauth_callback'):
                    with self.subTest(user=getattr(user, 'username', 'anonymous'), name=name):
                        if name == 'drive_oauth_callback':
                            response = client.get(reverse(name), {'code': CODE}, secure=True)
                        else:
                            response = client.post(reverse(name), {'csrfmiddlewaretoken': token},
                                                   secure=True, HTTP_REFERER='https://testserver/')
                        self.assertEqual(response.status_code, 302)
                        self.assertIn('next=', response.url)
                        self.assertEqual(self.snapshot(), before)
                        self.assertNotIn(oauth.SESSION_KEY, client.session)
            begin.assert_not_called()
            handle.assert_not_called()

    def test_callback_success_delegates_once_preserves_login_and_redirects_safely(self):
        # Token exchange/state validation is covered by the separate core suite.
        before = self.snapshot()
        actor_id = self.client.session[AUTH_SESSION_KEY]
        with patch(VIEW + '.handle_callback', return_value=object()) as handle:
            response = self.client.get(reverse('drive_oauth_callback'),
                                       {'code': CODE, 'state': 'offline-state'}, secure=True)
        handle.assert_called_once_with(response.wsgi_request)
        self.assertRedirects(response, self.settings_url, fetch_redirect_response=False)
        self.assert_no_store(response)
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertEqual(self.client.session[AUTH_SESSION_KEY], actor_id)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(any('Google Driveと連携しました' in str(message)
                            for message in get_messages(response.wsgi_request)))
        self.assert_no_credentials(response)

    def test_callback_error_is_local_no_store_no_referrer_and_get_only(self):
        before = self.snapshot()
        with patch(VIEW + '.handle_callback', side_effect=oauth.DriveOAuthError(oauth.AUTH_ERROR)) as handle:
            response = self.client.get(reverse('drive_oauth_callback'), {'code': CODE}, secure=True)
            self.assertRedirects(response, self.settings_url, fetch_redirect_response=False)
            self.assert_no_store(response)
            self.assertEqual(response['Referrer-Policy'], 'no-referrer')
            self.assertIn(oauth.AUTH_ERROR, [str(m) for m in get_messages(response.wsgi_request)])
            token = self.csrf_token()
            self.assertEqual(self.post('drive_oauth_callback', token).status_code, 405)
            self.assertEqual(handle.call_count, 1)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_credentials(response)

    def test_connect_error_redirects_without_connection_write(self):
        token = self.csrf_token()
        before = self.snapshot()
        with patch(VIEW + '.make_auth_request', side_effect=oauth.DriveOAuthError(oauth.AUTH_ERROR)):
            response = self.post('drive_oauth_connect', token)
        self.assertRedirects(response, self.settings_url, fetch_redirect_response=False)
        self.assert_no_store(response)
        self.assertEqual(self.snapshot(), before)

    def test_disconnect_requires_post_csrf_and_deletes_only_current_actor_environment(self):
        own = self.connection()
        production = self.connection(environment='production')
        other = self.connection(actor=self.other)
        token = self.csrf_token()
        session = self.client.session
        session[oauth.SESSION_KEY] = {'state': 'offline-pending'}
        session['unrelated'] = 'preserve-me'
        session.save()
        before = self.snapshot()
        for method in ('get', 'head'):
            self.assertEqual(getattr(self.client, method)(reverse('drive_oauth_disconnect'),
                                                         secure=True).status_code, 405)
        self.assertEqual(self.client.post(reverse('drive_oauth_disconnect'), secure=True,
                         HTTP_REFERER='https://testserver/').status_code, 403)
        self.assertEqual(self.post('drive_oauth_disconnect', 'invalid').status_code, 403)
        different = 'A' * 32 if token != 'A' * 32 else 'B' * 32
        self.assertEqual(self.post('drive_oauth_disconnect', different).status_code, 403)
        self.assertEqual(self.snapshot(), before)
        self.assertIn(oauth.SESSION_KEY, self.client.session)
        response = self.post('drive_oauth_disconnect', token)
        self.assertRedirects(response, self.settings_url, fetch_redirect_response=False)
        self.assert_no_store(response)
        self.assertFalse(DriveOAuthConnection.objects.filter(pk=own.pk).exists())
        self.assertEqual(self.snapshot(), [row for row in before if row['id'] in (production.pk, other.pk)])
        self.assertNotIn(oauth.SESSION_KEY, self.client.session)
        self.assertEqual(self.client.session['unrelated'], 'preserve-me')
        self.assertEqual(self.client.session[AUTH_SESSION_KEY], str(self.actor.pk))

    def test_settings_displays_exact_https_callback_and_never_credentials(self):
        row = self.connection()
        before = self.snapshot()
        response = self.client.get(self.settings_url, {'month': '2026-11'}, secure=True)
        callback = 'https://testserver' + reverse('drive_oauth_callback')
        self.assertEqual(response.context['drive_callback_uri'], callback)
        self.assertContains(response, '<span class="user-select-all">' + callback + '</span>', html=True)
        self.assertContains(response, row.email)
        self.assertContains(response, 'action="' + reverse('drive_oauth_connect') + '"')
        self.assertContains(response, 'action="' + reverse('drive_oauth_disconnect') + '"')
        self.assert_no_credentials(response)
        self.assertNotContains(response, row.encrypted_refresh_token)
        self.assertEqual(self.snapshot(), before)
