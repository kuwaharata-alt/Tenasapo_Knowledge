"""Article management GET filters, toolbar, permissions and DOM contracts."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.views import redirect_to_login
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .models import ArticleGood, KnowledgeArticle, TipsArticle, TipsGood, UserProfile
from .test_review_list_layout import ReviewDOMParser
from .views import ArticleManagementView, CONTRIBUTOR_GROUP_NAME


class ArticleManagementLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.staff = users.objects.create_user(username='management-layout-staff', is_staff=True)
        cls.creator = users.objects.create_user(username='management-layout-contributor')
        cls.other = users.objects.create_user(username='management-layout-other')
        cls.author_name = '投稿者 <& "表示名">'
        UserProfile.objects.update_or_create(
            user=cls.creator, defaults={'display_name': cls.author_name},
        )
        group, _ = Group.objects.get_or_create(name=CONTRIBUTOR_GROUP_NAME)
        cls.creator.groups.add(group)
        voters = [users.objects.create_user(username=f'management-layout-voter-{i}')
                  for i in range(5)]
        cls.start = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        cls.items = []
        for kind, model, good_model, relation in (
            ('faq', KnowledgeArticle, ArticleGood, 'article'),
            ('tips', TipsArticle, TipsGood, 'tip'),
        ):
            for state in ('published', 'pending', 'unpublished'):
                index = len(cls.items)
                views = (4, 1, 6, 2, 5, 3)[index]
                item = model.objects.create(
                    title=f'{kind} {state} <& "タイトル">', body='本文 <strong>詳細</strong>',
                    category='PC/設定', created_by=cls.creator,
                    # A saved login ID must resolve to the contributor's display name.
                    created_by_name=cls.creator.username,
                    is_published=state != 'unpublished', is_approved=state != 'pending',
                    visible_to_demo=index % 2 == 0,
                )
                model.objects.filter(pk=item.pk).update(**{
                    'created_at': cls.start + timedelta(days=index),
                    'updated_at': cls.start + timedelta(days=20 - index),
                    'answer_view_count' if kind == 'faq' else 'view_count': views,
                })
                for voter in voters[:index]:
                    good_model.objects.create(**{relation: item, 'user': voter})
                item.refresh_from_db()
                cls.items.append({'type': kind, 'state': state, 'obj': item,
                                  'good_count': index, 'view_count': views})
            for creator, day in ((cls.other, 1), (cls.creator, -1), (cls.creator, 6)):
                extra = model.objects.create(
                    title=f'{kind} 除外対象 {day}', body='対象外', created_by=creator,
                    is_published=True, is_approved=True,
                )
                model.objects.filter(pk=extra.pk).update(created_at=cls.start + timedelta(days=day))

    def setUp(self):
        self.client.force_login(self.staff)

    def params(self, **overrides):
        return {'author': self.author_name, 'date_from': '2026-10-10',
                'date_to': '2026-10-15', **overrides}

    def context(self, params=None):
        request = RequestFactory().get(reverse('article_management'), params or {})
        request.user = self.staff
        view = ArticleManagementView()
        view.setup(request)
        return view.get_context_data(), request

    def parse(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'tenasapo_knowledge/article_management.html')
        parser = ReviewDOMParser()
        parser.feed(response.content.decode(response.charset))
        return parser

    def assert_items(self, context, expected):
        self.assertEqual({(item['type'], item['id']) for item in context['articles']},
                         {(item['type'], item['obj'].pk) for item in expected})

    def test_modal_get_form_keeps_filter_keys_dates_sort_and_actions(self):
        params = self.params(article_type='tips', status='unpublished', sort='view_count', dir='asc')
        response = self.client.get(reverse('article_management'), params)
        parser = self.parse(response)
        panel = parser.find(id='managementFilterPanel')[0]
        self.assertIn('modal', panel['attrs']['class'].split())
        self.assertNotIn('collapse', panel['attrs']['class'].split())
        self.assertEqual(panel['attrs']['aria-labelledby'], 'managementFilterLabel')
        form = parser.find(tag='form', id='managementFilterForm')[0]
        self.assertIn(panel, form['parents'])
        self.assertEqual(form['attrs']['method'], 'get')
        controls = [node for node in parser.nodes if form in node['parents']
                    and node['tag'] in ('input', 'select') and 'name' in node['attrs']]
        self.assertEqual({node['attrs']['name'] for node in controls},
                         {'status', 'article_type', 'author', 'date_from', 'date_to', 'sort', 'dir'})
        for name in ('status', 'article_type'):
            radios = parser.find(tag='input', name=name, type='radio')
            self.assertEqual([node['attrs']['value'] for node in radios],
                             ['', 'published', 'unpublished'] if name == 'status' else ['', 'faq', 'tips'])
            self.assertEqual([node['attrs']['value'] for node in radios if 'checked' in node['attrs']],
                             [params[name]])
            self.assertTrue(all('disabled' not in node['attrs'] for node in radios))
        for name in ('date_from', 'date_to', 'sort', 'dir'):
            control = next(node for node in controls if node['attrs']['name'] == name)
            self.assertEqual(control['attrs']['value'], params[name])
            self.assertEqual(control['attrs']['type'], 'date' if name.startswith('date_') else 'hidden')
        author = parser.find(tag='select', name='author')[0]
        selected = [node for node in parser.find(tag='option')
                    if author in node['parents'] and 'selected' in node['attrs']]
        self.assertEqual([node['attrs']['value'] for node in selected], [self.author_name])
        self.assertIn({'id': self.creator.pk, 'name': self.author_name}, response.context['author_choices'])
        self.assertIn('非公開には未承認の記事も含まれます。', parser.find(id='mgmtStatusHelp')[0]['text'])
        footer = next(node for node in parser.find(css_class='modal-footer') if form in node['parents'])
        submit = next(node for node in parser.find(tag='button', type='submit') if form in node['parents'])
        self.assertIn(footer, submit['parents'])
        self.assertEqual(submit['text'].strip(), '絞り込み')
        reset = next(node for node in parser.find(tag='a') if footer in node['parents'])
        self.assertEqual(reset['attrs']['href'], reverse('article_management'))
        self.assertTrue(any(footer in node['parents'] for node in parser.find(
            tag='button', **{'data-bs-dismiss': 'modal'},
        )))

    def test_status_type_and_author_filters_keep_existing_meaning(self):
        for kind in ('', 'faq', 'tips'):
            for status in ('', 'published', 'unpublished'):
                with self.subTest(kind=kind, status=status):
                    context, _ = self.context(self.params(article_type=kind, status=status))
                    expected = [item for item in self.items
                                if (not kind or item['type'] == kind)
                                and (not status or (item['state'] == 'published') == (status == 'published'))]
                    self.assert_items(context, expected)
                    self.assertTrue(all(item['creator_display_name'] == self.author_name
                                        for item in context['articles']))
        for author in (self.creator.username, self.author_name[:-1], '存在しない投稿者'):
            with self.subTest(author=author):
                context, _ = self.context(self.params(author=author))
                self.assert_items(context, [])
        context, _ = self.context(self.params(author=self.other.username))
        self.assertEqual(len(context['articles']), 2)
        self.assertEqual({item['creator_display_name'] for item in context['articles']}, {self.other.username})

    def test_created_date_range_is_inclusive_and_not_updated_date(self):
        for lower, upper, indexes in (
            ('2026-10-10', '2026-10-10', [0]),
            ('2026-10-11', '2026-10-14', [1, 2, 3, 4]),
            ('2026-10-15', '2026-10-15', [5]),
            ('2026-10-15', '2026-10-10', []),
        ):
            with self.subTest(lower=lower, upper=upper):
                context, _ = self.context(self.params(date_from=lower, date_to=upper))
                self.assertEqual((context['date_from'], context['date_to']), (lower, upper))
                self.assert_items(context, [self.items[index] for index in indexes])

    def test_sort_options_preserve_repeated_get_values_and_direction_without_mutation(self):
        keys = ('created_at', 'good_count', 'view_count')
        for key in keys:
            for direction in ('asc', 'desc'):
                with self.subTest(key=key, direction=direction):
                    context, request = self.context(self.params(
                        article_type='faq', status='unpublished', sort=key, dir=direction,
                        extra=['a&b', '日本語 空白'],
                    ))
                    original = request.GET.copy()
                    options = context['management_sort_options']
                    self.assertEqual([option['label'] for option in options], ['作成日', 'いいね', '閲覧数'])
                    for target, option in zip(keys, options):
                        self.assertEqual(set(option), {'label', 'url', 'active', 'direction'})
                        self.assertIs(option['active'], target == key)
                        self.assertEqual(option['direction'], direction)
                        expected = dict(request.GET.lists())
                        expected.update(sort=[target], dir=[
                            'asc' if target == key and direction == 'desc' else 'desc',
                        ])
                        self.assertEqual(parse_qs(urlsplit(option['url']).query), expected)
                    self.assertEqual(request.GET, original)
                    response = self.client.get(reverse('article_management'), request.GET)
                    parser = self.parse(response)
                    toolbar = parser.find(tag='nav', css_class='mgmt-sort-links')[0]
                    links = [node for node in parser.find(tag='a') if toolbar in node['parents']]
                    self.assertEqual([node['attrs']['href'] for node in links],
                                     [option['url'] for option in options])
                    active = [node for node in links if node['attrs'].get('aria-current') == 'true']
                    self.assertEqual(len(active), 1)
                    icon = 'bi-arrow-up' if direction == 'asc' else 'bi-arrow-down'
                    self.assertTrue(any(active[0] in node['parents'] for node in parser.find(css_class=icon)))
                    self.assertIn('昇順' if direction == 'asc' else '降順', active[0]['text'])
                    toggle = parser.find(css_class='mgmt-filter-toggle')[0]
                    self.assertEqual(toggle['attrs']['data-bs-toggle'], 'modal')
                    self.assertEqual(toggle['attrs']['data-bs-target'], '#managementFilterPanel')
                    self.assertIn('適用中', toggle['text'])

    def test_sort_orders_both_article_types_and_invalid_values_fall_back(self):
        for key in ('created_at', 'good_count', 'view_count'):
            for direction in ('asc', 'desc'):
                with self.subTest(key=key, direction=direction):
                    context, _ = self.context(self.params(sort=key, dir=direction))
                    expected = sorted(self.items, key=lambda item: (
                        item['obj'].created_at if key == 'created_at' else item[key]
                    ), reverse=direction == 'desc')
                    self.assertEqual([(item['type'], item['id']) for item in context['articles']],
                                     [(item['type'], item['obj'].pk) for item in expected])
        context, _ = self.context(self.params(sort='invalid', dir='invalid'))
        self.assertEqual((context['sort_by'], context['sort_dir']), ('created_at', 'desc'))

    def test_six_column_results_scroll_metadata_status_and_preview_contracts(self):
        response = self.client.get(reverse('article_management'), self.params())
        parser = self.parse(response)
        self.assertIn('article-management-page', parser.find(tag='body')[0]['attrs']['class'].split())
        card = parser.find(css_class='mgmt-results-card')[0]
        region = parser.find(css_class='mgmt-results')[0]
        self.assertIn(card, region['parents'])
        self.assertEqual((region['attrs']['role'], region['attrs']['tabindex']), ('region', '0'))
        self.assertEqual(region['attrs']['aria-label'], '記事管理一覧')
        table = parser.find(tag='table', id='mgmt-table')[0]
        self.assertIn(region, table['parents'])
        panel = parser.find(id='managementFilterPanel')[0]
        self.assertNotIn(panel, region['parents'])
        self.assertNotIn(region, panel['parents'])
        headers = [node for node in parser.find(tag='th') if table in node['parents']]
        self.assertEqual([node['text'].strip() for node in headers],
                         ['記事', '公開状態', 'いいね', '閲覧', 'デモ閲覧', '操作'])
        self.assertTrue(all(node['attrs']['scope'] == 'col' for node in headers))
        rows = parser.find(tag='tr', css_class='mgmt-expandable-row')
        self.assertEqual(len(rows), 6)
        for row in rows:
            cells = [node for node in parser.find(tag='td') if node['parents'][-1] is row]
            self.assertEqual(len(cells), 6)
            title = next(node for node in parser.find(css_class='mgmt-title-text') if row in node['parents'])
            record = next(item for item in self.items if item['obj'].title == title['text'])
            item = record['obj']
            meta = next(node for node in parser.find(css_class='mgmt-item-meta') if row in node['parents'])
            for text in (item.management_code, self.author_name, '作成者', '作成日', '更新日'):
                self.assertIn(text, meta['text'])
            self.assertEqual(cells[1]['text'].strip(), {
                'published': '公開中', 'pending': '未承認', 'unpublished': '非公開',
            }[record['state']])
            self.assertEqual(cells[2]['text'].strip(), str(record['good_count']))
            self.assertEqual(cells[3]['text'].strip(), str(record['view_count']))
            detail_id = f"inline-detail-{record['type']}-{item.pk}"
            preview = parser.find(id=detail_id)[0]
            self.assertIn('collapse', preview['attrs']['class'].split())
            preview_cell = next(node for node in reversed(preview['parents']) if node['tag'] == 'td')
            self.assertEqual(preview_cell['attrs']['colspan'], '6')
            self.assertNotIn(row, preview['parents'])
            source_id = f"detail-{record['type']}-{item.pk}"
            self.assertEqual(len(parser.find(id=source_id)), 1)
            self.assertEqual(len(parser.find(**{'data-detail-source-id': source_id})), 1)
        styles = '\n'.join(node['text'] for node in parser.find(tag='style'))
        for rule in ('.mgmt-results', 'overflow: auto', 'min-height: 0',
                     '.mgmt-results thead', 'position: sticky', 'table-layout: fixed'):
            self.assertIn(rule, styles)

    def test_rightmost_toggle_edit_and_demo_form_are_retained(self):
        response = self.client.get(reverse('article_management'), self.params())
        parser = self.parse(response)
        self.assertTrue(response.context['can_edit'])
        for record in self.items:
            kind, item = record['type'], record['obj']
            toggle = parser.find(tag='button', **{'data-detail-id': f'detail-{kind}-{item.pk}'})[0]
            actions = toggle['parents'][-1]
            self.assertIn('mgmt-row-actions', actions['attrs']['class'].split())
            children = [node for node in parser.nodes if node['parents'] and node['parents'][-1] is actions]
            self.assertEqual(len(children), 2)
            self.assertIn('mgmt-edit-action', children[0]['attrs']['class'].split())
            self.assertIs(children[-1], toggle)
            edit = next(node for node in parser.find(tag='a') if children[0] in node['parents'])
            self.assertEqual(edit['attrs']['href'], reverse('article_edit' if kind == 'faq' else 'tip_edit', args=[item.pk]))
            self.assertEqual(toggle['attrs']['data-bs-toggle'], 'collapse')
            self.assertEqual(toggle['attrs']['data-bs-target'], f'#inline-detail-{kind}-{item.pk}')
            self.assertEqual(toggle['attrs']['aria-controls'], f'inline-detail-{kind}-{item.pk}')
            self.assertEqual(toggle['attrs']['aria-expanded'], 'false')
            self.assertIn('本文を展開・折りたたみ', toggle['attrs']['aria-label'])
            self.assertTrue(any(toggle in node['parents'] for node in parser.find(css_class='mgmt-toggle-icon')))
            row = next(node for node in toggle['parents'] if node['tag'] == 'tr')
            form = next(node for node in parser.find(css_class='demo-visibility-form') if row in node['parents'])
            self.assertEqual((form['attrs']['method'], form['attrs']['action']), ('post', reverse('article_management')))
            inputs = [node for node in parser.find(tag='input') if form in node['parents']]
            values = {node['attrs']['name']: node['attrs'].get('value') for node in inputs if 'name' in node['attrs']}
            self.assertEqual(set(values), {'csrfmiddlewaretoken', 'article_type', 'article_id'})
            self.assertTrue(values['csrfmiddlewaretoken'])
            self.assertEqual((values['article_type'], values['article_id']), (kind, str(item.pk)))
            checkbox = next(node for node in inputs if node['attrs'].get('type') == 'checkbox')
            self.assertEqual('checked' in checkbox['attrs'], item.visible_to_demo)
            self.assertNotIn('disabled', checkbox['attrs'])
            self.assertEqual(checkbox['attrs']['aria-label'], f'{item.title}のデモ閲覧')
            status = next(node for node in parser.find(css_class='demo-visibility-status') if form in node['parents'])
            self.assertEqual((status['attrs']['role'], status['attrs']['aria-live']), ('status', 'polite'))

    def test_readonly_staff_retains_toggle_but_no_edit_or_demo_write_controls(self):
        with patch('tenasapo_knowledge.views.can_edit_article', return_value=False):
            response = self.client.get(reverse('article_management'), self.params())
            parser = self.parse(response)
            self.assertFalse(response.context['can_edit'])
            self.assertFalse(parser.find(css_class='demo-visibility-form'))
            self.assertFalse(parser.find(css_class='demo-visibility-checkbox'))
            self.assertEqual(len(parser.find(css_class='mgmt-title-btn')), 6)
            edit_urls = {reverse('article_edit' if item['type'] == 'faq' else 'tip_edit', args=[item['obj'].pk])
                         for item in self.items}
            self.assertFalse(any(node['attrs'].get('href') in edit_urls for node in parser.find(tag='a')))
            for row in parser.find(css_class='mgmt-expandable-row'):
                checkbox = next(node for node in parser.find(tag='input', type='checkbox') if row in node['parents'])
                self.assertIn('disabled', checkbox['attrs'])
                record = next(item for item in self.items
                              if checkbox['attrs']['aria-label'] == f"{item['obj'].title}のデモ閲覧")
                self.assertEqual('checked' in checkbox['attrs'], record['obj'].visible_to_demo)
            item = self.items[0]['obj']
            denied = self.client.post(reverse('article_management'), {
                'article_type': 'faq', 'article_id': item.pk, 'visible_to_demo': '0',
            })
            self.assertEqual(denied.status_code, 403)
            item.refresh_from_db()
            self.assertTrue(item.visible_to_demo)

    def test_non_staff_contributor_and_anonymous_cannot_enter(self):
        for user in (self.creator, self.other):
            with self.subTest(user=user):
                self.client.logout()
                self.client.force_login(user)
                response = self.client.get(reverse('article_management'))
                self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
        self.client.logout()
        response = self.client.get(reverse('article_management'))
        self.assertRedirects(response, redirect_to_login(reverse('article_management')).url,
                             fetch_redirect_response=False)

    def test_empty_result_keeps_six_column_placeholder(self):
        response = self.client.get(reverse('article_management'), self.params(author='存在しない投稿者'))
        parser = self.parse(response)
        self.assertFalse(parser.find(css_class='mgmt-expandable-row'))
        table = parser.find(id='mgmt-table')[0]
        cells = [node for node in parser.find(tag='td') if table in node['parents']]
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]['attrs']['colspan'], '6')
        self.assertIn('該当する記事がありません。', cells[0]['text'])