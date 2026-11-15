"""Document repository regressions; use test_knowledge_tabs_settings only.

Real routed views/templates, memory DB, no uploads or live network.
"""

import re
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from .category_genres import CATEGORY_GENRES
from .models import FAQCategory, ProjectDocument, UserProfile
from .test_category_genres import Tags
from .test_knowledge_tabs import Links, NoNetwork, OfflineChrome


class ProjectDocumentGenreTests(NoNetwork, OfflineChrome, TestCase):
    def setUp(self):
        super().setUp()
        self.staff = get_user_model().objects.create_user(username='document-genres', is_staff=True)
        self.reader = get_user_model().objects.create_user(username='document-reader')
        self.client.force_login(self.staff)
        for genre in CATEGORY_GENRES:
            for parent in genre['parents']:
                FAQCategory.objects.create(parent_name=parent, middle_name='genre-direct')
        self.leaf1 = FAQCategory.objects.create(parent_name='PC', middle_name='genre-middle', child_name='子1')
        self.leaf2 = FAQCategory.objects.create(parent_name='PC', middle_name='genre-middle', child_name='子2')
        self.pc = self.document('PC/genre-direct', is_template=True)
        self.cross = self.document('PC/genre-direct,PC/genre-direct,サーバー/genre-direct')
        self.server = self.document('サーバー/genre-direct', is_template=True)
        self.app = self.document('Microsoft 365/genre-direct')
        self.ops = self.document('移行・導入/genre-direct')
        self.hidden = self.document('PC/genre-direct', is_published=False, title='非公開の秘密')

    def document(self, category, **overrides):
        values = dict(title='genre keyword', project_number='PRJ-GENRE', customer_name='顧客',
                      product_name='製品', product_version='v42', category=category,
                      document_type=ProjectDocument.DOCUMENT_TYPE_PROCEDURE,
                      created_by=self.staff, created_by_name='保存済み氏名')
        values.update(overrides)
        return ProjectDocument.objects.create(**values)

    def page(self, params=None):
        response = self.client.get(reverse('project_document_list'), params or {})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'tenasapo_knowledge/project_document_list.html')
        return response, response.context, response.content.decode()

    def ids(self, context):
        return [document.pk for document in context['documents']]

    def test_same_four_genres_distinct_counts_and_selected_all_label(self):
        _, context, source = self.page({'genre': 'client'})
        self.assertEqual(self.ids(context), [self.cross.pk, self.pc.pk])
        self.assertEqual(context['all_count'], 2)
        self.assertEqual(context['genre_all_count'], 5)
        self.assertEqual([(g['key'], g['count']) for g in context['genre_options']],
                         [('client', 2), ('infrastructure', 2), ('applications', 1), ('operations', 1)])
        self.assertEqual({p['name'] for p in context['parent_categories']}, set(CATEGORY_GENRES[0]['parents']))
        self.assertIn('このジャンルのすべて', source)
        self.assertNotIn(self.hidden.title, source)
        _, all_context, source = self.page()
        keys = [p['genre_key'] for p in all_context['parent_categories']]
        self.assertEqual(keys, sorted(keys, key=[g['key'] for g in CATEGORY_GENRES].index))
        self.assertEqual(len({p['name'] for p in all_context['parent_categories']}), 12)
        self.assertEqual(source.count('<div class="category-genre-heading">'), 4)
        self.assertNotIn('このジャンルのすべて', source)

    def test_other_exact_parent_matching_and_empty_results(self):
        unknown = self.document('PC拡張/設定')
        blank = self.document('')
        _, context, _ = self.page({'genre': 'other'})
        self.assertCountEqual(self.ids(context), [unknown.pk, blank.pk])
        self.assertEqual(context['all_count'], 2)
        _, context, _ = self.page({'genre': 'client'})
        self.assertNotIn(unknown.pk, self.ids(context))
        _, context, source = self.page({'genre': 'operations', 'q': 'not-found'})
        self.assertEqual(self.ids(context), [])
        self.assertEqual(context['all_count'], 1)
        self.assertIn('該当するドキュメントはありません。', source)
        ProjectDocument.objects.filter(pk__in=[unknown.pk, blank.pk]).delete()
        FAQCategory.objects.create(parent_name='将来の親', middle_name='設定')
        _, context, _ = self.page()
        self.assertEqual(context['genre_options'][-1]['key'], 'other')
        self.assertEqual(context['genre_options'][-1]['count'], 0)

    def test_switch_urls_preserve_filters_and_clear_category_page(self):
        params = {'genre': 'client', 'parent_category': 'PC', 'category': 'PC/genre-direct',
                  'q': 'keyword & 日本語', 'template': 'true', 'page': '9',
                  'order': 'oldest', 'page_size': '20', 'filter_custom': ['a', 'b']}
        _, context, source = self.page(params)
        retained = {k: v if isinstance(v, list) else [v] for k, v in params.items()
                    if k not in {'genre', 'parent_category', 'category', 'page'}}
        for option in [*context['genre_options'], {'key': '', 'url': context['genre_all_url']}]:
            self.assertEqual(parse_qs(urlsplit(option['url']).query),
                             {**retained, **({'genre': [option['key']]} if option['key'] else {})})
        for link in [context['category_all_url'], *[p['url'] for p in context['parent_categories']]]:
            query = parse_qs(urlsplit(link).query)
            self.assertEqual(query['genre'], ['client'])
            self.assertEqual(query['template'], ['true'])
            self.assertNotIn('page', query)
        pc = next(p for p in context['parent_categories'] if p['name'] == 'PC')
        child = next(c for c in pc['children'] if c['full_name'] == 'PC/genre-direct')
        self.assertEqual(parse_qs(urlsplit(child['url']).query)['category'], ['PC/genre-direct'])
        self.assertTrue(any(a['href'] == child['url'] for a in Links(source).anchors))

    def test_incompatible_navigation_normalized_and_legacy_filters_unchanged(self):
        for stale in ({'parent_category': 'PC'}, {'category': 'PC/genre-direct'}):
            response, context, _ = self.page({'genre': 'infrastructure', 'page': '9', **stale})
            self.assertCountEqual(self.ids(context), [self.server.pk, self.cross.pk])
            self.assertEqual(context['selected_parent_category'], '')
            self.assertEqual(context['selected_category'], '')
            self.assertNotIn('page', response.wsgi_request.GET)
        for params in ({'parent_category': 'PC'}, {'category': 'PC/genre-direct'},
                       {'genre': 'invalid', 'category': 'PC/genre-direct'}):
            response, context, _ = self.page(params)
            self.assertEqual(context['selected_parent_category'], 'PC')
            self.assertEqual(context['selected_genre'], '')
            if 'category' in params:
                self.assertEqual(self.ids(context), [self.pc.pk])  # Legacy exact category equality.
            else:
                self.assertCountEqual(self.ids(context), [self.cross.pk, self.pc.pk])
            self.assertNotIn('genre', response.wsgi_request.GET)
        _, context, _ = self.page({'genre': 'client', 'category': 'PC/genre-direct'})
        self.assertEqual(self.ids(context), [self.pc.pk])

    def test_search_temp_order_and_existing_unpaginated_behavior(self):
        for query in ('keyword', 'PRJ-GENRE', '顧客', '製品', 'v42'):
            _, context, _ = self.page({'genre': 'client', 'q': query, 'template': 'true'})
            self.assertEqual(self.ids(context), [self.pc.pk])
            self.assertEqual(context['documents'][0].uploader_display_name, '保存済み氏名')
        added = [self.document('PC/genre-direct') for _ in range(22)]
        _, context, _ = self.page({'genre': 'client', 'page': '2', 'order': 'oldest'})
        self.assertEqual(self.ids(context), [d.pk for d in reversed(added)] + [self.cross.pk, self.pc.pk])
        self.assertFalse(context['is_paginated'])

    def test_sidebar_real_render_structure_hidden_genre_and_permissions(self):
        for user in (self.staff, self.reader):
            self.client.force_login(user)
            response, context, source = self.page({'genre': 'client'})
            self.assertTemplateUsed(response, 'includes/category_genre_selector.html')
            parsed = Tags(source)
            selectors = [i for i, (tag, attrs) in enumerate(parsed.tags)
                         if tag == 'select' and 'data-category-genre-navigation' in attrs]
            self.assertEqual(len(selectors), 1)
            index = selectors[0]
            self.assertEqual(parsed.tags[index][1]['id'], 'documentCategoryGenreNavigation')
            self.assertNotIn('name', parsed.tags[index][1])
            self.assertTrue(any(tag == 'aside' for tag, _ in parsed.ancestors[index]))
            self.assertFalse(any(tag == 'form' for tag, _ in parsed.ancestors[index]))
            self.assertInHTML('<input type="hidden" name="genre" value="client">', source)
            self.assertEqual(context['can_create_document'], user == self.staff)
            self.assertEqual(context['genre_options'][0]['count'], 2)
            options = [a for tag, a in parsed.tags if tag == 'option' and 'data-genre' in a]
            fallback = [a for tag, a in parsed.tags if tag == 'a' and 'data-genre' in a]
            self.assertEqual([o['value'] for o in options], [a['href'] for a in fallback])
            self.assertEqual([o['data-genre'] for o in options if 'selected' in o], ['client'])
        for profile_only in (False, True):
            customer = get_user_model().objects.create_user(username='document-customer-' + str(profile_only))
            if profile_only:
                UserProfile.objects.create(user=customer, user_type='customer')
            else:
                customer.groups.add(Group.objects.get_or_create(name='カスタマー')[0])
            self.client.force_login(customer)
            response = self.client.get(reverse('project_document_list'), {'genre': 'client'})
            self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
            self.assertNotIn('data-category-genre-navigation', response.content.decode())

    def test_middle_counts_are_distinct_per_document(self):
        self.document(self.leaf1.full_name + ',' + self.leaf2.full_name + ',' + self.leaf1.full_name)
        _, context, source = self.page({'genre': 'client'})
        pc = next(p for p in context['parent_categories'] if p['name'] == 'PC')
        middle = next(m for m in pc['middle_groups'] if m['name'] == 'genre-middle')
        self.assertEqual(middle['count'], 1)
        self.assertEqual([c['count'] for c in middle['children']], [1, 1])
        for child in middle['children']:
            self.assertTrue(any(a['href'] == child['url'] for a in Links(source).anchors))

    def test_gets_preserve_stored_ids_categories_documents_and_escape_labels(self):
        unsafe = '未知 <img src=x onerror=evil()> & "'
        FAQCategory.objects.create(parent_name=unsafe, middle_name='設定')
        item = self.document(unsafe + '/設定', title=unsafe)
        categories = list(FAQCategory.objects.values_list('pk', 'parent_name', 'middle_name', 'child_name'))
        documents = list(ProjectDocument.objects.values())
        for genre in ('', 'client', 'infrastructure', 'applications', 'operations', 'other'):
            _, context, source = self.page({'genre': genre})
            if genre == 'other':
                self.assertIn(item.pk, self.ids(context))
                self.assertIn(str(escape(unsafe)), source)
        self.assertEqual(categories, list(FAQCategory.objects.values_list('pk', 'parent_name', 'middle_name', 'child_name')))
        self.assertEqual(documents, list(ProjectDocument.objects.values()))
        name = settings.DATABASES['default']['NAME']
        self.assertTrue(name == ':memory:' or ('mode=memory' in name and name.startswith('file:memorydb_')))

    def test_offline_actual_document_sidebar_indentation_and_group_headings(self):
        self.document(self.leaf1.full_name)
        _, _, source = self.page({'genre': 'client', 'parent_category': 'PC'})
        source = re.sub(r'<script\b[^>]*>.*?</script\s*>', '', source, flags=re.S | re.I)
        source = re.sub(r'<link\b[^>]*>', '', source, flags=re.I)
        source = source.replace('<head>', '''<head><meta http-equiv="Content-Security-Policy"
content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'">
<style>* {box-sizing:border-box;} .collapse:not(.show) {display:none;}</style>''', 1)
        harness = '''<pre id="result">PENDING</pre><script>
try {
 const assert = (ok, message) => {if (!ok) throw new Error(message);};
 const sidebar = document.querySelector('.category-sidebar-with-genre');
 const select = sidebar.querySelector('[data-category-genre-navigation]');
 assert(select.labels.length === 1, 'accessible native dropdown');
 assert(sidebar.querySelector('.category-genre-heading').textContent === 'クライアント環境', 'heading');
 for (const size of [16, 20]) {
  document.documentElement.style.fontSize = size + 'px';
  const parent = sidebar.querySelector('.list-group > .category-sidebar-toggle');
  const children = sidebar.querySelector('.list-group > .collapse > .category-child-list');
  const p = getComputedStyle(parent), c = getComputedStyle(children);
  assert(Math.abs(parseFloat(p.paddingLeft) - (size * .8 + parseFloat(p.fontSize))) < .6, 'parent 1em indent');
  assert(Math.abs(parseFloat(c.paddingLeft) - (size * (.8 - .45) + parseFloat(c.fontSize) * 2)) < .6, 'child 2em indent');
 }
 document.getElementById('result').textContent = 'PASS: document sidebar genres';
} catch(e) {document.getElementById('result').textContent = 'FAIL: ' + e.message;}
</script>'''
        source = source.replace('</body>', harness + '</body>', 1)
        for width in (390, 1280):
            with self.subTest(width=width):
                self.run_offline_page(source, expected='PASS: document sidebar genres', width=width)