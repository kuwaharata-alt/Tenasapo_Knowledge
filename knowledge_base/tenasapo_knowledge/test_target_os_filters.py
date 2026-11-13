"""Backend regression tests shared by the FAQ and Tips list views."""

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory, TestCase
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from . import views
from .models import ArticleFavorite, KnowledgeArticle, TipsArticle, TipsFavorite


class TargetOSListTestsMixin:
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='os-filter-user')
        group, _ = Group.objects.get_or_create(name=views.CUSTOMER_GROUP_NAME)
        self.user.groups.add(group)
        self.factory = RequestFactory()
        self.enterContext(patch.object(views, 'FAQ_APPROVAL_ENABLED', True))
        self.pc = self.create_item('Windows PC 11 以降, Windows PC 10, Ubuntu 24.04')
        self.server = self.create_item('Windows Server 2022')
        self.ubuntu = self.create_item('Ubuntu 22.04 以前')
        self.blank = self.create_item('')
        self.legacy = self.create_item('unrecognized legacy value')

    def create_item(self, target_os, **overrides):
        values = {
            'title': '対象 keyword & 日本語',
            'body': '本文',
            'category': '親/子',
            'target_os': target_os,
            'expires_on': None,
        }
        values.update(overrides)
        return self.model.objects.create(**values)

    def get_context(self, params=None, *, user=None, url=None):
        request = self.factory.get(url or reverse(self.url_name), params or {})
        request.user = user or self.user
        request.session = {}
        # Exercise the real CBV queryset/context without rendering templates.
        response = self.view_class.as_view()(request)
        self.assertEqual(response.status_code, 200)
        return response.context_data, request

    def option_counts(self, context):
        return {option['name']: option['count'] for option in context['target_os_options']}

    def test_os_filter_template_renders_and_preserves_selection(self):
        context, request = self.get_context({'target_os': 'Windows PC', 'q': 'keyword'})
        html = render_to_string(self.view_class.template_name, context, request=request)
        self.assertIn('category-sidebar-col has-os-filter', html)
        self.assertIn('aria-label="対象OSの絞り込み"', html)
        self.assertIn('name="target_os" value="Windows PC"', html)
        self.assertIn('target_os=Windows+PC', html)
        self.assertIn('Windows PC', html)

    def test_exact_os_name_and_counts_before_os_filter(self):
        context, _ = self.get_context({'target_os': 'Windows PC'})
        self.assertEqual([item.pk for item in context[self.context_name]], [self.pc.pk])
        self.assertEqual(self.option_counts(context), {
            'Windows PC': 1, 'Windows Server': 1, 'Ubuntu': 2,
        })
        self.assertEqual(context['selected_target_os'], 'Windows PC')
        self.assertEqual(
            [option['name'] for option in context['target_os_options'] if option['selected']],
            ['Windows PC'],
        )
        self.assertEqual([item.pk for item in context['object_list']], [self.pc.pk])

    def test_missing_or_blank_selection_keeps_blank_and_legacy_rows(self):
        for params in ({}, {'target_os': ''}):
            with self.subTest(params=params):
                context, _ = self.get_context(params)
                self.assertEqual(len(context[self.context_name]), 5)
                self.assertEqual(context['selected_target_os'], '')
                self.assertFalse(any(option['selected'] for option in context['target_os_options']))
                self.assertEqual(self.option_counts(context)['Ubuntu'], 2)

    def test_partial_version_unknown_and_case_mismatch_match_nothing(self):
        for name in ('Windows', 'Windows PC 11', 'windows pc', 'unknown'):
            with self.subTest(name=name):
                context, _ = self.get_context({'target_os': name})
                self.assertEqual(list(context[self.context_name]), [])
                self.assertEqual(context['selected_target_os'], name)
                self.assertEqual(self.option_counts(context)['Windows PC'], 1)

    def test_category_query_and_favorites_constrain_counts_and_rows(self):
        self.favorite_model.objects.create(user=self.user, **{self.favorite_field: self.pc})
        self.favorite_model.objects.create(user=self.user, **{self.favorite_field: self.server})
        self.create_item('Android 15', category='別/子')
        self.create_item('iOS 18', title='検索対象外')
        for category_params in ({'category': '親/子'}, {'parent_category': '親'}):
            with self.subTest(category_params=category_params):
                context, _ = self.get_context({
                    **category_params, 'q': 'keyword & 日本語',
                    'favorite_only': '1', 'target_os': 'Ubuntu',
                })
                self.assertEqual([item.pk for item in context[self.context_name]], [self.pc.pk])
                self.assertEqual(self.option_counts(context), {
                    'Windows PC': 1, 'Windows Server': 1, 'Ubuntu': 1,
                })

    def test_unauthorized_expired_and_unpublished_rows_do_not_leak_into_options(self):
        for overrides in (
            {'visible_to_customer': False},
            {'visible_to_customer': False, 'visible_to_systena': False},
            {'is_published': False},
            {'is_approved': False},
            {'expires_on': timezone.localdate() - timedelta(days=1)},
            {'category': '非表示/子'},
        ):
            self.create_item('macOS 15 Sequoia', **overrides)
        with patch.object(views, 'hidden_parent_category_names_for_customer', return_value={'非表示'}):
            context, _ = self.get_context({'target_os': 'macOS'})
        self.assertEqual(list(context[self.context_name]), [])
        self.assertNotIn('macOS', self.option_counts(context))

    def test_demo_lists_all_customer_visible_options(self):
        demo_group, _ = Group.objects.get_or_create(name='Demo')
        self.user.groups.add(demo_group)
        self.pc.visible_to_demo = True
        self.pc.save(update_fields=['visible_to_demo'])
        context, _ = self.get_context()
        self.assertEqual({item.pk for item in context[self.context_name]},
                 {self.pc.pk, self.server.pk, self.ubuntu.pk, self.blank.pk, self.legacy.pk})
        self.assertEqual(self.option_counts(context), {'Windows PC': 1, 'Windows Server': 1, 'Ubuntu': 2})

    def test_editor_filters_are_applied_before_os_counts(self):
        editor = get_user_model().objects.create_user(username='os-editor', is_staff=True)
        pending = self.create_item('Android 15', is_approved=False, created_by=editor)
        self.create_item('macOS', is_approved=False)
        context, _ = self.get_context({
            'filter_approval': 'pending', 'filter_creator': str(editor.pk),
            'target_os': 'Android',
        }, user=editor)
        self.assertEqual([item.pk for item in context[self.context_name]], [pending.pk])
        self.assertEqual(self.option_counts(context), {'Android': 1})

    def test_links_preserve_parameters_and_repeated_values_but_remove_page(self):
        params = {
            'q': 'keyword & 日本語', 'category': '親/子', 'parent_category': '親',
            'favorite_only': 'true', 'filter_approval': 'approved',
            'target_os': 'Windows PC', 'page': '9', 'extra': ['a&b', 'c d'],
        }
        self.favorite_model.objects.create(user=self.user, **{self.favorite_field: self.pc})
        context, request = self.get_context(params)
        original = request.GET.copy()
        for option in context['target_os_options']:
            self.assertEqual(set(option), {'name', 'count', 'url', 'selected'})
            parts = urlsplit(option['url'])
            self.assertEqual(parts.path, request.path)
            expected = {key: values for key, values in request.GET.lists() if key != 'page'}
            expected['target_os'] = [option['name']]
            self.assertEqual(parse_qs(parts.query), expected)
        expected = {
            key: values for key, values in request.GET.lists()
            if key not in {'page', 'target_os'}
        }
        self.assertEqual(parse_qs(urlsplit(context['target_os_all_url']).query), expected)
        self.assertEqual(request.GET, original)
        cleared, _ = self.get_context(url=context['target_os_all_url'])
        self.assertEqual(cleared['selected_target_os'], '')
        self.assertEqual([item.pk for item in cleared[self.context_name]], [self.pc.pk])

    def test_empty_results_and_all_link_without_query_string(self):
        context, _ = self.get_context({'q': 'no such title', 'target_os': 'Ubuntu'})
        self.assertEqual(context['target_os_options'], [])
        self.assertEqual(list(context[self.context_name]), [])
        context, _ = self.get_context({'target_os': 'Ubuntu', 'page': '3'})
        self.assertEqual(context['target_os_all_url'], reverse(self.url_name))

    def test_unavailable_favorites_produce_no_rows_or_counts(self):
        self.user.groups.clear()
        context, _ = self.get_context({'favorite_only': '1', 'target_os': 'Ubuntu'})
        self.assertEqual(list(context[self.context_name]), [])
        self.assertEqual(context['target_os_options'], [])

    def test_helper_uses_one_projection_query_without_loading_related_objects(self):
        request = self.factory.get(reverse(self.url_name), {'target_os': 'Ubuntu'})
        queryset = self.model.objects.prefetch_related('images', 'attachments').order_by('-pk')
        with self.assertNumQueries(1) as captured:
            filtered, context = views.filter_queryset_by_target_os(queryset, request)
        sql = captured.captured_queries[0]['sql']
        self.assertNotIn('"body"', sql)
        self.assertEqual(list(filtered.values_list('pk', flat=True)), [self.ubuntu.pk, self.pc.pk])
        self.assertEqual(self.option_counts(context)['Ubuntu'], 2)


class FAQTargetOSFilterTests(TargetOSListTestsMixin, TestCase):
    model = KnowledgeArticle
    view_class = views.ArticleListView
    url_name = 'article_list'
    context_name = 'articles'
    favorite_model = ArticleFavorite
    favorite_field = 'article'


class TipsTargetOSFilterTests(TargetOSListTestsMixin, TestCase):
    model = TipsArticle
    view_class = views.TipsListView
    url_name = 'tip_list'
    context_name = 'tips_list'
    favorite_model = TipsFavorite
    favorite_field = 'tip'