"""Compatibility and dependency contracts for the first menu extraction."""

import importlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import resolve, reverse

from . import forms, views
from .features.input import views as input_views
from .features.knowledge import views as knowledge_views
from .features.manuals import views as manual_views
from .features.reports import views as report_views
from .features.users import views as user_views
from .models import ConvenienceFavorite, ConvenienceFeature, Manual, ViewHistory


class FeatureBoundaryTests(SimpleTestCase):
    def test_legacy_exports_are_the_implementation_objects_not_wrappers(self):
        groups = {
            'views': (
                'input.views', 'knowledge.views', 'manuals.views', 'reports.views',
                'users.views', 'shared.access', 'shared.history',
            ),
            'forms': ('input.forms', 'manuals.forms', 'reports.forms', 'users.forms'),
        }
        for legacy_name, suffixes in groups.items():
            legacy = importlib.import_module('tenasapo_knowledge.' + legacy_name)
            for suffix in suffixes:
                module = importlib.import_module('tenasapo_knowledge.features.' + suffix)
                for name, value in vars(module).items():
                    if not (inspect.isclass(value) or inspect.isfunction(value)):
                        continue
                    if value.__module__ != module.__name__:
                        continue
                    with self.subTest(module=suffix, name=name):
                        self.assertIs(getattr(legacy, name), value)
        from .features.shared import qr_categories
        self.assertIs(forms.get_qr_category_hierarchy, qr_categories.get_qr_category_hierarchy)
        self.assertIs(forms.DEFAULT_QR_CATEGORY_HIERARCHY,
                      qr_categories.DEFAULT_QR_CATEGORY_HIERARCHY)

    def test_each_feature_imports_without_loading_legacy_views_or_forms(self):
        # A fresh process catches cycles hidden by test discovery/import order.
        for feature in ('input', 'knowledge', 'manuals', 'reports', 'users'):
            code = (
                'import django, importlib, sys; django.setup(); '
                f"importlib.import_module('tenasapo_knowledge.features.{feature}.views'); "
                "assert 'tenasapo_knowledge.views' not in sys.modules; "
                "assert 'tenasapo_knowledge.forms' not in sys.modules; "
                "importlib.import_module('tenasapo_knowledge.forms'); "
                "importlib.import_module('tenasapo_knowledge.views'); "
                "importlib.import_module('config.urls')"
            )
            with self.subTest(feature=feature):
                result = subprocess.run(
                    [sys.executable, '-B', '-c', code],
                    cwd=Path(__file__).resolve().parent.parent,
                    env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings_ci'},
                    capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_existing_named_routes_resolve_directly_to_feature_classes(self):
        cases = (
            ('knowledge_input', '/knowledge/input/', input_views.KnowledgeInputHubView),
            ('convenience_list', '/convenience/', knowledge_views.ConvenienceListView),
            ('convenience_create', '/convenience/create/', input_views.ConvenienceCreateView),
            ('convenience_toggle_favorite', '/convenience/toggle-favorite/',
             knowledge_views.ConvenienceFavoriteToggleView),
            ('user_demo_issue', '/users/issue-demo/', user_views.UserDemoIssueView),
            ('user_contract_issue', '/users/issue-contract/', user_views.UserContractIssueView),
            ('user_create', '/users/create/', user_views.UserCreateView),
            ('posting_quota_settings', '/summary/quotas/', report_views.PostingQuotaSettingsView),
            ('manual_list', '/manuals/', manual_views.ManualListView),
            ('manual_create', '/manuals/create/', manual_views.ManualCreateView),
        )
        for name, path, implementation in cases:
            with self.subTest(name=name):
                self.assertEqual(reverse(name), path)
                match = resolve(path)
                self.assertEqual(match.url_name, name)
                self.assertIs(match.func.view_class, implementation)
        for name, path, implementation in (
            ('convenience_edit', '/convenience/7/edit/', input_views.ConvenienceUpdateView),
            ('manual_detail', '/manuals/7/', manual_views.ManualDetailView),
            ('manual_edit', '/manuals/7/edit/', manual_views.ManualUpdateView),
            ('manual_delete', '/manuals/7/delete/', manual_views.ManualDeleteView),
        ):
            with self.subTest(name=name):
                self.assertEqual(reverse(name, args=[7]), path)
                self.assertIs(resolve(path).func.view_class, implementation)

    def test_root_manual_aliases_keep_paths_and_implementation(self):
        from config.urls import urlpatterns
        aliases = {pattern.name: pattern for pattern in urlpatterns
                   if getattr(pattern, 'name', None)}
        for name, path, implementation in (
            ('manual_list_no_slash', 'manuals', manual_views.ManualListView),
            ('manual_list_root', 'manuals/', manual_views.ManualListView),
            ('manual_create_root', 'manuals/create/', manual_views.ManualCreateView),
            ('manual_detail_root', 'manuals/<int:pk>/', manual_views.ManualDetailView),
            ('manual_edit_root', 'manuals/<int:pk>/edit/', manual_views.ManualUpdateView),
            ('manual_delete_root', 'manuals/<int:pk>/delete/', manual_views.ManualDeleteView),
        ):
            with self.subTest(name=name):
                self.assertEqual(str(aliases[name].pattern), path)
                self.assertIs(aliases[name].callback.view_class, implementation)

    def test_form_classes_are_bound_to_the_feature_implementations(self):
        self.assertIs(input_views.ConvenienceCreateView.form_class, forms.ConvenienceFeatureCreateForm)
        self.assertIs(input_views.ConvenienceUpdateView.form_class, forms.ConvenienceFeatureCreateForm)
        self.assertIs(input_views.ConvenienceCategoryCreateView.form_class, forms.ConvenienceCategoryCreateForm)
        self.assertIs(manual_views.ManualCreateView.form_class, forms.ManualForm)
        self.assertIs(manual_views.ManualUpdateView.form_class, forms.ManualForm)
        self.assertIs(report_views.PostingQuotaSettingsView.form_class, forms.PostingQuotaForm)
        self.assertIs(user_views.UserDemoIssueView().get_form_class(), forms.UserDemoIssueForm)
        self.assertIs(user_views.UserContractIssueView().get_form_class(), forms.UserContractIssueForm)
        self.assertIs(user_views.UserCreateView.form_class, forms.UserCreateForm)
        self.assertIs(views.UserUpdateView.form_class, forms.UserUpdateForm)


class ExtractedFeatureBehaviorTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username='feature-admin', is_staff=True)
        self.reader = get_user_model().objects.create_user(username='feature-reader')
        self.factory = RequestFactory()

    def feature(self, text, frequency):
        return ConvenienceFeature.objects.create(
            reference_type='shortcut', category='Winodws', middle_category='デスクトップ',
            shortcut_key='Ctrl + C', display_text=text, usage_frequency=frequency,
        )

    def qr_context(self, query):
        request = self.factory.get('/convenience/', query)
        request.user = self.staff
        view = knowledge_views.ConvenienceListView()
        view.setup(request)
        return view.get_context_data()

    def test_qr_sorting_favorites_search_and_invalid_selection_keep_context_contract(self):
        high = self.feature('コピー', '5')
        favorite = self.feature('お気に入り', '2')
        ConvenienceFavorite.objects.create(feature=favorite, user=self.staff)
        context = self.qr_context({'sort': 'favorite'})
        items = context['category_groups'][0]['features']
        self.assertEqual([item.pk for item in items], [favorite.pk, high.pk])
        self.assertTrue(items[0].is_favorited)
        self.assertEqual(items[0].usage_frequency_stars, '★★☆☆☆')
        context = self.qr_context({'sort': 'unknown', 'big_category': 'not-registered', 'q': 'コピー'})
        self.assertEqual(context['sort_mode'], 'frequency')
        self.assertEqual(context['selected_big'], '')
        self.assertEqual([item.pk for item in context['category_groups'][0]['features']], [high.pk])
        self.assertIn('sidebar_hierarchy', context)

    def test_qr_favorite_response_statuses_and_toggle_are_unchanged(self):
        feature = self.feature('コピー', '3')

        def post(user, payload):
            request = self.factory.post('/convenience/toggle-favorite/', json.dumps(payload),
                                        content_type='application/json')
            request.user = user
            return knowledge_views.ConvenienceFavoriteToggleView.as_view()(request)

        self.assertEqual(post(AnonymousUser(), {'feature_id': feature.pk}).status_code, 401)
        self.assertEqual(post(self.reader, {'feature_id': feature.pk}).status_code, 403)
        self.assertEqual(post(self.staff, {}).status_code, 400)
        for expected in (True, False):
            response = post(self.staff, {'feature_id': feature.pk})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.content), {'ok': True, 'favorited': expected})
            self.assertEqual(ConvenienceFavorite.objects.filter(feature=feature, user=self.staff).exists(), expected)

    def test_manual_crud_keeps_creator_safe_return_and_fixed_menu_delete_guard(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('manual_create'), {
            'title': '補足手順', 'description': '操作手順\n登録する', 'order': 5,
            'is_published': 'on',
        })
        self.assertRedirects(response, reverse('manual_list'), fetch_redirect_response=False)
        manual = Manual.objects.get(title='補足手順')
        self.assertEqual(manual.created_by_id, self.staff.pk)
        response = self.client.post(reverse('manual_edit', args=[manual.pk]), {
            'title': '更新した手順', 'description': '変更する', 'order': 6,
            'is_published': 'on', 'next': 'https://external.invalid/',
        })
        self.assertRedirects(response, reverse('manual_list'), fetch_redirect_response=False)
        manual.refresh_from_db()
        self.assertEqual((manual.title, manual.created_by_id), ('更新した手順', self.staff.pk))
        self.client.get(reverse('manual_detail', args=[manual.pk]))
        self.assertTrue(ViewHistory.objects.filter(user=self.staff, page_name='マニュアル詳細: 更新した手順').exists())
        fixed = Manual.objects.create(menu_key='home', title='固定', description='固定手順')
        self.client.post(reverse('manual_delete', args=[fixed.pk]))
        self.assertTrue(Manual.objects.filter(pk=fixed.pk).exists())
        self.client.post(reverse('manual_delete', args=[manual.pk]))
        self.assertFalse(Manual.objects.filter(pk=manual.pk).exists())