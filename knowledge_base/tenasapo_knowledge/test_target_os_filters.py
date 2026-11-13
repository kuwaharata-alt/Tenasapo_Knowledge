"""Backend regression tests shared by the FAQ and Tips list views."""

from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory, TestCase, override_settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from . import views
from .models import ArticleFavorite, FAQCategory, KnowledgeArticle, TipsArticle, TipsFavorite


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

    def test_approval_states_follow_approved_corrected_remanded_registered_priority(self):
        staff = get_user_model().objects.create_user(username='approval-staff', is_staff=True)
        states = {
            'registered': self.create_item('Android 15', is_approved=False, remand_reason=' \t\n'),
            'remanded': self.create_item('Android 15', is_approved=False, remand_reason='要修正'),
            'corrected': self.create_item(
                'Android 15', is_approved=False, is_corrected=True, remand_reason='以前の差戻し',
            ),
            'approved': self.create_item(
                'Android 15', is_approved=True, is_corrected=True, remand_reason='以前の差戻し',
            ),
        }
        for status, item in states.items():
            with self.subTest(status=status):
                context, _ = self.get_context({
                    'filter_approval': status, 'target_os': 'Android',
                }, user=staff)
                self.assertEqual([row.pk for row in context[self.context_name]], [item.pk])
                self.assertEqual(context[self.context_name][0].approval_status, status)
                self.assertEqual(context['filter_approval'], [status])
                expected_counts = {'Android': 1}
                if status == 'approved':
                    # The shared fixture also contains approved PC/server/Ubuntu rows.
                    expected_counts.update({'Windows PC': 1, 'Windows Server': 1, 'Ubuntu': 2})
                self.assertEqual(self.option_counts(context), expected_counts)

    def test_multiple_approval_states_are_or_combined_and_normalized(self):
        staff = get_user_model().objects.create_user(username='approval-or-staff', is_staff=True)
        registered = self.create_item('Android 15', is_approved=False)
        corrected = self.create_item('Android 15', is_approved=False, is_corrected=True)
        self.create_item('Android 15', is_approved=False, remand_reason='要修正')
        self.create_item('Android 15')
        context, _ = self.get_context({
            'filter_approval': ['corrected', 'registered', 'corrected', 'unknown'],
            'target_os': 'Android',
        }, user=staff)
        self.assertEqual(
            {row.pk for row in context[self.context_name]}, {registered.pk, corrected.pk},
        )
        self.assertEqual(context['filter_approval'], ['registered', 'corrected'])
        self.assertEqual(context['active_filter_count'], 1)
        self.assertEqual(self.option_counts(context), {'Android': 2})

    def test_pending_approval_alias_selects_registered_only(self):
        staff = get_user_model().objects.create_user(username='approval-alias-staff', is_staff=True)
        registered = self.create_item('Android 15', is_approved=False)
        self.create_item('Android 15', is_approved=False, remand_reason='要修正')
        context, _ = self.get_context({'filter_approval': 'pending'}, user=staff)
        self.assertEqual([row.pk for row in context[self.context_name]], [registered.pk])
        self.assertEqual(context['filter_approval'], ['registered'])

    @override_settings(USE_TZ=True)
    def test_created_month_uses_local_boundaries_and_excludes_adjacent_months(self):
        staff = get_user_model().objects.create_user(username='month-staff', is_staff=True)
        with timezone.override('Asia/Tokyo'):
            local_timezone = timezone.get_current_timezone()
            moments = (
                datetime(2025, 12, 31, 23, 59, 59, 999999),
                datetime(2026, 1, 1),
                datetime(2026, 1, 31, 23, 59, 59, 999999),
                datetime(2026, 2, 1),
            )
            items = []
            for moment in moments:
                item = self.create_item('Android 15')
                self.model.objects.filter(pk=item.pk).update(
                    created_at=timezone.make_aware(moment, local_timezone),
                )
                items.append(item)
            for month, expected in (
                ('2025-12', {items[0].pk}),
                ('2026-01', {items[1].pk, items[2].pk}),
                ('2026-02', {items[3].pk}),
            ):
                with self.subTest(month=month):
                    context, _ = self.get_context({
                        'filter_created_month': month, 'target_os': 'Android',
                    }, user=staff)
                    self.assertEqual({row.pk for row in context[self.context_name]}, expected)
                    self.assertEqual(context['filter_created_month'], month)
                    self.assertEqual(self.option_counts(context), {'Android': len(expected)})

    def test_invalid_created_year_or_month_is_ignored(self):
        staff = get_user_model().objects.create_user(username='invalid-month-staff', is_staff=True)
        expected = {self.pc.pk, self.server.pk, self.ubuntu.pk, self.blank.pk, self.legacy.pk}
        for month in ('', 'invalid', '0000-01', '10000-01', '2026-00', '2026-13',
                      '2026-1', '26-01', '2026-01-01', '2026-01 ', '2026-aa'):
            with self.subTest(month=month):
                context, _ = self.get_context({'filter_created_month': month}, user=staff)
                self.assertEqual({row.pk for row in context[self.context_name]}, expected)
                self.assertEqual(context['filter_created_month'], '')
                self.assertEqual(context['active_filter_count'], 0)

    def test_removed_approver_and_expiry_filters_are_ignored_and_absent_from_ui(self):
        staff = get_user_model().objects.create_user(username='removed-filter-staff', is_staff=True)
        baseline, _ = self.get_context(user=staff)
        removed = {
            'filter_approver': str(staff.pk),
            'filter_expiry': 'expired',
            'filter_expires_from': '2999-01-01',
            'filter_expires_to': '1900-01-01',
        }
        context, request = self.get_context(removed, user=staff)
        self.assertEqual(
            {row.pk for row in context[self.context_name]},
            {row.pk for row in baseline[self.context_name]},
        )
        self.assertEqual(self.option_counts(context), self.option_counts(baseline))
        self.assertEqual(context['active_filter_count'], 0)
        html = render_to_string(self.view_class.template_name, context, request=request)
        for name in removed:
            with self.subTest(name=name):
                self.assertNotIn(f'name="{name}"', html)
                self.assertNotIn(name, context)
        panel = render_to_string('includes/knowledge_list_filters.html', context, request=request)
        self.assertNotIn('承認者', panel)
        self.assertNotIn('掲載期限', panel)

    def test_navigation_urls_preserve_repeated_filters_and_clear_only_requested_parameters(self):
        staff = get_user_model().objects.create_user(username='navigation-staff', is_staff=True)
        FAQCategory.objects.get_or_create(parent_name='親', middle_name='', child_name='子')
        FAQCategory.objects.get_or_create(parent_name='親', middle_name='中', child_name='孫')
        self.favorite_model.objects.create(user=staff, **{self.favorite_field: self.pc})
        params = {
            'filter_approval': ['registered', 'approved'],
            'filter_creator': str(staff.pk), 'filter_created_month': '2026-01',
            'parent_category': '親', 'category': '親/子', 'target_os': 'Windows PC',
            'favorite_only': '1', 'q': 'keyword & 日本語',
            'page': '9', 'open_code': 'FAQ-0001', 'open_id': str(self.pc.pk),
        }
        context, request = self.get_context(params, user=staff)
        original = request.GET.copy()
        retained = {
            key: values for key, values in request.GET.lists()
            if key not in {'page', 'open_code', 'open_id'}
        }
        cases = [
            (context['category_all_url'], {
                key: value for key, value in retained.items()
                if key not in {'parent_category', 'category'}
            }),
            (context['favorite_toggle_url'], {
                key: value for key, value in retained.items() if key != 'favorite_only'
            }),
            (context['filter_clear_url'], {
                key: value for key, value in retained.items() if not key.startswith('filter_')
            }),
        ]
        parent = next(group for group in context['parent_categories'] if group['name'] == '親')
        cases.append((parent['url'], {
            key: value for key, value in retained.items() if key != 'category'
        }))
        children = list(parent['children'])
        for middle in parent['middle_groups']:
            children.extend(middle['children'])
        self.assertEqual({child['full_name'] for child in children}, {'親/子', '親/中/孫'})
        for child in children:
            cases.append((child['url'], {**retained, 'category': [child['full_name']]}))
        for url, expected in cases:
            with self.subTest(url=url):
                parts = urlsplit(url)
                self.assertEqual(parts.path, request.path)
                self.assertEqual(parse_qs(parts.query), expected)
        self.assertEqual(request.GET, original)
        cleared, _ = self.get_context(url=context['filter_clear_url'], user=staff)
        self.assertEqual([row.pk for row in cleared[self.context_name]], [self.pc.pk])
        self.assertEqual(cleared['active_filter_count'], 0)

    def test_favorite_toggle_enables_favorites_without_losing_repeated_filters(self):
        staff = get_user_model().objects.create_user(username='favorite-link-staff', is_staff=True)
        context, request = self.get_context({
            'filter_approval': ['registered', 'approved'],
            'filter_created_month': '2026-01', 'category': '親/子',
            'target_os': 'Ubuntu', 'q': 'keyword & 日本語', 'page': '2',
        }, user=staff)
        expected = {key: values for key, values in request.GET.lists() if key != 'page'}
        expected['favorite_only'] = ['1']
        self.assertEqual(parse_qs(urlsplit(context['favorite_toggle_url']).query), expected)

    def test_os_navigation_preserves_repeated_approval_category_and_month_filters(self):
        staff = get_user_model().objects.create_user(username='os-navigation-staff', is_staff=True)
        with timezone.override('Asia/Tokyo'):
            self.model.objects.filter(pk=self.pc.pk).update(created_at=timezone.make_aware(
                datetime(2026, 1, 15), timezone.get_current_timezone(),
            ))
            context, request = self.get_context({
                'filter_approval': ['registered', 'approved'],
                'filter_created_month': '2026-01', 'category': '親/子',
                'target_os': 'Windows PC', 'page': '2',
            }, user=staff)
        self.assertTrue(context['target_os_options'])
        expected = {
            key: values for key, values in request.GET.lists() if key not in {'page', 'target_os'}
        }
        self.assertEqual(parse_qs(urlsplit(context['target_os_all_url']).query), expected)
        for option in context['target_os_options']:
            with self.subTest(os=option['name']):
                self.assertEqual(
                    parse_qs(urlsplit(option['url']).query),
                    {**expected, 'target_os': [option['name']]},
                )

    def test_rendered_approval_checkboxes_month_and_hidden_favorite_selection(self):
        staff = get_user_model().objects.create_user(username='render-filter-staff', is_staff=True)
        context, request = self.get_context({
            'filter_approval': ['registered', 'corrected'],
            'filter_created_month': '2026-01', 'favorite_only': '1',
        }, user=staff)
        html = render_to_string(self.view_class.template_name, context, request=request)
        prefix = 'faq' if self.model is KnowledgeArticle else 'tips'
        for index, status in enumerate(('registered', 'remanded', 'corrected', 'approved'), 1):
            checked = ' checked' if status in {'registered', 'corrected'} else ''
            with self.subTest(status=status):
                self.assertInHTML(
                    f'<input type="checkbox" class="btn-check" name="filter_approval" '
                    f'id="{prefix}Approval-{index}" value="{status}"{checked}>', html,
                )
        self.assertInHTML(
            '<input type="month" class="form-control form-control-sm" '
            f'name="filter_created_month" id="{prefix}CreatedMonth" value="2026-01">', html,
        )
        self.assertInHTML('<input type="hidden" name="favorite_only" value="1">', html)
        self.assertEqual(context['active_filter_count'], 2)

    def test_favorite_hidden_input_and_control_visibility(self):
        for favorite_only in ('', '1'):
            with self.subTest(favorite_only=favorite_only):
                context, request = self.get_context({'favorite_only': favorite_only})
                html = render_to_string(self.view_class.template_name, context, request=request)
                self.assertEqual('name="favorite_only"' in html, bool(favorite_only))
        self.user.groups.clear()
        context, request = self.get_context()
        html = render_to_string(self.view_class.template_name, context, request=request)
        self.assertFalse(context['can_use_favorite'])
        self.assertNotIn('☆のみ表示', html)
        self.assertNotIn('☆表示解除', html)

    def test_hidden_favorites_do_not_leak_into_results_or_os_options(self):
        self.favorite_model.objects.create(user=self.user, **{self.favorite_field: self.pc})
        for overrides in (
            {'visible_to_customer': False},
            {'visible_to_customer': False, 'visible_to_systena': False},
            {'is_published': False},
            {'is_approved': False},
            {'expires_on': timezone.localdate() - timedelta(days=1)},
            {'category': '非表示/子'},
        ):
            hidden = self.create_item('macOS 15 Sequoia', **overrides)
            self.favorite_model.objects.create(user=self.user, **{self.favorite_field: hidden})
        with patch.object(views, 'hidden_parent_category_names_for_customer', return_value={'非表示'}):
            context, _ = self.get_context({'favorite_only': '1'})
        self.assertEqual([row.pk for row in context[self.context_name]], [self.pc.pk])
        self.assertEqual([row.pk for row in context['object_list']], [self.pc.pk])
        self.assertEqual(self.option_counts(context), {'Windows PC': 1, 'Ubuntu': 1})


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