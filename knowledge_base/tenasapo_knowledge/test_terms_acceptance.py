"""Consent tests use only the offline in-memory database/settings."""

from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from .middleware import LoginRequiredExceptAssetsMiddleware
from .models import TermsAcceptance, TermsOfUse, UserProfile
from .terms import terms_acceptance_applies


@override_settings(MIDDLEWARE=[*settings.MIDDLEWARE,
    'tenasapo_knowledge.middleware.LoginRequiredExceptAssetsMiddleware'])
class TermsAcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.member = users.objects.create_user(username='consent-member', password='secure-test-pass')
        UserProfile.objects.create(user=cls.member, user_type=UserProfile.USER_TYPE_CUSTOMER)
        cls.staff = users.objects.create_user(username='staff', is_staff=True)
        cls.root = users.objects.create_user(username='root', is_superuser=True)
        cls.upper = users.objects.create_user(username='Admin', is_staff=True)
        cls.customer = users.objects.create_user(username='customer')
        cls.demo = users.objects.create_user(username='demo')
        cls.group_admin = users.objects.create_user(username='group-admin')
        cls.group_admin.groups.add(Group.objects.get_or_create(name='管理者')[0])
        UserProfile.objects.create(user=cls.customer, user_type=UserProfile.USER_TYPE_CUSTOMER)
        cls.demo.groups.add(Group.objects.get_or_create(name='デモ')[0])

    def setUp(self):
        self.terms = TermsOfUse.objects.create(title='規約 <script>title</script>',
                                              description='本文 <script>body</script>\n次の行')

    def token(self, client=None):
        return (client or self.client).get(reverse('terms_accept')).context['terms_token']

    def accept(self, user=None):
        user = user or self.member
        self.client.force_login(user)
        if not terms_acceptance_applies(user):
            return self.client.post(reverse('terms_accept'), {'terms_accepted': 'on'})
        return self.client.post(reverse('terms_accept'), {
            'terms_token': self.token(), 'terms_accepted': 'on',
        })

    def test_unchecked_or_invalid_checkbox_denied_without_record(self):
        self.client.force_login(self.member)
        token = self.token()
        for value in (None, '', 'false', 'off', '0', 'forged'):
            with self.subTest(value=value):
                payload = {'terms_token': token}
                if value is not None:
                    payload['terms_accepted'] = value
                response = self.client.post(reverse('terms_accept'), payload)
                self.assertEqual(response.status_code, 400)
                self.assertContains(response, '利用規約に同意するにチェックを入れてください。', status_code=400)
                self.assertContains(response, 'name="terms_token"', status_code=400)
                self.assertFalse(TermsAcceptance.objects.exists())

    def test_back_uses_independent_csrf_logout_post_and_returns_to_login(self):
        from .test_review_list_layout import ReviewDOMParser

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.member)
        response = client.get(reverse('terms_accept'))
        parser = ReviewDOMParser()
        parser.feed(response.content.decode())
        back, = parser.find(id='terms-back-form')
        self.assertEqual(back['attrs']['method'], 'post')
        self.assertEqual(back['attrs']['action'], reverse('logout'))
        self.assertFalse(any(node for node in parser.find(name='terms_accepted') if back in node['parents']))
        csrf, = [node for node in parser.find(name='csrfmiddlewaretoken') if back in node['parents']]
        next_field, = [node for node in parser.find(name='next') if back in node['parents']]
        self.assertEqual(next_field['attrs']['value'], reverse('login'))
        self.assertEqual(client.get(reverse('logout')).status_code, 405)
        self.assertEqual(client.post(reverse('logout')).status_code, 403)
        self.assertIn('_auth_user_id', client.session)
        with override_settings(SOCIALACCOUNT_PROVIDERS={'google': {'APP': {
                'client_id': 'offline', 'secret': 'offline', 'key': ''}}}):
            result = client.post(reverse('logout'), {
                'csrfmiddlewaretoken': csrf['attrs']['value'], 'next': next_field['attrs']['value'],
            }, follow=True)
        self.assertEqual(result.redirect_chain, [(reverse('login'), 302)])
        self.assertEqual(result.status_code, 200)
        self.assertNotIn('_auth_user_id', client.session)
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_first_authenticated_request_is_gated(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/terms/accept/')

    def test_customer_roles_and_existing_sessions_are_gated_without_auto_acceptance(self):
        for user in (self.member, self.customer):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                for method in ('get', 'post'):
                    response = getattr(self.client, method)(reverse('home'))
                    self.assertRedirects(response, reverse('terms_accept'), fetch_redirect_response=False)
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_preview_modes_never_require_or_record_consent_and_cannot_edit(self):
        for mode in ('demo', 'cs'):
            self.client.force_login(self.upper)
            session = self.client.session
            session['account_view_mode'] = mode
            session.save()
            self.assertNotEqual(getattr(self.client.get(reverse('home')), 'url', ''), reverse('terms_accept'))
            for method in ('get', 'head', 'options', 'post'):
                response = getattr(self.client, method)(reverse('terms_accept'))
                self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
            self.assertEqual(self.client.get(reverse('terms_edit')).status_code, 403)
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_noncustomers_bypass_login_middleware_and_direct_consent_posts(self):
        ordinary = get_user_model().objects.create_user(username='ordinary', password='test-password')
        internal = get_user_model().objects.create_user(username='internal', password='test-password')
        UserProfile.objects.create(user=internal, user_type=UserProfile.USER_TYPE_SYSTENA)
        for user in (ordinary, internal, self.staff, self.root, self.upper, self.demo, self.group_admin):
            with self.subTest(user=user.username):
                self.client.force_login(self.member)
                stolen_token = self.token()
                self.client.force_login(user)
                self.assertNotEqual(getattr(self.client.get(reverse('home')), 'url', ''), reverse('terms_accept'))
                for method in ('get', 'head', 'options'):
                    self.assertRedirects(getattr(self.client, method)(reverse('terms_accept')),
                                         reverse('home'), fetch_redirect_response=False)
                self.assertRedirects(self.client.post(reverse('terms_accept') + '?next=https://evil.example', {
                    'terms_token': stolen_token, 'terms_accepted': 'on', 'next': 'https://evil.example',
                }), reverse('home'), fetch_redirect_response=False)
                self.assertEqual(self.client.get(reverse('terms_detail')).status_code, 200)
        for user in (ordinary, internal):
            self.client.logout()
            response = self.client.post(reverse('login'), {'username': user.username, 'password': 'test-password'})
            self.assertNotEqual(getattr(response, 'url', ''), reverse('terms_accept'))
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_mixed_roles_follow_effective_profile_demo_and_admin_precedence(self):
        users = get_user_model()
        customer_group = Group.objects.get_or_create(name='カスタマー')[0]
        internal = users.objects.create_user(username='internal-with-customer-group')
        UserProfile.objects.create(user=internal, user_type=UserProfile.USER_TYPE_SYSTENA)
        internal.groups.add(customer_group)
        group_customer = users.objects.create_user(username='group-customer')
        group_customer.groups.add(customer_group)
        profile_customer = users.objects.create_user(username='profile-customer-with-internal-group')
        UserProfile.objects.create(user=profile_customer, user_type=UserProfile.USER_TYPE_CUSTOMER)
        profile_customer.groups.add(Group.objects.get_or_create(name='システナ')[0])
        self.demo.groups.add(customer_group)
        UserProfile.objects.create(user=self.demo, user_type=UserProfile.USER_TYPE_CUSTOMER)
        for user in (self.staff, self.root, self.upper):
            user.groups.add(customer_group)
            UserProfile.objects.create(user=user, user_type=UserProfile.USER_TYPE_CUSTOMER)
        for user in (internal, self.demo, self.staff, self.root, self.upper, group_customer, profile_customer):
            with self.subTest(user=user.username):
                applicable = user in (group_customer, profile_customer)
                self.assertEqual(terms_acceptance_applies(user), applicable)
                self.client.force_login(user)
                response = self.client.get(reverse('home'))
                self.assertEqual(getattr(response, 'url', '') == reverse('terms_accept'), applicable)
                if applicable:
                    self.assertEqual(self.accept(user).status_code, 302)
                else:
                    self.assertRedirects(self.client.post(reverse('terms_accept'), {'terms_accepted': 'on'}),
                                         reverse('home'), fetch_redirect_response=False)
        self.assertEqual(set(TermsAcceptance.objects.values_list('user_id', flat=True)),
                         {group_customer.pk, profile_customer.pk})

    def test_customer_changed_to_internal_cannot_submit_previously_valid_token(self):
        self.client.force_login(self.member)
        token = self.token()
        UserProfile.objects.filter(user=self.member).update(user_type=UserProfile.USER_TYPE_SYSTENA)
        self.assertRedirects(self.client.post(reverse('terms_accept'), {
            'terms_token': token, 'terms_accepted': 'on',
        }), reverse('home'), fetch_redirect_response=False)
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_get_head_options_do_not_mutate_and_escape_plaintext(self):
        self.client.force_login(self.member)
        for method in ('get', 'head', 'options'):
            self.assertEqual(getattr(self.client, method)(reverse('terms_accept')).status_code, 200)
        response = self.client.get(reverse('terms_accept'))
        self.assertContains(response, escape(self.terms.title))
        self.assertContains(response, escape(self.terms.description))
        self.assertNotContains(response, '<script>body</script>')
        self.assertContains(response, 'type="checkbox" name="terms_accepted"')
        self.assertContains(response, 'value="on" required')
        self.assertContains(response, '<label class="form-check-label" for="terms-accepted">利用規約に同意する</label>', html=True)
        self.assertContains(response, '>次へ</button>')
        self.assertContains(response, '>戻る</button>')
        self.assertNotContains(response, '同意して契約する')
        self.assertNotContains(response, '最終更新')
        self.assertNotContains(response, timezone.localtime(self.terms.updated_at).strftime('%Y/%m/%d %H:%M'))
        self.assertIn('no-store', response['Cache-Control'])
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_anonymous_terms_and_login_remain_public_but_consent_requires_login(self):
        self.assertEqual(self.client.get(reverse('terms_detail')).status_code, 200)
        for method in ('get', 'post'):
            response = getattr(self.client, method)(reverse('terms_accept'))
            self.assertEqual(response.status_code, 302)
            self.assertIn('login', response.url)
        with override_settings(SOCIALACCOUNT_PROVIDERS={'google': {'APP': {
                'client_id': 'offline', 'secret': 'offline', 'key': ''}}}):
            self.assertEqual(self.client.get(reverse('login')).status_code, 200)
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_display_retains_nonprimary_first_title_without_mutating_terms_or_consent(self):
        from .test_review_list_layout import ReviewDOMParser

        TermsOfUse.objects.filter(pk=self.terms.pk).update(title='保存済み <先頭> & 規約')
        second = TermsOfUse.objects.create(title='後続規約', description='第2条（追加）\n1. 本文')
        before_terms = list(TermsOfUse.objects.values())
        before_acceptance = list(TermsAcceptance.objects.values())
        self.client.force_login(self.member)
        for name in ('terms_detail', 'terms_accept'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                parser = ReviewDOMParser()
                parser.feed(response.content.decode())
                header, = parser.find(id='terms-page-title')
                self.assertEqual(header['text'], 'Tenasapo Knowledge Site 「Nexus」 サイトの利用条件')
                self.assertEqual([node['text'] for node in parser.find(css_class='terms-document-title')],
                                 [before_terms[0]['title'], second.title])
                self.assertEqual(list(TermsOfUse.objects.values()), before_terms)
                self.assertEqual(list(TermsAcceptance.objects.values()), before_acceptance)

    def test_post_stores_only_current_user_exact_snapshot_and_duplicate_is_idempotent(self):
        self.client.force_login(self.member)
        payload = {'terms_token': self.token(), 'terms_accepted': 'on', 'user': self.customer.pk,
                   'user_id': self.customer.pk, 'accepted_at': '2000-01-01',
                   'terms_title': 'forged', 'terms_description': 'forged'}
        before = timezone.now()
        response = self.client.post(reverse('terms_accept'), payload)
        after = timezone.now()
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
        record = TermsAcceptance.objects.get()
        self.assertEqual(record.user, self.member)
        self.assertEqual(record.terms_title, self.terms.title)
        self.assertEqual(record.terms_description, self.terms.description)
        self.assertEqual(record.terms_updated_at, self.terms.updated_at)
        self.assertLessEqual(before, record.accepted_at)
        self.assertLessEqual(record.accepted_at, after)
        original = (record.pk, record.accepted_at, record.terms_description)
        self.terms.description = '新規約'
        self.terms.save()
        self.assertEqual(self.client.post(reverse('terms_accept'), payload).status_code, 302)
        record.refresh_from_db()
        self.assertEqual((record.pk, record.accepted_at, record.terms_description), original)
        self.assertEqual(TermsAcceptance.objects.count(), 1)
        self.assertEqual(self.client.post(reverse('terms_accept'), {}).status_code, 302)
        record.refresh_from_db()
        self.assertEqual((record.pk, record.accepted_at, record.terms_description), original)

    def test_accepted_users_bypass_gate_even_after_update_delete_and_relogin(self):
        self.accept()
        self.terms.description = '更新済み'
        self.terms.save()
        for _ in range(2):
            self.client.logout()
            self.client.force_login(self.member)
            response = self.client.get(reverse('home'))
            self.assertNotEqual(getattr(response, 'url', ''), reverse('terms_accept'))
        self.terms.delete()
        self.assertEqual(TermsAcceptance.objects.count(), 1)

    def test_missing_tampered_or_other_users_token_rejected(self):
        self.client.force_login(self.customer)
        other_token = self.token()
        self.client.force_login(self.member)
        for token in ('', 'invalid', other_token, self.token() + 'tampered'):
            self.assertEqual(self.client.post(reverse('terms_accept'), {
                'terms_token': token, 'terms_accepted': 'on',
            }).status_code, 409)
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_stale_title_body_or_version_rejected_and_latest_redisplayed(self):
        self.client.force_login(self.member)
        for field in ('title', 'description', 'updated_at'):
            token = self.token()
            if field == 'updated_at':
                self.terms.save()
            else:
                # Even an edit that fails to bump updated_at must invalidate the token.
                TermsOfUse.objects.filter(pk=1).update(**{field: '変更済み'})
                self.terms.refresh_from_db()
            response = self.client.post(reverse('terms_accept'), {'terms_token': token, 'terms_accepted': 'on'})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.context['terms_list'][0].description, self.terms.description)
            self.assertFalse(TermsAcceptance.objects.exists())
        self.assertEqual(self.client.post(reverse('terms_accept'), {
            'terms_token': self.token(), 'terms_accepted': 'on',
        }).status_code, 302)

    def test_consent_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.member)
        token = self.token(client)
        payload = {'terms_token': token, 'terms_accepted': 'on'}
        for headers in ({}, {'HTTP_X_CSRFTOKEN': 'invalid'},
                        {'HTTP_X_CSRFTOKEN': client.cookies['csrftoken'].value,
                         'HTTP_ORIGIN': 'https://evil.example'}):
            self.assertEqual(client.post(reverse('terms_accept'), payload, **headers).status_code, 403)
        self.assertFalse(TermsAcceptance.objects.exists())
        self.assertEqual(client.post(reverse('terms_accept'), {'terms_token': token},
            HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value).status_code, 400)
        self.assertFalse(TermsAcceptance.objects.exists())
        self.assertEqual(client.post(reverse('terms_accept'), payload,
            HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value).status_code, 302)
        self.assertEqual(TermsAcceptance.objects.count(), 1)

    def test_no_external_next_redirect_or_loop(self):
        self.client.force_login(self.member)
        for target in ('https://evil.example', '//evil.example', '/terms/accept/', '/accounts/logout/'):
            TermsAcceptance.objects.filter(user=self.member).delete()
            response = self.client.post(reverse('terms_accept') + '?next=' + target,
                                        {'terms_token': self.token(), 'terms_accepted': 'on', 'next': target})
            self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
            self.assertTrue(TermsAcceptance.objects.filter(user=self.member).exists())

    def test_absent_or_whitespace_terms_allow_use_without_acceptance(self):
        self.client.force_login(self.member)
        for title, body in (('', 'body'), ('title', ' \n\t'), (' ', 'body')):
            TermsOfUse.objects.filter(pk=1).update(title=title, description=body)
            self.assertNotEqual(getattr(self.client.get(reverse('home')), 'url', ''), reverse('terms_accept'))
            self.assertNotContains(self.client.get(reverse('terms_accept')), 'name="terms_accepted"')
            self.assertEqual(self.client.post(reverse('terms_accept')).status_code, 302)
        self.terms.delete()
        self.assertNotEqual(getattr(self.client.get(reverse('home')), 'url', ''), reverse('terms_accept'))
        self.assertEqual(self.client.post(reverse('terms_accept')).status_code, 302)
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_deleted_viewed_terms_cannot_be_accepted(self):
        self.client.force_login(self.member)
        token = self.token()
        self.terms.delete()
        self.assertEqual(self.client.post(reverse('terms_accept'), {
            'terms_token': token, 'terms_accepted': 'on',
        }).status_code, 409)
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_privileged_editor_works_before_consent_even_with_initial_password(self):
        self.upper.set_password(self.upper.username)
        self.upper.save(update_fields=['password'])
        self.client.force_login(self.upper)
        self.assertEqual(self.client.get(reverse('terms_edit')).status_code, 200)
        self.assertEqual(self.client.post(reverse('terms_edit'), {'title': '更新', 'description': '更新本文'}).status_code, 302)
        self.assertEqual(self.client.get(reverse('terms_detail')).status_code, 200)
        self.assertRedirects(self.client.get(reverse('terms_accept')), reverse('home'), fetch_redirect_response=False)
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_password_change_remains_accessible_and_consent_precedes_normal_use(self):
        self.member.set_password(self.member.username)
        self.member.save(update_fields=['password'])
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse('password_change')).status_code, 200)
        self.assertRedirects(self.client.get(reverse('home')), reverse('terms_accept'))
        self.accept()
        self.assertRedirects(self.client.get(reverse('home')), reverse('password_change'))

    def test_password_login_followed_by_consent(self):
        response = self.client.post(reverse('login'), {'username': self.member.username,
                                                      'password': 'secure-test-pass'})
        self.assertRedirects(response, reverse('terms_accept'))
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_initial_password_login_shows_consent_before_password_change(self):
        self.member.set_password(self.member.username)
        self.member.save(update_fields=['password'])
        response = self.client.post(reverse('login'), {
            'username': self.member.username, 'password': self.member.username,
        })
        self.assertRedirects(response, reverse('terms_accept'))
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_auth_logout_and_assets_exempt_without_network(self):
        self.client.force_login(self.member)
        for name in ('password_reset', 'password_reset_complete'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        # Exercise the actual resolved Google/allauth view without OAuth traffic.
        from django.urls import resolve
        middleware = LoginRequiredExceptAssetsMiddleware(lambda request: HttpResponse())
        for path in ('/accounts/google/login/', '/accounts/google/login/callback/',
                     '/accounts/login/', '/accounts/logout/', '/static/offline.css', '/media/offline.png'):
            request = RequestFactory().get(path)
            request.user = self.member
            request.session = self.client.session
            request.resolver_match = resolve(path)
            self.assertIsNone(middleware.process_view(request, request.resolver_match.func, (), {}), path)
        self.assertIn(self.client.post(reverse('logout')).status_code, (200, 302))
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertFalse(TermsAcceptance.objects.exists())

    def test_gate_blocks_mutating_application_view_before_execution(self):
        self.client.force_login(self.member)
        with patch('tenasapo_knowledge.views.UserPasswordResetView.post') as handler:
            response = self.client.post(reverse('user_password_reset', args=[self.member.pk]))
        self.assertRedirects(response, reverse('terms_accept'))
        handler.assert_not_called()

    def test_user_detail_status_and_existing_authorization(self):
        self.accept()
        for viewer in (self.staff, self.root, self.upper):
            self.client.force_login(viewer)
            # Staff can manage customers without being asked to consent themselves.
            self.assertRedirects(self.client.post(reverse('terms_accept'), {'terms_accepted': 'on'}),
                                 reverse('home'), fetch_redirect_response=False)
            page = self.client.get(reverse('user_detail', args=[self.member.pk]))
            self.assertContains(page, '同意済み')
            self.assertContains(page, '同意日時')
            self.assertContains(page, '同意した規約の版（更新日時）')
            self.assertContains(page, escape(self.terms.title))
            page = self.client.get(reverse('user_detail', args=[self.customer.pk]))
            self.assertContains(page, '未同意')
        self.client.force_login(self.member)
        response = self.client.get(reverse('user_detail', args=[self.customer.pk]))
        self.assertRedirects(response, reverse('article_list'), fetch_redirect_response=False)
        self.client.logout()
        response = self.client.get(reverse('user_detail', args=[self.member.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_footer_terms_is_last_element_after_original_copyright(self):
        html = render_to_string('base.html')
        footer = html.split('<footer class="app-footer">')[1].split('</footer>')[0]
        self.assertLess(footer.index('COPYRIGHT'), footer.index('>利用規約</a>'))
        self.assertTrue(footer.strip().endswith('>利用規約</a>'))

    def test_user_detail_and_user_edit_cannot_forge_or_rewrite_acceptance(self):
        self.accept(self.upper)
        for target in (self.customer, self.upper):
            with self.subTest(target=target.username):
                before = list(TermsAcceptance.objects.values())
                payload = {'accepted_at': '2000-01-01', 'terms_accepted': 'on',
                           'terms_title': 'forged', 'terms_description': 'forged'}
                response = self.client.post(reverse('user_detail', args=[target.pk]), payload)
                self.assertEqual(response.status_code, 405)
                page = self.client.get(reverse('user_edit', args=[target.pk]))
                self.assertEqual(page.status_code, 200)
                self.assertTrue(set(payload).isdisjoint(page.context['form'].fields))
                response = self.client.post(reverse('user_edit', args=[target.pk]), {
                    **{key: '' if value is None else value
                       for key, value in page.context['form'].initial.items()}, **payload,
                    'user_type': 'customer', 'company_name': 'テスト会社',
                })
                self.assertEqual(response.status_code, 302)
                self.assertEqual(list(TermsAcceptance.objects.values()), before)

    def test_accepted_nonstaff_and_preview_roles_cannot_read_other_user_consent(self):
        for viewer in (self.member, self.customer, self.demo, self.group_admin):
            self.accept(viewer)
            response = self.client.get(reverse('user_detail', args=[self.upper.pk]))
            self.assertRedirects(response, reverse('article_list'), fetch_redirect_response=False)
        self.accept(self.upper)
        for mode in ('demo', 'cs'):
            session = self.client.session
            session['account_view_mode'] = mode
            session.save()
            response = self.client.get(reverse('user_detail', args=[self.member.pk]))
            self.assertRedirects(response, reverse('article_list'), fetch_redirect_response=False)

    def test_user_list_consent_timestamp_and_existing_permissions(self):
        self.accept(self.member)
        record = TermsAcceptance.objects.get(user=self.member)
        expected = timezone.localtime(record.accepted_at).strftime('%Y/%m/%d %H:%M:%S')
        for viewer in (self.staff, self.root, self.upper):
            self.accept(viewer)
            response = self.client.get(reverse('user_list'), {'q': self.member.username})
            self.assertContains(response, '利用規約同意日時')
            self.assertContains(response, expected)
        for viewer in (self.member, self.customer, self.demo, self.group_admin):
            self.accept(viewer)
            response = self.client.get(reverse('user_list'))
            self.assertRedirects(response, reverse('article_list'), fetch_redirect_response=False)
            self.assertNotIn(expected, response.content.decode())
        self.accept(self.upper)
        for mode in ('demo', 'cs'):
            session = self.client.session
            session['account_view_mode'] = mode
            session.save()
            self.assertRedirects(self.client.get(reverse('user_list')), reverse('article_list'),
                                 fetch_redirect_response=False)
        self.client.logout()
        response = self.client.get(reverse('user_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)