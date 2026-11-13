"""Login-history presentation and existing search/pagination contracts."""

from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import LoginHistory, UserProfile
from .test_review_list_layout import ReviewDOMParser
from .views import LoginHistoryListView


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.append(dict(attrs))


class LoginHistoryLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.staff = users.objects.create_user(username='history-staff', is_staff=True)
        cls.member = users.objects.create_user(username='history-member')
        UserProfile.objects.update_or_create(
            user=cls.member, defaults={'display_name': '現在の表示名'},
        )

    def page(self, params=None, user=None):
        request = RequestFactory().get(reverse('login_history_list'), params or {})
        request.user = self.staff if user is None else user
        request.session = {}
        request._messages = FallbackStorage(request)
        response = LoginHistoryListView.as_view()(request)
        if hasattr(response, 'render'):
            response.render()
        return response

    def test_empty_state_and_accessible_search_layout(self):
        response = self.page()
        self.assertContains(response, 'ログイン履歴がありません。')
        self.assertContains(response, '全<strong class="text-body">0</strong>件中')
        self.assertContains(response, 'role="search"')
        self.assertContains(response, 'id="loginHistoryResultsTitle"')
        self.assertContains(response, 'aria-label="ログイン履歴一覧（横スクロールできます）" tabindex="0"')
        self.assertContains(response, 'position: sticky; top: 0;')
        self.assertContains(response, '(max-height: 650px)')
        self.assertNotContains(response, '検索条件をクリア')
        self.assertNotContains(response, '>適用中</span>')

    def test_filter_button_opens_modal_containing_the_only_query_field(self):
        response = self.page({'q': '入力済み'})
        parser = ReviewDOMParser()
        parser.feed(response.content.decode())
        panel = parser.find(id='loginHistoryFilterPanel')[0]
        self.assertIn('modal', panel['attrs']['class'].split())
        self.assertEqual(panel['attrs']['aria-labelledby'], 'loginHistoryFilterLabel')
        fields = parser.find(tag='input', name='q')
        self.assertEqual(len(fields), 1)
        self.assertIn(panel, fields[0]['parents'])
        self.assertEqual(fields[0]['attrs']['value'], '入力済み')
        form = next(node for node in fields[0]['parents'] if node['tag'] == 'form')
        self.assertEqual(form['attrs']['method'], 'get')
        self.assertEqual(form['attrs']['action'], reverse('login_history_list'))
        self.assertFalse(parser.find(tag='input', name='page'))
        button = parser.find(tag='button', **{'data-bs-target': '#loginHistoryFilterPanel'})[0]
        self.assertNotIn(panel, button['parents'])
        self.assertEqual(button['attrs']['data-bs-toggle'], 'modal')
        self.assertContains(response, '>適用中</span>')
        self.assertContains(response, 'type="submit">絞り込み</button>')

    def test_search_matches_each_existing_target(self):
        record = LoginHistory.objects.create(user=self.member, username='記録時の名前')
        LoginHistory.objects.create(user=self.staff, username='別の人')
        for term in ('記録時の名前', self.member.username, '現在の表示名', '  記録時の名前  '):
            with self.subTest(term=term):
                response = self.page({'q': term})
                self.assertEqual(list(response.context_data['login_histories']), [record])
                self.assertContains(response, '・検索結果')
                self.assertContains(response, '>クリア</a>')

    def test_missing_search_has_clear_action_and_escaped_query(self):
        response = self.page({'q': '<script>alert("x")</script>'})
        self.assertContains(response, '検索条件に一致するログイン履歴がありません。')
        self.assertContains(response, '検索条件をクリア')
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert("x")</script>')

    def test_agent_full_text_is_escaped_in_native_disclosure(self):
        agent = 'Browser <script>alert("agent")</script> ' + 'long-agent/' * 50
        LoginHistory.objects.create(
            user=self.member, username='<b>記録名</b>',
            ip_address='2001:db8::1', user_agent=agent,
        )
        response = self.page()
        self.assertContains(response, '<details class="lh-agent">')
        self.assertContains(response, '詳細を表示')
        self.assertContains(response, '&lt;script&gt;alert(&quot;agent&quot;)&lt;/script&gt;')
        self.assertContains(response, '&lt;b&gt;記録名&lt;/b&gt;')
        self.assertContains(response, '2001:db8::1')
        self.assertNotContains(response, '<script>alert("agent")</script>')
        self.assertNotContains(response, 'ログイン中')

    def test_deleted_user_and_blank_agent_remain_readable(self):
        deleted = get_user_model().objects.create_user(username='deleted-history-user')
        record = LoginHistory.objects.create(user=deleted, username='削除前の記録名')
        deleted.delete()
        response = self.page()
        record.refresh_from_db()
        self.assertIsNone(record.user_id)
        self.assertContains(response, '削除前の記録名')
        self.assertContains(response, '<span class="font-monospace">-</span>')
        self.assertNotContains(response, '<details class="lh-agent">')

    def test_pagination_keeps_unicode_and_reserved_characters(self):
        term = '検索 & + 名前'
        records = [LoginHistory.objects.create(username=term) for _ in range(51)]
        start = timezone.now()
        for index, record in enumerate(records):
            LoginHistory.objects.filter(pk=record.pk).update(
                logged_in_at=start + timedelta(seconds=index),
            )
        first = self.page({'q': term})
        self.assertEqual(len(first.context_data['login_histories']), 50)
        self.assertEqual(first.context_data['login_histories'][0].pk, records[-1].pk)
        self.assertContains(first, '1–50')
        parser = LinkParser()
        parser.feed(first.content.decode())
        links = [parse_qs(urlsplit(link.get('href', '')).query)
                 for link in parser.links if 'page=' in link.get('href', '')]
        self.assertTrue(links)
        for params in links:
            self.assertEqual(params['q'], [term])
            self.assertEqual(params['page'], ['2'])
        second = self.page({'q': term, 'page': 2})
        self.assertEqual(len(second.context_data['login_histories']), 1)
        self.assertContains(second, '51–51')
        self.assertContains(second, 'aria-label="最初のページ"')

    def test_existing_permission_contract_is_unchanged(self):
        response = self.page(user=self.member)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('article_list'))
        with self.assertRaises(PermissionDenied):
            self.page(user=AnonymousUser())
