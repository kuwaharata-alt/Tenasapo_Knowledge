"""Upper-admin-only history access and navigation; no history data is removed."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from . import views
from .context_processors import user_display_name
from .models import Manual, UserProfile, ViewHistory
from .test_review_list_layout import ReviewDOMParser


@override_settings(PASSWORD_MANAGER_USERNAMES=['Admin', 'SystenaAdmin'])
class ViewHistoryPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.admin = users.objects.create_user(username='aDmIn', is_staff=True)
        cls.systena_admin = users.objects.create_user(
            username='sYsTeNaAdMiN', is_superuser=True, is_staff=False,
        )
        cls.staff = users.objects.create_user(username='history-staff', is_staff=True)
        cls.superuser = users.objects.create_user(username='other-root', is_superuser=True)
        cls.member = users.objects.create_user(username='history-reader')
        cls.named_member = users.objects.create_user(username='ADMIN')
        cls.record = ViewHistory.objects.create(
            user=cls.member, username='保存名', page_name='保存ページ', path='/articles/',
        )
        cls.manual = Manual.objects.create(
            menu_key='view-history', title='閲覧履歴の使い方', description='履歴マニュアル本文を維持',
        )

    def setUp(self):
        self.factory = RequestFactory()
        # Existing views cache the configured names at import time.
        self.enterContext(patch.object(views, 'PASSWORD_MANAGER_USERNAMES', {'admin', 'systenaadmin'}))

    def request(self, user, mode=''):
        request = self.factory.get(reverse('view_history_list'))
        request.user = user
        request.session = {'account_view_mode': mode} if mode else {}
        request._messages = FallbackStorage(request)
        return request

    def page(self, request):
        return views.ViewHistoryListView.as_view()(request)

    def assert_denied(self, request):
        # Denial occurs before any private queryset is evaluated.
        with patch.object(views.ViewHistoryListView, 'get_queryset', side_effect=AssertionError('private query')):
            response = self.page(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('article_list'))
        self.assertTrue(ViewHistory.objects.filter(pk=self.record.pk).exists())

    def test_only_configured_names_with_effective_staff_or_superuser_flags_can_access(self):
        for user in (self.admin, self.systena_admin):
            with self.subTest(user=user.username):
                self.assertEqual(self.page(self.request(user)).status_code, 200)
        for user in (self.staff, self.superuser, self.member, self.named_member):
            with self.subTest(user=user.username):
                self.assert_denied(self.request(user))

    def test_anonymous_direct_view_keeps_existing_denial_and_real_url_requires_login(self):
        with self.assertRaises(PermissionDenied):
            self.page(self.request(AnonymousUser()))
        response = self.client.get(reverse('view_history_list'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login') + '?next='))

    def test_real_url_rejects_staff_and_unconfigured_superuser(self):
        for user in (self.staff, self.superuser):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertRedirects(self.client.get(reverse('view_history_list')),
                                     reverse('article_list'), fetch_redirect_response=False)

    def test_preview_modes_block_even_before_middleware_clears_effective_flags(self):
        for original in (self.admin, self.systena_admin):
            for mode in ('demo', 'cs'):
                for source in ('session', 'user'):
                    with self.subTest(user=original.username, mode=mode, source=source):
                        user = get_user_model().objects.get(pk=original.pk)
                        request = self.request(user, mode if source == 'session' else '')
                        if source == 'user':
                            user._view_mode_override = mode
                        self.assert_denied(request)
                        self.assertFalse(user_display_name(request)['can_view_view_history'])
                        self.assertNotIn('view_history_list', self.home_routes(request))

    def home_routes(self, request):
        view = views.HomeView()
        view.setup(request)
        return {item['url_name'] for group in view.get_context_data()['menu_groups']
                for item in group['items']}

    def test_home_menu_only_exposes_history_to_upper_admins_and_keeps_login_history(self):
        for user in (self.admin, self.systena_admin, self.staff, self.superuser,
                     self.member, self.named_member, AnonymousUser()):
            with self.subTest(user=str(user)):
                routes = self.home_routes(self.request(user))
                self.assertEqual('view_history_list' in routes, user in (self.admin, self.systena_admin))
                if user.is_staff or user.is_superuser:
                    self.assertIn('login_history_list', routes)
                    self.assertIn('manual_list', routes)

    def test_shared_desktop_mobile_sidebar_uses_flag_and_never_grants_superuser_bypass(self):
        for user in (self.admin, self.systena_admin, self.staff, self.superuser,
                     self.member, self.named_member, AnonymousUser()):
            with self.subTest(user=str(user)):
                request = self.request(user)
                allowed = user in (self.admin, self.systena_admin)
                self.assertEqual(user_display_name(request)['can_view_view_history'], allowed)
                html = render_to_string('base.html', {}, request=request)
                parser = ReviewDOMParser()
                parser.feed(html)
                links = parser.find(tag='a', href=reverse('view_history_list'))
                self.assertEqual(len(links), int(allowed))
                if allowed:
                    sidebar, = parser.find(id='app-sidebar')
                    self.assertIn('offcanvas-lg', sidebar['attrs']['class'])
                    self.assertIn('offcanvas-start', sidebar['attrs']['class'])
                    self.assertIn(sidebar, links[0]['parents'])

    def test_configured_custom_upper_admin_name_is_case_insensitive_everywhere(self):
        with override_settings(PASSWORD_MANAGER_USERNAMES=['HiStOrY-StAfF']), patch.object(
            views, 'PASSWORD_MANAGER_USERNAMES', {'history-staff'},
        ):
            request = self.request(self.staff)
            self.assertEqual(self.page(request).status_code, 200)
            self.assertTrue(user_display_name(request)['can_view_view_history'])
            self.assertIn('view_history_list', self.home_routes(request))
            self.assert_denied(self.request(self.admin))

    def test_customer_and_demo_sidebar_remain_hidden_for_named_staff(self):
        for source in ('profile', 'customer-group', 'demo-group'):
            with self.subTest(source=source):
                user = get_user_model().objects.get(pk=self.admin.pk)
                if source == 'profile':
                    UserProfile.objects.update_or_create(user=user, defaults={'user_type': 'customer'})
                else:
                    UserProfile.objects.filter(user=user).delete()
                    group, _ = Group.objects.get_or_create(
                        name='カスタマー' if source == 'customer-group' else 'demo',
                    )
                    user.groups.set([group])
                request = self.request(user)
                self.assertFalse(user_display_name(request)['can_view_view_history'])
                self.assertNotIn('href="' + reverse('view_history_list') + '"',
                                 render_to_string('base.html', {}, request=request))

    def test_manual_text_stays_accessible_but_history_menu_link_is_upper_admin_only(self):
        before = list(Manual.objects.values())
        for user in (self.admin, self.systena_admin, self.staff, self.superuser):
            with self.subTest(user=user.username):
                request = self.request(user)
                response = views.ManualListView.as_view()(request)
                response.render()
                self.assertContains(response, self.manual.description)
                self.assertContains(response, 'id="manual-view-history"')
                parser = ReviewDOMParser()
                parser.feed(response.content.decode())
                section, = parser.find(id='manual-view-history')
                links = [node for node in parser.find(tag='a', href=reverse('view_history_list'))
                         if section in node['parents']]
                self.assertEqual(len(links), int(user in (self.admin, self.systena_admin)))
        self.assertEqual(list(Manual.objects.values()), before)