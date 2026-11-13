"""Registration duplicate candidates: direct API and create/edit integration."""

import json
import re
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.test import RequestFactory, TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from . import views
from .models import FAQCategory, KnowledgeArticle, TipsArticle


class KnowledgeDuplicateCandidatesTests(TestCase):
    today = date(2026, 11, 11)
    models_and_kinds = ((KnowledgeArticle, 'FAQ'), (TipsArticle, 'Tips'))

    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.staff = users.objects.create_user(username='duplicate-staff', is_staff=True)
        # Superuser access must not accidentally depend on the staff flag.
        cls.superuser = users.objects.create_user(
            username='duplicate-superuser', is_superuser=True, is_staff=False,
        )
        cls.regular = users.objects.create_user(username='duplicate-regular')
        cls.old_two_level = FAQCategory.objects.create(
            parent_name='PC', middle_name='', child_name='設定',
        )
        cls.middle = FAQCategory.objects.create(parent_name='PC', middle_name='設定')
        cls.child = FAQCategory.objects.create(
            parent_name='PC', middle_name='設定', child_name='ネットワーク',
        )
        cls.other = FAQCategory.objects.create(parent_name='別カテゴリ', middle_name='操作')

    def setUp(self):
        self.factory = RequestFactory()
        self.enterContext(patch.object(views.timezone, 'localdate', return_value=self.today))

    def request(self, params=None, user=None):
        request = self.factory.get(reverse('knowledge_duplicate_candidates'), params or {})
        request.user = self.staff if user is None else user
        request.session = {}
        mode = getattr(request.user, '_view_mode_override', '')
        if mode:
            request.session[views.ACCOUNT_VIEW_MODE_SESSION_KEY] = mode
        return views.KnowledgeDuplicateCandidatesView.as_view()(request)

    def candidates(self, ids=None, user=None, page=None):
        params = {'registered_category': ids if ids is not None else [self.middle.pk]}
        if page is not None:
            params['page'] = page
        return self.request(params, user=user)

    def payload(self, response, status=200):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response['Content-Type'].split(';')[0], 'application/json')
        self.assertEqual(response['Cache-Control'], 'no-store')
        data = json.loads(response.content)
        if status != 200:
            self.assertTrue(data.get('error'))
            self.assertNotIn('results', data)
            self.assertNotIn('Location', response)
        return data

    def article(self, model=KnowledgeArticle, **overrides):
        values = {
            'title': '既存記事', 'body': 'BODY_SECRET_DUPLICATE_CHECK',
            'category': self.middle.full_name,
            'is_published': True, 'is_approved': True,
            'visible_to_customer': True, 'visible_to_systena': True,
            'visible_to_demo': False, 'expires_on': None,
            'ai_review': 'AI_SECRET_DUPLICATE_CHECK',
            'reviewer_comment': 'REVIEW_SECRET_DUPLICATE_CHECK',
            'remand_reason': 'REMAND_SECRET_DUPLICATE_CHECK',
        }
        values.update(overrides)
        return model.objects.create(**values)

    def test_named_route_resolves_to_candidates_view(self):
        match = resolve(reverse('knowledge_duplicate_candidates'))
        self.assertIs(match.func.view_class, views.KnowledgeDuplicateCandidatesView)

    def test_anonymous_regular_and_nonstaff_groups_receive_json_403(self):
        denied = [AnonymousUser(), self.regular]
        for index, name in enumerate((
            views.CUSTOMER_GROUP_NAME, views.DEMO_GROUP_NAME,
            views.SYSTENA_GROUP_NAME, views.REVIEWER_GROUP_NAME,
        )):
            user = get_user_model().objects.create_user(username=f'duplicate-group-{index}')
            group, _ = Group.objects.get_or_create(name=name)
            user.groups.add(group)
            denied.append(user)
        for user in denied:
            with self.subTest(user=str(user)):
                self.payload(self.candidates(user=user), status=403)
                # Authorization precedes validation; invalid input must not expose metadata.
                self.payload(self.request(user=user), status=403)

    def test_staff_and_superuser_can_see_pending_faq_and_tips(self):
        expected = {
            (kind, self.article(model, is_approved=False).management_code)
            for model, kind in self.models_and_kinds
        }
        with patch.object(views, 'FAQ_APPROVAL_ENABLED', True):
            for user in (self.staff, self.superuser):
                with self.subTest(user=user.username):
                    data = self.payload(self.candidates(user=user))
                    self.assertEqual(data['total'], 2)
                    self.assertEqual(
                        {(row['kind'], row['management_code']) for row in data['results']},
                        expected,
                    )

    def test_effective_customer_and_demo_modes_cannot_use_original_admin_flags(self):
        for original in (self.staff, self.superuser):
            for mode in (views.ACCOUNT_VIEW_MODE_CS, views.ACCOUNT_VIEW_MODE_DEMO):
                with self.subTest(user=original.username, mode=mode):
                    user = get_user_model().objects.get(pk=original.pk)
                    user._original_is_staff = user.is_staff
                    user._original_is_superuser = user.is_superuser
                    user._view_mode_override = mode
                    user.is_staff = False
                    user.is_superuser = False
                    self.payload(self.candidates(user=user), status=403)

    def test_missing_malformed_nonpositive_overflow_and_unknown_ids_are_rejected(self):
        deleted = FAQCategory.objects.create(parent_name='削除予定', middle_name='操作')
        deleted_id = deleted.pk
        deleted.delete()
        invalid = (
            {}, {'registered_category': []}, {'registered_category': ['']},
            {'registered_category': ['abc']}, {'registered_category': ['1.5']},
            {'registered_category': ['0']}, {'registered_category': ['-1']},
            {'registered_category': ['2147483648']},
            {'registered_category': ['9' * 100]},
            {'registered_category': [deleted_id]},
            {'registered_category': [self.middle.pk, deleted_id]},
            {'registered_category': [self.middle.pk, 'bad']},
            {'registered_category': [self.middle.pk] * 101},
        )
        for params in invalid:
            with self.subTest(params=params):
                self.payload(self.request(params), status=400)

    def test_one_hundred_inputs_and_largest_existing_32bit_id_are_allowed(self):
        data = self.payload(self.candidates([self.middle.pk] * 100))
        self.assertEqual(data['categories'], [self.middle.full_name])
        categories = [FAQCategory.objects.create(
            parent_name=f'上限カテゴリ{index:03d}', middle_name='操作',
        ) for index in range(100)]
        data = self.payload(self.candidates([item.pk for item in categories]))
        self.assertEqual(len(data['categories']), 100)
        boundary = FAQCategory.objects.create(pk=2147483647, parent_name='最大ID')
        self.assertEqual(
            self.payload(self.candidates([boundary.pk]))['categories'], [boundary.full_name],
        )

    def test_page_validation_and_empty_result_envelope(self):
        for value in ('', 'bad', '1.5', '0', '-1', '2', '999999999999999999999'):
            with self.subTest(page=value):
                self.payload(self.candidates(page=value), status=400)
        expected = {
            'categories': [self.middle.full_name], 'results': [],
            'total': 0, 'page': 1, 'pages': 1,
        }
        self.assertEqual(self.payload(self.candidates()), expected)
        self.assertEqual(self.payload(self.candidates(page='1')), expected)

    def test_exact_stripped_tokens_share_old_two_level_and_middle_full_names(self):
        self.assertEqual(self.old_two_level.full_name, self.middle.full_name)
        for model, kind in self.models_and_kinds:
            exact = self.article(model, category=f'  {self.middle.full_name}  , 無関係/操作 ')
            child = self.article(model, category=self.child.full_name)
            self.article(model, category='PC/設定追加')
            self.article(model, category='別PC/設定')
            self.article(model, category='PC')
            self.article(model, category='')
            for selected in (self.old_two_level, self.middle):
                with self.subTest(kind=kind, selected=selected.pk):
                    rows = [row for row in self.payload(
                        self.candidates([selected.pk]),
                    )['results'] if row['kind'] == kind]
                    self.assertEqual([row['management_code'] for row in rows], [exact.management_code])
                    self.assertEqual(rows[0]['categories'], [selected.full_name])
            rows = [row for row in self.payload(
                self.candidates([self.child.pk]),
            )['results'] if row['kind'] == kind]
            self.assertEqual([row['management_code'] for row in rows], [child.management_code])

    def test_multi_selection_is_or_and_repeated_tokens_never_duplicate_an_item(self):
        expected = set()
        for model, kind in self.models_and_kinds:
            for category in (
                self.middle.full_name, self.child.full_name,
                f'{self.child.full_name}, {self.middle.full_name}, {self.child.full_name}',
            ):
                item = self.article(model, category=category)
                expected.add((kind, item.management_code))
            self.article(model, category=self.other.full_name)
        data = self.payload(self.candidates([
            self.child.pk, self.middle.pk, self.old_two_level.pk, self.middle.pk,
        ]))
        self.assertEqual(data['total'], 6)
        self.assertEqual(len(data['results']), 6)
        self.assertEqual(
            {(row['kind'], row['management_code']) for row in data['results']}, expected,
        )
        for row in data['results']:
            self.assertEqual(row['categories'], sorted(set(row['categories'])))
            self.assertTrue(set(row['categories']) <= {self.middle.full_name, self.child.full_name})
        both = [row for row in data['results'] if len(row['categories']) == 2]
        self.assertEqual(len(both), 2)

    def test_visibility_expiry_and_publication_for_both_models(self):
        states = {
            'customer_only': {'visible_to_systena': False},
            'systena_only': {'visible_to_customer': False},
            'pending': {'is_approved': False},
            'expires_today': {'expires_on': self.today},
            'expires_future': {'expires_on': self.today + timedelta(days=1)},
            'no_expiry': {'expires_on': None},
            'expired': {'expires_on': self.today - timedelta(days=1)},
            'unpublished': {'is_published': False},
            'hidden': {'visible_to_customer': False, 'visible_to_systena': False},
            'demo_only': {
                'visible_to_customer': False, 'visible_to_systena': False, 'visible_to_demo': True,
            },
        }
        expected = set()
        for model, kind in self.models_and_kinds:
            for name, overrides in states.items():
                item = self.article(model, title=f'{kind} {name}', **overrides)
                if name not in {'expired', 'unpublished', 'hidden', 'demo_only'}:
                    expected.add((kind, item.management_code))
        for user in (self.staff, self.superuser):
            with self.subTest(user=user.username):
                rows = self.payload(self.candidates(user=user))['results']
                self.assertEqual({(row['kind'], row['management_code']) for row in rows}, expected)

    def test_pagination_twenty_stable_ties_and_no_missing_or_duplicate_results(self):
        timestamp = timezone.now().replace(microsecond=0)
        expected = []
        for index in range(45):
            model, kind = self.models_and_kinds[index % 2]
            item = self.article(model, title=f'順序{index:02d}')
            # Deliberate cross-kind and within-kind ties exercise both tie breakers.
            updated = timestamp + timedelta(minutes=index % 3)
            model.objects.filter(pk=item.pk).update(updated_at=updated)
            expected.append((updated, kind, item.management_code))
        expected.sort(reverse=True)
        all_rows = []
        for page, size in ((1, 20), (2, 20), (3, 5)):
            data = self.payload(self.candidates(page=page))
            self.assertEqual((data['total'], data['page'], data['pages']), (45, page, 3))
            self.assertEqual(len(data['results']), size)
            self.assertEqual(data, self.payload(self.candidates(page=page)))
            all_rows.extend(data['results'])
        actual = [(row['kind'], row['management_code']) for row in all_rows]
        self.assertEqual(actual, [(kind, code) for _, kind, code in expected])
        self.assertEqual(len(set(actual)), 45)
        self.payload(self.candidates(page=4), status=400)

    def test_response_contains_only_metadata_and_reversible_detail_links(self):
        items = {}
        for model, kind in self.models_and_kinds:
            extra = {'summary': 'SUMMARY_SECRET_DUPLICATE_CHECK'} if kind == 'FAQ' else {}
            item = self.article(model, **extra)
            items[kind] = item
        response = self.candidates()
        data = self.payload(response)
        self.assertEqual(set(data), {'categories', 'results', 'total', 'page', 'pages'})
        self.assertEqual(len(data['results']), 2)
        for row in data['results']:
            self.assertEqual(set(row), {
                'kind', 'title', 'management_code', 'categories', 'url', 'updated_at',
            })
            item = items[row['kind']]
            url_name = 'article_detail' if row['kind'] == 'FAQ' else 'tip_detail'
            self.assertEqual(row['url'], reverse(url_name, kwargs={'management_code': item.management_code}))
            self.assertEqual(row['title'], item.title)
            self.assertEqual(row['categories'], [self.middle.full_name])
            self.assertEqual(
                timezone.datetime.fromisoformat(row['updated_at'].replace('Z', '+00:00')),
                item.updated_at.replace(microsecond=(item.updated_at.microsecond // 1000) * 1000),
            )
        for secret in ('BODY', 'SUMMARY', 'AI', 'REVIEW', 'REMAND'):
            self.assertNotIn(f'{secret}_SECRET_DUPLICATE_CHECK', response.content.decode())


class KnowledgeDuplicateFormIntegrationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='duplicate-form-admin', email='duplicate@example.test', password='test-password',
        )
        self.client.force_login(self.user)

    def test_only_new_faq_and_tips_forms_include_modal_and_preview_gate(self):
        specs = (
            (KnowledgeArticle, 'article_create', 'article_edit', 'articleMainForm', 'Faq', 'showFaqPreview'),
            (TipsArticle, 'tip_create', 'tip_edit', 'tipsMainForm', 'Tips', 'showTipsPreview'),
        )
        for model, create_url, edit_url, form_id, kind, preview in specs:
            with self.subTest(kind=kind):
                response = self.client.get(reverse(create_url))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="knowledgeDuplicateModal"', count=1)
                self.assertContains(response, 'id="knowledgeDuplicateContinue"', count=1)
                self.assertContains(response, 'data-knowledge-duplicate-check', count=2)
                self.assertContains(response, 'type="button" class="btn btn-outline-primary btn-sm" data-knowledge-duplicate-check')
                self.assertLess(
                    response.content.decode().index('>重複チェック'),
                    response.content.decode().index('id="form-section-os"'),
                )
                self.assertContains(response, reverse('knowledge_duplicate_candidates'))
                html = response.content.decode()
                self.assertRegex(html, r'window\.confirmKnowledgeDuplicates\s*=\s*function')
                self.assertRegex(html, re.escape(
                    f'window.confirmKnowledgeDuplicates({form_id}, {preview})',
                ))
                # Final preview submission must return before it opens the duplicate modal again.
                self.assertRegex(html, (
                    rf'if\s*\(is{kind}PreviewConfirmed\)\s*\{{\s*return;\s*\}}'
                    r'\s*e\.preventDefault\(\);\s*if\s*\(typeof window\.confirmKnowledgeDuplicates'
                ))
                item = model.objects.create(
                    title='編集対象', body='本文', created_by=self.user,
                    is_published=True, is_approved=True, expires_on=None,
                )
                response = self.client.get(reverse(edit_url, args=[item.pk]))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'id="knowledgeDuplicateModal"')
                self.assertNotContains(response, 'id="knowledgeDuplicateContinue"')
                self.assertNotContains(response, 'data-knowledge-duplicate-check')
                self.assertNotRegex(
                    response.content.decode(), r'window\.confirmKnowledgeDuplicates\s*=\s*function',
                )