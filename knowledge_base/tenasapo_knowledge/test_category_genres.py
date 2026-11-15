"""Offline regressions: run with --settings=test_knowledge_tabs_settings.

That settings module never imports dotenv/config.settings and uses only an
in-memory database. Network attempts fail rather than silently being mocked.
"""

from datetime import timedelta
from html.parser import HTMLParser
import json
import re
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from . import views
from .category_genres import (
    CATEGORY_GENRES, category_genre_keys, category_matches_genre, genre_for_parent,
    normalize_genre_key,
)
from .models import FAQCategory, KnowledgeArticle, TipsArticle, ArticleFavorite, TipsFavorite
from .test_knowledge_tabs import Links, OfflineChrome


EXPECTED = {
    'client': ('PC', 'ハードウェア', '周辺機器'),
    'infrastructure': ('サーバー', 'ネットワーク', 'Azure', 'クラウドインフラ'),
    'applications': ('Microsoft 365', 'ソフトウェア', 'グループウェア'),
    'operations': ('移行・導入', '運用・保守'),
}


class NoNetwork:
    def setUp(self):
        super().setUp()
        for target in ('socket.socket.connect', 'socket.socket.connect_ex',
                       'urllib.request.urlopen', 'requests.sessions.Session.request'):
            guard = self.enterContext(patch(target, side_effect=AssertionError('network forbidden')))
            self.addCleanup(guard.assert_not_called)


