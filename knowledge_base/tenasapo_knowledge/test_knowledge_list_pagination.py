"""Pagination contracts exercised through the real FAQ and Tips list CBVs."""

from collections import Counter
from datetime import datetime, timedelta
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from . import views
from .models import ArticleFavorite, FAQCategory, KnowledgeArticle, TipsArticle, TipsFavorite


class PaginationDOM(HTMLParser):
    """Record decoded attributes and ancestry without an additional dependency."""

    void_tags = {
        'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
        'link', 'meta', 'param', 'source', 'track', 'wbr',
    }

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.stack = []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        index = len(self.elements)
        self.elements.append({
            'tag': tag, 'attrs': dict(attrs), 'ancestors': tuple(self.stack),
        })
        if tag not in self.void_tags:
            self.stack.append(index)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.void_tags:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for position in range(len(self.stack) - 1, -1, -1):
            if self.elements[self.stack[position]]['tag'] == tag:
                del self.stack[position:]
                break

    def find(self, tag=None, *, within=None, css_class=None):
        return [
            (index, element) for index, element in enumerate(self.elements)
            if (tag is None or element['tag'] == tag)
            and (within is None or within in element['ancestors'])
            and (css_class is None
                 or css_class in element['attrs'].get('class', '').split())
        ]


class KnowledgeListPaginationTestsMixin:
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='pagination-staff', is_staff=True,
        )
        self.factory = RequestFactory()
        self.sequence = 0
        self.enterContext(patch.object(views, 'FAQ_APPROVAL_ENABLED', True))

    def create_items(self, count, **overrides):
        items = []
        for _ in range(count):
            self.sequence += 1
            values = {
                # save() normally allocates this code; keep fixtures deterministic
                # and avoid its extra MAX/last lookup for every inserted article.
                'management_code': f'{self.code_prefix}{self.sequence:05d}',
                'title': '対象 keyword & 日本語',
                'body': '本文',
                'category': '親/子',
                'target_os': 'Ubuntu 24.04',
                'expires_on': None,
                'created_by': self.user,
            }
            values.update(overrides)
            # Retain the real model defaults: published and approved are True.
            items.append(self.model.objects.create(**values))
        return items

    def get_context(self, params=None, *, user=None, url=None):
        request = self.factory.get(url or reverse(self.url_name), params or {})
        request.user = self.user if user is None else user
        request.session = {}
        response = self.view_class.as_view()(request)
        self.assertEqual(response.status_code, 200)
        return response.context_data, request

    def render(self, context, request, *, panel_only=False):
        template = (
            'includes/knowledge_list_pagination.html'
            if panel_only else self.view_class.template_name
        )
        html = render_to_string(template, context, request=request)
        return html, PaginationDOM(html)

    def ordered_ids(self, items=None):
        queryset = self.model.objects.all()
        if items is not None:
            queryset = queryset.filter(pk__in=[item.pk for item in items])
        return list(queryset.order_by('-management_code', '-created_at').values_list('pk', flat=True))

    def assert_page(self, context, expected_ids, number, total):
        page = context['page_obj']
        self.assertEqual(page.number, number)
        self.assertEqual(page.paginator.count, total)
        self.assertEqual(page.paginator.per_page, 20)
        self.assertEqual(page.paginator.num_pages, max(1, (total + 19) // 20))
        self.assertEqual(context['paginator'], page.paginator)
        self.assertEqual(context['is_paginated'], total > 20)
        self.assertEqual([item.pk for item in context[self.context_name]], expected_ids)
        self.assertEqual([item.pk for item in context['object_list']], expected_ids)
        self.assertEqual([item.pk for item in page.object_list], expected_ids)
        self.assertEqual(len(expected_ids), len(set(expected_ids)))
        self.assertEqual(
            page.paginator.object_list.query.order_by,
            ('-management_code', '-created_at'),
        )

    def assert_url(self, url, request, number):
        parts = urlsplit(url)
        self.assertEqual(parts.path, request.path)
        expected = {
            key: values for key, values in request.GET.lists()
            if key not in {'page', 'open_code', 'open_id'}
        }
        expected['page'] = [str(number)]
        self.assertEqual(parse_qs(parts.query, keep_blank_values=True), expected)
        self.assertFalse(parts.fragment)

    def test_zero_twenty_twenty_one_and_forty_one_boundaries(self):
        previous_total = 0
        for total in (0, 20, 21, 41):
            with self.subTest(total=total):
                self.create_items(total - previous_total)
                previous_total = total
                # Oppose creation time to code order, rather than accidentally
                # accepting the model's default published_at ordering.
                for index, pk in enumerate(self.ordered_ids()):
                    self.model.objects.filter(pk=pk).update(
                        created_at=timezone.now() - timedelta(days=total - index),
                    )
                expected = self.ordered_ids()
                seen = []
                for number in range(1, max(1, (total + 19) // 20) + 1):
                    context, request = self.get_context({'page': str(number)})
                    ids = expected[(number - 1) * 20:number * 20]
                    self.assert_page(context, ids, number, total)
                    self.assertTrue(set(seen).isdisjoint(ids))
                    seen.extend(ids)
                    self.assertEqual(bool(context['pagination_previous_url']), number > 1)
                    self.assertEqual(bool(context['pagination_next_url']), number * 20 < total)
                    self.assertEqual(
                        [item['number'] for item in context['pagination_first_pages']],
                        list(range(1, context['paginator'].num_pages + 1)),
                    )
                    self.assertEqual(context['pagination_last_pages'], [])
                    self.assertFalse(context['pagination_has_gap'])
                    html, dom = self.render(context, request, panel_only=True)
                    self.assertEqual(len(dom.find('nav')), int(total > 20))
                    if total <= 20:
                        self.assertEqual(html.strip(), '')
                self.assertEqual(seen, expected)

    def test_invalid_page_numbers_follow_get_page_and_last_alias(self):
        self.create_items(41)
        expected = self.ordered_ids()
        for value, number in (
            ('not-a-number', 1), ('', 1), ('1.5', 1),
            ('0', 3), ('-1', 3), ('4', 3), ('999999999', 3), ('last', 3),
        ):
            with self.subTest(page=value):
                context, _ = self.get_context({'page': value})
                self.assert_page(context, expected[(number - 1) * 20:number * 20], number, 41)

    def test_empty_list_accepts_invalid_out_of_range_and_last_pages(self):
        for value in ('invalid', '0', '-1', '999', 'last'):
            with self.subTest(page=value):
                context, _ = self.get_context({'page': value})
                self.assert_page(context, [], 1, 0)
                self.assertEqual(context['pagination_previous_url'], '')
                self.assertEqual(context['pagination_next_url'], '')

    def test_open_code_and_id_select_containing_page_only_without_explicit_page(self):
        self.create_items(41)
        expected = self.ordered_ids()
        for position in (0, 19, 20, 39, 40):
            target = self.model.objects.get(pk=expected[position])
            for key, value in (('open_code', target.management_code), ('open_id', str(target.pk))):
                with self.subTest(position=position, key=key):
                    number = position // 20 + 1
                    context, _ = self.get_context({key: f' {value} '})
                    self.assert_page(context, expected[(number - 1) * 20:number * 20], number, 41)
                    self.assertEqual(context[self.open_context_name], target.pk)
        target = self.model.objects.get(pk=expected[-1])
        for key, value in (('open_code', target.management_code), ('open_id', str(target.pk))):
            for explicit, number in (('1', 1), ('invalid', 1), ('', 1), ('last', 3)):
                with self.subTest(key=key, page=explicit):
                    context, _ = self.get_context({key: value, 'page': explicit})
                    self.assert_page(context, expected[(number - 1) * 20:number * 20], number, 41)
                    self.assertEqual(
                        context[self.open_context_name], target.pk if number == 3 else None,
                    )

    def test_missing_filtered_out_and_conflicting_open_targets(self):
        self.create_items(41)
        expected = self.ordered_ids()
        first = self.model.objects.get(pk=expected[0])
        last = self.model.objects.get(pk=expected[-1])
        for params in (
            {'open_code': 'missing'}, {'open_id': '999999999'},
            {'open_id': 'not-an-id'}, {'open_id': '-1'},
            {'open_code': 'missing', 'open_id': str(last.pk)},
            {'q': 'does not exist', 'open_code': last.management_code},
        ):
            with self.subTest(params=params):
                context, _ = self.get_context(params)
                filtered = 'q' in params
                self.assert_page(context, [] if filtered else expected[:20], 1, 0 if filtered else 41)
                self.assertIsNone(context[self.open_context_name])
        context, _ = self.get_context({
            'open_code': first.management_code, 'open_id': str(last.pk),
        })
        self.assert_page(context, expected[:20], 1, 41)
        self.assertEqual(context[self.open_context_name], first.pk)

    def test_compact_page_windows_gap_threshold_and_middle_jump(self):
        previous_total = 0
        for total in (101, 121, 141, 181, 201):
            with self.subTest(total=total):
                self.create_items(total - previous_total)
                previous_total = total
                pages = (total + 19) // 20
                number = 6
                context, request = self.get_context({'page': str(number)})
                expected = self.ordered_ids()
                self.assert_page(context, expected[100:120], number, total)
                first = [item['number'] for item in context['pagination_first_pages']]
                last = [item['number'] for item in context['pagination_last_pages']]
                self.assertEqual(first, [1, 2, 3, 4, 5])
                self.assertEqual(last, list(range(max(6, pages - 1), pages + 1)))
                self.assertTrue(set(first).isdisjoint(last))
                self.assertEqual(context['pagination_has_gap'], pages > 7)
                for item in context['pagination_first_pages'] + context['pagination_last_pages']:
                    self.assert_url(item['url'], request, item['number'])
                self.assert_url(context['pagination_previous_url'], request, 5)
                if pages > 6:
                    self.assert_url(context['pagination_next_url'], request, 7)
                _, dom = self.render(context, request, panel_only=True)
                gaps = [
                    element for _, element in dom.find('span')
                    if element['attrs'].get('aria-hidden') == 'true'
                ]
                self.assertEqual(len(gaps), 2 if pages > 7 else 0)
                inputs = [element['attrs'] for _, element in dom.find('input')]
                number_input, = [attrs for attrs in inputs if attrs.get('name') == 'page']
                self.assertEqual(number_input['type'], 'number')
                self.assertEqual(number_input['min'], '1')
                self.assertEqual(number_input['max'], str(pages))
                self.assertEqual(number_input['value'], '6')
                self.assertEqual(number_input['step'], '1')
                self.assertIn('required', number_input)
                if pages > 7:
                    numbered_links = [
                        int(element['attrs']['aria-label'].removesuffix('ページ目'))
                        for _, element in dom.find('a')
                        if element['attrs'].get('aria-label', '').endswith('ページ目')
                    ]
                    self.assertEqual(numbered_links, first + last)
                    self.assertNotIn(6, numbered_links)
                form, = dom.find('form')
                self.assertEqual(form[1]['attrs']['method'], 'get')
                # Submit the actual jump input, not a fabricated context URL.
                jumped, _ = self.get_context(url=request.path + '?' + urlencode({'page': number_input['value']}))
                self.assert_page(jumped, expected[100:120], 6, total)

    def test_full_template_places_pagination_after_and_outside_results(self):
        self.create_items(41)
        for number in (1, 2, 3):
            with self.subTest(page=number):
                context, request = self.get_context({'page': str(number)})
                html, dom = self.render(context, request)
                nav_index, nav = dom.find('nav', css_class='knowledge-pagination')[0]
                self.assertEqual(len(dom.find('nav', css_class='knowledge-pagination')), 1)
                results_index, results = dom.find(css_class='knowledge-results')[0]
                self.assertNotIn(results_index, nav['ancestors'])
                self.assertEqual(nav['ancestors'], results['ancestors'])
                self.assertGreater(nav_index, max(
                    [results_index] + [index for index, _ in dom.find(within=results_index)],
                ))
                panel, _ = self.render(context, request, panel_only=True)
                self.assertIn(panel.strip(), html)
                self.assertEqual(nav['attrs']['aria-label'], '一覧のページ移動')
                self.assertIn('41件中', panel)
                self.assertIn(f'{context["page_obj"].start_index()}–{context["page_obj"].end_index()}件', panel)
                links = [element['attrs'] for _, element in dom.find('a', within=nav_index)]
                current, = [attrs for attrs in links if attrs.get('aria-current') == 'page']
                self.assert_url(current['href'], request, number)
                labels = [attrs.get('aria-label') for attrs in links]
                self.assertEqual('前のページ' in labels, number > 1)
                self.assertEqual('次のページ' in labels, number < 3)
                for attrs in links:
                    destination = int(parse_qs(urlsplit(attrs['href']).query)['page'][0])
                    self.assert_url(attrs['href'], request, destination)
                    linked, _ = self.get_context(url=attrs['href'])
                    expected = self.ordered_ids()[(destination - 1) * 20:destination * 20]
                    self.assert_page(linked, expected, destination, 41)

    def test_filters_repeated_values_and_escaping_survive_links_and_jump_form(self):
        query = 'keyword & 日本語 "<script>alert(1)</script>'
        category = '親 & "分類/子 <分類>'
        FAQCategory.objects.get_or_create(
            parent_name='親 & "分類', middle_name='', child_name='子 <分類>',
        )
        items = self.create_items(41, title=query, category=category)
        created_at = timezone.make_aware(datetime(2026, 1, 15, 12))
        self.model.objects.filter(pk__in=[item.pk for item in items]).update(created_at=created_at)
        self.favorite_model.objects.bulk_create([
            self.favorite_model(user=self.user, **{self.favorite_field: item}) for item in items
        ])
        # Each decoy fails exactly one of the selected filters.
        other_user = get_user_model().objects.create_user(username='other-pagination-creator')
        for overrides in (
            {'title': 'other'}, {'category': '別/子'}, {'target_os': 'Windows PC 11'},
            {'is_approved': False, 'remand_reason': '要修正'}, {'created_by': other_user},
        ):
            values = {'title': query, 'category': category}
            values.update(overrides)
            decoy, = self.create_items(1, **values)
            self.model.objects.filter(pk=decoy.pk).update(created_at=created_at)
            self.favorite_model.objects.create(user=self.user, **{self.favorite_field: decoy})
        not_favorite, wrong_month = self.create_items(2, title=query, category=category)
        self.model.objects.filter(pk=not_favorite.pk).update(created_at=created_at)
        self.model.objects.filter(pk=wrong_month.pk).update(
            created_at=timezone.make_aware(datetime(2026, 2, 15, 12)),
        )
        self.favorite_model.objects.create(user=self.user, **{self.favorite_field: wrong_month})
        hostile_name = 'extra"><img src=x onerror=alert(1)>'
        params = {
            'q': query, 'category': category, 'parent_category': '親 & "分類',
            'filter_approval': ['registered', 'approved', 'approved'],
            'target_os': 'Ubuntu', 'favorite_only': '1',
            'filter_created_month': '2026-01', 'filter_creator': str(self.user.pk),
            'extra': ['a&b', 'c d', ''], hostile_name: ['"<script>alert(2)</script>'],
            'page': '2', 'open_code': items[0].management_code, 'open_id': str(items[0].pk),
        }
        context, request = self.get_context(params)
        original = request.GET.copy()
        expected = self.ordered_ids(items)
        self.assert_page(context, expected[20:40], 2, 41)
        self.assertEqual(
            {option['name']: option['count'] for option in context['target_os_options']},
            {'Ubuntu': 41, 'Windows PC': 1},
        )
        retained = [
            (key, value) for key, values in original.lists()
            if key not in {'page', 'open_code', 'open_id'} for value in values
        ]
        self.assertEqual(context['pagination_parameters'], retained)
        self.assertEqual(context['filter_approval'], ['registered', 'approved'])
        html, dom = self.render(context, request)
        nav_index, _ = dom.find('nav', css_class='knowledge-pagination')[0]
        form_index, form = dom.find('form', within=nav_index)[0]
        self.assertEqual(form['attrs']['method'], 'get')
        hidden = [
            (element['attrs']['name'], element['attrs']['value'])
            for _, element in dom.find('input', within=form_index)
            if element['attrs'].get('type') == 'hidden'
        ]
        self.assertEqual(Counter(hidden), Counter(retained))
        self.assertEqual([value for name, value in hidden if name == 'filter_approval'], params['filter_approval'])
        panel, panel_dom = self.render(context, request, panel_only=True)
        self.assertNotIn('<script>', panel)
        self.assertNotIn('<img', panel)
        self.assertEqual(panel_dom.find('script'), [])
        self.assertEqual(panel_dom.find('img'), [])
        self.assertIn('&lt;script&gt;', panel)
        self.assertIn('&quot;', panel)
        self.assertIn('&amp;', panel)
        self.assertIn(panel.strip(), html)
        for _, element in dom.find('a', within=nav_index):
            url = element['attrs']['href']
            number = int(parse_qs(urlsplit(url).query)['page'][0])
            self.assert_url(url, request, number)
            linked, _ = self.get_context(url=url)
            self.assert_page(linked, expected[(number - 1) * 20:number * 20], number, 41)
        for number in (1, 3):
            jumped, _ = self.get_context(url=request.path + '?' + urlencode(hidden + [('page', number)]))
            self.assert_page(jumped, expected[(number - 1) * 20:number * 20], number, 41)
        self.assertEqual(request.GET, original)

    def test_os_counts_cover_all_filtered_pages_not_only_the_current_page(self):
        self.create_items(21, target_os='Ubuntu 24.04, Ubuntu 22.04 以前, Windows PC 11')
        self.create_items(20, target_os='Windows Server 2022')
        for number in (1, 2, 3):
            with self.subTest(page=number):
                context, _ = self.get_context({'page': str(number)})
                self.assertEqual(
                    {option['name']: option['count'] for option in context['target_os_options']},
                    {'Ubuntu': 21, 'Windows PC': 21, 'Windows Server': 20},
                )
        for number in (1, 2):
            with self.subTest(filtered_page=number):
                context, _ = self.get_context({'target_os': 'Ubuntu', 'page': str(number)})
                self.assertEqual(context['paginator'].count, 21)
                self.assertEqual(len(context[self.context_name]), 20 if number == 1 else 1)
                self.assertEqual(
                    {option['name']: option['count'] for option in context['target_os_options']},
                    {'Ubuntu': 21, 'Windows PC': 21, 'Windows Server': 20},
                )

    def test_customer_visibility_excludes_hidden_rows_from_pages_open_targets_and_counts(self):
        customer = get_user_model().objects.create_user(username='pagination-customer')
        group, _ = Group.objects.get_or_create(name=views.CUSTOMER_GROUP_NAME)
        customer.groups.add(group)
        visible = self.create_items(21)
        hidden = []
        for overrides in (
            {'visible_to_customer': False},
            {'visible_to_customer': False, 'visible_to_systena': False},
            {'is_published': False}, {'is_approved': False},
            {'expires_on': timezone.localdate() - timedelta(days=1)},
            {'category': '非表示/子'},
        ):
            hidden.extend(self.create_items(1, title='SECRET unpublished item', target_os='macOS 15', **overrides))
        expected = self.ordered_ids(visible)
        with patch.object(views, 'hidden_parent_category_names_for_customer', return_value={'非表示'}):
            for number in (1, 2):
                context, request = self.get_context({'page': str(number)}, user=customer)
                self.assert_page(context, expected[(number - 1) * 20:number * 20], number, 21)
                self.assertEqual(context['all_count'], 21)
                self.assertEqual(
                    {option['name']: option['count'] for option in context['target_os_options']},
                    {'Ubuntu': 21},
                )
                html, _ = self.render(context, request)
                self.assertNotIn('SECRET unpublished item', html)
                panel, _ = self.render(context, request, panel_only=True)
                self.assertIn('21件中', panel)
                for item in hidden:
                    self.assertNotIn(item.management_code, html)
            for item in hidden:
                for key, value in (('open_code', item.management_code), ('open_id', str(item.pk))):
                    context, _ = self.get_context({key: value}, user=customer)
                    self.assert_page(context, expected[:20], 1, 21)
                    self.assertIsNone(context[self.open_context_name])
            context, _ = self.get_context({'target_os': 'macOS'}, user=customer)
            self.assert_page(context, [], 1, 0)
            self.assertEqual(
                {option['name']: option['count'] for option in context['target_os_options']},
                {'Ubuntu': 21},
            )


class FAQListPaginationTests(KnowledgeListPaginationTestsMixin, TestCase):
    model = KnowledgeArticle
    view_class = views.ArticleListView
    url_name = 'article_list'
    context_name = 'articles'
    open_context_name = 'open_article_id'
    code_prefix = 'FQ'
    favorite_model = ArticleFavorite
    favorite_field = 'article'


class TipsListPaginationTests(KnowledgeListPaginationTestsMixin, TestCase):
    model = TipsArticle
    view_class = views.TipsListView
    url_name = 'tip_list'
    context_name = 'tips_list'
    open_context_name = 'open_tip_id'
    code_prefix = 'TP'
    favorite_model = TipsFavorite
    favorite_field = 'tip'