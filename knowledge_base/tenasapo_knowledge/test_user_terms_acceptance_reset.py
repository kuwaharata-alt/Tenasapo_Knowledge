"""Per-user destructive consent reset, using only the offline CI database."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import connection
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from . import terms, views
from .models import LoginHistory, TermsAcceptance, TermsOfUse, UserProfile, ViewHistory
from .test_review_list_layout import ReviewDOMParser


@override_settings(PASSWORD_MANAGER_USERNAMES=['Admin', 'SystenaAdmin'])
class UserTermsAcceptanceResetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.admin = users.objects.create_user(username='aDmIn', is_staff=True)
        cls.upper_root = users.objects.create_user(username='sYsTeNaAdMiN', is_superuser=True)
        cls.staff = users.objects.create_user(username='reset-staff', is_staff=True)
        cls.root = users.objects.create_user(username='reset-root', is_superuser=True)
        cls.named_member = users.objects.create_user(username='ADMIN')
        cls.member = users.objects.create_user(username='reset-member')
        cls.customer = users.objects.create_user(username='reset-customer')
        UserProfile.objects.create(user=cls.customer, user_type=UserProfile.USER_TYPE_CUSTOMER)
        cls.internal = users.objects.create_user(username='reset-internal')
        UserProfile.objects.create(user=cls.internal, user_type=UserProfile.USER_TYPE_SYSTENA)
        cls.group_admin = users.objects.create_user(username='reset-group-admin')
        cls.group_admin.groups.add(Group.objects.get_or_create(name='管理者')[0])
        cls.demo = users.objects.create_user(username='reset-demo')
        cls.demo.groups.add(Group.objects.get_or_create(name='demo')[0])
        cls.document = TermsOfUse.objects.create(title='現在の規約', description='現在の本文')
        for user in (cls.customer, cls.internal, cls.admin):
            cls.make_acceptance(user)

    @classmethod
    def make_acceptance(cls, user):
        return TermsAcceptance.objects.create(
            user=user, terms_updated_at=cls.document.updated_at,
            terms_title='以前の規約', terms_description='以前の本文',
            terms_snapshots=[{'id': cls.document.pk, 'title': '以前の規約',
                              'description': '以前の本文',
                              'updated_at': cls.document.updated_at.isoformat()}],
        )

    def url(self, user=None):
        return reverse('user_terms_acceptance_reset', args=[(user or self.customer).pk])

    def request(self, user, method='post', mode='', source='session'):
        request = getattr(RequestFactory(), method)(self.url(), {})
        request.user = user
        request.session = {'account_view_mode': mode} if mode and source == 'session' else {}
        if source == 'user':
            user._view_mode_override = mode
        request._messages = FallbackStorage(request)
        # Permission tests bypass CSRF just as Django's default test client does.
        # The separate CSRF test below uses a fully enforcing client.
        request._dont_enforce_csrf_checks = True
        return request

    def direct(self, user, pk=None, **kwargs):
        return views.user_terms_acceptance_reset(
            self.request(user, **kwargs), pk=self.customer.pk if pk is None else pk,
        )

    def test_exact_permission_matrix_and_denial_before_target_lookup(self):
        for actor in (self.staff, self.root, self.named_member, self.member,
                      self.customer, self.internal, self.group_admin, self.demo, AnonymousUser()):
            for pk in (self.customer.pk, 999999):
                with self.subTest(actor=str(actor), pk=pk):
                    with patch.object(views, 'get_object_or_404', side_effect=AssertionError('target lookup')):
                        with self.assertNumQueries(0):
                            self.assertEqual(self.direct(actor, pk=pk).status_code, 403)
        self.assertTrue(TermsAcceptance.objects.filter(user=self.customer).exists())
        for actor in (self.admin, self.upper_root):
            with self.subTest(allowed=actor.username):
                self.assertEqual(self.direct(actor).status_code, 302)
                self.assertFalse(TermsAcceptance.objects.filter(user=self.customer).exists())
                self.make_acceptance(self.customer)

    def test_preview_modes_deny_original_admin_flags_from_either_source(self):
        for original in (self.admin, self.upper_root):
            for mode in ('demo', 'cs', ' DEMO ', ' CS '):
                for source in ('session', 'user'):
                    with self.subTest(actor=original.username, mode=mode, source=source):
                        actor = get_user_model().objects.get(pk=original.pk)
                        with patch.object(views, 'get_object_or_404', side_effect=AssertionError('target lookup')):
                            self.assertEqual(self.direct(actor, mode=mode, source=source).status_code, 403)
        self.assertTrue(TermsAcceptance.objects.filter(user=self.customer).exists())

    def test_effective_flags_not_original_flags_control_permission(self):
        actor = get_user_model().objects.get(pk=self.admin.pk)
        actor._original_is_staff = True
        actor._original_is_superuser = True
        actor.is_staff = actor.is_superuser = False
        self.assertEqual(self.direct(actor).status_code, 403)

    def test_real_url_denies_staff_root_named_member_and_preview(self):
        for actor in (self.staff, self.root, self.named_member, self.member, self.demo):
            with self.subTest(actor=actor.username):
                client = Client()
                client.force_login(actor)
                self.assertEqual(client.post(self.url()).status_code, 403)
        for actor in (self.admin, self.upper_root):
            for mode in ('demo', 'cs'):
                with self.subTest(actor=actor.username, mode=mode):
                    client = Client()
                    client.force_login(actor)
                    session = client.session
                    session['account_view_mode'] = mode
                    session.save()
                    self.assertEqual(client.post(self.url()).status_code, 403)
        anonymous = self.client.post(self.url())
        self.assertEqual(anonymous.status_code, 302)
        self.assertTrue(anonymous.url.startswith(reverse('login') + '?next='))
        self.assertTrue(TermsAcceptance.objects.filter(user=self.customer).exists())

    def test_get_head_options_and_other_methods_are_not_mutating(self):
        self.client.force_login(self.admin)
        for method in ('get', 'head', 'options', 'put', 'patch', 'delete'):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url())
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.headers['Allow'], 'POST')
                self.assertTrue(TermsAcceptance.objects.filter(user=self.customer).exists())

    def test_csrf_required_and_valid_token_allows_reset(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(self.url()).status_code, 403)
        client.get(reverse('user_detail', args=[self.customer.pk]))
        self.assertEqual(client.post(self.url(), {'csrfmiddlewaretoken': 'invalid'}).status_code, 403)
        self.assertTrue(TermsAcceptance.objects.filter(user=self.customer).exists())
        response = client.post(self.url(), {'csrfmiddlewaretoken': client.cookies['csrftoken'].value})
        self.assertRedirects(response, reverse('user_detail', args=[self.customer.pk]),
                             fetch_redirect_response=False)
        self.assertFalse(TermsAcceptance.objects.filter(user=self.customer).exists())

    def test_only_url_target_record_is_removed_and_no_other_data_changes(self):
        self.client.force_login(self.admin)
        LoginHistory.objects.create(user=self.customer, username=self.customer.username)
        ViewHistory.objects.create(user=self.customer, username=self.customer.username,
                                   page_name='保存ページ', path='/faq/')
        other_records = list(TermsAcceptance.objects.exclude(user=self.customer).values())
        models = (get_user_model(), UserProfile, TermsOfUse, LoginHistory, ViewHistory)
        before = [list(model.objects.order_by('pk').values()) for model in models]
        response = self.client.post(self.url(), {'user': self.internal.pk, 'pk': self.admin.pk,
                                                'terms_accepted': 'on', 'accepted_at': 'forged'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TermsAcceptance.objects.filter(user=self.customer).exists())
        self.assertEqual(list(TermsAcceptance.objects.order_by('pk').values()), other_records)
        self.assertEqual([list(model.objects.order_by('pk').values()) for model in models], before)

    def test_reset_is_idempotent_when_record_is_absent(self):
        self.client.force_login(self.admin)
        for _ in range(2):
            self.assertEqual(self.client.post(self.url()).status_code, 302)
            self.assertFalse(TermsAcceptance.objects.filter(user=self.customer).exists())
        self.assertEqual(self.client.post(self.url(self.member)).status_code, 302)
        self.assertFalse(TermsAcceptance.objects.filter(user=self.member).exists())

    def test_unknown_user_is_404(self):
        self.client.force_login(self.admin)
        before = list(TermsAcceptance.objects.values())
        self.assertEqual(self.client.post(reverse('user_terms_acceptance_reset', args=[999999])).status_code, 404)
        self.assertEqual(list(TermsAcceptance.objects.values()), before)

    def test_noncustomer_and_admin_records_can_also_be_reset(self):
        self.client.force_login(self.upper_root)
        for target in (self.internal, self.admin):
            with self.subTest(target=target.username):
                self.assertEqual(self.client.post(self.url(target)).status_code, 302)
                self.assertFalse(TermsAcceptance.objects.filter(user=target).exists())

    def test_shared_terms_lock_is_acquired_inside_atomic_before_deletion(self):
        self.client.force_login(self.admin)
        baseline = len(connection.atomic_blocks)

        def lock():
            self.assertGreater(len(connection.atomic_blocks), baseline)
            self.assertTrue(TermsAcceptance.objects.filter(user=self.customer).exists())
            original_lock()

        original_lock = terms.lock_terms_set
        with patch.object(terms, 'lock_terms_set', side_effect=lock) as locked:
            self.assertEqual(self.client.post(self.url()).status_code, 302)
        locked.assert_called_once_with()
        self.assertFalse(TermsAcceptance.objects.filter(user=self.customer).exists())

    def test_lock_failure_does_not_remove_acceptance(self):
        self.client.force_login(self.admin)
        with patch.object(terms, 'lock_terms_set', side_effect=RuntimeError('lock failed')):
            with self.assertRaisesMessage(RuntimeError, 'lock failed'):
                self.client.post(self.url())
        self.assertTrue(TermsAcceptance.objects.filter(user=self.customer).exists())

    def test_safe_redirect_preserves_filtered_list_and_detail_paths(self):
        self.client.force_login(self.admin)
        for next_path in (reverse('user_list') + '?scope=customer&q=reset&page=2',
                          reverse('user_detail', args=[self.customer.pk])):
            with self.subTest(next_path=next_path):
                response = self.client.post(self.url(), {'next': next_path})
                self.assertRedirects(response, next_path, fetch_redirect_response=False)

    def test_unsafe_redirects_fall_back_to_user_detail(self):
        self.client.force_login(self.admin)
        for next_path in ('', 'https://evil.example/', '//evil.example/',
                          'javascript:alert(1)', '/\\evil.example/', 'http://testserver/users/'):
            with self.subTest(next_path=next_path):
                response = self.client.post(self.url(), {'next': next_path}, secure=True)
                self.assertRedirects(response, reverse('user_detail', args=[self.customer.pk]),
                                     fetch_redirect_response=False)

    def page(self, actor, detail=False, mode='', source='session'):
        request = self.request(actor, method='get', mode=mode, source=source)
        request.path = reverse('user_detail', args=[self.customer.pk]) if detail else reverse('user_list')
        if detail:
            response = views.UserDetailView.as_view()(request, pk=self.customer.pk)
        else:
            response = views.UserListView.as_view()(request)
        if hasattr(response, 'render'):
            response.render()
        return response

    def test_ui_permission_matrix_and_previews_on_both_screens(self):
        for original in (self.admin, self.upper_root, self.staff, self.root, self.named_member, self.member):
            for detail in (False, True):
                with self.subTest(actor=original.username, detail=detail):
                    response = self.page(original, detail=detail)
                    html = response.content.decode()
                    self.assertEqual(self.url() in html, original in (self.admin, self.upper_root))
        for original in (self.admin, self.upper_root):
            for mode in ('demo', 'cs'):
                for source in ('session', 'user'):
                    for detail in (False, True):
                        with self.subTest(actor=original.username, mode=mode, source=source, detail=detail):
                            actor = get_user_model().objects.get(pk=original.pk)
                            self.assertNotContains(self.page(actor, detail, mode, source), self.url())

    def test_forms_location_csrf_next_and_clear_confirmation(self):
        for detail in (False, True):
            with self.subTest(detail=detail):
                response = self.page(self.admin, detail=detail)
                parser = ReviewDOMParser()
                parser.feed(response.content.decode())
                form, = parser.find(tag='form', action=self.url())
                self.assertEqual(form['attrs']['method'], 'post')
                self.assertTrue(any(form in node['parents'] for node in parser.find(name='csrfmiddlewaretoken')))
                next_field, = [node for node in parser.find(name='next') if form in node['parents']]
                expected_next = reverse('user_detail', args=[self.customer.pk]) if detail else reverse('user_list')
                self.assertEqual(next_field['attrs']['value'], expected_next)
                button, = [node for node in parser.find(tag='button') if form in node['parents']]
                self.assertIn('現在の利用規約同意記録', button['attrs']['onclick'])
                self.assertIn('保存内容）を削除', button['attrs']['onclick'])
                self.assertIn('次回アクセス時に再同意が必要', button['attrs']['onclick'])
                if detail:
                    self.assertTrue(any(parent['tag'] == 'section'
                                        and parent['attrs'].get('aria-label') == '利用規約への同意'
                                        for parent in form['parents']))
                else:
                    self.assertTrue(any('dropdown-menu' in parent['attrs'].get('class', '')
                                        for parent in form['parents']))
                    panel, = parser.find(tag='section', **{'aria-labelledby': f'user-other-title-{self.customer.pk}'})
                    self.assertFalse(any(panel in node['parents'] for node in parser.find(tag='form')))

    def test_ui_hidden_without_acceptance_and_shown_for_noncustomer_record(self):
        self.client.force_login(self.admin)
        listing = self.client.get(reverse('user_list'))
        self.assertContains(listing, self.url(self.internal))
        self.assertNotContains(listing, self.url(self.member))
        self.assertContains(self.client.get(reverse('user_detail', args=[self.internal.pk])), self.url(self.internal))
        self.client.post(self.url())
        self.assertNotContains(self.client.get(reverse('user_list')), self.url())
        self.assertNotContains(self.client.get(reverse('user_detail', args=[self.customer.pk])), self.url())

    def test_custom_configured_name_uses_exact_permission_in_endpoint_and_ui(self):
        with override_settings(PASSWORD_MANAGER_USERNAMES=['ReSeT-StAfF']):
            self.assertEqual(self.direct(self.admin).status_code, 403)
            for detail in (False, True):
                self.assertNotContains(self.page(self.admin, detail), self.url())
                self.assertContains(self.page(self.staff, detail), self.url())
            self.assertEqual(self.direct(self.staff).status_code, 302)

    def test_existing_customer_session_is_gated_and_can_explicitly_reaccept(self):
        customer_client = Client()
        customer_client.force_login(self.customer)
        session_key = customer_client.session.session_key
        self.assertNotEqual(getattr(customer_client.get(reverse('home')), 'url', ''), reverse('terms_accept'))
        old_record = TermsAcceptance.objects.get(user=self.customer)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url()).status_code, 302)
        self.assertEqual(customer_client.session.session_key, session_key)
        self.assertEqual(str(customer_client.session['_auth_user_id']), str(self.customer.pk))
        for method in ('get', 'post'):
            self.assertRedirects(getattr(customer_client, method)(reverse('home')), reverse('terms_accept'),
                                 fetch_redirect_response=False)
        page = customer_client.get(reverse('terms_accept'))
        self.assertFalse(TermsAcceptance.objects.filter(user=self.customer).exists())
        payload = {'terms_token': page.context['terms_token']}
        self.assertEqual(customer_client.post(reverse('terms_accept'), payload).status_code, 400)
        payload['terms_accepted'] = 'on'
        self.assertRedirects(customer_client.post(reverse('terms_accept'), payload), reverse('home'),
                             fetch_redirect_response=False)
        new_record = TermsAcceptance.objects.get(user=self.customer)
        self.assertNotEqual(new_record.pk, old_record.pk)
        self.assertGreater(new_record.accepted_at, old_record.accepted_at)
        self.assertEqual(new_record.terms_snapshots, terms.terms_snapshots([self.document]))
        self.assertEqual(new_record.terms_title, self.document.title)
        self.assertNotEqual(getattr(customer_client.get(reverse('home')), 'url', ''), reverse('terms_accept'))