class Tags(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.ancestors = []
        self.stack = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        self.ancestors.append(list(self.stack))
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                       'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


class GenreMappingTests(NoNetwork, SimpleTestCase):
    def test_fixed_order_all_twelve_names_and_labels(self):
        self.assertEqual([g['key'] for g in CATEGORY_GENRES], list(EXPECTED))
        self.assertEqual([g['name'] for g in CATEGORY_GENRES], [
            'クライアント環境', 'システム基盤', '業務アプリ・サービス', '共通業務・運用',
        ])
        self.assertEqual(sum(len(names) for names in EXPECTED.values()), 12)
        for key, names in EXPECTED.items():
            for name in names:
                self.assertEqual(genre_for_parent(name)['key'], key)
                self.assertEqual(category_genre_keys(name + '/中/子'), {key})

    def test_exact_names_fallback_duplicates_and_valid_slugs(self):
        for name in ('PC拡張', 'AzureAD', 'Microsoft 365拡張', '未知', '', None):
            self.assertEqual(genre_for_parent(name)['key'], 'other')
        self.assertEqual(category_genre_keys(' PC/設定,PC/設定,サーバー/設定 '),
                         {'client', 'infrastructure'})
        self.assertEqual(category_genre_keys(''), {'other'})
        self.assertFalse(category_matches_genre('PC拡張/設定', 'client'))
        for invalid in ('CLIENT', 'client ', 'unknown', '<script>', 'all'):
            self.assertEqual(normalize_genre_key(invalid), '')
            self.assertTrue(category_matches_genre('PC/設定', invalid))

    def test_isolated_settings(self):
        # Django changes :memory: into a shared in-memory URI during tests.
        name = settings.DATABASES['default']['NAME']
        self.assertTrue(name == ':memory:' or (name.startswith('file:memorydb_') and 'mode=memory' in name))
        self.assertFalse(settings.GOOGLE_CHAT_SCHEDULED_SEND_ENABLED)
        self.assertFalse(settings.GOOGLE_CHAT_REPORT_PDF_ENABLED)


class GenreBuilderTests(NoNetwork, TestCase):
    def test_additive_fields_keep_all_ids_names_and_full_names(self):
        staff = get_user_model().objects.create_user(username='genre-builder', is_staff=True)
        categories = [FAQCategory.objects.create(parent_name=name, middle_name='中', child_name='子')
                      for names in EXPECTED.values() for name in names]
        leaf = FAQCategory.objects.create(parent_name='PC', middle_name='中', child_name='')
        unknown = FAQCategory.objects.create(parent_name='将来の親', middle_name='設定', child_name='')
        before = list(FAQCategory.objects.values_list('pk', 'parent_name', 'middle_name', 'child_name'))
        # Include seed categories installed by migrations in the memory DB.
        full_names = [category.full_name for category in FAQCategory.objects.all()]
        browser = views.FAQCategoryCreateView.category_browser_data()
        groups = views.build_parent_category_groups(user=staff)
        for source in (browser, groups):
            self.assertEqual(len(source), 13)
            self.assertEqual(len({parent['name'] for parent in source}), 13)
            for parent in source:
                self.assertEqual(parent['genre_key'], genre_for_parent(parent['name'])['key'])
                self.assertEqual(parent['genre_name'], genre_for_parent(parent['name'])['name'])
        ids = []
        for parent in browser:
            ids.extend(child['id'] for child in parent['direct_children'])
            for middle in parent['middles']:
                ids.extend(child['id'] for child in middle['children'])
        self.assertCountEqual(ids, [row[0] for row in before])
        names = []
        for parent in groups:
            names.extend(child['full_name'] for child in parent['children'])
            for middle in parent['middle_groups']:
                names.extend(middle['full_names'])
        self.assertCountEqual(names, full_names)
        pc = next(parent for parent in browser if parent['name'] == 'PC')
        self.assertEqual(pc['direct_children'], [{'id': leaf.pk, 'name': '中'}])
        self.assertEqual(before, list(FAQCategory.objects.values_list(
            'pk', 'parent_name', 'middle_name', 'child_name')))


class GenreListTestsMixin(NoNetwork, OfflineChrome):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username='genre-customer')
        self.user.groups.add(Group.objects.get_or_create(name=views.CUSTOMER_GROUP_NAME)[0])
        self.admin = get_user_model().objects.create_user(username='genre-admin', is_staff=True)
        self.demo = get_user_model().objects.create_user(username='genre-demo')
        self.demo.groups.add(Group.objects.get_or_create(name='demo')[0])
        for names in EXPECTED.values():
            for name in names:
                FAQCategory.objects.create(parent_name=name, middle_name='設定', child_name='')
        self.pc = self.item('PC/設定,PC/設定,ハードウェア/設定')
        self.cross = self.item('PC/設定,サーバー/設定')
        self.server = self.item('サーバー/設定')
        self.app = self.item('Microsoft 365/設定')
        self.ops = self.item('移行・導入/設定')

    def item(self, category, **overrides):
        values = {'title': 'genre keyword', 'body': 'PRIVATE_GENRE_BODY', 'category': category,
                  'target_os': 'Windows PC 11', 'expires_on': None, 'visible_to_demo': True}
        values.update(overrides)
        return self.model.objects.create(**values)

    def page(self, params=None, user=None):
        request = RequestFactory().get(reverse(self.url_name), params or {})
        request.user = user or self.user
        request.session = {}
        response = self.view_class.as_view()(request)
        response.render()
        self.assertEqual(response.status_code, 200)
        return response.context_data, response.content.decode(), request

    def ids(self, context):
        return [item.pk for item in context[self.context_name]]

    def test_genre_distinct_counts_and_ordered_parents(self):
        context, source, _ = self.page({'genre': 'client'})
        self.assertCountEqual(self.ids(context), [self.pc.pk, self.cross.pk])
        self.assertEqual(context['page_obj'].paginator.count, 2)
        self.assertEqual({o['key']: o['count'] for o in context['genre_options']},
                         {'client': 2, 'infrastructure': 2, 'applications': 1, 'operations': 1})
        self.assertEqual(context['all_count'], 2)
        self.assertEqual(context['genre_all_count'], 5)
        self.assertEqual({parent['name'] for parent in context['parent_categories']}, set(EXPECTED['client']))
        tags = Tags(source).tags
        self.assertEqual(sum(tag == 'a' and attrs.get('data-genre') == 'client' for tag, attrs in tags), 1)
        self.assertIn(('input', {'type': 'hidden', 'name': 'genre', 'value': 'client'}), tags)
        all_context, all_source, _ = self.page()
        self.assertEqual(all_context['all_count'], 5)
        for page_context, rendered, label in (
                (context, source, 'このジャンルのすべて'), (all_context, all_source, 'すべて')):
            category_link = re.search(r'aria-label="カテゴリ項目">\s*(<a\b.*?</a>)', rendered, re.S)
            self.assertIsNotNone(category_link)
            links = Links(category_link.group(1)).anchors
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0]['href'], page_context['category_all_url'])
            self.assertEqual(links[0]['text'].split(), [label, f"{page_context['all_count']}件"])
        keys = [parent['genre_key'] for parent in all_context['parent_categories']]
        self.assertEqual(keys, sorted(keys, key=list(EXPECTED).index))
        self.assertEqual(len({p['name'] for p in all_context['parent_categories']}), 12)

    def test_other_needed_for_unknown_articles_or_registered_parents(self):
        context, _, _ = self.page()
        self.assertNotIn('other', [o['key'] for o in context['genre_options']])
        unknown = self.item('PC拡張/設定')
        blank = self.item('')
        context, source, _ = self.page({'genre': 'other'})
        self.assertCountEqual(self.ids(context), [unknown.pk, blank.pk])
        self.assertEqual(context['all_count'], 2)
        self.assertIn('data-genre="other"', source)
        self.model.objects.filter(pk__in=[unknown.pk, blank.pk]).delete()
        FAQCategory.objects.create(parent_name='未来', middle_name='設定', child_name='')
        context, _, _ = self.page()
        self.assertEqual(context['genre_options'][-1]['key'], 'other')
        self.assertEqual(context['genre_options'][-1]['count'], 0)
        self.assertEqual(context['parent_categories'][-1]['name'], '未来')

    def test_exact_filter_no_prefix_collisions_or_data_rewrites(self):
        unknown = self.item('PC拡張/設定')
        before = list(self.model.objects.values_list('pk', 'category', 'title', 'body'))
        context, _, _ = self.page({'genre': 'client'})
        self.assertNotIn(unknown.pk, self.ids(context))
        for genre in ('infrastructure', 'applications', 'operations', 'other'):
            self.page({'genre': genre})
        self.assertEqual(before, list(self.model.objects.values_list('pk', 'category', 'title', 'body')))

    def test_legacy_parent_child_urls_and_invalid_genre_keep_behavior(self):
        for params in ({'parent_category': 'PC'}, {'category': 'PC/設定'}):
            context, _, _ = self.page(params)
            self.assertCountEqual(self.ids(context), [self.pc.pk, self.cross.pk])
            self.assertEqual(context['selected_genre'], '')
            self.assertEqual(context['selected_parent_category'], 'PC')
        context, _, request = self.page({'genre': 'client-prefix', 'category': 'PC/設定'})
        self.assertCountEqual(self.ids(context), [self.pc.pk, self.cross.pk])
        self.assertNotIn('genre', request.GET)

    def test_mismatched_parent_or_child_clears_stale_state(self):
        for old in ({'parent_category': 'PC'}, {'category': 'PC/設定'}):
            context, _, request = self.page({'genre': 'infrastructure', 'page': '9', **old})
            self.assertCountEqual(self.ids(context), [self.server.pk, self.cross.pk])
            self.assertEqual(context['selected_parent_category'], '')
            self.assertEqual(context['selected_category'], '')
            self.assertNotIn('page', request.GET)
        context, _, _ = self.page({'genre': 'client', 'parent_category': 'PC', 'category': 'PC/設定'})
        self.assertCountEqual(self.ids(context), [self.pc.pk, self.cross.pk])

    def test_links_preserve_legacy_filters_reset_only_category_and_page(self):
        params = {'genre': 'client', 'parent_category': 'PC', 'category': 'PC/設定', 'page': '2',
                  'q': 'keyword', 'target_os': 'Windows PC', 'favorite_only': '1',
                  'filter_approval': ['approved', 'registered'], 'order': 'oldest', 'page_size': '20'}
        context, _, _ = self.page(params, user=self.admin)
        for option in [*context['genre_options'], {'url': context['genre_all_url']}]:
            query = parse_qs(urlsplit(option['url']).query)
            for key in ('parent_category', 'category', 'page'):
                self.assertNotIn(key, query)
            for key in ('q', 'target_os', 'favorite_only', 'filter_approval', 'order', 'page_size'):
                self.assertEqual(query[key], params[key] if isinstance(params[key], list) else [params[key]])
        for link in (context['category_all_url'], context['favorite_toggle_url']):
            self.assertEqual(parse_qs(urlsplit(link).query)['genre'], ['client'])
        pc = next(p for p in context['parent_categories'] if p['name'] == 'PC')
        self.assertEqual(parse_qs(urlsplit(pc['children'][0]['url']).query)['category'], ['PC/設定'])
        self.assertEqual(parse_qs(urlsplit(pc['url']).query)['genre'], ['client'])

    def test_real_render_has_one_labeled_sidebar_dropdown_before_parents_for_all_roles(self):
        prefix = 'faq' if self.model is KnowledgeArticle else 'tips'
        panel_id = prefix + 'FilterPanel'
        for user in (self.admin, self.user, self.demo):
            for genre in ('', 'invalid', 'client'):
                with self.subTest(user=user.username, genre=genre):
                    context, source, _ = self.page({'genre': genre}, user=user)
                    parsed = Tags(source)
                    indexes = [i for i, (tag, attrs) in enumerate(parsed.tags)
                               if tag == 'select' and 'data-category-genre-navigation' in attrs]
                    self.assertEqual(len(indexes), 1)
                    index = indexes[0]
                    navigation = parsed.tags[index][1]
                    label_id = prefix + 'CategoryGenreNavigation'
                    self.assertEqual(navigation['id'], label_id)
                    self.assertEqual(sum(attrs.get('id') == label_id for _, attrs in parsed.tags), 1)
                    labels = [attrs for tag, attrs in parsed.tags
                              if tag == 'label' and attrs.get('for') == label_id]
                    self.assertEqual(labels, [{'class': 'form-label mb-1', 'for': label_id}])
                    self.assertInHTML(f'<label class="form-label mb-1" for="{label_id}">ジャンル</label>', source)
                    self.assertNotIn('name', navigation)
                    ancestors = parsed.ancestors[index]
                    self.assertTrue(any('category-sidebar' in attrs.get('class', '').split()
                                        for _, attrs in ancestors))
                    self.assertTrue(any(tag == 'aside' for tag, _ in ancestors))
                    self.assertFalse(any(tag == 'form' or attrs.get('id') == panel_id
                                         for tag, attrs in ancestors))
                    title_index = next(i for i, (_, attrs) in enumerate(parsed.tags)
                                       if attrs.get('class') == 'category-sidebar-title')
                    parents_index = next(i for i, (_, attrs) in enumerate(parsed.tags)
                                         if attrs.get('aria-label') == 'カテゴリ項目')
                    self.assertLess(title_index, index)
                    self.assertLess(index, parents_index)
                    self.assertNotIn('category-genre-tabs', source)
                    self.assertIn('.category-genre-heading {', source)
                    panel = next(attrs for _, attrs in parsed.tags if attrs.get('id') == panel_id)
                    self.assertNotIn('show', panel['class'].split())
                    toggle = next(attrs for _, attrs in parsed.tags
                                  if attrs.get('aria-controls') == panel_id)
                    self.assertEqual(toggle['aria-expanded'], 'false')
                    self.assertNotIn('is-selected', toggle['class'].split())
                    self.assertEqual(context['active_filter_count'], 0)
                    hidden = [attrs for tag, attrs in parsed.tags if tag == 'input' and attrs.get('name') == 'genre']
                    self.assertEqual(hidden, [{'type': 'hidden', 'name': 'genre', 'value': 'client'}]
                                     if genre == 'client' else [])
                    self.assertEqual('name="filter_creator"' in source, user == self.admin)
                    self.assertEqual('name="filter_approval"' in source, user == self.admin)
                    self.assertEqual('name="filter_created_month"' in source, user == self.admin)
                    options = [attrs for tag, attrs in parsed.tags if tag == 'option' and 'data-genre' in attrs]
                    self.assertEqual([o['data-genre'] for o in options],
                                     ['', *[o['key'] for o in context['genre_options']]])
                    selected = [o for o in options if 'selected' in o]
                    self.assertEqual(len(selected), 1)
                    self.assertEqual(selected[0]['data-genre'], 'client' if genre == 'client' else '')
                    links = [attrs for tag, attrs in parsed.tags if tag == 'a' and 'data-genre' in attrs]
                    self.assertEqual([a['data-genre'] for a in links],
                                     [o['data-genre'] for o in options])
                    self.assertEqual([a['data-genre'] for a in links if a.get('aria-current') == 'page'],
                                     [selected[0]['data-genre']])
                    self.assertNotIn('category-genre-choices', source)

    def test_selected_genre_does_not_change_combined_filter_badge(self):
        params = {'genre': 'client', 'filter_approval': ['registered', 'approved'],
                  'filter_creator': str(self.admin.pk),
                  'filter_created_month': timezone.localdate().strftime('%Y-%m')}
        context, source, _ = self.page(params, user=self.admin)
        self.assertEqual(context['active_filter_count'], 3)
        self.assertInHTML('<span class="badge rounded-pill bg-danger ms-1" style="font-size:0.65rem;">3</span>', source)
        panel_id = ('faq' if self.model is KnowledgeArticle else 'tips') + 'FilterPanel'
        panel = next(attrs for _, attrs in Tags(source).tags if attrs.get('id') == panel_id)
        self.assertIn('show', panel['class'].split())
        params.pop('genre')
        context, _, _ = self.page(params, user=self.admin)
        self.assertEqual(context['active_filter_count'], 3)
        context, _, _ = self.page({**params, 'genre': 'client'}, user=self.user)
        self.assertEqual(context['active_filter_count'], 0)

    def test_rendered_genre_options_and_fallback_preserve_filters_and_reset_stale_navigation(self):
        params = {'genre': 'client', 'parent_category': 'PC', 'category': 'PC/設定',
                  'page': '2', 'open_code': 'stale', 'open_id': '999',
                  'q': 'keyword & 日本語', 'target_os': 'Windows PC', 'favorite_only': '1',
                  'filter_approval': ['registered', 'approved'], 'order': 'oldest', 'page_size': '20'}
        context, source, _ = self.page(params, user=self.admin)
        parsed = Tags(source)
        options = [attrs for tag, attrs in parsed.tags if tag == 'option' and 'data-genre' in attrs]
        links = [attrs for tag, attrs in parsed.tags if tag == 'a' and 'data-genre' in attrs]
        expected_urls = [('', context['genre_all_url']),
                         *[(o['key'], o['url']) for o in context['genre_options']]]
        self.assertEqual([(o['data-genre'], o['value']) for o in options], expected_urls)
        self.assertEqual([(a['data-genre'], a['href']) for a in links], expected_urls)
        self.assertEqual([o['data-genre'] for o in options if 'selected' in o], ['client'])
        for index, (tag, attrs) in enumerate(parsed.tags):
            if tag == 'option' and 'data-genre' in attrs:
                self.assertTrue(any(tag == 'select' and 'data-category-genre-navigation' in parent
                                    for tag, parent in parsed.ancestors[index]))
            if tag == 'a' and 'data-genre' in attrs:
                self.assertTrue(any(tag == 'nav' and parent.get('class') == 'category-genre-fallback'
                                    for tag, parent in parsed.ancestors[index]))
                self.assertTrue(any(tag == 'noscript' for tag, _ in parsed.ancestors[index]))
                self.assertFalse(any(tag == 'form' for tag, _ in parsed.ancestors[index]))
                self.assertFalse(any(key.startswith('on') for key in attrs))
        retained = {key: value if isinstance(value, list) else [value]
                    for key, value in params.items()
                    if key not in {'genre', 'parent_category', 'category', 'page', 'open_code', 'open_id'}}
        for choices, url_attr in ((options, 'value'), (links, 'href')):
            for choice in choices:
                expected = {**retained, **({'genre': [choice['data-genre']]} if choice['data-genre'] else {})}
                self.assertEqual(parse_qs(urlsplit(choice[url_attr]).query), expected)
                self.assertEqual(urlsplit(choice[url_attr]).path, reverse(self.url_name))

    def test_filter_clear_preserves_genre_and_category_removes_only_panel_filters(self):
        params = {'genre': 'client', 'parent_category': 'PC', 'category': 'PC/設定',
                  'q': 'keyword', 'target_os': 'Windows PC', 'favorite_only': '1',
                  'filter_approval': ['registered', 'approved'], 'filter_creator': str(self.admin.pk),
                  'filter_created_month': timezone.localdate().strftime('%Y-%m'),
                  'order': 'oldest', 'page_size': '20', 'page': '2', 'open_id': '999'}
        for user in (self.admin, self.user):
            with self.subTest(user=user.username):
                context, source, _ = self.page(params, user=user)
                expected = {key: [value] for key, value in params.items()
                            if not key.startswith('filter_') and key not in {'page', 'open_id'}}
                self.assertEqual(parse_qs(urlsplit(context['filter_clear_url']).query), expected)
                self.assertTrue(any(tag == 'a' and attrs.get('href') == context['filter_clear_url']
                                    for tag, attrs in Tags(source).tags))
                cleared, _, _ = self.page(expected, user=user)
                self.assertEqual(cleared['selected_genre'], 'client')
                self.assertEqual(cleared['selected_parent_category'], 'PC')
                self.assertEqual(cleared['selected_category'], 'PC/設定')
                self.assertEqual(cleared['active_filter_count'], 0)

    def test_search_favorite_os_approval_and_pagination_compose(self):
        self.favorite_model.objects.create(user=self.admin, **{self.favorite_field: self.pc})
        self.favorite_model.objects.create(user=self.admin, **{self.favorite_field: self.server})
        context, _, _ = self.page({'genre': 'client', 'q': 'keyword', 'favorite_only': '1',
                                  'target_os': 'Windows PC', 'filter_approval': 'approved'}, user=self.admin)
        self.assertEqual(self.ids(context), [self.pc.pk])
        self.assertEqual(context['target_os_options'][0]['count'], 1)
        for i in range(23):
            self.item('PC/設定', title=f'pagination {i}')
        context, _, _ = self.page({'genre': 'client', 'q': 'pagination'})
        self.assertEqual(context['page_obj'].paginator.count, 23)
        query = parse_qs(urlsplit(context['pagination_next_url']).query)
        self.assertEqual(query, {'genre': ['client'], 'q': ['pagination'], 'page': ['2']})
        context, _, _ = self.page(query)
        self.assertEqual(len(self.ids(context)), 3)

    def test_offline_browser_dropdown_focus_narrow_layout_and_change_navigation(self):
        prefix = 'faq' if self.model is KnowledgeArticle else 'tips'
        context, rendered, _ = self.page({
            'genre': 'client', 'q': '日本語 & " </script> onfocus=evil()',
            'parent_category': 'PC', 'category': 'PC/設定', 'page': '2',
            'filter_approval': ['registered', 'approved'], 'order': 'oldest',
        }, user=self.admin)
        # Keep the real generated query strings, but navigate within the local
        # fixture using hashes. No HTTP server, CDN or application URL is opened.
        fixture_context = {
            **context, 'genre_id_prefix': prefix,
            'genre_all_url': '#' + context['genre_all_url'],
            'genre_options': [{**option, 'url': '#' + option['url']}
                              for option in context['genre_options']],
        }
        selector = render_to_string('includes/category_genre_selector.html', fixture_context)
        parsed = Tags(selector)
        options = [attrs for tag, attrs in parsed.tags if tag == 'option' and 'data-genre' in attrs]
        self.assertEqual(options[0]['value'], '#' + context['genre_all_url'])
        self.assertEqual([o['value'] for o in options[1:]],
                         ['#' + o['url'] for o in context['genre_options']])
        for name in ('クライアント環境', 'システム基盤', '業務アプリ・サービス', '共通業務・運用'):
            self.assertIn(name, selector)
        self.assertEqual(sum(tag == 'select' for tag, _ in parsed.tags), 1)
        self.assertEqual(sum(tag == 'noscript' for tag, _ in parsed.tags), 1)
        self.assertIn('window.location.assign(select.value)', selector)
        self.assertFalse(any(key.startswith('on') for _, attrs in parsed.tags for key in attrs))
        base_css = re.search(r'<style>.*?</style>', rendered, re.S).group(0)
        source = '''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none';
style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'">
<style>* { box-sizing: border-box; } html { font-size: ROOT_FONTpx; }</style>
''' + base_css + '''</head><body>
<div class="knowledge-split-row"><div class="category-sidebar category-sidebar-with-genre"
style="width:180px;height:SIDEBAR_HEIGHTpx">
<div class="category-sidebar-title">カテゴリ</div>''' + selector + '''
<div class="list-group"><div class="category-genre-heading">クライアント環境</div>
<a class="category-sidebar-link category-sidebar-toggle">PC</a>
<div class="collapse"><div class="category-child-list">設定</div></div>
<div style="height:500px">カテゴリ一覧</div></div></div></div>
<pre id="result">PENDING</pre><script>
document.addEventListener('DOMContentLoaded', async () => {
    const assert = (ok, message) => { if (!ok) throw new Error(message); };
    const near = (actual, expected, message) => assert(Math.abs(actual - expected) < .6, message);
    const select = document.querySelector('[data-category-genre-navigation]');
    const options = [...select.options];
    const navigate = option => new Promise(resolve => {
        window.addEventListener('hashchange', resolve, {once:true});
        select.value = option.value;
        select.dispatchEvent(new Event('change', {bubbles:true}));
    });
    try {
        assert(select instanceof HTMLSelectElement && select.labels.length === 1,
               'real labeled native select');
        assert(select.labels[0].htmlFor === select.id && select.labels[0].textContent === 'ジャンル',
               'accessible dropdown label');
        assert(!select.hasAttribute('name') && !select.closest('form'), 'navigation outside search form');
        const selected = options.filter(option => option.selected);
        assert(selected.length === 1 && selected[0].dataset.genre === 'client', 'current genre');
        const sidebar = select.closest('.category-sidebar');
        const list = sidebar.querySelector('.list-group');
        const selector = sidebar.querySelector('.category-genre-selector');
        const title = sidebar.querySelector('.category-sidebar-title');
        assert(getComputedStyle(sidebar).gridTemplateRows.split(' ').length === 3, 'three sidebar rows');
        const remainingHeight = sidebar.clientHeight - title.getBoundingClientRect().height
            - selector.getBoundingClientRect().height;
        near(list.getBoundingClientRect().height, remainingHeight, 'category list fills remaining row');
        assert(selector.scrollHeight <= selector.clientHeight, 'dropdown row needs no vertical scrolling');
        assert(select.tabIndex === 0, 'native keyboard-accessible select');
        select.focus();
        assert(document.activeElement === select, 'dropdown receives keyboard focus');
        assert(select.dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowDown', cancelable:true})),
               'native keyboard activation not intercepted');
        const bounds = select.getBoundingClientRect(), parent = sidebar.getBoundingClientRect();
        assert(bounds.width > 0 && bounds.height > 0, 'dropdown has visible layout');
        assert(bounds.left >= parent.left && bounds.right <= parent.right, 'fits 180px sidebar');
        for (const element of [sidebar, selector, select, list]) {
            assert(element.scrollWidth <= element.clientWidth, 'no horizontal sidebar/dropdown/list overflow');
        }
        assert(list.clientHeight > 0 && list.scrollHeight > list.clientHeight, 'parent list remains scrollable');
        assert(['auto', 'scroll'].includes(getComputedStyle(list).overflowY), 'category list allows vertical scrolling');
        list.scrollTop = list.scrollHeight;
        assert(list.scrollTop > 0, 'category list actually scrolls');
        list.scrollTop = 0;
        assert(getComputedStyle(sidebar.querySelector('.category-genre-heading')).fontWeight === '700', 'genre heading CSS retained');
        const rootSize = parseFloat(getComputedStyle(document.documentElement).fontSize);
        near(parseFloat(getComputedStyle(title).fontSize), rootSize * .85, 'title typography retained');
        const parentLink = sidebar.querySelector('.category-sidebar-toggle');
        const children = sidebar.querySelector('.category-child-list');
        near(parseFloat(getComputedStyle(parentLink).fontSize), rootSize * .85, 'parent typography retained');
        near(parseFloat(getComputedStyle(children).fontSize), rootSize * .85, 'child typography retained');
        near(parseFloat(getComputedStyle(parentLink).paddingLeft), rootSize * (.8 + .85), 'parent indentation retained');
        near(parseFloat(getComputedStyle(children).paddingLeft), rootSize * (.8 + 2 * .85 - .45), 'child indentation retained');
        for (const index of [2, 0]) {
            const expected = new URL(options[index].value, location.href).hash;
            await navigate(options[index]);
            assert(location.hash === expected, 'change navigates to selected option URL');
            assert(select.selectedOptions[0] === options[index], 'native selected option updated');
            const params = new URLSearchParams(location.hash.split('?')[1]);
            assert(params.get('q') === QUERY, 'escaped query preserved through navigation');
            assert(params.getAll('filter_approval').length === 2, 'repeated filters preserved');
            assert(!params.has('parent_category') && !params.has('category') && !params.has('page'), 'stale navigation cleared');
            assert(params.get('genre') === (index === 0 ? null : 'infrastructure'), 'genre/all navigation');
        }
        document.getElementById('result').textContent = 'PASS: sidebar genre dropdown';
    } catch (error) {
        document.getElementById('result').textContent = 'FAIL: ' + error.message;
    }
});</script></body></html>'''.replace('QUERY', json.dumps(context['query']).replace('<', '\\u003c'))
        for height in (240, 350):
            for root_font in (16, 20):
                with self.subTest(height=height, root_font=root_font):
                    fixture = source.replace('SIDEBAR_HEIGHT', str(height)).replace('ROOT_FONT', str(root_font))
                    self.run_offline_page(fixture, expected='PASS: sidebar genre dropdown')

    def test_offline_browser_noscript_fallback_links_preserve_navigation(self):
        context, _, _ = self.page({'genre': 'client', 'q': '日本語 & keyword'})
        context['genre_id_prefix'] = 'faq' if self.model is KnowledgeArticle else 'tips'
        selector = render_to_string('includes/category_genre_selector.html', context)
        parsed = Tags(selector)
        self.assertEqual(sum(tag == 'noscript' for tag, _ in parsed.tags), 1)
        links = [attrs for tag, attrs in parsed.tags if tag == 'a' and 'data-genre' in attrs]
        self.assertEqual(len(links), len(context['genre_options']) + 1)
        self.assertEqual([a['href'] for a in links],
                         [context['genre_all_url'], *[o['url'] for o in context['genre_options']]])
        self.assertEqual([a['data-genre'] for a in links if a.get('aria-current') == 'page'], ['client'])
        for index, (tag, attrs) in enumerate(parsed.tags):
            if tag == 'a' and 'data-genre' in attrs:
                self.assertTrue(any(tag == 'noscript' for tag, _ in parsed.ancestors[index]))
                self.assertTrue(any(tag == 'nav' and parent.get('class') == 'category-genre-fallback'
                                    and parent.get('aria-label') == 'ジャンル選択'
                                    for tag, parent in parsed.ancestors[index]))
        # Chrome's scriptEnabled=false disables --dump-dom serialization.
        # Materialize the actual noscript contents as a JS-disabled parser
        # would, then block all scripts with CSP. This tests the rendered
        # fallback, not Chrome's native noscript activation.
        fallback = re.search(r'<noscript>(.*?)</noscript>', selector, re.S).group(1)
        self.assertIn('.category-genre-selector select, .category-genre-selector label { display: none; }', fallback)
        selector = re.sub(r'<script\b[^>]*>.*?</script\s*>', '', selector, flags=re.S | re.I)
        selector = re.sub(r'<noscript>.*?</noscript>', lambda _: fallback, selector, flags=re.S)
        source = '''<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none';
    style-src 'unsafe-inline'; script-src 'none'; connect-src 'none'">
</head><body>''' + selector + '''<pre id="result">PASS: scripts disabled</pre>
<script>document.getElementById('result').textContent = 'FAIL: scripts enabled';</script>
</body></html>'''
        dom = self.run_offline_page(source, expected='PASS: scripts disabled')
        parsed = Tags(dom)
        links = [attrs for tag, attrs in parsed.tags if tag == 'a' and 'data-genre' in attrs]
        self.assertEqual(len(links), len(context['genre_options']) + 1)
        self.assertEqual([a['href'] for a in links],
                         [context['genre_all_url'], *[o['url'] for o in context['genre_options']]])
        self.assertEqual([a['data-genre'] for a in links if a.get('aria-current') == 'page'], ['client'])
        fallback_indexes = [i for i, (tag, attrs) in enumerate(parsed.tags)
                            if tag == 'a' and 'data-genre' in attrs]
        for index in fallback_indexes:
            attrs = parsed.tags[index][1]
            self.assertNotIn('hidden', attrs)
            self.assertNotIn('aria-hidden', attrs)
            self.assertFalse(any(key.startswith('on') for key in attrs))
            self.assertTrue(any(tag == 'nav' and parent.get('class') == 'category-genre-fallback'
                                for tag, parent in parsed.ancestors[index]))
            # Follow the href through the real view without JS or a server.
            query = parse_qs(urlsplit(attrs['href']).query)
            followed, following_source, _ = self.page(query)
            self.assertEqual(followed['selected_genre'], attrs['data-genre'])
            self.assertEqual(followed['query'], context['query'])
            following_tags = Tags(following_source).tags
            current = [a['data-genre'] for tag, a in following_tags
                       if tag == 'a' and a.get('aria-current') == 'page' and 'data-genre' in a]
            self.assertEqual(current, [attrs['data-genre']])
            selected = [o['data-genre'] for tag, o in following_tags
                        if tag == 'option' and 'selected' in o and 'data-genre' in o]
            self.assertEqual(selected, [attrs['data-genre']])

    def test_permissions_apply_to_results_and_genre_counts(self):
        for restriction in ({'is_published': False}, {'visible_to_customer': False},
                            {'is_approved': False},
                            {'expires_on': timezone.localdate() - timedelta(days=1)}):
            self.item('PC/設定', **restriction)
        context, _, _ = self.page({'genre': 'client'})
        self.assertCountEqual(self.ids(context), [self.pc.pk, self.cross.pk])
        self.assertEqual(context['genre_options'][0]['count'], 2)
        admin_context, _, _ = self.page({'genre': 'client'}, user=self.admin)
        self.assertEqual(admin_context['genre_options'][0]['count'], 4)
        demo_context, source, _ = self.page({'genre': 'client'}, user=self.demo)
        self.assertEqual(demo_context['genre_options'][0]['count'], 2)
        self.assertNotIn('PRIVATE_GENRE_BODY', source)
        with patch.object(views, 'hidden_parent_category_names_for_customer', return_value={'PC'}):
            hidden_context, _, _ = self.page({'genre': 'client'})
        self.assertEqual(self.ids(hidden_context), [])
        self.assertEqual(hidden_context['genre_options'][0]['count'], 0)
        self.assertNotIn('PC', [p['name'] for p in hidden_context['parent_categories']])


class ArticleGenreListTests(GenreListTestsMixin, TestCase):
    model = KnowledgeArticle
    view_class = views.ArticleListView
    url_name = 'article_list'
    context_name = 'articles'
    favorite_model = ArticleFavorite
    favorite_field = 'article'


class TipsGenreListTests(GenreListTestsMixin, TestCase):
    model = TipsArticle
    view_class = views.TipsListView
    url_name = 'tip_list'
    context_name = 'tips_list'
    favorite_model = TipsFavorite
    favorite_field = 'tip'