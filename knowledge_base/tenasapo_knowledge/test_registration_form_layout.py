"""Registration layout regressions; first-login coverage is render-only.

Run this module together with the existing CategoryEditLinkTests,
CategoryDeleteTests and UserCreateViewTests in tenasapo_knowledge.tests.
No production save implementation is replaced or mocked here.
"""

from django.contrib.auth import get_user_model
from django.middleware.csrf import get_token
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .forms import (
    ConvenienceCategoryCreateForm,
    FAQCategoryCreateForm,
    ManualForm,
    RelatedTagCreateForm,
    RevisionHistoryForm,
    UserCreateForm,
    UserUpdateForm,
)
from .models import FAQCategory, Manual, RelatedTag, RevisionHistory, UserProfile
from .test_knowledge_form_layout import FormControlsParser


class RegistrationControlsParser(FormControlsParser):
    """Keep the shared parser's API, adding ancestry and duplicate-name controls."""

    void_tags = frozenset((
        'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
        'link', 'meta', 'param', 'source', 'track', 'wbr',
    ))

    def __init__(self):
        super().__init__()
        self.nodes = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        node = {
            'tag': tag, 'attrs': dict(attrs),
            'ancestors': tuple(self.stack), 'text': '',
        }
        self.nodes.append(node)
        if tag not in self.void_tags:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for node in self.stack:
            node['text'] += data

    def find(self, tag=None, **attrs):
        return [
            node for node in self.nodes
            if (tag is None or node['tag'] == tag)
            and all(node['attrs'].get(key) == value for key, value in attrs.items())
        ]


