"""Dedicated issuance pages retain the existing account issuance contracts."""

from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from .forms import UserContractIssueForm, UserDemoIssueForm
from .models import Customer, UserProfile
from .test_review_list_layout import ReviewDOMParser
from .views import DEMO_GROUP_NAME, UserContractIssueView, UserDemoIssueView


class UserIssuePageTests(TestCase):
    routes = (
        ('user_demo_issue', UserDemoIssueView, UserDemoIssueForm),
        ('user_contract_issue', UserContractIssueView, UserContractIssueForm),
    )

    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.staff = users.objects.create_user(username='issue-page-staff', is_staff=True)
        cls.superuser = users.objects.create_user(
            username='issue-page-superuser', is_superuser=True, is_staff=False,
        )
        cls.member = users.objects.create_user(username='issue-page-member')
        # A real second page requires enough matching customers, and customer
        # filters cannot also select the Systena-only department/group tree.
        for index in range(21):
            user = users.objects.create_user(username=f'issue-filter-customer-{index:02}')
            UserProfile.objects.update_or_create(user=user, defaults={
                'display_name': '顧客 & + 名前', 'company_name': '顧客 & + 社',
                'user_type': UserProfile.USER_TYPE_CUSTOMER,
            })

    def setUp(self):
        self.client.force_login(self.staff)
        self.params = {
            'q': '顧客 & + 名前', 'authority': 'user', 'scope': 'customer',
            'customer_name': '顧客 & + 社', 'department': '',
            'profile_group': '', 'page': '2',
        }
        self.return_to = reverse('user_list') + '?' + urlencode(self.params)
        self.contract = {
            'display_name': ' 契約 <& "名前"> ', 'company_name': ' 顧客 & + 社 ',
            'department': ' 開発 & 支援 ', 'email_addresses': ' Contract.User@example.test ',
            'note': ' 備考 <& "保持">\n二行目 ', 'next': self.return_to,
        }

    def payload(self, route):
        return ({'company_name': self.contract['company_name'], 'next': self.return_to}
                if route == 'user_demo_issue' else dict(self.contract))

    def dom(self, response):
        parser = ReviewDOMParser()
        parser.feed(response.content.decode())
        return parser

    def assert_bound_values(self, response, payload, fields):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'tenasapo_knowledge/user_issue_form.html')
        form = response.context['form']
        self.assertTrue(form.is_bound)
        parser = self.dom(response)
        for name in fields:
            self.assertEqual(form[name].value(), payload[name])
            node, = parser.find(name=name)
            actual = node['text'] if node['tag'] == 'textarea' else node['attrs'].get('value', '')
            # Django's textarea template inserts a leading newline; browsers
            # discard exactly that one newline when parsing textarea content.
            if node['tag'] == 'textarea' and actual.startswith('\n'):
                actual = actual[1:]
            self.assertEqual(actual, payload[name])
        return form

    def test_existing_named_urls_get_pages_without_issuing(self):
        before = get_user_model().objects.count()
        for route, view, form_class in self.routes:
            with self.subTest(route=route):
                self.assertIs(resolve(reverse(route)).func.view_class, view)
                response = self.client.get(reverse(route), {'next': self.return_to})
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, 'tenasapo_knowledge/user_issue_form.html')
                self.assertIsInstance(response.context['form'], form_class)
                self.assertFalse(response.context['form'].is_bound)
                self.assertEqual(response.context['return_to'], self.return_to)
                self.assertContains(response, '管理者アカウントのみ利用できます。')
                self.assertContains(response, 'page-title-sub')
                self.assertNotContains(response, 'user-company-field')
                self.assertNotContains(response, 'user_form_company_assets')
        self.assertEqual(get_user_model().objects.count(), before)

    def test_layout_labels_sections_and_fixed_policy_have_no_editable_controls(self):
        for route, _, _ in self.routes:
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                parser = self.dom(response)
                form, = parser.find(tag='form', **{'data-knowledge-completion': 'user-issue'})
                self.assertIn('card', form['attrs']['class'].split())
                nav, = parser.find(css_class='knowledge-form-nav')
                self.assertEqual(nav['attrs']['aria-label'], '入力項目へ移動')
                links = [node for node in parser.find(tag='a') if nav in node['parents']]
                expected_sections = ['account', 'permissions']
                if route == 'user_contract_issue':
                    expected_sections.append('other')
                self.assertEqual([node['attrs']['href'] for node in links],
                                 ['#form-section-' + key for key in expected_sections])
                for key in expected_sections:
                    heading, = parser.find(id='form-section-' + key)
                    self.assertIn('data-form-section', heading['attrs'])
                    enclosure = heading['parents'][-1]
                    self.assertNotIn('border', enclosure['attrs'].get('class', '').split())
                controls = [node for node in parser.nodes if form in node['parents']
                            and node['tag'] in ('input', 'textarea', 'select')
                            and node['attrs'].get('type') != 'hidden'
                            and 'disabled' not in node['attrs']]
                fields = response.context['form'].fields
                self.assertEqual({node['attrs']['name'] for node in controls}, set(fields))
                for control in controls:
                    label, = parser.find(tag='label', **{'for': control['attrs']['id']})
                    self.assertLess(parser.nodes.index(label), parser.nodes.index(control))
                    self.assertIn('d-block', label['attrs']['class'])
                    required = fields[control['attrs']['name']].required
                    self.assertEqual('required' in control['attrs'], required)
                    self.assertEqual('*' in label['text'], required)
                for name in ('role', 'password', 'user_type', 'groups', 'exclude_from_analysis'):
                    self.assertFalse(parser.find(name=name))
                self.assertContains(response, 'カスタマー')
                fixed_keys = ['type', 'uid', 'login', 'password', 'role', 'analysis',
                              'demo', 'groups', 'expiry']
                if route == 'user_demo_issue':
                    fixed_keys.append('display-name')
                fixed = [node for node in parser.find(tag='input')
                         if node['attrs'].get('id', '').startswith('issue-preview-')]
                self.assertCountEqual([node['attrs']['id'] for node in fixed],
                                      ['issue-preview-' + key for key in fixed_keys])
                for control in fixed:
                    attrs = control['attrs']
                    self.assertIn('disabled', attrs)
                    self.assertNotIn('name', attrs)
                    self.assertIn('bg-light', attrs['class'].split())
                    label, = parser.find(tag='label', **{'for': attrs['id']})
                    self.assertLess(parser.nodes.index(label), parser.nodes.index(control))
                    self.assertIn('d-block', label['attrs']['class'].split())
                    self.assertIn(form, control['parents'])
                for key in ('type', 'role', 'analysis', 'demo'):
                    switch, = parser.find(id='issue-preview-' + key)
                    self.assertEqual(switch['attrs']['type'], 'checkbox')
                    self.assertEqual(switch['attrs']['role'], 'switch')
                    self.assertEqual('checked' in switch['attrs'],
                                     key == 'demo' and route == 'user_demo_issue')
                for key in ('uid', 'login', 'password'):
                    control, = parser.find(id='issue-preview-' + key)
                    self.assertEqual(control['attrs']['type'], 'text')
                    self.assertIn('col-md-4', control['parents'][-1]['attrs']['class'].split())
                self.assertContains(response, 'プレビュー・表示中')
                self.assertContains(response, '番号は発行時に確定')
                self.assertContains(response, '同時発行により変わる')
                self.assertFalse(parser.find(tag='dl'))
                self.assertNotContains(response, '発行時の固定設定')
                self.assertEqual(parser.find(id='issue-preview-groups')[0]['attrs']['value'],
                                 'デモ' if route == 'user_demo_issue' else '設定なし')
                self.assertEqual(parser.find(id='issue-preview-expiry')[0]['attrs']['value'],
                                 '発行から30日' if route == 'user_demo_issue' else '適用なし')
                if route == 'user_contract_issue':
                    department, = parser.find(tag='label', **{'for': 'id_department'})
                    self.assertIn('（任意）', department['text'])
                    for key in ('login', 'password'):
                        self.assertEqual(parser.find(id='issue-preview-' + key)[0]['attrs']['value'], '')
                    self.assertIn('メールアドレスを入力すると',
                                  parser.find(id='issue-preview-email-help')[0]['text'])
                else:
                    self.assertEqual(parser.find(id='issue-preview-display-name')[0]['attrs']['value'],
                                     'Demoアカウント')
                self.assertContains(response, 'function genericComplete')
                self.assertContains(response, 'knowledge-form-nav-links')

    def test_next_unused_preview_is_readonly_repeatable_and_refreshed_without_reservation(self):
        users = get_user_model()
        for route, view, _ in self.routes:
            demo = view is UserDemoIssueView
            prefix, start = ('CSDemo', 900001) if demo else ('CSUser', 800001)
            users.objects.create_user(username=f'{prefix}{start}')
            UserProfile.objects.update_or_create(user=self.member, defaults={'uid': str(start + 1)})
            counts = (users.objects.count(), UserProfile.objects.count(), Customer.objects.count())
            for _ in range(2):
                response = self.client.get(reverse(route))
                parser = self.dom(response)
                uid = str(start + 2)
                self.assertEqual(parser.find(id='issue-preview-uid')[0]['attrs']['value'], uid)
                for key in ('login', 'password'):
                    self.assertEqual(parser.find(id='issue-preview-' + key)[0]['attrs']['value'],
                                     prefix + uid if demo else '')
                self.assertEqual((users.objects.count(), UserProfile.objects.count(),
                                  Customer.objects.count()), counts)
                self.assertNotIn('issued_demo_account', self.client.session)
            # A different issuer consumes the previewed UID; the next GET must advance.
            other = users.objects.create_user(username=f'preview-consumed-{start}')
            UserProfile.objects.update_or_create(user=other, defaults={'uid': str(start + 2)})
            response = self.client.get(reverse(route))
            self.assertEqual(self.dom(response).find(id='issue-preview-uid')[0]['attrs']['value'],
                             str(start + 3))

    def test_contract_bound_email_preview_is_trimmed_escaped_and_not_a_posted_secret(self):
        for email, password in (
            ('  Contract.User@example.test  ', 'Contract.User'),
            ('  <img src=x onerror="alert(1)">&local @example.test  ',
             '<img src=x onerror="alert(1)">&local'),
            ('  not-an-email  ', ''), ('   ', ''),
        ):
            with self.subTest(email=email):
                payload = {**self.contract, 'company_name': '', 'email_addresses': email,
                           'password': 'POSTED_SECRET_NEVER_REDISPLAY', 'uid': '123456',
                           'username': 'SPOOFED_LOGIN'}
                before = get_user_model().objects.count()
                response = self.client.post(reverse('user_contract_issue'), payload)
                parser = self.dom(response)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(parser.find(id='issue-preview-login')[0]['attrs']['value'], email.strip())
                self.assertEqual(parser.find(id='issue-preview-password')[0]['attrs']['value'], password)
                self.assertEqual(parser.find(id='issue-preview-uid')[0]['attrs']['value'], '800001')
                self.assertNotContains(response, 'POSTED_SECRET_NEVER_REDISPLAY')
                self.assertNotContains(response, 'SPOOFED_LOGIN')
                self.assertNotContains(response, '<img src=x')
                self.assertEqual(get_user_model().objects.count(), before)

    def test_capacity_get_and_invalid_rerender_show_no_fabricated_number(self):
        for route, view, _ in self.routes:
            method = '_resolve_next_demo_number' if view is UserDemoIssueView else '_resolve_next_contract_number'
            for post in (False, True):
                with self.subTest(route=route, post=post), patch.object(view, method, return_value=None):
                    response = (self.client.post(reverse(route), {**self.payload(route), 'company_name': ''})
                                if post else self.client.get(reverse(route)))
                    parser = self.dom(response)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(parser.find(id='issue-preview-uid')[0]['attrs']['value'], '')
                    self.assertIn('発行上限に達しています', parser.find(id='issue-preview-uid-help')[0]['text'])
                    if view is UserDemoIssueView:
                        for key in ('login', 'password'):
                            self.assertEqual(parser.find(id='issue-preview-' + key)[0]['attrs']['value'], '')

    def test_staff_permissions_for_real_get_and_post(self):
        before = get_user_model().objects.count()
        for route, _, _ in self.routes:
            for method in ('get', 'post'):
                with self.subTest(route=route, method=method):
                    self.client.logout()
                    response = getattr(self.client, method)(reverse(route), self.payload(route))
                    self.assertIn(response.status_code, (302, 403))
                    if response.status_code == 302:
                        self.assertEqual(urlsplit(response.url).path, reverse('login'))
                    self.client.force_login(self.member)
                    response = getattr(self.client, method)(reverse(route), self.payload(route))
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.url, reverse('article_list'))
        self.assertEqual(get_user_model().objects.count(), before)
        self.client.force_login(self.superuser)
        for route, _, _ in self.routes:
            self.assertEqual(self.client.get(reverse(route)).status_code, 200)
            response = self.client.post(reverse(route), self.payload(route))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, self.return_to)

    def test_menu_links_encode_complete_filter_path_and_remove_only_input_modals(self):
        response = self.client.get(reverse('user_list'), self.params)
        parser = self.dom(response)
        expected = response.wsgi_request.get_full_path()
        for route, _, _ in self.routes:
            links = [node for node in parser.find(tag='a')
                     if urlsplit(node['attrs'].get('href', '')).path == reverse(route)]
            self.assertEqual(len(links), 1)
            self.assertEqual(parse_qs(urlsplit(links[0]['attrs']['href']).query), {'next': [expected]})
            self.assertNotIn('data-bs-toggle', links[0]['attrs'])
        for removed in ('demoAccountIssueModal', 'contractAccountIssueModal', 'account-issue-form'):
            self.assertNotContains(response, removed)
        self.assertContains(response, 'id="userListFilterPanel"')
        self.assertContains(response, "document.getElementById('demoAccountResultModal')")

    def test_safe_next_get_post_fallback_and_post_precedence(self):
        unsafe = ('https://evil.test/users/', '//evil.test/users/', 'javascript:alert(1)',
                  'https://testserver.evil.test/users/', 'http://testserver/users/')
        for route, view, _ in self.routes:
            for candidate in unsafe:
                with self.subTest(route=route, next=candidate):
                    response = self.client.get(reverse(route), {'next': candidate}, secure=True)
                    self.assertEqual(str(response.context['return_to']), reverse('user_list'))
                    with patch.object(view, '_resolve_next_' + ('demo' if view is UserDemoIssueView else 'contract')
                                      + '_number', return_value=None):
                        response = self.client.post(reverse(route), {**self.payload(route), 'next': candidate},
                                                    secure=True)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(str(response.context['return_to']), reverse('user_list'))
            response = self.client.post(reverse(route) + '?' + urlencode({'next': '//evil.test/'}),
                                        {**self.payload(route), 'company_name': ''})
            self.assertEqual(response.context['return_to'], self.return_to)
            parser = self.dom(response)
            self.assertEqual(parser.find(name='next')[0]['attrs']['value'], self.return_to)
            back = parser.find(tag='a', css_class='btn-outline-secondary')
            self.assertTrue(any(node['attrs']['href'] == self.return_to for node in back))
        # Successful issuance also refuses external redirects, not just invalid forms.
        for route, _, _ in self.routes:
            response = self.client.post(reverse(route), {**self.payload(route), 'next': '//evil.test/'})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, reverse('user_list'))

    def test_required_fields_report_all_errors_without_creating_accounts(self):
        before = get_user_model().objects.count()
        for route, _, _ in self.routes:
            payload = {name: ' ' for name in self.payload(route)}
            payload['next'] = self.return_to
            response = self.client.post(reverse(route), payload)
            form = self.assert_bound_values(response, payload, set(payload) - {'next'})
            expected = {'company_name'} if route == 'user_demo_issue' else {
                'display_name', 'company_name', 'email_addresses',
            }
            self.assertEqual(set(form.errors), expected)
            self.assertContains(response, 'invalid-feedback d-block', count=len(expected))
        self.assertEqual(get_user_model().objects.count(), before)

    def test_invalid_and_duplicate_email_retain_every_value_and_escape_markup(self):
        get_user_model().objects.create_user(username='duplicate@example.test')
        for email in ('not-an-email', 'one@example.test;two@example.test', ' duplicate@example.test '):
            with self.subTest(email=email):
                payload = {**self.contract, 'email_addresses': email}
                response = self.client.post(reverse('user_contract_issue'), payload)
                form = self.assert_bound_values(response, payload, set(payload) - {'next'})
                self.assertEqual(set(form.errors), {'email_addresses'})
                self.assertContains(response, '&lt;&amp; &quot;保持&quot;&gt;')
                self.assertNotContains(response, '<& "保持">')
                self.assertEqual(response.context['return_to'], self.return_to)

    def test_max_lengths_and_optional_department_and_note(self):
        for route, _, form_class in self.routes:
            for name, maximum in (('company_name', 120), ('display_name', 150),
                                  ('department', 255), ('email_addresses', 150)):
                if name not in form_class.base_fields:
                    continue
                with self.subTest(route=route, field=name):
                    payload = {**self.payload(route), name: 'x' * (maximum + 1)}
                    response = self.client.post(reverse(route), payload)
                    form = self.assert_bound_values(response, payload, [name])
                    self.assertEqual(form.errors.as_data()[name][0].code, 'max_length')
                    self.assertEqual(self.dom(response).find(name=name)[0]['attrs']['maxlength'], str(maximum))
        payload = {key: value for key, value in self.contract.items() if key not in ('department', 'note')}
        response = self.client.post(reverse('user_contract_issue'), payload)
        self.assertEqual(response.status_code, 302)
        profile = UserProfile.objects.get(user__username=self.contract['email_addresses'].strip())
        self.assertEqual(profile.department, '')
        self.assertEqual(profile.note, '')

    def test_demo_saved_policy_password_numbering_customer_and_result_consumed_once(self):
        users = get_user_model()
        users.objects.create_user(username='CSDemo900001')
        UserProfile.objects.update_or_create(user=self.member, defaults={'uid': '900002'})
        now = timezone.now()
        payload = {**self.payload('user_demo_issue'), 'user_type': 'systena', 'role': 'admin',
                   'groups': ['投稿者'], 'password': 'must-not-use', 'display_name': 'must-not-use',
                   'uid': '123456', 'username': 'spoof-demo-login', 'exclude_from_analysis': ''}
        with patch('tenasapo_knowledge.views.timezone.now', return_value=now):
            response = self.client.post(reverse('user_demo_issue'), payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.return_to)
        user = users.objects.get(username='CSDemo900003')
        self.assertTrue(user.check_password(user.username))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.email, '')
        self.assertEqual(list(user.groups.values_list('name', flat=True)), [DEMO_GROUP_NAME])
        profile = user.knowledge_profile
        self.assertEqual(profile.uid, '900003')
        self.assertEqual(profile.display_name, 'Demoアカウント')
        self.assertEqual(profile.company_name, self.contract['company_name'].strip())
        self.assertEqual(profile.user_type, UserProfile.USER_TYPE_CUSTOMER)
        self.assertEqual(profile.email_addresses, '')
        self.assertEqual(profile.note, '')
        self.assertTrue(profile.exclude_from_analysis)
        self.assertEqual(profile.demo_account_expires_at, now + timedelta(days=30))
        self.assertEqual(list(Customer.objects.get(name=profile.company_name).users.all()), [user])
        expected = {'login_id': user.username, 'password': user.username}
        self.assertEqual(self.client.session['issued_demo_account'], expected)
        first = self.client.get(response.url)
        self.assertEqual(first.context['issued_demo_account'], expected)
        self.assertContains(first, 'id="demoAccountResultModal"')
        self.assertNotIn('issued_demo_account', self.client.session)
        second = self.client.get(response.url)
        self.assertIsNone(second.context['issued_demo_account'])
        self.assertNotContains(second, 'id="demoAccountResultModal"')

    def test_contract_saved_policy_password_numbering_and_existing_customer_relation(self):
        users = get_user_model()
        users.objects.create_user(username='CSUser800001')
        UserProfile.objects.update_or_create(user=self.member, defaults={'uid': '800002'})
        customer = Customer.objects.create(name=self.contract['company_name'].strip())
        customer.users.add(self.member)
        payload = {**self.contract, 'user_type': 'systena', 'role': 'admin',
                   'groups': [DEMO_GROUP_NAME], 'password': 'must-not-use',
                   'uid': '123456', 'username': 'spoof-contract-login', 'exclude_from_analysis': ''}
        response = self.client.post(reverse('user_contract_issue'), payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.return_to)
        email = self.contract['email_addresses'].strip()
        user = users.objects.get(username=email)
        self.assertEqual(user.email, email)
        self.assertTrue(user.check_password('Contract.User'))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.groups.exists())
        profile = user.knowledge_profile
        self.assertEqual(profile.uid, '800003')
        for field in ('display_name', 'company_name', 'department', 'email_addresses', 'note'):
            self.assertEqual(getattr(profile, field), self.contract[field].strip())
        self.assertEqual(profile.user_type, UserProfile.USER_TYPE_CUSTOMER)
        self.assertTrue(profile.exclude_from_analysis)
        self.assertIsNone(profile.demo_account_expires_at)
        self.assertEqual(set(customer.users.values_list('pk', flat=True)), {user.pk, self.member.pk})
        self.assertNotIn('issued_demo_account', self.client.session)

    def test_capacity_errors_rerender_bound_page_and_do_not_issue(self):
        before = get_user_model().objects.count()
        for route, view, _ in self.routes:
            method = '_resolve_next_demo_number' if view is UserDemoIssueView else '_resolve_next_contract_number'
            payload = self.payload(route)
            with self.subTest(route=route), patch.object(view, method, return_value=None) as numbering:
                response = self.client.post(reverse(route), payload)
            self.assertEqual(numbering.call_count, 2)  # Issuance check and read-only rerender preview.
            form = self.assert_bound_values(response, payload, set(payload) - {'next'})
            self.assertEqual(set(form.errors), {'__all__'})
            self.assertIn('発行上限', str(form.non_field_errors()))
            self.assertContains(response, 'role="alert"')
            self.assertEqual(response.context['return_to'], self.return_to)
        self.assertEqual(get_user_model().objects.count(), before)
        self.assertNotIn('issued_demo_account', self.client.session)

    def test_integrity_retry_exhaustion_rolls_back_users_profiles_and_customers(self):
        before = get_user_model().objects.count()
        for route, _, _ in self.routes:
            payload = self.payload(route)
            with self.subTest(route=route), patch.object(
                UserProfile.objects, 'create', side_effect=IntegrityError('forced collision'),
            ) as create:
                response = self.client.post(reverse(route), payload)
            self.assertEqual(create.call_count, 10)
            form = self.assert_bound_values(response, payload, set(payload) - {'next'})
            self.assertEqual(set(form.errors), {'__all__'})
            self.assertIn('競合', str(form.non_field_errors()))
            self.assertEqual(get_user_model().objects.count(), before)
            self.assertFalse(Customer.objects.filter(name=self.contract['company_name'].strip()).exists())
        self.assertNotIn('issued_demo_account', self.client.session)

    def test_single_integrity_collision_retries_then_succeeds(self):
        real_create = UserProfile.objects.create
        for route, _, _ in self.routes:
            calls = []

            def create_after_collision(*args, **kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    raise IntegrityError('forced collision')
                return real_create(*args, **kwargs)

            with self.subTest(route=route), patch.object(UserProfile.objects, 'create',
                                                      side_effect=create_after_collision):
                response = self.client.post(reverse(route), self.payload(route))
            self.assertEqual(len(calls), 2)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, self.return_to)