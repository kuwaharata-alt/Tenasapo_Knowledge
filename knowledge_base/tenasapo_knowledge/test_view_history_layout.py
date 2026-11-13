"""User-grouped history UI, filter and pagination regression tests."""

from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import LoginHistory, UserProfile, ViewHistory
from .test_login_history_layout import LinkParser
from .test_review_list_layout import ReviewDOMParser
from .views import ViewHistoryListView


class ViewHistoryLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.staff = users.objects.create_user(username='Admin', is_staff=True)
        cls.member = users.objects.create_user(username='view-member')
        UserProfile.objects.update_or_create(
            user=cls.member, defaults={'display_name': '表示名 <& "ユーザー">'},
        )
        cls.params = {'q': 'view-member', 'page_name': 'FAQ & + 一覧',
                      'parent_category': 'PC/設定', 'category': 'ネット & 通信'}
        ViewHistory.objects.create(
            user=cls.member, username='記録名', path='/articles/',
            page_name=cls.params['page_name'],
            parent_category=cls.params['parent_category'],
            category=cls.params['category'], search_query='非表示の閲覧検索語',
        )

    def page(self, params=None, user=None):
        request = RequestFactory().get(reverse('view_history_list'), params or {})
        request.user = self.staff if user is None else user
        request.session = {}
        request._messages = FallbackStorage(request)
        response = ViewHistoryListView.as_view()(request)
        if hasattr(response, 'render'):
            response.render()
        return response

    def test_all_filter_controls_are_in_modal_and_preserve_values(self):
        response = self.page(self.params)
        parser = ReviewDOMParser()
        parser.feed(response.content.decode())
        panel = parser.find(id='viewHistoryFilterPanel')[0]
        self.assertIn('modal', panel['attrs']['class'].split())
        self.assertEqual(panel['attrs']['aria-labelledby'], 'viewHistoryFilterLabel')
        controls = [node for node in parser.nodes
                    if node['tag'] in ('input', 'select')
                    and node['attrs'].get('name') in self.params]
        self.assertEqual({node['attrs']['name'] for node in controls}, set(self.params))
        self.assertEqual(len(controls), 4)
        form_nodes = [node for node in parser.find(tag='form') if panel in node['parents']]
        self.assertEqual(len(form_nodes), 1)
        filter_controls = [node for node in parser.nodes if form_nodes[0] in node['parents']
                           and node['tag'] in ('input', 'select') and 'name' in node['attrs']]
        self.assertEqual({node['attrs']['name'] for node in filter_controls}, set(self.params))
        for node in controls:
            self.assertIn(panel, node['parents'])
        self.assertEqual(parser.find(name='q')[0]['attrs']['value'], self.params['q'])
        for name in ('page_name', 'parent_category', 'category'):
            select = parser.find(tag='select', name=name)[0]
            selected = [node for node in parser.find(tag='option')
                        if select in node['parents'] and 'selected' in node['attrs']]
            self.assertEqual([node['attrs']['value'] for node in selected], [self.params[name]])
        form = next(node for node in controls[0]['parents'] if node['tag'] == 'form')
        self.assertEqual(form['attrs']['method'], 'get')
        self.assertEqual(form['attrs']['action'], reverse('view_history_list'))
        button = parser.find(tag='button', **{'data-bs-target': '#viewHistoryFilterPanel'})[0]
        self.assertNotIn(panel, button['parents'])
        self.assertContains(response, '>適用中</span>')

    def test_existing_filter_targets_select_matching_user(self):
        for params in ({'q': '表示名'}, {'q': '  view-member  '},
                       *({key: value} for key, value in self.params.items()), self.params):
            with self.subTest(params=params):
                response = self.page(params)
                self.assertEqual(list(response.context_data['users']), [self.member])

    def test_group_disclosure_and_login_details_are_accessible_and_escaped(self):
        LoginHistory.objects.create(user=self.member, username='記録名', ip_address='2001:db8::1')
        LoginHistory.objects.create(user=self.member, username='記録名', logged_out_at=timezone.now())
        response = self.page({'q': 'view-member'})
        self.assertContains(response, 'ログイン 2件')
        self.assertContains(response, 'ログアウト未記録')
        self.assertNotContains(response, 'ログイン中')
        self.assertContains(response, '2001:db8::1')
        self.assertContains(response, '表示名 &lt;&amp; &quot;ユーザー&quot;&gt;')
        self.assertNotContains(response, '非表示の閲覧検索語')
        self.assertContains(response, '現在、閲覧履歴は非表示です。')
        parser = ReviewDOMParser()
        parser.feed(response.content.decode())
        heading = parser.find(id='userHeading1')[0]
        body = parser.find(id='userBody1')[0]
        button = parser.find(tag='button', **{'data-bs-target': '#userBody1'})[0]
        self.assertIn(heading, button['parents'])
        self.assertEqual(body['attrs']['aria-labelledby'], heading['attrs']['id'])
        self.assertEqual(body['attrs']['data-bs-parent'], '#userAccordion')
        self.assertEqual(button['attrs']['aria-controls'], body['attrs']['id'])

    def test_no_login_history_and_inactive_filter_state(self):
        response = self.page()
        self.assertContains(response, 'ログイン履歴がありません。')
        self.assertNotContains(response, '>適用中</span>')
        self.assertContains(response, 'aria-describedby="viewHistoryNotice" tabindex="0"')
        self.assertContains(response, '(max-height: 650px)')

    def test_no_matches_and_escaped_query(self):
        response = self.page({'q': '<script>invalid()</script>'})
        self.assertContains(response, '条件に一致するユーザーがいません。')
        self.assertContains(response, '条件をクリア')
        self.assertContains(response, '&lt;script&gt;invalid()&lt;/script&gt;')
        self.assertNotContains(response, '<script>invalid()</script>')
        self.assertContains(response, '0–0')

    def test_twenty_user_pagination_preserves_all_conditions(self):
        term = '検索 & + 名前'
        for index in range(21):
            user = get_user_model().objects.create_user(username=f'view-page-{index:02}')
            UserProfile.objects.update_or_create(user=user, defaults={'display_name': term})
            ViewHistory.objects.create(
                user=user, username=term, path='/articles/',
                page_name=self.params['page_name'],
                parent_category=self.params['parent_category'], category=self.params['category'],
            )
        params = {**self.params, 'q': term}
        first = self.page(params)
        self.assertEqual(len(first.context_data['grouped_user_histories']), 20)
        self.assertEqual(first.context_data['paginator'].count, 21)
        self.assertContains(first, '1–20')
        parser = LinkParser()
        parser.feed(first.content.decode())
        links = [parse_qs(urlsplit(link.get('href', '')).query)
                 for link in parser.links if 'page=' in link.get('href', '')]
        self.assertTrue(links)
        for link in links:
            self.assertEqual(link, {**{key: [value] for key, value in params.items()}, 'page': ['2']})
        second = self.page({**params, 'page': 2})
        self.assertEqual(len(second.context_data['grouped_user_histories']), 1)
        self.assertContains(second, '21–21')
        self.assertContains(second, 'aria-label="最初のページ"')

    def test_permission_contract_is_unchanged(self):
        response = self.page(user=self.member)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('article_list'))
        with self.assertRaises(PermissionDenied):
            self.page(user=AnonymousUser())