@override_settings(USER_ROLES=['投稿者', '承認者', 'demo', '保持する役割'])
class RegistrationFormLayoutTests(TestCase):
    category_sections = ('parent', 'middle', 'child')
    manual_sections = ('title', 'description', 'order', 'is_published')
    user_sections = ('account', 'permissions', 'other')
    revision_sections = ('metadata', 'category', 'title', 'content')
    first_login_sections = ('department', 'group', 'employee', 'login', 'name')

    def setUp(self):
        # These views additionally check the account name, not just superuser.
        self.admin = get_user_model().objects.create_superuser(
            username='Admin', email='layout-admin@example.test', password='test-password'
        )
        self.client.force_login(self.admin)
        self.category = FAQCategory.objects.create(
            parent_name='PC', middle_name='登録テスト', child_name='既存の小カテゴリ'
        )
        # A stored path is enough: tests neither open nor write production media.
        self.manual = Manual.objects.create(
            title='既存マニュアル', description='既存の説明',
            pdf_file='manuals/layout-existing.pdf', order=3,
            is_published=True, created_by=self.admin,
        )
        self.revision = RevisionHistory.objects.create(
            category=RevisionHistory.CATEGORY_UPDATE, title='既存履歴',
            update_content='既存の更新内容', updated_by_user=self.admin,
            updated_by_name=self.admin.username,
        )
        self.member = get_user_model().objects.create_user(
            username='layout-member', email='member@example.test', password='old-password'
        )
        self.profile = UserProfile.objects.create(
            user=self.member, uid='654321', display_name='既存ユーザー',
            company_name='テスト会社', department='既存部署',
            user_type=UserProfile.USER_TYPE_CUSTOMER,
        )
        self.member.groups.create(name='保持する役割')

    def parse(self, html):
        parser = RegistrationControlsParser()
        parser.feed(html)
        parser.close()
        return parser

    def assert_layout_html(self, html, sections):
        parser = self.parse(html)
        expected = ['form-section-' + name for name in sections]
        self.assertEqual(parser.section_ids, expected)
        self.assertEqual(parser.section_links, expected)
        links = [node for node in parser.find('a')
                 if node['attrs'].get('href', '').startswith('#form-section-')]
        self.assertEqual(links[0]['attrs'].get('aria-current'), 'location')
        self.assertIn('is-current', links[0]['attrs'].get('class', '').split())
        self.assertEqual(len(parser.find(**{'aria-current': 'location'})), 1)
        for section_id in expected:
            nodes = parser.find(id=section_id)
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0]['attrs'].get('tabindex'), '-1')
        self.assertEqual(len(parser.find('nav', **{'aria-label': '入力項目へ移動'})), 1)
        for marker in (
            'knowledge-form-layout', 'knowledge-form-nav-column',
            'counter(form-section, decimal-leading-zero)',
            'counter(form-nav, decimal-leading-zero)',
            "root.addEventListener('scroll', scheduleUpdate",
        ):
            self.assertIn(marker, html)
        self.assertEqual(html.count('counter(form-section, decimal-leading-zero)'), 1)

        # Inspect actual elements, not CSS strings or unrelated navbar columns.
        panels = [node for node in parser.find('div')
                  if 'page-title-panel' in node['attrs'].get('class', '').split()]
        self.assertEqual(len(panels), 1)
        ancestors = panels[0]['ancestors']
        self.assertEqual(ancestors[-1]['attrs'].get('class'), 'col-12')
        self.assertEqual(ancestors[-2]['attrs'].get('class'), 'row justify-content-center')
        self.assertTrue(any(
            'app-page-header' in node['attrs'].get('class', '').split()
            for node in ancestors
        ))
        self.assertFalse(any(node['attrs'].get('id') == 'app-main' for node in ancestors))

        forms = [node for node in parser.find('form')
                 if 'card' in node['attrs'].get('class', '').split()]
        self.assertEqual(len(forms), 1)
        form = forms[0]
        self.assertEqual(form['attrs'].get('method'), 'post')
        self.assertIn(form['attrs'].get('data-knowledge-completion'), (
            'category', 'first-login', 'user', 'generic',
        ))
        self.assertIn('form-completion-icon', html)
        self.assertIn('必須未入力・要確認', html)
        self.assertNotIn('action', form['attrs'])
        tokens = [node for node in parser.find('input', name='csrfmiddlewaretoken')
                  if any(parent is form for parent in node['ancestors'])]
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]['attrs'].get('type'), 'hidden')
        self.assertTrue(tokens[0]['attrs'].get('value'))
        saves = [node for node in parser.find('button', type='submit')
                 if 'btn-primary' in node['attrs'].get('class', '').split()
                 and any(parent is form for parent in node['ancestors'])]
        self.assertEqual(len(saves), 1)
        self.assertNotIn('formaction', saves[0]['attrs'])
        self.assertNotIn('disabled', saves[0]['attrs'])
        self.assertTrue(saves[0]['text'].strip())
        return parser

    def assert_layout(self, response, sections, template, form_class):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'tenasapo_knowledge/' + template + '.html')
        self.assertTemplateUsed(response, 'includes/knowledge_form_navigation_assets.html')
        self.assertIsInstance(response.context['form'], form_class)
        return self.assert_layout_html(response.content.decode(), sections)

    def assert_controls(self, parser, required=(), optional=()):
        for name in (*required, *optional):
            with self.subTest(control=name):
                self.assertIn(name, parser.controls)
                self.assertNotIn('disabled', parser.controls[name])
                self.assertEqual('required' in parser.controls[name], name in required)

    def assert_widget_contract(self, parser, form, names):
        """Compare semantic widget attributes; custom layout classes may differ."""
        for name in names:
            with self.subTest(widget=name):
                baseline = self.parse(str(form[name]))
                self.assertIn(form[name].html_name, parser.controls)
                actual = parser.controls[form[name].html_name]
                original = baseline.controls[form[name].html_name]
                for attr in ('name', 'id', 'type'):
                    self.assertEqual(actual.get(attr), original.get(attr))
                for attr in ('required', 'disabled'):
                    self.assertEqual(attr in actual, attr in original)

    def test_category_faq_and_qr_get_have_matching_sections_and_return_links(self):
        target = reverse('article_create') + '?source=layout&keep=1'
        for tab, form_class, required, optional in (
            ('faq', FAQCategoryCreateForm, ('middle_name',),
             ('parent_name', 'new_parent_name', 'child_name')),
            ('qr', ConvenienceCategoryCreateForm, ('category',),
             ('reference_type', 'new_reference_type', 'middle_category')),
        ):
            with self.subTest(tab=tab):
                response = self.client.get(reverse('category_create'), {'tab': tab, 'next': target})
                parser = self.assert_layout(response, self.category_sections, 'category_form', form_class)
                self.assert_controls(parser, required, optional)
                self.assertEqual(parser.controls['tab']['value'], tab)
                self.assertEqual(parser.controls['tab']['type'], 'hidden')
                self.assertEqual(response.context['return_to'], target)
                self.assertEqual(parser.controls['next']['value'], target)
                self.assertEqual(parser.controls['next']['type'], 'hidden')
                self.assertTrue(parser.find('a', href=target))
                for other_tab in ('faq', 'qr'):
                    tab_url = reverse('category_create') + '?tab=' + other_tab
                    self.assertTrue(any(
                        node['attrs'].get('href', '').startswith(tab_url + '&next=')
                        for node in parser.find('a')
                    ))
                self.assertFalse(parser.find('form', id='categoryDeleteForm'))
                self.assert_widget_contract(parser, response.context['form'], required + optional)

    def test_category_edit_keeps_separate_csrf_delete_form(self):
        response = self.client.get(reverse('category_edit', args=[self.category.pk]))
        parser = self.assert_layout(response, self.category_sections, 'category_form', FAQCategoryCreateForm)
        self.assert_controls(parser, ('middle_name',), ('child_name',))
        self.assertEqual(parser.controls['middle_name']['value'], self.category.middle_name)
        self.assertEqual(parser.controls['child_name']['value'], self.category.child_name)
        forms = parser.find('form', id='categoryDeleteForm')
        self.assertEqual(len(forms), 1)
        delete = forms[0]
        self.assertEqual(delete['attrs']['method'], 'post')
        self.assertEqual(delete['attrs']['action'], reverse('category_delete', args=[self.category.pk]))
        self.assertIn('confirm(', delete['attrs']['onsubmit'])
        self.assertFalse(any(parent['tag'] == 'form' for parent in delete['ancestors']))
        self.assertEqual(len([
            node for node in parser.find('input', name='csrfmiddlewaretoken')
            if any(parent is delete for parent in node['ancestors'])
        ]), 1)
        self.assertEqual(len(parser.find('button', type='submit', form='categoryDeleteForm')), 1)
        cancel = reverse('category_create') + f'?focus_category={self.category.pk}'
        self.assertTrue(parser.find('a', href=cancel))

    def test_category_post_saves_exactly_one_with_required_middle_and_empty_child(self):
        count = FAQCategory.objects.count()
        target = reverse('article_create')
        response = self.client.post(reverse('category_create'), {
            'tab': 'faq', 'parent_name': 'PC', 'middle_name': '中カテゴリだけ',
            'child_name': '', 'next': target,
        })
        self.assertRedirects(response, target, fetch_redirect_response=False)
        self.assertEqual(FAQCategory.objects.count(), count + 1)
        saved = FAQCategory.objects.get(parent_name='PC', middle_name='中カテゴリだけ', child_name='')
        self.assertEqual(saved.full_name, 'PC/中カテゴリだけ')

    def test_category_validation_error_restores_inputs_and_next_without_saving(self):
        count = FAQCategory.objects.count()
        target = reverse('article_create') + '?source=category&keep=1'
        data = {
            'tab': 'faq', 'parent_name': 'PC', 'new_parent_name': '新規大 <& "保持">',
            'middle_name': '', 'child_name': '小 <& "保持">', 'next': target,
        }
        response = self.client.post(reverse('category_create'), data)
        parser = self.assert_layout(response, self.category_sections, 'category_form', FAQCategoryCreateForm)
        self.assertIn('middle_name', response.context['form'].errors)
        self.assertEqual(FAQCategory.objects.count(), count)
        for name in ('new_parent_name', 'middle_name', 'child_name', 'next', 'tab'):
            self.assertEqual(parser.controls[name]['value'], data[name])
        selected = parser.find('option', value='PC')
        self.assertTrue(any('selected' in node['attrs'] for node in selected))
        self.assertIn('is-invalid', parser.controls['middle_name']['class'])
        self.assert_controls(parser, ('middle_name',), ('child_name',))

    def test_category_and_tag_reject_external_return_to(self):
        for route in ('category_create', 'tag_create'):
            with self.subTest(route=route):
                response = self.client.get(reverse(route), {'next': 'https://outside.example/return'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['return_to'], '')
                self.assertNotIn('next', self.parse(response.content.decode()).controls)

    def test_tag_get_preserves_name_and_return_to(self):
        target = reverse('article_create')
        response = self.client.get(reverse('tag_create'), {'next': target})
        parser = self.assert_layout(response, ('name',), 'tag_form', RelatedTagCreateForm)
        self.assert_controls(parser, ('name',))
        self.assert_widget_contract(parser, response.context['form'], ('name',))
        self.assertEqual(parser.controls['next']['value'], target)
        self.assertTrue(parser.find('a', href=target))

    def test_tag_validation_error_preserves_bound_name(self):
        RelatedTag.objects.create(name='重複 <& "タグ">')
        count = RelatedTag.objects.count()
        response = self.client.post(reverse('tag_create'), {'name': '重複 <& "タグ">'})
        parser = self.assert_layout(response, ('name',), 'tag_form', RelatedTagCreateForm)
        self.assertIn('name', response.context['form'].errors)
        self.assertEqual(parser.controls['name']['value'], '重複 <& "タグ">')
        self.assertEqual(RelatedTag.objects.count(), count)

    def test_manual_create_and_edit_get_preserve_widget_contract(self):
        for route, args in (('manual_create', []), ('manual_edit', [self.manual.pk])):
            with self.subTest(route=route):
                response = self.client.get(reverse(route, args=args))
                parser = self.assert_layout(response, self.manual_sections, 'manual_form', ManualForm)
                self.assert_controls(parser, ('title', 'description', 'order'), ('is_published',))
                self.assert_widget_contract(parser, response.context['form'],
                                            ('title', 'description', 'order', 'is_published'))
                form = next(node for node in parser.find('form')
                            if 'card' in node['attrs'].get('class', '').split())
                self.assertNotIn('enctype', form['attrs'])
                self.assertIn('novalidate', form['attrs'])
                self.assertNotIn('pdf_file', parser.controls)
                self.assertNotIn('menu_key', parser.controls)
                self.assertNotIn('pdf_file', response.context['form'].fields)
                self.assertEqual(parser.controls['description']['rows'], '18')
                self.assertEqual(response.context['form'].fields['description'].label, '使い方・操作手順')
                if args:
                    self.assertFalse(parser.find('a', href=self.manual.pdf_file.url))

    def test_manual_update_without_upload_preserves_file_and_creator(self):
        count = Manual.objects.count()
        response = self.client.post(reverse('manual_edit', args=[self.manual.pk]), {
            'title': '更新マニュアル', 'description': '更新の説明', 'order': '7',
        })
        self.assertRedirects(response, reverse('manual_list'), fetch_redirect_response=False)
        self.manual.refresh_from_db()
        self.assertEqual(Manual.objects.count(), count)
        self.assertEqual(self.manual.title, '更新マニュアル')
        self.assertEqual(self.manual.description, '更新の説明')
        self.assertEqual(self.manual.order, 7)
        self.assertFalse(self.manual.is_published)
        self.assertEqual(self.manual.pdf_file.name, 'manuals/layout-existing.pdf')
        self.assertEqual(self.manual.created_by_id, self.admin.pk)

    def test_manual_validation_error_restores_widgets_and_does_not_update(self):
        response = self.client.post(reverse('manual_edit', args=[self.manual.pk]), {
            'title': '', 'description': '入力途中 <& "説明">', 'order': '9', 'is_published': 'on',
        })
        parser = self.assert_layout(response, self.manual_sections, 'manual_form', ManualForm)
        self.assertIn('title', response.context['form'].errors)
        # Django's textarea widget inserts a leading newline in the HTML source.
        self.assertEqual(parser.find('textarea', name='description')[0]['text'].lstrip('\n'),
                 '入力途中 <& "説明">')
        self.assertEqual(parser.controls['order']['value'], '9')
        self.assertIn('checked', parser.controls['is_published'])
        self.manual.refresh_from_db()
        self.assertEqual(self.manual.title, '既存マニュアル')
        self.assertEqual(self.manual.order, 3)

    def test_user_create_and_edit_get_preserve_account_permission_controls(self):
        for route, args, form_class in (
            ('user_create', [], UserCreateForm), ('user_edit', [self.member.pk], UserUpdateForm),
        ):
            with self.subTest(route=route):
                response = self.client.get(reverse(route, args=args))
                parser = self.assert_layout(response, self.user_sections, 'user_form', form_class)
                self.assertTemplateUsed(response, 'includes/user_form_company_assets.html')
                self.assert_user_section_positions(parser, editing=bool(args))
                self.assert_controls(parser, ('company_name',), (
                    'uid', 'display_name', 'email_addresses', 'department', 'note',
                    'role', 'user_type', 'groups', 'exclude_from_analysis',
                ))
                self.assert_widget_contract(parser, response.context['form'],
                                            ('uid', 'display_name', 'department', 'note'))
                self.assertEqual(parser.controls['email_addresses']['type'], 'email')
                roles = parser.find('input', name='role')
                self.assertEqual([(node['attrs']['type'], node['attrs']['value']) for node in roles],
                                 [('hidden', 'user'), ('checkbox', 'admin')])
                if args:
                    self.assertNotIn('username', parser.controls)
                    self.assertNotIn('password', parser.controls)
                    logins = parser.find('input', value=self.member.username)
                    self.assertEqual(len(logins), 1)
                    self.assertIn('readonly', logins[0]['attrs'])
                    self.assertEqual(parser.controls['user_type']['type'], 'hidden')
                    self.assertEqual(parser.controls['user_type']['value'], 'customer')
                    self.assert_controls(parser, optional=('skip_login_lp',))
                    self.assertTrue(parser.find('input', name='groups', type='hidden', value='保持する役割'))
                else:
                    self.assert_controls(parser, ('username', 'password'))
                    self.assertNotIn('readonly', parser.controls['company_name'])
                    self.assertNotIn('bg-light', parser.controls['company_name'].get('class', '').split())
                    self.assertNotIn('hidden', parser.find(id='user-company-field')[0]['attrs'])
                    self.assertEqual(parser.controls['password']['type'], 'password')
                    self.assert_widget_contract(parser, response.context['form'], ('username', 'password'))
                    self.assertEqual([(node['attrs']['type'], node['attrs']['value'])
                                      for node in parser.find('input', name='user_type')],
                                     [('hidden', 'customer'), ('checkbox', 'systena')])

    def assert_user_section_positions(self, parser, *, editing):
        account = parser.find(id='form-section-account')[0]['ancestors'][-1]
        permissions = parser.find(id='form-section-permissions')[0]['ancestors'][-1]
        other = parser.find(id='form-section-other')[0]['ancestors'][-1]
        for section in (account, permissions, other):
            heading = next(node for node in parser.nodes
                           if 'data-form-section' in node['attrs']
                           and node['ancestors'][-1] is section)
            enclosure = next(node for node in parser.nodes[parser.nodes.index(heading) + 1:]
                             if node['tag'] == 'div' and node['ancestors'][-1] is section)
            classes = enclosure['attrs'].get('class', '').split()
            self.assertIn('p-3', classes)
            self.assertNotIn('border', classes)
            self.assertNotIn('rounded', classes)
        account_inputs = [node for node in parser.find('input')
                          if any(parent is account for parent in node['ancestors'])]
        self.assertEqual(account_inputs[0]['attrs']['name'], 'user_type')
        type_inputs = parser.find('input', name='user_type')
        for node in type_inputs:
            self.assertTrue(any(parent is account for parent in node['ancestors']))
            self.assertFalse(any(parent is permissions for parent in node['ancestors']))
        type_row = next(parent for parent in reversed(type_inputs[0]['ancestors'])
                        if 'col-12' in parent['attrs'].get('class', '').split())
        self.assertEqual(type_row['attrs']['class'], 'col-12')
        type_label = next(node for node in parser.find('label')
                  if node['text'].startswith('ユーザー区分'))
        self.assertIn('d-block', type_label['attrs'].get('class', '').split())
        self.assertIs(type_label['ancestors'][-1], type_row)
        if editing:
            type_control = next(node for node in parser.nodes
                                if node['tag'] == 'div'
                                and node['ancestors'][-1] is type_row
                                and 'form-control' in node['attrs'].get('class', '').split())
        else:
            type_control = type_inputs[-1]['ancestors'][-1]
        self.assertIs(type_control['ancestors'][-1], type_row)
        self.assertLess(parser.nodes.index(type_label), parser.nodes.index(type_control))
        self.assertNotIn('w-100', type_control['attrs'].get('class', '').split())
        self.assertNotIn('placeholder', parser.controls['uid'])
        wrapper = parser.find(id='user-company-field')[0]
        self.assertTrue(any(parent is account for parent in wrapper['ancestors']))
        self.assertTrue(any(parent is wrapper for parent in
                            parser.find('input', name='company_name')[0]['ancestors']))
        self.assertNotIn('ユーザー区分', permissions['text'])
        department = parser.find('label', **{'for': parser.controls['department']['id']})[0]
        self.assertIn('（任意）', department['text'])
        if editing:
            self.assertIn('（変更不可）', account['text'])
            self.assertFalse(parser.find('input', name='user_type', type='checkbox'))
        else:
            self.assertIn('form-switch', type_control['attrs'].get('class', '').split())
            self.assertEqual(type_inputs[-1]['attrs'].get('role'), 'switch')
            self.assertEqual(type_label['attrs'].get('for'), type_inputs[-1]['attrs']['id'])
            password = parser.find('input', name='password')[0]
            self.assertTrue(any(parent is account for parent in password['ancestors']))
            self.assertNotIn('value', password['attrs'])
            names = [node['attrs'].get('name') for node in account_inputs]
            self.assertEqual(names[names.index('username') + 1], 'password')
            self.assertLess(names.index('password'), names.index('display_name'))

    def test_user_create_company_validation_depends_on_cleaned_type(self):
        base = {'username': 'company-validation-new', 'password': 'test-password',
                'role': 'user', 'department': ''}
        for company in (None, '', '入力不要の会社'):
            with self.subTest(company=company):
                data = {**base, 'user_type': 'systena'}
                if company is not None:
                    data['company_name'] = company
                form = UserCreateForm(data)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data['company_name'], 'システナ')
                self.assertEqual(form.cleaned_data['department'], '')
        for company in ('', '   '):
            form = UserCreateForm({**base, 'user_type': 'customer', 'company_name': company})
            self.assertFalse(form.is_valid())
            self.assertIn('company_name', form.errors)
        form = UserCreateForm({**base, 'user_type': 'customer', 'company_name': '  顧客会社  '})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['company_name'], '顧客会社')
        for user_type in ('customer', 'systena'):
            form = UserCreateForm({**base, 'user_type': user_type, 'company_name': 'x' * 121})
            self.assertFalse(form.is_valid())
            self.assertEqual(form.errors.as_data()['company_name'][0].code, 'max_length')
        form = UserCreateForm({**base, 'user_type': 'invalid'})
        self.assertFalse(form.is_valid())
        self.assertIn('user_type', form.errors)
        self.assertNotIn('company_name', form.errors)

    def test_user_systena_create_without_company_saves_fixed_company_and_optional_department(self):
        response = self.client.post(reverse('user_create'), {
            'username': 'systena-without-company', 'password': 'new-systena-password',
            'role': ['user', 'admin'], 'user_type': ['customer', 'systena'],
            'department': '',
        })
        self.assertRedirects(response, reverse('user_list'), fetch_redirect_response=False)
        user = get_user_model().objects.get(username='systena-without-company')
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.company_name, 'システナ')
        self.assertEqual(profile.user_type, 'systena')
        self.assertEqual(profile.department, '')
        self.assertTrue(user.check_password('new-systena-password'))

    def assert_systena_creation_company(self, parser):
        company = parser.find('input', name='company_name')[0]
        self.assertEqual(company['attrs'].get('value'), 'システナ')
        self.assertEqual(company['attrs'].get('type'), 'text')
        self.assertIn('readonly', company['attrs'])
        self.assertIn('bg-light', company['attrs'].get('class', '').split())
        self.assert_controls(parser, optional=('company_name', 'department'))
        for node in (*company['ancestors'], company):
            self.assertNotIn('hidden', node['attrs'])
            self.assertNotIn('disabled', node['attrs'])
            self.assertNotIn('d-none', node['attrs'].get('class', '').split())

    def test_user_initial_systena_renders_visible_automatic_company(self):
        request = RequestFactory().get(reverse('user_create'))
        request.user = self.admin
        request.session = {}
        html = render_to_string('tenasapo_knowledge/user_form.html', {
            'form': UserCreateForm(initial={'user_type': 'systena'}),
            'is_edit': False, 'return_to': reverse('user_list'),
            'csrf_token': get_token(request),
        }, request=request)
        parser = self.assert_layout_html(html, self.user_sections)
        self.assert_user_section_positions(parser, editing=False)
        self.assertIn('checked', parser.controls['user_type'])
        self.assert_systena_creation_company(parser)

    def test_user_systena_create_error_redisplays_automatic_company_without_password(self):
        count = get_user_model().objects.count()
        response = self.client.post(reverse('user_create'), {
            'username': '', 'uid': 'bad', 'user_type': ['customer', 'systena'],
            'role': ['user', 'admin'], 'password': 'redisplay-secret-not-in-html',
            'display_name': '入力途中のシステナ', 'department': '',
            'company_name': 'システナ',
        })
        parser = self.assert_layout(response, self.user_sections, 'user_form', UserCreateForm)
        self.assert_user_section_positions(parser, editing=False)
        self.assertIn('username', response.context['form'].errors)
        self.assertNotIn('company_name', response.context['form'].errors)
        self.assertIn('checked', parser.controls['user_type'])
        self.assertIn('checked', parser.controls['role'])
        self.assert_systena_creation_company(parser)
        self.assertNotContains(response, 'redisplay-secret-not-in-html')
        self.assertEqual(parser.controls['display_name']['value'], '入力途中のシステナ')
        self.assertEqual(get_user_model().objects.count(), count)

    def test_user_systena_edit_keeps_immutable_type_and_visible_company(self):
        self.profile.user_type = UserProfile.USER_TYPE_SYSTENA
        self.profile.company_name = 'システナ'
        self.profile.save(update_fields=['user_type', 'company_name'])
        response = self.client.get(reverse('user_edit', args=[self.member.pk]))
        parser = self.assert_layout(response, self.user_sections, 'user_form', UserUpdateForm)
        self.assert_user_section_positions(parser, editing=True)
        self.assertEqual(parser.controls['user_type']['value'], 'systena')
        self.assertEqual(parser.controls['company_name']['value'], 'システナ')
        self.assertNotIn('hidden', parser.find(id='user-company-field')[0]['attrs'])
        self.assert_controls(parser, ('company_name',), ('department',))

    def test_user_update_preserves_identity_password_and_hidden_role_values(self):
        target = reverse('user_list') + '?source=layout'
        response = self.client.post(reverse('user_edit', args=[self.member.pk]), {
            'uid': '654321', 'display_name': '更新ユーザー', 'company_name': '更新会社',
            'department': '更新部署', 'role': 'user', 'user_type': 'customer',
            'groups': ['保持する役割'], 'email_addresses': 'updated@example.test',
            'note': '更新備考', 'skip_login_lp': 'on', 'exclude_from_analysis': 'on', 'next': target,
        })
        self.assertRedirects(response, target, fetch_redirect_response=False)
        self.member.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.member.username, 'layout-member')
        self.assertTrue(self.member.check_password('old-password'))
        self.assertEqual(self.member.email, 'updated@example.test')
        self.assertFalse(self.member.is_staff)
        self.assertFalse(self.member.is_superuser)
        self.assertEqual(list(self.member.groups.values_list('name', flat=True)), ['保持する役割'])
        for name, value in (
            ('uid', '654321'), ('display_name', '更新ユーザー'), ('company_name', '更新会社'),
            ('department', '更新部署'), ('user_type', 'customer'),
            ('email_addresses', 'updated@example.test'), ('note', '更新備考'),
            ('skip_login_lp', True), ('exclude_from_analysis', True),
        ):
            self.assertEqual(getattr(self.profile, name), value)

    def test_user_validation_error_restores_values_and_return_to(self):
        target = reverse('user_list') + '?source=layout&keep=1'
        response = self.client.post(reverse('user_edit', args=[self.member.pk]), {
            'uid': 'bad', 'display_name': '入力途中 <& "名前">', 'company_name': 'テスト会社',
            'role': 'user', 'user_type': 'customer', 'groups': ['保持する役割'],
            'email_addresses': 'member@example.test', 'department': '入力途中部署',
            'note': '入力途中 <& "備考">', 'skip_login_lp': 'on', 'next': target,
        })
        parser = self.assert_layout(response, self.user_sections, 'user_form', UserUpdateForm)
        self.assertIn('uid', response.context['form'].errors)
        self.assertEqual(parser.controls['uid']['value'], 'bad')
        self.assertEqual(parser.controls['display_name']['value'], '入力途中 <& "名前">')
        self.assertEqual(parser.find('textarea', name='note')[0]['text'], '入力途中 <& "備考">')
        self.assertIn('checked', parser.controls['skip_login_lp'])
        self.assertEqual(parser.controls['next']['value'], target)
        self.assertTrue(parser.find('a', href=target))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.uid, '654321')
        self.assertEqual(self.profile.display_name, '既存ユーザー')

    def test_revision_create_and_edit_get_preserve_fields_metadata_and_delete(self):
        for route, args in (
            ('revision_history_create', []), ('revision_history_edit', [self.revision.pk]),
        ):
            with self.subTest(route=route):
                response = self.client.get(reverse(route, args=args))
                parser = self.assert_layout(response, self.revision_sections,
                                            'revision_history_form', RevisionHistoryForm)
                self.assert_controls(parser, ('category',), ('title', 'update_content'))
                self.assert_widget_contract(parser, response.context['form'],
                                            ('category', 'title', 'update_content'))
                self.assertNotIn('updated_by_user', parser.controls)
                self.assertNotIn('updated_by_name', parser.controls)
                self.assertNotIn('update_date', parser.controls)
                metadata = [node for node in parser.find('input')
                            if 'readonly' in node['attrs'] and 'name' not in node['attrs']
                            and any(parent['attrs'].get('class') == 'row g-3'
                                    for parent in node['ancestors'])]
                self.assertEqual(len(metadata), 2)
                self.assertEqual(metadata[0]['attrs']['value'], self.admin.username)
                self.assertTrue(metadata[1]['attrs']['value'])
                delete_url = reverse('revision_history_delete', args=[self.revision.pk])
                deletes = parser.find('button', formaction=delete_url)
                self.assertEqual(len(deletes), 1 if args else 0)
                if args:
                    self.assertEqual(deletes[0]['attrs']['formmethod'], 'post')
                    self.assertIn('formnovalidate', deletes[0]['attrs'])
                    self.assertEqual(parser.controls['title']['value'], self.revision.title)
                    self.assertEqual(parser.find('textarea', name='update_content')[0]['text'],
                                     self.revision.update_content)

    def test_revision_validation_error_restores_optional_fields(self):
        count = RevisionHistory.objects.count()
        response = self.client.post(reverse('revision_history_create'), {
            'category': '', 'title': '入力途中 <& "履歴">', 'update_content': '入力途中の内容',
        })
        parser = self.assert_layout(response, self.revision_sections,
                                    'revision_history_form', RevisionHistoryForm)
        self.assertIn('category', response.context['form'].errors)
        self.assertEqual(parser.controls['title']['value'], '入力途中 <& "履歴">')
        self.assertEqual(parser.find('textarea', name='update_content')[0]['text'], '入力途中の内容')
        self.assertEqual(RevisionHistory.objects.count(), count)

    def test_first_login_explicit_context_render_only_not_view_access(self):
        # Deliberately does NOT exercise dispatch, middleware, or registration POST.
        request = RequestFactory().get(reverse('first_login_registration'))
        request.user = self.admin
        request.session = {}
        context = {
            'department_name': '登録済み部署 <& "確認">',
            'allowed_groups': ['グループA', 'グループB'], 'selected_group': 'グループB',
            'initial_employee_number': '123456', 'initial_login_id': 'layout-login',
            'initial_user_name': '登録 <& "太郎">', 'form_error': '入力内容を確認してください。',
            'csrf_token': get_token(request),
        }
        html = render_to_string('tenasapo_knowledge/first_login_registration.html',
                                context, request=request)
        parser = self.assert_layout_html(html, self.first_login_sections)
        self.assert_controls(parser, ('group', 'employee_number', 'login_id'), ('user_name',))
        department = parser.find('input', id='departmentName')[0]['attrs']
        self.assertIn('readonly', department)
        self.assertNotIn('name', department)
        self.assertEqual(department['value'], context['department_name'])
        self.assertEqual(parser.controls['employee_number']['maxlength'], '6')
        for name, context_name in (
            ('employee_number', 'initial_employee_number'), ('login_id', 'initial_login_id'),
            ('user_name', 'initial_user_name'),
        ):
            self.assertEqual(parser.controls[name]['value'], context[context_name])
        selected = parser.find('option', value='グループB')
        self.assertEqual(len(selected), 1)
        self.assertIn('selected', selected[0]['attrs'])
        self.assertIn(context['form_error'], html)