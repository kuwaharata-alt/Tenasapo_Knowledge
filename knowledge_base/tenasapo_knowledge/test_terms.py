"""Offline terms coverage; all persistence is in the isolated test database."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.template.loader import render_to_string
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils.html import escape

from .models import TermsOfUse, UserProfile
from .terms import TermsOfUseForm


# Display-only fixture, not legal content; never saved to the application DB.
TERMS_PASTE_SECTIONS = (
    ('第1条（目的および適用）', 3),
    ('第2条（利用資格およびアカウントの発行）', 2),
    ('第3条（アカウントの管理）', 4),
)
TERMS_PASTE_SAMPLE = '\n\n\n\n'.join(
    heading + '\n' + '\n'.join(
        f'{number}. ' + 'これは貼り付け表示の検証用文章です。長い日本語の段落が番号の右側で揃って折り返されることを確認します。' * 8
        for number in range(1, count + 1)
    )
    for heading, count in TERMS_PASTE_SECTIONS
)


@override_settings(
    PASSWORD_MANAGER_USERNAMES=['Admin', 'SystenaAdmin'],
    MIDDLEWARE=[*settings.MIDDLEWARE,
                'tenasapo_knowledge.middleware.LoginRequiredExceptAssetsMiddleware'],
)
class TermsOfUseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.upper = users.objects.create_user(username='aDmIn', is_staff=True)
        cls.upper_root = users.objects.create_user(username='sYsTeNaAdMiN', is_superuser=True)
        cls.staff = users.objects.create_user(username='ordinary-staff', is_staff=True)
        cls.root = users.objects.create_user(username='ordinary-root', is_superuser=True)
        cls.group_admin = users.objects.create_user(username='group-admin')
        cls.group_admin.groups.add(Group.objects.get_or_create(name='管理者')[0])
        cls.customer = users.objects.create_user(username='customer')
        UserProfile.objects.create(user=cls.customer, user_type=UserProfile.USER_TYPE_CUSTOMER)
        cls.member = users.objects.create_user(username='member')
        cls.named_member = users.objects.create_user(username='ADMIN')

    def payload(self, **overrides):
        return {'title': 'テスト用規約', 'description': 'テスト用の本文\n次の行', **overrides}

    def test_exact_requested_title_is_initial_only_without_inventing_body(self):
        title = 'Tenasapo Knowledge Site 「Nexus」 サイトの利用条件'
        self.assertEqual(TermsOfUseForm()['title'].value(), title)
        self.assertFalse(TermsOfUseForm()['description'].value())
        self.client.force_login(self.upper)
        response = self.client.get(reverse('terms_edit'))
        self.assertEqual(response.context['form']['title'].value(), title)
        self.assertFalse(TermsOfUse.objects.exists())

    def test_initial_title_never_overrides_existing_explicit_or_bound_values(self):
        terms = TermsOfUse.objects.create(**self.payload())
        self.assertEqual(TermsOfUseForm(instance=terms)['title'].value(), terms.title)
        self.assertEqual(TermsOfUseForm(initial={'title': '明示指定'})['title'].value(), '明示指定')
        form = TermsOfUseForm(data={'title': '', 'description': '本文'})
        self.assertFalse(form.is_valid())
        self.assertEqual(form['title'].value(), '')

    def test_unregistered_public_page_uses_exact_requested_title_without_saving(self):
        response = self.client.get(reverse('terms_detail'))
        # Browser title, visible heading and its full-text native tooltip.
        self.assertContains(response, 'Tenasapo Knowledge Site 「Nexus」 サイトの利用条件', count=3)
        self.assertFalse(TermsOfUse.objects.exists())

    def login_as(self, user):
        self.client.logout()
        if user is not None:
            self.client.force_login(user)

    def readers(self):
        return (None, self.member, self.customer, self.staff, self.root,
                self.group_admin, self.named_member)

    def test_empty_state_is_public_and_does_not_create_document(self):
        for user in (*self.readers(), self.upper, self.upper_root):
            with self.subTest(user=getattr(user, 'username', None)):
                self.login_as(user)
                response = self.client.get(reverse('terms_detail'))
                self.assertContains(response, '利用規約はまだ登録されていません。')
                self.assertEqual(response.context['can_edit_terms'], user in (self.upper, self.upper_root))
                self.assertEqual(TermsOfUse.objects.count(), 0)
                if user in (self.upper, self.upper_root):
                    self.assertContains(response, '利用規約を登録')
                else:
                    self.assertNotContains(response, reverse('terms_edit'))

    def test_other_roles_cannot_get_or_post_editor_even_by_direct_url(self):
        terms = TermsOfUse.objects.create(**self.payload())
        for user in self.readers():
            for url in (reverse('terms_edit'), reverse('terms_edit', kwargs={'pk': terms.pk})):
                for method in ('get', 'post'):
                    with self.subTest(user=getattr(user, 'username', None), method=method, url=url):
                        self.login_as(user)
                        response = getattr(self.client, method)(url, self.payload(title='改ざん', is_header_title='on'))
                        if user is None:
                            self.assertEqual(response.status_code, 302)
                            self.assertTrue(response.url.startswith(reverse('login') + '?next='))
                        else:
                            self.assertEqual(response.status_code, 403)
                        terms.refresh_from_db()
                        self.assertEqual(terms.title, self.payload()['title'])
                        self.assertFalse(terms.is_header_title)
                        self.assertEqual(TermsOfUse.objects.count(), 1)

    def test_denial_does_not_create_first_document(self):
        for user in self.readers():
            self.login_as(user)
            self.client.post(reverse('terms_edit'), self.payload())
        self.assertFalse(TermsOfUse.objects.exists())

    def test_each_upper_admin_can_register_and_edit_individual_documents(self):
        for user in (self.upper, self.upper_root):
            with self.subTest(user=user.username):
                self.login_as(user)
                response = self.client.get(reverse('terms_edit'))
                self.assertEqual(response.status_code, 200)
                response = self.client.post(reverse('terms_edit'), self.payload(title=user.username))
                self.assertRedirects(response, reverse('terms_detail'))
                terms = TermsOfUse.objects.get(title=user.username)
                self.assertEqual(terms.title, user.username)
                self.assertEqual(terms.description, self.payload()['description'])
                self.assertIsNotNone(terms.updated_at)
                edit_url = reverse('terms_edit', kwargs={'pk': terms.pk})
                response = self.client.get(edit_url)
                self.assertEqual(response.context['form'].initial['title'], user.username)
                self.assertContains(response, '利用規約を編集')
                response = self.client.post(edit_url, self.payload(title='更新済み'))
                self.assertRedirects(response, reverse('terms_detail'))
                terms.refresh_from_db()
                self.assertEqual(terms.title, '更新済み')
                self.assertEqual(TermsOfUse.objects.filter(title='更新済み').count(),
                                 1 if user == self.upper else 2)

    def test_multiple_registration_forms_create_separate_rows_and_ignore_forged_ids(self):
        self.client.force_login(self.upper)
        for _ in range(2):
            self.assertContains(self.client.get(reverse('terms_edit')), '利用規約を登録')
        for number in range(2):
            response = self.client.post(reverse('terms_edit'), self.payload(
                title=f'保存{number}', id='99', pk='99', is_published='',
                next='https://invalid.example/',
            ))
            self.assertRedirects(response, reverse('terms_detail'))
        self.assertEqual(list(TermsOfUse.objects.values_list('pk', 'title')), [(1, '保存0'), (2, '保存1')])

    def test_database_auto_increments_after_existing_ids_and_allows_bulk_inserts(self):
        TermsOfUse.objects.create(pk=1, **self.payload())
        TermsOfUse.objects.bulk_create([TermsOfUse(id=8, **self.payload())])
        terms = TermsOfUse.objects.create(**self.payload())
        self.assertGreater(terms.pk, 8)
        self.assertEqual(TermsOfUse.objects.count(), 3)

    def test_required_fields_and_length_validation_do_not_create_or_overwrite(self):
        self.client.force_login(self.upper)
        for exists in (False, True):
            if exists:
                TermsOfUse.objects.create(**self.payload())
            for invalid in ({'title': ''}, {'title': 'x' * 201}, {'description': ''},
                            {'description': ' \t\n '}):
                with self.subTest(exists=exists, invalid=invalid):
                    response = self.client.post(reverse('terms_edit'), self.payload(**invalid))
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.context['form'].errors)
                    self.assertEqual(TermsOfUse.objects.count(), int(exists))
                    if exists:
                        self.assertEqual(TermsOfUse.objects.get().description, self.payload()['description'])

    def test_editor_reuses_manual_plaintext_fields_and_navigation(self):
        form = TermsOfUseForm()
        self.assertEqual(list(form.fields), ['title', 'is_header_title', 'description'])
        self.assertEqual(form.fields['description'].label, '内容')
        self.assertEqual(form.fields['description'].widget.attrs['rows'], 18)
        self.client.force_login(self.upper)
        response = self.client.get(reverse('terms_edit'))
        self.assertContains(response, 'id="form-section-title"')
        self.assertContains(response, 'id="form-section-description"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, f'href="{reverse("terms_detail")}"')
        self.assertContains(response, 'HTMLタグは文字として扱われます。')
        self.assertContains(response, '第1条（目的および適用）')
        self.assertContains(response, '第1条　目的および適用')
        self.assertContains(response, '1. 本文')
        self.assertContains(response, '条の間に空行を3行')
        self.assertNotContains(response, '操作手順を記載')
        self.assertNotContains(response, 'name="is_published"')
        self.assertNotContains(response, 'name="order"')

    def test_saved_title_body_and_bound_editor_escape_all_html(self):
        title = '<img src=x onerror=alert(1)> & テスト'
        body = '<script>alert(1)</script>\n<img src=x onerror=alert(2)>\n<a href="javascript:alert(3)">link</a>'
        self.client.force_login(self.upper)
        self.assertRedirects(self.client.post(reverse('terms_edit'), self.payload(
            title=title, description=body,
        )), reverse('terms_detail'))
        self.assertEqual(TermsOfUse.objects.get().description, body)
        for user in (*self.readers(), self.upper):
            self.login_as(user)
            response = self.client.get(reverse('terms_detail'))
            self.assertContains(response, escape(title))
            self.assertContains(response, escape(body))
            self.assertNotContains(response, '<script>alert(1)</script>')
            self.assertNotContains(response, '<img src=x')
            self.assertNotContains(response, '<a href="javascript:')
            self.assertContains(response, 'white-space: pre-wrap')
        self.client.force_login(self.upper)
        response = self.client.get(reverse('terms_edit', kwargs={'pk': TermsOfUse.objects.get().pk}))
        self.assertContains(response, escape(body))
        response = self.client.post(reverse('terms_edit'), self.payload(title=title, description=''))
        self.assertContains(response, escape(title))

    def test_detail_is_read_only_even_for_upper_admin(self):
        terms = TermsOfUse.objects.create(**self.payload())
        for user in (None, self.customer, self.upper):
            self.login_as(user)
            self.assertEqual(self.client.post(reverse('terms_detail'), self.payload(title='改ざん')).status_code, 405)
            terms.refresh_from_db()
            self.assertEqual(terms.title, self.payload()['title'])

    def test_public_display_omits_update_date_but_preserves_audit_metadata(self):
        from django.utils import timezone

        terms = TermsOfUse.objects.create(**self.payload())
        updated_at = terms.updated_at
        for user in (None, self.member, self.upper):
            self.login_as(user)
            response = self.client.get(reverse('terms_detail'))
            self.assertContains(response, terms.title)
            self.assertContains(response, escape(terms.description))
            self.assertNotContains(response, '最終更新')
            self.assertNotContains(response, timezone.localtime(updated_at).strftime('%Y/%m/%d %H:%M'))
        terms.refresh_from_db()
        self.assertEqual(terms.updated_at, updated_at)

    def test_public_detail_lists_all_documents_in_pk_order_with_separate_management_links(self):
        first = TermsOfUse.objects.create(**self.payload(title='先の規約'))
        second = TermsOfUse.objects.create(**self.payload(title='後の規約', description='第2条（追加）\n1. 本文'))
        for user in (None, self.member, self.customer, self.upper, self.upper_root):
            self.login_as(user)
            page = self.client.get(reverse('terms_detail'))
            self.assertEqual([term.pk for term in page.context['terms_list']], [first.pk, second.pk])
            self.assertContains(page, first.title)
            self.assertContains(page, second.title)
            self.assertContains(page, '第2条（追加）')
            self.assertNotContains(page, '最終更新')
            for term in (first, second):
                url = reverse('terms_edit', kwargs={'pk': term.pk})
                if user in (self.upper, self.upper_root):
                    self.assertContains(page, f'href="{url}"')
                else:
                    self.assertNotContains(page, url)
            if user in (self.upper, self.upper_root):
                self.assertContains(page, f'href="{reverse("terms_edit")}"')

    def test_edit_by_id_preserves_other_documents_and_missing_id_is_not_created(self):
        first = TermsOfUse.objects.create(**self.payload(title='変更しない'))
        second = TermsOfUse.objects.create(**self.payload(title='編集対象'))
        self.client.force_login(self.upper)
        self.assertRedirects(self.client.post(reverse('terms_edit', kwargs={'pk': second.pk}),
            self.payload(title='編集済み', pk=first.pk, id=first.pk)), reverse('terms_detail'))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.title, '変更しない')
        self.assertEqual(second.title, '編集済み')
        missing = reverse('terms_edit', kwargs={'pk': 999})
        for method in ('get', 'post'):
            self.assertEqual(getattr(self.client, method)(missing, self.payload()).status_code, 404)
        self.assertEqual(TermsOfUse.objects.count(), 2)

    def test_upper_admin_preview_modes_cannot_edit_or_see_action(self):
        terms = TermsOfUse.objects.create(**self.payload())
        for user in (self.upper, self.upper_root):
            for mode in ('demo', 'cs'):
                with self.subTest(user=user.username, mode=mode):
                    self.login_as(user)
                    session = self.client.session
                    session['account_view_mode'] = mode
                    session.save()
                    self.assertNotContains(self.client.get(reverse('terms_detail')), reverse('terms_edit'))
                    self.assertEqual(self.client.get(reverse('terms_edit')).status_code, 403)
                    self.assertEqual(self.client.post(reverse('terms_edit'), self.payload()).status_code, 403)
                    edit_url = reverse('terms_edit', kwargs={'pk': terms.pk})
                    self.assertNotContains(self.client.get(reverse('terms_detail')), edit_url)
                    self.assertEqual(self.client.get(edit_url).status_code, 403)
                    self.assertEqual(self.client.post(edit_url, self.payload()).status_code, 403)
        self.assertEqual(TermsOfUse.objects.count(), 1)

    def test_upper_admin_configuration_uses_existing_helper(self):
        with override_settings(PASSWORD_MANAGER_USERNAMES=['ordinary-staff']):
            self.login_as(self.upper)
            self.assertEqual(self.client.get(reverse('terms_edit')).status_code, 403)
            self.login_as(self.staff)
            self.assertEqual(self.client.get(reverse('terms_edit')).status_code, 200)

    def test_csrf_required_for_save(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.upper)
        self.assertEqual(client.post(reverse('terms_edit'), self.payload()).status_code, 403)
        self.assertFalse(TermsOfUse.objects.exists())
        self.assertEqual(client.get(reverse('terms_edit')).status_code, 200)
        token = client.cookies['csrftoken'].value
        self.assertEqual(client.post(reverse('terms_edit'), self.payload(),
                                     HTTP_X_CSRFTOKEN='invalid-token').status_code, 403)
        self.assertEqual(client.post(reverse('terms_edit'), self.payload(),
                         HTTP_X_CSRFTOKEN=token,
                         HTTP_ORIGIN='https://invalid.example').status_code, 403)
        response = client.post(reverse('terms_edit'), self.payload(), HTTP_X_CSRFTOKEN=token)
        self.assertRedirects(response, reverse('terms_detail'))
        self.assertEqual(TermsOfUse.objects.count(), 1)
        edit_url = reverse('terms_edit', kwargs={'pk': TermsOfUse.objects.get().pk})
        self.assertEqual(client.post(edit_url, self.payload(title='改ざん')).status_code, 403)
        self.assertEqual(TermsOfUse.objects.get().title, self.payload()['title'])
        self.assertRedirects(client.post(edit_url, self.payload(title='更新'),
                        HTTP_X_CSRFTOKEN=token), reverse('terms_detail'))

    def test_anonymous_login_footer_links_to_public_terms(self):
        with override_settings(SOCIALACCOUNT_PROVIDERS={'google': {'APP': {
            'client_id': 'offline-test-client', 'secret': 'offline-test-value', 'key': '',
        }}}):
            response = self.client.get(reverse('login'))
        self.assertContains(response, f'href="{reverse("terms_detail")}"')
        self.assertContains(response, '>利用規約</a>')
        self.assertContains(self.client.get(reverse('terms_detail')), '利用規約はまだ登録されていません。')

    def test_shared_footer_layout_keeps_viewport_grid_and_split_screen_rules(self):
        html = render_to_string('base.html')
        footer = html.split('<footer class="app-footer">', 1)[1].split('</footer>', 1)[0]
        self.assertIn(f'href="{reverse("terms_detail")}"', footer)
        self.assertIn('COPYRIGHT', footer)
        self.assertIn('grid-template-rows: auto auto minmax(0, 1fr) auto;', html)
        self.assertIn('.knowledge-split-page .app-main {\n            overflow: hidden;', html)
        self.assertIn('grid-row: 4;', html)
        self.assertIn('height: 100dvh;', html)


class TermsFormattingTests(SimpleTestCase):
    def test_embedded_urls_inside_japanese_punctuation_keep_exact_destination(self):
        from .test_review_list_layout import ReviewDOMParser

        url = 'https://www.systena.co.jp/about/license/policy/'
        for text in (f'「個人情報保護方針（{url}）」および補足',
                     f'参照「{url}」。', f'確認（{url}）および本文'):
            for prefix in ('', '1. ', '1. 本文\n(1) ', '・'):
                with self.subTest(text=text, prefix=prefix):
                    parser = ReviewDOMParser()
                    parser.feed(self.render_body(prefix + text))
                    link, = parser.find(tag='a')
                    self.assertEqual(link['attrs']['href'], url)
                    self.assertEqual(link['text'], url)
                    container, = (parser.find(css_class='terms-item-text')[-1:]
                                  or parser.find(css_class='terms-paragraph'))
                    self.assertEqual(container['text'], text)

    def test_urls_are_linked_in_paragraphs_and_nested_items_on_both_pages(self):
        from .test_review_list_layout import ReviewDOMParser

        urls = [
            'https://example.com/policy?from=terms&lang=ja#privacy',
            'http://example.com/help',
            'https://example.com/nested',
            'https://example.com/' + 'long-path/' * 30,
        ]
        body = (f'参照先 {urls[0]}\n\n1. 詳細 {urls[1]}\n'
                f'(1) 補足 {urls[2]}\n・参考 {urls[3]}')
        for page in ('terms_detail.html', 'terms_accept.html'):
            with self.subTest(page=page):
                parser = ReviewDOMParser()
                parser.feed(render_to_string('tenasapo_knowledge/' + page, {
                    'terms_list': [{'pk': 1, 'title': '規約', 'description': body}],
                    'terms_registered': True,
                }))
                links = [node for node in parser.find(tag='a')
                         if any('terms-text-body' in parent['attrs'].get('class', '').split()
                                for parent in node['parents'])]
                self.assertEqual([node['attrs']['href'] for node in links], urls)
                self.assertEqual([node['text'] for node in links], urls)
                self.assertTrue(all(node['attrs'].get('target') == '_blank' for node in links))
                self.assertTrue(all(set(node['attrs'].get('rel', '').split())
                                    == {'nofollow', 'noopener', 'noreferrer'} for node in links))

    def test_url_linking_still_escapes_html_and_rejects_script_links(self):
        from .test_review_list_layout import ReviewDOMParser

        body = ('<script>alert(1)</script> <img src=x onerror=alert(2)>\n'
                'javascript:alert(3) data:text/html,unsafe\n'
                'https://example.com/?q=test&lang=ja')
        html = self.render_body(body)
        parser = ReviewDOMParser()
        parser.feed(html)
        self.assertFalse(parser.find(tag='script'))
        self.assertFalse(parser.find(tag='img'))
        links = parser.find(tag='a')
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['attrs']['href'], 'https://example.com/?q=test&lang=ja')
        self.assertIn(escape('<script>alert(1)</script>'), html)
        self.assertIn('javascript:alert(3)', html)

    def test_edit_links_share_section_heading_row_and_hidden_primary_has_own_row(self):
        from .test_review_list_layout import ReviewDOMParser
        from .terms import DEFAULT_TERMS_TITLE

        documents = [
            {'pk': 1, 'title': DEFAULT_TERMS_TITLE, 'description': '検証用前文', 'is_header_title': True},
            {'pk': 2, 'title': '第1条（<表示> & 検証）', 'description': '1. 検証用本文'},
        ]
        for editable in (False, True):
            with self.subTest(editable=editable):
                parser = ReviewDOMParser()
                parser.feed(render_to_string('tenasapo_knowledge/terms_detail.html', {
                    'terms_list': documents, 'can_edit_terms': editable,
                }))
                sections = parser.find(css_class='terms-document-section')
                self.assertEqual(len(sections), 2)
                edits = parser.find(css_class='terms-section-edit')
                self.assertEqual(len(edits), 2 if editable else 0)
                self.assertEqual(len(parser.find(css_class='terms-register')), int(editable))
                self.assertEqual(len(parser.find(css_class='terms-document-title')), 1)
                for section, document in zip(sections, documents):
                    rows = [node for node in parser.find(css_class='terms-section-heading')
                            if section in node['parents']]
                    self.assertEqual(len(rows), int(editable or document['pk'] != 1))
                    if not rows:
                        continue
                    row, = rows
                    title = [node for node in parser.find(css_class='terms-document-title')
                             if section in node['parents']]
                    if title:
                        self.assertIn(row, title[0]['parents'])
                        self.assertEqual(title[0]['text'], document['title'])
                    if editable:
                        edit, = [node for node in edits if section in node['parents']]
                        self.assertIn(row, edit['parents'])
                        self.assertEqual(edit['attrs']['href'], reverse('terms_edit', kwargs={'pk': document['pk']}))
                        self.assertEqual(edit['attrs']['aria-label'], document['title'] + 'を編集')
                        body, = [node for node in parser.find(css_class='terms-text-body')
                                 if section in node['parents']]
                        self.assertNotIn(row, body['parents'])
                        self.assertLess(parser.nodes.index(row), parser.nodes.index(body))

    def test_registered_article_titles_have_lines_including_the_first_record(self):
        from .test_review_list_layout import ReviewDOMParser
        from .terms import DEFAULT_TERMS_TITLE

        articles = [{'pk': pk, 'title': heading, 'description': '1. 検証用本文\n2. 次の項目'}
                    for pk, (heading, _) in enumerate(TERMS_PASTE_SECTIONS, start=2)]
        primary = {'pk': 1, 'title': DEFAULT_TERMS_TITLE, 'description': '検証用前文', 'is_header_title': True}
        duplicate = {**primary, 'pk': 9, 'is_header_title': False}
        for documents in ([primary, *articles], articles, [primary, *articles, duplicate]):
            for page in ('terms_detail.html', 'terms_accept.html'):
                with self.subTest(page=page, first=documents[0]['pk'], count=len(documents)):
                    parser = ReviewDOMParser()
                    parser.feed(render_to_string('tenasapo_knowledge/' + page, {
                        'terms_list': documents, 'terms_registered': True,
                    }))
                    header, = parser.find(id='terms-page-title')
                    self.assertEqual(header['text'], DEFAULT_TERMS_TITLE)
                    self.assertEqual([node['text'] for node in parser.find(css_class='terms-article-heading')],
                                     [article['title'] for article in articles])
                    self.assertEqual([node['text'] for node in parser.find(css_class='terms-document-title')],
                                     [document['title'] for document in documents if not document.get('is_header_title')])
                    self.assertEqual(len(parser.find(css_class='terms-numbered-item')), 6)
                    for section in parser.find(css_class='terms-document-section'):
                        self.assertEqual(len(parser.find(id=section['attrs']['aria-labelledby'])), 1)

    def render_body(self, body):
        return render_to_string('tenasapo_knowledge/includes/terms_content.html', {
            'terms': {'description': body},
        })

    def test_pasted_three_articles_with_single_newlines_and_three_blank_lines(self):
        from .templatetags.terms_extras import terms_blocks
        from .test_review_list_layout import ReviewDOMParser

        blocks = terms_blocks(TERMS_PASTE_SAMPLE)
        self.assertEqual([block['kind'] for block in blocks],
                         ['heading', 'list', 'blank', 'blank', 'blank',
                          'heading', 'list', 'blank', 'blank', 'blank', 'heading', 'list'])
        parser = ReviewDOMParser()
        parser.feed(self.render_body(TERMS_PASTE_SAMPLE))
        headings = parser.find(css_class='terms-article-heading')
        self.assertEqual([node['text'] for node in headings],
                         [heading for heading, _ in TERMS_PASTE_SECTIONS])
        self.assertEqual([node['tag'] for node in headings], ['h2'] * 3)
        lists = parser.find(tag='ol', css_class='terms-numbered-list')
        self.assertEqual(len(lists), 3)
        for node, section, block in zip(lists, TERMS_PASTE_SECTIONS,
                                       [block for block in blocks if block['kind'] == 'list']):
            items = [item for item in parser.find(tag='li') if node in item['parents']]
            self.assertEqual(len(items), section[1])
            self.assertEqual([item['text'] for item in parser.find(css_class='terms-number')
                              if node in item['parents']],
                             [f'{number}.' for number in range(1, section[1] + 1)])
            self.assertEqual([item['text'] for item in parser.find(css_class='terms-item-text')
                              if node in item['parents']],
                             [item['text'] for item in block['items']])
        self.assertFalse(parser.find(css_class='terms-paragraph'))
        self.assertEqual(len(parser.find(css_class='terms-blank-line')), 6)

    def test_space_delimited_article_headings_preserve_fullwidth_and_kanji_numbers(self):
        from .templatetags.terms_extras import terms_blocks

        for heading in ('第1条　目的および適用', '第１条（目的および適用）',
                        '第１条　目的および適用', '第一条　目的および適用',
                        '第十二条（アカウントの管理）'):
            with self.subTest(heading=heading):
                self.assertEqual(terms_blocks(heading + '\n1. 検証用の本文'), [
                    {'kind': 'heading', 'text': heading},
                    {'kind': 'list', 'items': [{'marker': '1.', 'text': '検証用の本文'}]},
                ])
        for line in ('第1条の説明', '第1条　', '第１条', '本文中の第1条（目的）について'):
            with self.subTest(line=line):
                self.assertEqual(terms_blocks(line), [{'kind': 'paragraph', 'text': line}])

    def test_japanese_articles_fullwidth_digits_and_numbered_items(self):
        from .test_review_list_layout import ReviewDOMParser

        body = ('前文です。\r\n次の行です。\r\n\r\n第1条（適用）\n'
                '1. 一つ目\n2．二つ目\n３、三つ目\n\n'
                '　第　２　条 (利用条件)　\n通常の段落\n第十二条（その他）')
        parser = ReviewDOMParser()
        parser.feed(self.render_body(body))
        self.assertEqual([node['text'] for node in parser.find(css_class='terms-article-heading')],
                         ['第1条（適用）', '　第　２　条 (利用条件)　', '第十二条（その他）'])
        self.assertEqual([node['tag'] for node in parser.find(css_class='terms-article-heading')], ['h2'] * 3)
        self.assertEqual([node['text'] for node in parser.find(css_class='terms-number')], ['1.', '2．', '３、'])
        self.assertEqual([node['text'] for node in parser.find(css_class='terms-item-text')],
                         ['一つ目', '二つ目', '三つ目'])
        self.assertEqual([node['text'] for node in parser.find(css_class='terms-paragraph')],
                         ['前文です。\n次の行です。', '通常の段落'])
        self.assertEqual(len(parser.find(css_class='terms-blank-line')), 2)

    def test_continuations_nested_and_unknown_lines_are_not_swallowed(self):
        from .templatetags.terms_extras import terms_blocks

        blocks = terms_blocks('1. 本文\n  続き\n  （1）内訳\n  1. 入れ子\n'
                              '普通の行\n1.2 バージョン\n2026.11.16\n第1条の説明\n\n2、末尾\n')
        self.assertEqual(blocks[0], {'kind': 'list', 'items': [{
            'marker': '1.', 'text': '本文\n  続き', 'children': [
                {'kind': 'list', 'style': 'parenthetical', 'items': [
                    {'marker': '（1）', 'text': '内訳'}]},
            ],
        }]})
        self.assertEqual(blocks[1], {'kind': 'paragraph', 'text':
            '  1. 入れ子\n普通の行\n1.2 バージョン\n2026.11.16\n第1条の説明'})
        self.assertEqual(blocks[2]['kind'], 'blank')
        self.assertEqual(blocks[3], {'kind': 'list', 'items': [{'marker': '2、', 'text': '末尾'}]})
        self.assertEqual(blocks[4]['kind'], 'blank')

    def test_parenthetical_and_bullet_hierarchy_preserves_markers_and_continuations(self):
        from .templatetags.terms_extras import terms_blocks

        blocks = terms_blocks('1. 本文\n ・ 直下の箇条書き\n (1) 内訳\n  ・詳細\n   続き\n'
                              ' （２）　次の内訳\n  ･ 半角中黒\n10．次項\n１００、最後')
        self.assertEqual(blocks, [{'kind': 'list', 'items': [
            {'marker': '1.', 'text': '本文', 'children': [
                {'kind': 'list', 'style': 'bullet', 'items': [{'marker': '・', 'text': '直下の箇条書き'}]},
                {'kind': 'list', 'style': 'parenthetical', 'items': [
                    {'marker': '(1)', 'text': '内訳', 'children': [
                        {'kind': 'list', 'style': 'bullet', 'items': [
                            {'marker': '・', 'text': '詳細\n   続き'}]}]},
                    {'marker': '（２）', 'text': '次の内訳', 'children': [
                        {'kind': 'list', 'style': 'bullet', 'items': [
                            {'marker': '･', 'text': '半角中黒'}]}]},
                ]},
            ]},
            {'marker': '10．', 'text': '次項'}, {'marker': '１００、', 'text': '最後'},
        ]}])

    def test_standalone_lists_and_boundaries_do_not_reuse_previous_parent(self):
        from .templatetags.terms_extras import terms_blocks

        blocks = terms_blocks('1. 本文\n\n・単独\n\n（１）単独の内訳\n通常の行\n'
                              '・別のリスト\n第2条（見出し）\n(2)別の内訳')
        self.assertEqual([block['kind'] for block in blocks],
                         ['list', 'blank', 'list', 'blank', 'list', 'paragraph', 'list', 'heading', 'list'])
        self.assertEqual([block.get('style', 'numbered') for block in blocks if block['kind'] == 'list'],
                         ['numbered', 'bullet', 'parenthetical', 'bullet', 'parenthetical'])
        self.assertTrue(all('children' not in item for block in blocks if block['kind'] == 'list'
                            for item in block['items']))

    def test_decimal_embedded_bullets_and_unknown_markers_remain_plaintext(self):
        from .templatetags.terms_extras import terms_blocks

        for line in ('1.2 バージョン', '１．２ バージョン', '2026.11.16',
                     '本文・本文', '本文 (1) 内訳', '(1）混在括弧', '（１)混在括弧',
                     '・', '･　', '(1)', '（１２３４）本文', '1.'):
            with self.subTest(line=line):
                self.assertEqual(terms_blocks(line), [{'kind': 'paragraph', 'text': line}])

    def test_nested_lists_are_semantic_and_escape_all_plaintext_on_both_pages(self):
        from django.utils.safestring import mark_safe
        from .test_review_list_layout import ReviewDOMParser

        body = mark_safe('第1条（適用）\n1. <b>本文</b>\n(1) <script>内訳</script>\n'
                         '・<img src=x onerror=alert(1)> & "詳細"\n（２）全角\n10. 次項')
        for page in ('terms_detail.html', 'terms_accept.html'):
            with self.subTest(page=page):
                parser = ReviewDOMParser()
                parser.feed(render_to_string('tenasapo_knowledge/' + page, {
                    'terms_list': [{'pk': 1, 'title': '規約', 'description': body}],
                    'terms_registered': True,
                }))
                root, = [node for node in parser.find(tag='ol', css_class='terms-numbered-list')
                         if 'terms-parenthetical-list' not in node['attrs'].get('class', '').split()]
                sub, = parser.find(tag='ol', css_class='terms-parenthetical-list')
                bullet, = parser.find(tag='ul', css_class='terms-bullet-list')
                self.assertIn(root, sub['parents'])
                self.assertIn(sub, bullet['parents'])
                self.assertEqual([node['text'] for node in parser.find(css_class='terms-number')],
                                 ['1.', '(1)', '・', '（２）', '10.'])
                self.assertEqual([node['text'] for node in parser.find(css_class='terms-item-text')],
                                 ['<b>本文</b>', '<script>内訳</script>',
                                  '<img src=x onerror=alert(1)> & "詳細"', '全角', '次項'])
                content, = parser.find(css_class='terms-text-body')
                self.assertFalse(any(node['tag'] in {'b', 'script', 'img'} and content in node['parents']
                                     for node in parser.nodes))

    def test_all_structured_fragments_escape_html_even_if_input_marked_safe(self):
        from django.utils.safestring import mark_safe
        from .test_review_list_layout import ReviewDOMParser

        body = ('第1条（<img src=x onerror=alert(1)>）\n'
                '1. <script>alert(2)</script> & "quoted"\n'
                '  <svg onload=alert(3)>\n\n'
                '<a href="javascript:alert(4)">普通の行</a>\n[b]強調しない[/b]')
        rendered = self.render_body(mark_safe(body))
        parser = ReviewDOMParser()
        parser.feed(rendered)
        self.assertFalse(any(node['tag'] in {'script', 'img', 'svg', 'a', 'b', 'strong'} for node in parser.nodes))
        self.assertEqual(parser.find(css_class='terms-article-heading')[0]['text'], body.split('\n')[0])
        self.assertEqual(parser.find(css_class='terms-item-text')[0]['text'],
                         '<script>alert(2)</script> & "quoted"\n  <svg onload=alert(3)>')
        self.assertIn('[b]強調しない[/b]', rendered)

    def test_public_and_consent_use_identical_structure_and_scoped_styles(self):
        context = {'terms': {'title': 'Tenasapo Knowledge Site 「Nexus」 サイトの利用条件',
                            'description': '第1条（適用）\n1. 本文\n2．次の項'},
                   'terms_registered': True}
        context['terms_list'] = [context['terms']]
        shared = self.render_body(context['terms']['description'])
        for page in ('terms_detail.html', 'terms_accept.html'):
            rendered = render_to_string('tenasapo_knowledge/' + page, context)
            self.assertIn(shared, rendered)
            self.assertIn('Tenasapo Knowledge Site 「Nexus」 サイトの利用条件', rendered)
            self.assertIn('--terms-accent: #147d78;', rendered)
            self.assertIn('border-left: 4px solid #ed7d22;', rendered)
            self.assertIn('line-height: 1.5;', rendered)
            self.assertIn('background: #faf8f3;', rendered)
            self.assertNotIn('最終更新', rendered)
        base = render_to_string('base.html')
        self.assertNotIn('--terms-accent', base)

    def test_multiple_documents_share_one_accessible_pane_with_external_consent_controls(self):
        from .test_review_list_layout import ReviewDOMParser

        documents = [{'pk': pk, 'title': f'規約{pk}', 'description': f'第{pk}条（適用）\n1. 本文{pk}'}
                     for pk in (1, 2, 3)]
        for page in ('terms_detail.html', 'terms_accept.html'):
            with self.subTest(page=page):
                parser = ReviewDOMParser()
                parser.feed(render_to_string('tenasapo_knowledge/' + page, {
                    'terms_list': documents, 'terms': documents[0], 'terms_registered': True,
                }))
                pane, = parser.find(css_class='terms-scroll')
                self.assertEqual(pane['attrs']['tabindex'], '0')
                self.assertEqual(pane['attrs']['role'], 'region')
                self.assertTrue(pane['attrs']['aria-label'])
                self.assertEqual(len(parser.find(css_class='terms-document')), 1)
                sections = parser.find(css_class='terms-document-section')
                self.assertEqual(len(sections), 3)
                self.assertTrue(all(pane in section['parents'] for section in sections))
                self.assertEqual([node['text'] for node in parser.find(css_class='terms-document-title')],
                                 [document['title'] for document in documents])
                header, = parser.find(id='terms-page-title')
                self.assertEqual(header['tag'], 'h1')
                self.assertEqual(header['text'].strip(), 'Tenasapo Knowledge Site 「Nexus」 サイトの利用条件')
                for index, section in enumerate(sections):
                    label = section['attrs']['aria-labelledby']
                    labelled_by, = parser.find(id=label)
                    self.assertEqual(labelled_by['text'].strip(), documents[index]['title'])
                for control in parser.find(tag='form'):
                    self.assertNotIn(pane, control['parents'])

    def test_first_nonprimary_title_is_retained_and_escaped_without_detail_context(self):
        from .test_review_list_layout import ReviewDOMParser

        # Deliberately omit the detail-only `terms` context: consent receives
        # terms_list. Nonprimary saved titles must not be hidden, even first.
        title = '  保存済み <特別> & 規約  '
        documents = [{'pk': 41, 'title': title, 'description': '第1条（適用）\n1. 本文'}]
        for page in ('terms_detail.html', 'terms_accept.html'):
            with self.subTest(page=page):
                parser = ReviewDOMParser()
                parser.feed(render_to_string('tenasapo_knowledge/' + page, {
                    'terms_list': documents, 'terms_registered': True,
                }))
                header, = parser.find(id='terms-page-title')
                self.assertEqual(header['text'].strip(), 'Tenasapo Knowledge Site 「Nexus」 サイトの利用条件')
                self.assertEqual([node['text'] for node in parser.find(css_class='terms-document-title')], [title])
                self.assertFalse(parser.find(css_class='page-title-sub'))
                self.assertEqual([node['text'] for node in parser.find(css_class='terms-article-heading')],
                                 ['第1条（適用）'])
                self.assertFalse(parser.find(tag='特別'))