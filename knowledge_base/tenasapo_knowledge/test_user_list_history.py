"""User management retains its contracts while embedding existing history UI."""

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import LoginHistory, UserProfile, ViewHistory
from .test_login_history_layout import LinkParser
from .test_review_list_layout import ReviewDOMParser
from .views import UserListView


class UserListHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.staff = users.objects.create_user(username='user-history-staff', is_staff=True)
        cls.member = users.objects.create_user(username='user-history-member')
        UserProfile.objects.update_or_create(user=cls.member, defaults={
            'display_name': '顧客 <& "名前">', 'uid': '456789',
            'user_type': UserProfile.USER_TYPE_CUSTOMER, 'company_name': '顧客 & + 社',
        })
        cls.newer = LoginHistory.objects.create(
            user=cls.member, username='顧客', ip_address='2001:db8::1',
        )
        cls.older = LoginHistory.objects.create(
            user=cls.member, username='顧客', logged_out_at=timezone.now(),
        )
        LoginHistory.objects.filter(pk=cls.older.pk).update(
            logged_in_at=timezone.now() - timedelta(days=1),
        )
        LoginHistory.objects.create(user=cls.staff, username='管理者', ip_address='192.0.2.99')
        ViewHistory.objects.create(user=cls.member, username='顧客', page_name='非表示ページ',
                                   path='/private-history/', search_query='非表示の検索語')

    def request(self, params=None, user=None):
        request = RequestFactory().get(reverse('user_list'), params or {})
        request.user = self.staff if user is None else user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def page(self, params=None, user=None):
        response = UserListView.as_view()(self.request(params, user))
        if hasattr(response, 'render'):
            response.render()
        return response

    def test_only_matching_users_records_are_loaded_in_order(self):
        response = self.page({'q': '456789'})
        account, = response.context_data['users']
        self.assertEqual(account.pk, self.member.pk)
        with self.assertNumQueries(0):
            self.assertEqual([item.pk for item in account.history_login_records],
                             [self.newer.pk, self.older.pk])
            self.assertEqual(account.history_login_records[0].ip_address, '2001:db8::1')
        self.assertContains(response, '2001:db8::1')
        self.assertNotContains(response, '192.0.2.99')
        self.assertContains(response, 'ログアウト未記録')
        self.assertContains(response, '顧客 &lt;&amp; &quot;名前&quot;&gt;')
        self.assertNotContains(response, '非表示ページ')
        self.assertNotContains(response, '非表示の検索語')

    def test_history_is_in_modal_outside_account_collapse(self):
        response = self.page({'q': self.member.username})
        parser = ReviewDOMParser()
        parser.feed(response.content.decode())
        body = parser.find(id=f'user-details-{self.member.pk}')[0]
        history = parser.find(id=f'user-history-{self.member.pk}')[0]
        modal = parser.find(id=f'userHistoryModal-{self.member.pk}')[0]
        self.assertIn(modal, history['parents'])
        self.assertNotIn(body, history['parents'])
        self.assertEqual(modal['attrs']['aria-labelledby'], f'user-history-title-{self.member.pk}')
        triggers = parser.find(tag='button', **{'data-bs-target': f'#userHistoryModal-{self.member.pk}'})
        self.assertEqual(len(triggers), 1)
        for trigger in triggers:
            self.assertEqual(trigger['attrs']['data-bs-toggle'], 'modal')
            self.assertEqual(trigger['attrs']['aria-haspopup'], 'dialog')
        self.assertEqual(body['attrs']['data-bs-parent'], '#userListAccordion')
        self.assertEqual(body['attrs']['aria-labelledby'], f'user-heading-{self.member.pk}')
        self.assertEqual(history['attrs']['aria-labelledby'], f'user-history-title-{self.member.pk}')

    def test_empty_history_and_existing_self_management_controls(self):
        LoginHistory.objects.filter(user=self.staff).delete()
        response = self.page({'q': self.staff.username})
        self.assertContains(response, 'ログイン／ログアウト履歴はありません。')
        self.assertContains(response, reverse('user_edit', args=[self.staff.pk]))
        self.assertContains(response, f'data-bs-target="#passwordChangeModal{self.staff.pk}"')
        self.assertContains(response, f'id="passwordChangeModal{self.staff.pk}"')
        self.assertContains(response, f'id="deleteModal{self.staff.pk}"')
        self.assertContains(response, reverse('user_password_reset_link_issue', args=[self.staff.pk]))
        self.assertContains(response, reverse('user_contract_issue'))
        self.assertContains(response, reverse('user_demo_issue'))
        self.assertNotContains(response, 'contractAccountIssueModal')
        self.assertNotContains(response, 'demoAccountIssueModal')

    def test_staff_customer_password_actions_do_not_grant_edit(self):
        response = self.page({'q': self.member.username})
        self.assertContains(response, f'id="passwordChangeModal{self.member.pk}"')
        self.assertContains(response, reverse('user_password_reset_link_issue', args=[self.member.pk]))
        self.assertNotContains(response, reverse('user_edit', args=[self.member.pk]))
        self.assertNotContains(response, f'data-bs-target="#deleteModal{self.member.pk}"')

    def test_filter_modal_keeps_six_fields_and_sidebar(self):
        params = {'q': '顧客', 'authority': 'user', 'scope': 'customer',
                  'customer_name': '顧客 & + 社'}
        response = self.page(params)
        parser = ReviewDOMParser()
        parser.feed(response.content.decode())
        panel = parser.find(id='userListFilterPanel')[0]
        fields = [node for node in parser.nodes if panel in node['parents']
                  and node['tag'] in ('input', 'select') and 'name' in node['attrs']]
        self.assertEqual({node['attrs']['name'] for node in fields},
                         {'q', 'authority', 'scope', 'department', 'profile_group', 'customer_name'})
        self.assertEqual(len(fields), 6)
        self.assertEqual(parser.find(name='q')[0]['attrs']['value'], params['q'])
        self.assertContains(response, 'ユーザーフィルタ')
        self.assertContains(response, '>適用中</span>')
        self.assertEqual(list(response.context_data['users'])[0].pk, self.member.pk)

    def test_pagination_fetches_history_once_for_current_page_only(self):
        for index in range(21):
            user = get_user_model().objects.create_user(username=f'paginated-history-{index:02}')
            UserProfile.objects.update_or_create(user=user, defaults={
                'display_name': f'一覧{index:02}', 'uid': f'{index:06}',
                'user_type': UserProfile.USER_TYPE_CUSTOMER, 'company_name': 'ページ & + 社',
            })
            LoginHistory.objects.create(user=user, username=user.username)
        params = {'q': 'paginated-history-', 'authority': 'user', 'scope': 'customer',
                  'customer_name': 'ページ & + 社'}
        with CaptureQueriesContext(connection) as captured:
            first = self.page(params)
        history_queries = [entry['sql'] for entry in captured
                           if 'FROM "tenasapo_knowledge_loginhistory"' in entry['sql']]
        self.assertEqual(len(history_queries), 1)
        first_users = list(first.context_data['users'])
        self.assertEqual(len(first_users), 20)
        self.assertEqual(first.context_data['paginator'].count, 21)
        second = self.page({**params, 'page': 2})
        second_user, = second.context_data['users']
        self.assertNotIn(second_user.pk, {item.pk for item in first_users})
        self.assertEqual([record.user_id for record in second_user.history_login_records], [second_user.pk])
        for user in first_users:
            self.assertEqual([record.user_id for record in user.history_login_records], [user.pk])
        self.assertNotIn(str(second_user.pk), history_queries[0].split(' IN (', 1)[1].split(')', 1)[0].split(', '))
        parser = LinkParser()
        parser.feed(first.content.decode())
        links = [parse_qs(urlsplit(link.get('href', '')).query) for link in parser.links
                 if link.get('href', '').startswith('?page=')]
        self.assertTrue(links)
        for link in links:
            for key, value in params.items():
                self.assertEqual(link[key], [value])

    def test_empty_results_and_permission_contract(self):
        response = self.page({'q': 'nonexistent-history-user'})
        self.assertContains(response, 'ユーザーが見つかりません。')
        response = self.page(user=self.member)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('article_list'))
        with self.assertRaises(PermissionDenied):
            self.page(user=AnonymousUser())

    def test_issued_account_result_is_still_consumed_once(self):
        request = self.request({'q': self.staff.username})
        request.session['issued_password_reset_link'] = {'url': 'https://example.test/reset/'}
        view = UserListView()
        view.setup(request)
        view.object_list = view.get_queryset()
        context = view.get_context_data()
        self.assertEqual(context['issued_password_reset_link'], {'url': 'https://example.test/reset/'})
        self.assertNotIn('issued_password_reset_link', request.session)

    def test_review_style_table_and_detail_row_contract(self):
        response = self.page({'q': self.member.username})
        parser = ReviewDOMParser()
        parser.feed(response.content.decode())
        body = parser.find(id='userListAccordion')[0]
        self.assertEqual(body['tag'], 'tbody')
        table = next(node for node in body['parents'] if node['tag'] == 'table')
        headers = [node for node in parser.find(tag='th') if table in node['parents']
                   and not any(parent['tag'] == 'tbody' for parent in node['parents'])]
        self.assertEqual(len(headers), 4)
        details = parser.find(id=f'user-details-{self.member.pk}')[0]
        cell = next(node for node in reversed(details['parents']) if node['tag'] == 'td')
        self.assertEqual(cell['attrs']['colspan'], '4')
        toggle = parser.find(id=f'user-heading-{self.member.pk}')[0]
        self.assertEqual(toggle['tag'], 'button')
        self.assertEqual(toggle['attrs']['data-bs-target'], f'#user-details-{self.member.pk}')
        self.assertContains(response, 'position: sticky; top: 0; z-index: 2;')

    def test_latest_login_button_uses_newest_history_not_user_last_login(self):
        get_user_model().objects.filter(pk=self.member.pk).update(
            last_login=timezone.now() - timedelta(days=60),
        )
        response = self.page({'q': self.member.username})
        parser = ReviewDOMParser()
        parser.feed(response.content.decode())
        table_body = parser.find(id='userListAccordion')[0]
        times = [node for node in parser.find(tag='time') if table_body in node['parents']]
        self.assertEqual(len(times), 1)
        self.newer.refresh_from_db()
        self.assertEqual(times[0]['attrs']['datetime'],
                         timezone.localtime(self.newer.logged_in_at).isoformat())
        self.assertContains(response, '>最終ログイン日時</th>')
        self.assertNotContains(response, '>分析</th>')
        self.assertNotContains(response, '分析対象')
        self.assertNotContains(response, '分析除外')

    def test_unrecorded_latest_login_still_opens_empty_modal(self):
        LoginHistory.objects.filter(user=self.member).delete()
        response = self.page({'q': self.member.username})
        self.assertContains(response, '未記録')
        self.assertContains(response, 'ログイン／ログアウト履歴はありません。')
        self.assertContains(response, f'data-bs-target="#userHistoryModal-{self.member.pk}"', count=1)

    def test_history_icon_is_separate_from_time_and_metadata_order(self):
        response = self.page({'q': self.member.username})
        parser = ReviewDOMParser()
        parser.feed(response.content.decode())
        trigger, = parser.find(tag='button', **{'data-bs-target': f'#userHistoryModal-{self.member.pk}'})
        icons = [node for node in parser.find(tag='i') if trigger in node['parents']]
        self.assertEqual(len(icons), 1)
        self.assertIn('bi-clock-history', icons[0]['attrs']['class'])
        self.assertTrue(trigger['attrs']['aria-label'])
        for node in parser.find(tag='time'):
            self.assertNotIn(trigger, node['parents'])
        self.assertNotContains(response, '>ログイン履歴</th>')
        html = response.content.decode()
        metadata = html.split('<div class="user-account-meta">', 1)[1].split('</div>', 1)[0]
        self.assertLess(metadata.index('管理番号'), metadata.index('ログインID'))

    def test_information_panels_and_row_click_target_keep_details(self):
        response = self.page({'q': self.member.username})
        parser = ReviewDOMParser()
        parser.feed(response.content.decode())
        detail = parser.find(id=f'user-details-{self.member.pk}')[0]
        row, = parser.find(tag='tr', **{'data-user-detail-target': detail['attrs']['id']})
        self.assertIn('user-summary-row', row['attrs']['class'])
        for kind in ('basic', 'other'):
            heading, = parser.find(id=f'user-{kind}-title-{self.member.pk}')
            self.assertIn(detail, heading['parents'])
            section = next(node for node in heading['parents'] if node['tag'] == 'section')
            self.assertIn('user-info-panel', section['attrs']['class'])
            self.assertEqual(section['attrs']['aria-labelledby'], heading['attrs']['id'])
            tables = [node for node in parser.find(tag='table') if section in node['parents']]
            self.assertEqual(len(tables), 1)
            self.assertIn('user-info-table', tables[0]['attrs']['class'])

    def test_customer_navigation_highlights_only_selected_leaf_and_keeps_search(self):
        params = {'q': '顧客', 'authority': 'user', 'scope': 'customer',
                  'customer_name': '顧客 & + 社'}
        response = self.page(params)
        parser = ReviewDOMParser()
        parser.feed(response.content.decode())
        nav = parser.find(tag='nav', **{'aria-labelledby': 'userFilterHeading'})[0]
        active = [node for node in parser.find(tag='a') if nav in node['parents']
                  and node['attrs'].get('aria-current') == 'page']
        self.assertEqual(len(active), 1)
        self.assertEqual(parse_qs(urlsplit(active[0]['attrs']['href']).query),
                         {key: [value] for key, value in params.items()})
        branches = [node for node in active[0]['parents'] if node['tag'] == 'details']
        self.assertTrue(branches)
        self.assertTrue(all('open' in node['attrs'] for node in branches))
        reset = parser.find(tag='a', **{'aria-label': '所属の絞り込みをリセット（検索・権限は保持）'})[0]
        self.assertEqual(parse_qs(urlsplit(reset['attrs']['href']).query),
                         {'q': ['顧客'], 'authority': ['user'], 'scope': ['all']})

    def test_department_and_group_branch_opens_selected_path(self):
        UserProfile.objects.filter(user=self.member).update(
            user_type=UserProfile.USER_TYPE_SYSTENA, department='部署 & 開発', group='支援 + チーム',
        )
        params = {'q': '顧客', 'authority': 'user', 'scope': 'systena',
                  'department': '部署 & 開発', 'profile_group': '支援 + チーム'}
        response = self.page(params)
        parser = ReviewDOMParser()
        parser.feed(response.content.decode())
        nav = parser.find(tag='nav', **{'aria-labelledby': 'userFilterHeading'})[0]
        active = [node for node in parser.find(tag='a') if nav in node['parents']
                  and node['attrs'].get('aria-current') == 'page']
        self.assertEqual(len(active), 1)
        self.assertEqual(parse_qs(urlsplit(active[0]['attrs']['href']).query),
                         {key: [value] for key, value in params.items()})
        branches = [node for node in active[0]['parents'] if node['tag'] == 'details']
        self.assertEqual(len(branches), 2)
        self.assertTrue(all('open' in node['attrs'] for node in branches))