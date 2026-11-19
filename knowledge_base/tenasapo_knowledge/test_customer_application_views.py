"""申請画面からパスワード設定までのオフライン統合テスト。"""

import uuid
from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import Client, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from . import customer_applications as service
from .customer_application_models import CustomerApplication
from .models import UserProfile
from .test_customer_applications import application, row
from .views import should_show_first_login_registration


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   CUSTOMER_APPLICATION_PUBLIC_URL='https://nexus.example.com')
class ApplicationViewTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user('application-staff', is_staff=True)
        self.member = get_user_model().objects.create_user('application-member')
        self.item = application()
        self.client.force_login(self.staff)
        self.google = self.enterContext(patch.object(service, '_call_google', return_value={
            'ok': True, 'draft_id': 'draft_123',
        }))
        self.get_connection = self.enterContext(patch.object(
            service.google, 'get_connection', return_value=None,
        ))

    def url(self, action='detail', pk=None):
        return reverse('customer_application_' + action, kwargs={'pk': pk or self.item.pk})

    def test_staff_list_detail_and_entry_point(self):
        response = self.client.get(reverse('customer_application_list'))
        self.assertContains(response, self.item.email)
        self.assertContains(response, '回答を取込')
        response = self.client.get(self.url())
        self.assertContains(response, self.item.applicant_email)
        self.assertContains(response, self.item.email)
        self.assertContains(response, '承認してユーザー・下書きを作成')
        response = self.client.get(reverse('user_list'))
        self.assertContains(response, reverse('customer_application_list'))
        self.google.assert_not_called()

    def test_oauth_unconfigured_screens_still_render(self):
        self.get_connection.side_effect = service.google.CustomerGoogleError()
        for url in (reverse('customer_application_list'), self.url()):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, self.item.email)
                self.assertNotContains(response, 'GAS実行者')
        self.google.assert_not_called()

    def test_anonymous_and_member_cannot_read_or_change(self):
        urls = [reverse('customer_application_list'), self.url(),
                reverse('customer_application_import'),
                                *[reverse('customer_application_google_' + name)
                                    for name in ('connect', 'callback', 'disconnect')],
                *[self.url(name) for name in ('approve', 'reject', 'retry_draft')]]
        for user in (None, self.member):
            self.client.logout()
            if user:
                self.client.force_login(user)
            for url in urls:
                for method in ('get', 'post'):
                    with self.subTest(user=bool(user), url=url, method=method):
                        response = getattr(self.client, method)(url)
                        self.assertEqual(response.status_code, 403 if user else 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'pending')
        self.google.assert_not_called()

    def test_mutations_require_post_and_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        urls = [reverse('customer_application_import'),
            reverse('customer_application_google_connect'),
            reverse('customer_application_google_disconnect'),
                *[self.url(name) for name in ('approve', 'reject', 'retry_draft')]]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 405)
            self.assertEqual(client.post(url).status_code, 403)
        self.google.assert_not_called()

    def test_non_atomic_external_views(self):
        for url in (reverse('customer_application_import'), self.url('approve'), self.url('retry_draft'),
                    reverse('customer_application_google_callback')):
            self.assertIn('default', resolve(url).func._non_atomic_requests)

    def test_explicit_google_consent_redirect_and_local_disconnect(self):
        target = 'https://accounts.google.com/o/oauth2/v2/auth?state=synthetic'
        with patch.object(service.google, 'make_auth_request', return_value=target) as connect:
            response = self.client.post(reverse('customer_application_google_connect'))
            self.assertRedirects(response, target, fetch_redirect_response=False)
            self.assertEqual(connect.call_args.args[0].user.pk, self.staff.pk)
        with patch.object(service.google, 'disconnect') as disconnect:
            response = self.client.post(reverse('customer_application_google_disconnect'))
            self.assertRedirects(response, reverse('customer_application_list'), fetch_redirect_response=False)
            self.assertEqual(disconnect.call_args.args[0].user.pk, self.staff.pk)

    def test_google_callback_success_and_failure_do_not_echo_oauth_code(self):
        path = reverse('customer_application_google_callback')
        self.assertEqual(self.client.post(path).status_code, 405)
        for failure in (False, True):
            with patch.object(service.google, 'handle_callback',
                              side_effect=service.google.CustomerGoogleError() if failure else None) as callback:
                response = self.client.get(path, {'code': 'PRIVATE-CODE', 'state': 'PRIVATE-STATE'})
                callback.assert_called_once()
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response['Location'], reverse('customer_application_list'))
                self.assertEqual(response['Referrer-Policy'], 'no-referrer')
                self.assertNotContains(response, 'PRIVATE', status_code=302)

    def test_google_connection_shows_mailbox_without_token(self):
        from types import SimpleNamespace

        self.get_connection.return_value = SimpleNamespace(
            email='reviewer@example.test', encrypted_refresh_token='PRIVATE-REFRESH',
        )
        response = self.client.get(reverse('customer_application_list'))
        self.assertContains(response, 'reviewer@example.test')
        self.assertContains(response, 'Googleアクセスを再許可')
        self.assertNotContains(response, 'PRIVATE-REFRESH')

    def test_import_only_stages_then_approval_uses_f_column(self):
        self.google.return_value = {'ok': True, 'applications': [row(3)], 'next_row': None}
        self.client.post(reverse('customer_application_import'))
        self.assertEqual(self.google.call_args.args[1], self.staff)
        staged = CustomerApplication.objects.get(source_key=row(3)['source_key'])
        self.assertEqual(staged.status, 'pending')
        self.assertIsNone(staged.created_user_id)
        self.google.return_value = {'ok': True, 'draft_id': 'draft_123'}
        response = self.client.post(self.url('approve', staged.pk), follow=True)
        self.assertContains(response, 'ユーザーは作成済み')
        self.assertContains(response, '承認者のGmail')
        self.assertNotContains(response, 'GAS実行者')
        staged.refresh_from_db()
        self.assertEqual(staged.created_user.username, staged.email)
        self.assertEqual(self.google.call_args.args[0]['to'], staged.email)
        self.assertEqual(self.google.call_args.args[1], self.staff)
        self.assertNotIn('cc', self.google.call_args.args[0])
        self.assertNotIn(staged.applicant_email, self.google.call_args.args[0]['body'])
        self.google.reset_mock()
        self.client.post(self.url('approve', staged.pk))
        self.google.assert_not_called()

    def test_approval_preserved_after_draft_failure_and_retry(self):
        self.google.side_effect = service.ApplicationError('連携失敗')
        response = self.client.post(self.url('approve'), follow=True)
        self.assertContains(response, 'ユーザー作成・承認は完了しています')
        self.item.refresh_from_db()
        user_id = self.item.created_user_id
        self.assertEqual(self.item.draft_state, 'failed')
        self.google.side_effect = None
        self.google.return_value = {'ok': True, 'draft_id': 'retry_draft'}
        self.client.post(self.url('retry_draft'))
        self.item.refresh_from_db()
        self.assertEqual(self.item.created_user_id, user_id)
        self.assertEqual(self.item.draft_state, 'created')
        self.assertEqual(self.google.call_args.args[1], self.staff)

    def test_other_staff_cannot_retry_original_reviewers_draft(self):
        self.google.side_effect = service.ApplicationError('連携失敗')
        self.client.post(self.url('approve'))
        self.item.refresh_from_db()
        self.assertEqual(self.item.reviewed_by, self.staff)
        self.assertEqual(self.item.draft_state, 'failed')
        original_user_id = self.item.created_user_id
        other = get_user_model().objects.create_user('other-application-staff', is_staff=True)
        self.client.force_login(other)
        self.google.side_effect = None
        self.google.reset_mock()
        response = self.client.post(self.url('retry_draft'), follow=True)
        self.assertContains(response, '承認者本人')
        self.google.assert_not_called()
        self.item.refresh_from_db()
        self.assertEqual(self.item.created_user_id, original_user_id)
        self.assertEqual(self.item.reviewed_by, self.staff)
        self.assertEqual(self.item.draft_state, 'failed')

    def test_reject_does_not_create_user_or_draft(self):
        self.client.post(self.url('reject'))
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'rejected')
        self.assertEqual(self.item.reviewed_by_id, self.staff.pk)
        self.assertIsNone(self.item.created_user_id)
        self.client.post(self.url('approve'))
        self.google.assert_not_called()

    def test_status_filter_escaping_pagination_and_no_token_leak(self):
        self.item.company_name = '<script>alert(1)</script>'
        self.item.invitation_token = 'DO-NOT-EXPOSE-TOKEN'
        self.item.save()
        response = self.client.get(self.url())
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertNotContains(response, self.item.invitation_token)
        for number in range(3, 29):
            application(number)
        response = self.client.get(reverse('customer_application_list'), {'page': 2})
        self.assertEqual(len(response.context['page_obj']), 2)
        response = self.client.get(reverse('customer_application_list'), {'status': 'approved'})
        self.assertEqual(len(response.context['page_obj']), 0)
        self.assertEqual(self.client.get(self.url(pk=uuid.uuid4())).status_code, 404)

    def test_preview_mode_cannot_approve(self):
        session = self.client.session
        session['account_view_mode'] = 'cs'
        session.save()
        self.assertEqual(self.client.post(self.url('approve')).status_code, 403)
        self.google.assert_not_called()

    def test_approved_google_customer_cannot_enter_employee_registration(self):
        item = service.approve_application(self.item.pk, self.staff)
        user = item.created_user
        SocialAccount.objects.create(user=user, provider='google', uid='application-google')
        UserProfile.objects.filter(user=user).update(google_first_login_done=False)
        user = get_user_model().objects.get(pk=user.pk)
        self.assertFalse(should_show_first_login_registration(user))
        self.client.force_login(user)
        response = self.client.post(reverse('first_login_registration'), {
            'group': '管理者', 'employee_number': '123456', 'login_id': 'invite2',
        })
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.knowledge_profile.user_type, 'customer')

    def test_password_setup_works_in_separate_browser_and_token_is_single_use(self):
        item = service.approve_application(self.item.pk, self.staff)
        user = item.created_user
        path = reverse('password_reset_confirm', kwargs={
            'uidb64': urlsafe_base64_encode(force_bytes(user.pk)), 'token': item.invitation_token,
        })
        browser = Client()
        response = browser.get(path)
        self.assertEqual(response.status_code, 302)
        form_url = response.url
        response = browser.get(form_url)
        self.assertContains(response, 'new_password1')
        password = 'Nexus-Unique-Setup-Password!42'
        response = browser.post(form_url, {
            'email': item.email, 'new_password1': password, 'new_password2': password,
        })
        self.assertRedirects(response, reverse('password_reset_complete'), fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.check_password(password))
        self.assertFalse(default_token_generator.check_token(user, item.invitation_token))