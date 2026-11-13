"""Homepage posting rows share SummaryView's policy, not its full context work."""

from datetime import date, datetime, time, timedelta
from itertools import product
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.db import connection
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from . import views
from .middleware import LoginRequiredExceptAssetsMiddleware
from .models import ArticleGood, KnowledgeArticle, TipsArticle, TipsGood, UserProfile


class HomeMemberPostsTests(TestCase):
    today = date(2026, 11, 12)
    posting_keys = ('name', 'faq_post_count', 'tips_post_count', 'post_count_total')

    @classmethod
    def member(cls, username, name, uid, *, excluded=False, contributor=True, profile=True):
        user = get_user_model().objects.create_user(username=username)
        if contributor:
            user.groups.add(cls.contributors)
        if profile:
            UserProfile.objects.create(
                user=user, display_name=name, uid=uid, company_name='テスト',
                exclude_from_analysis=excluded,
            )
        return user

    @classmethod
    def setUpTestData(cls):
        cls.contributors, _ = Group.objects.get_or_create(name=views.CONTRIBUTOR_GROUP_NAME)
        cls.staff = get_user_model().objects.create_user(username='home-analysis-staff', is_staff=True)
        cls.superuser = get_user_model().objects.create_user(
            username='home-analysis-super', is_superuser=True, is_staff=False,
        )
        cls.zero = cls.member('zero-account', 'ゼロ', '000010')
        cls.primary = cls.member('primary-account', '同名', '000020')
        cls.duplicate = cls.member('duplicate-account', '同名', '000021')
        cls.alias = cls.member('alias-account', '別名', '000030')
        cls.excluded = cls.member('excluded-account', '除外', '000040', excluded=True)
        cls.admin = cls.member('admin-account', 'AdMiN', '000050')
        cls.no_profile = cls.member('no-profile', '', None, profile=False)
        cls.outsider = cls.member('outsider', 'グループ外', '000060', contributor=False)

        for model in (KnowledgeArticle, TipsArticle):
            # Every publication/approval/expiry combination, including demo-only hidden rows.
            for customer, systena, published, approved, expiry in product(
                (False, True), (False, True), (False, True), (False, True),
                (None, cls.today - timedelta(days=1), cls.today),
            ):
                cls.article(
                    model, cls.primary, cls.primary.username,
                    visible_to_customer=customer, visible_to_systena=systena,
                    visible_to_demo=True, is_published=published, is_approved=approved,
                    expires_on=expiry,
                )
            for user, saved in (
                (cls.primary, ''), (cls.primary, cls.primary.username),
                (cls.primary, ' 別名 '), (None, ' 同名 '),
                (cls.duplicate, ''), (cls.outsider, '同名'),
                (cls.primary, '以前の表示名'), (None, cls.primary.username),
                (cls.excluded, ''), (cls.no_profile, ''), (cls.outsider, ''),
                (cls.admin, ''), (None, ''),
            ):
                cls.article(model, user, saved)

    @classmethod
    def article(cls, model, user, saved='', *, day=None, **kwargs):
        values = {
            'title': '集計テスト', 'body': '本文', 'category': 'PC/設定',
            'created_by': user, 'created_by_name': saved,
            'visible_to_customer': True, 'visible_to_systena': False,
            'is_published': True, 'is_approved': True,
        }
        values.update(kwargs)
        article = model.objects.create(**values)
        created_at = timezone.make_aware(datetime.combine(day or date(2026, 10, 15), time(12)))
        model.objects.filter(pk=article.pk).update(created_at=created_at)
        return article

    def setUp(self):
        self.factory = RequestFactory()
        self.enterContext(patch.object(views.timezone, 'localdate', return_value=self.today))

    def context(self, view_class=views.HomeView, user=None, params=None):
        request = self.factory.get(reverse('home' if view_class is views.HomeView else 'summary'), params or {})
        request.user = user if user is not None else self.staff
        request.session = {}
        view = view_class()
        view.setup(request)
        return view.get_context_data(), request

    def posting_rows(self, rows):
        return [{key: row[key] for key in self.posting_keys} for row in rows]

    def test_exact_summary_parity_and_explicit_counts_for_all_article_states(self):
        expected = [
            dict(zip(self.posting_keys, ('ゼロ', 0, 0, 0))),
            dict(zip(self.posting_keys, ('同名', 41, 41, 82))),
            dict(zip(self.posting_keys, ('別名', 1, 1, 2))),
        ]
        for approval_enabled in (False, True):
            with self.subTest(approval_enabled=approval_enabled), patch.object(
                views, 'FAQ_APPROVAL_ENABLED', approval_enabled
            ):
                home, _ = self.context()
                summary, _ = self.context(views.SummaryView)
                self.assertEqual(home['home_member_posts'], expected)
                self.assertEqual(home['home_member_posts'], self.posting_rows(summary['member_summaries']))
                totals = home['home_member_post_totals']
                self.assertEqual(totals, {'faq_post_count': 42, 'tips_post_count': 42, 'post_count_total': 84})
                for key, value in totals.items():
                    self.assertEqual(value, summary['summary_totals'][key])
                shared = summary['member_summaries'][1]
                self.assertEqual(shared['member_id'], self.primary.pk)
                self.assertEqual(shared['management_uid'], '000020')
        print('Home/Summary fixture counts: FAQ=42, Tips=42, total=84; rows=3 (zero included).')

    def test_home_ignores_period_sort_and_include_hidden_query_parameters(self):
        baseline, _ = self.context()
        actual, _ = self.context(params={'period': 'current', 'sort_by': 'post_count', 'include_hidden': '1'})
        self.assertEqual(actual['home_member_posts'], baseline['home_member_posts'])
        self.assertEqual(actual['home_member_post_totals'], baseline['home_member_post_totals'])

    def test_summary_include_hidden_preserves_profile_and_name_exclusion_rules(self):
        context, _ = self.context(views.SummaryView, params={'include_hidden': '1'})
        rows = {item['name']: item for item in context['member_summaries']}
        self.assertEqual(set(rows), {'ゼロ', '同名', '別名', '除外', 'no-profile'})
        self.assertEqual(rows['除外']['post_count_total'], 2)
        self.assertEqual(rows['no-profile']['post_count_total'], 2)
        self.assertTrue(context['include_hidden'])
        default, _ = self.context(views.SummaryView)
        self.assertFalse(default['include_hidden'])
        self.assertEqual(len(default['member_summaries']), 3)

    def test_home_never_invokes_summary_context_or_dispatch(self):
        with patch.object(views.SummaryView, 'get_context_data', side_effect=AssertionError('full summary')), \
             patch.object(views.SummaryView, 'dispatch', side_effect=AssertionError('summary dispatch')):
            context, _ = self.context()
        self.assertEqual(context['home_member_post_totals']['post_count_total'], 84)
        self.assertFalse(any(key.startswith(('internal_', 'customer_', 'chart_')) for key in context))
        self.assertEqual(set(context['home_member_posts'][0]), set(self.posting_keys))

    def test_effective_staff_and_independent_superuser_get_rows(self):
        for user in (self.staff, self.superuser):
            with self.subTest(user=user.username):
                summary = views.SummaryView()
                summary.request = self.factory.get(reverse('summary'))
                summary.request.user = user
                self.assertTrue(summary.test_func())
                context, _ = self.context(user=user)
                self.assertEqual(context['home_member_post_totals']['post_count_total'], 84)

    def assert_no_analysis(self, user):
        with patch.object(views.SummaryView, 'member_post_counts', side_effect=AssertionError('private aggregation')):
            context, request = self.context(user=user, params={'include_hidden': '1'})
        self.assertNotIn('home_member_posts', context)
        self.assertNotIn('home_member_post_totals', context)
        self.assertNotIn('home_member_post_periods', context)
        self.assertNotIn('member_summaries', context)
        html = render_to_string(views.HomeView.template_name, context, request=request)
        self.assertNotIn('id="home-member-posts"', html)
        self.assertNotIn('メンバーの投稿数', html)
        self.assertNotIn('id="home-posting-status-title"', html)
        self.assertNotIn('home-member-post-data', html)
        self.assertNotIn('chart.js@', html)

    def test_nonstaff_roles_and_anonymous_never_receive_or_compute_aggregates(self):
        self.assert_no_analysis(AnonymousUser())
        for group_name in (
            views.CUSTOMER_GROUP_NAME, views.DEMO_GROUP_NAME, views.SYSTENA_GROUP_NAME,
            views.REVIEWER_GROUP_NAME, views.ADMIN_GROUP_NAME, views.CONTRIBUTOR_GROUP_NAME,
        ):
            with self.subTest(group=group_name):
                user = get_user_model().objects.create_user(username=f'role-{group_name}')
                group, _ = Group.objects.get_or_create(name=group_name)
                user.groups.add(group)
                self.assert_no_analysis(user)

    def test_forced_demo_and_cs_modes_do_not_expose_raw_aggregates(self):
        middleware = LoginRequiredExceptAssetsMiddleware(lambda request: None)
        for original in (self.staff, self.superuser):
            for mode in (views.ACCOUNT_VIEW_MODE_DEMO, views.ACCOUNT_VIEW_MODE_CS):
                with self.subTest(user=original.username, mode=mode):
                    user = get_user_model().objects.get(pk=original.pk)
                    request = self.factory.get(reverse('home'))
                    request.user = user
                    request.session = {views.ACCOUNT_VIEW_MODE_SESSION_KEY: mode}
                    with patch.object(views, 'should_force_password_change', return_value=False):
                        middleware.process_view(request, views.HomeView.as_view(), (), {})
                    self.assertFalse(user.is_staff or user.is_superuser)
                    self.assert_no_analysis(user)
                    # Defense in depth even if a caller has not cleared effective flags yet.
                    user.is_staff = original.is_staff
                    user.is_superuser = original.is_superuser
                    self.assert_no_analysis(user)

    def test_posting_helper_is_three_queries_without_body_or_good_aggregates(self):
        with CaptureQueriesContext(connection) as captured:
            rows, totals = views.SummaryView.member_post_counts()
        self.assertEqual(len(captured), 3)
        self.assertEqual(totals['post_count_total'], 84)
        self.assertEqual(len(rows), 3)
        for query in captured.captured_queries[1:]:
            sql = query['sql'].lower()
            self.assertNotIn('"body"', sql)
            self.assertNotIn('count(', sql)
            self.assertNotIn('good', sql)
        for index in range(5):
            user = self.member(f'query-member-{index}', f'追加{index}', f'{100 + index:06}')
            for model in (KnowledgeArticle, TipsArticle):
                self.article(model, user)
        with self.assertNumQueries(3):
            _, grown = views.SummaryView.member_post_counts()
        self.assertEqual(grown['post_count_total'], 94)
        print('Homepage posting helper: 3 queries, unchanged after adding 5 authors / 10 posts.')

    def test_analysis_is_after_latest_information_with_or_without_latest_rows(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                if empty:
                    KnowledgeArticle.objects.update(is_published=False)
                    TipsArticle.objects.update(is_published=False)
                context, request = self.context()
                html = render_to_string(views.HomeView.template_name, context, request=request)
                marker = '最新情報はありません。' if empty else 'Tips 最新情報'
                self.assertLess(html.index(marker), html.index('id="home-member-posts"'))
                heading = '<h2 class="h5 mb-0 fw-bold" id="home-posting-status-title">投稿状況</h2>'
                self.assertEqual(html.count(heading), 1)
                self.assertLess(html.index(marker), html.index(heading))
                self.assertLess(html.index(heading), html.index('id="home-member-posts"'))
                self.assertIn('全期間', html)
                self.assertIn('メンバーの投稿数', html)
                self.assertEqual(html.count('<canvas '), 1)
                self.assertEqual(html.count('id="home-member-posts"'), 1)
                self.assertEqual(html.count('data-home-post-period='), 3)
                self.assertIn('type="application/json"', html)
                section = html.split('id="home-member-posts"', 1)[1].split('</section>', 1)[0]
                self.assertIn('table-responsive', section)
                self.assertIn('<tfoot', section)
                self.assertIn('ゼロ', section)
                self.assertIn('>84</td>', section)
                self.assertNotIn('承認', section)
                self.assertEqual(context['home_member_post_totals']['post_count_total'], 84)

    def test_home_periods_match_summary_and_keep_default_member_order(self):
        self.reset_period_articles()
        for enabled in (False, True):
            with patch.object(views, 'FAQ_APPROVAL_ENABLED', enabled):
                home, _ = self.context()
                for period in ('all', 'current', 'previous'):
                    with self.subTest(period=period, approval_enabled=enabled):
                        summary, _ = self.context(views.SummaryView, params={'period': period})
                        data = home['home_member_post_periods'][period]
                        self.assertEqual(data['rows'], self.posting_rows(summary['member_summaries']))
                        self.assertEqual(data['target_chart'], summary['posting_target_chart'])
                        for key, value in data['totals'].items():
                            self.assertEqual(value, summary['summary_totals'][key])

    def test_local_month_boundaries_and_deleted_articles_match_summary(self):
        from zoneinfo import ZoneInfo

        KnowledgeArticle.objects.all().delete()
        TipsArticle.objects.all().delete()
        with timezone.override(ZoneInfo('Asia/Tokyo')):
            for model in (KnowledgeArticle, TipsArticle):
                for timestamp in (
                    '2026-09-30T14:59:59+00:00', '2026-09-30T15:00:00+00:00',
                    '2026-10-31T14:59:59+00:00', '2026-10-31T15:00:00+00:00',
                    '2026-11-12T14:59:59+00:00', '2026-11-12T15:00:00+00:00',
                ):
                    item = self.article(model, self.primary)
                    model.objects.filter(pk=item.pk).update(created_at=datetime.fromisoformat(timestamp))
                deleted = self.article(model, self.primary, day=self.today)
                deleted.delete()
            home, _ = self.context()
            for period, count in (('all', 6), ('current', 2), ('previous', 2)):
                summary, _ = self.context(views.SummaryView, params={'period': period})
                data = home['home_member_post_periods'][period]
                self.assertEqual(data['rows'], self.posting_rows(summary['member_summaries']))
                self.assertEqual(data['totals']['faq_post_count'], count)
                self.assertEqual(data['totals']['tips_post_count'], count)

    def test_unsafe_member_name_is_escaped_in_table_and_json_script(self):
        name = '</script><img src=x onerror="window.postXss=true">'
        UserProfile.objects.filter(user=self.zero).update(display_name=name)
        context, request = self.context()
        html = render_to_string(views.HomeView.template_name, context, request=request)
        self.assertNotIn(name, html)
        self.assertIn('\\u003C/script\\u003E', html)
        self.assertIn('&lt;/script&gt;&lt;img', html)

    def test_no_members_still_renders_empty_state_and_zero_footer(self):
        UserProfile.objects.update(exclude_from_analysis=True)
        context, request = self.context()
        self.assertEqual(context['home_member_posts'], [])
        self.assertEqual(context['home_member_post_totals']['post_count_total'], 0)
        html = render_to_string(views.HomeView.template_name, context, request=request)
        self.assertIn('id="home-member-posts"', html)
        self.assertIn('集計対象のメンバーはいません。', html)

    def reset_period_articles(self):
        KnowledgeArticle.objects.all().delete()
        TipsArticle.objects.all().delete()
        self.article(KnowledgeArticle, self.primary, day=date(2026, 9, 30), answer_view_count=2)
        faq = self.article(KnowledgeArticle, self.primary, day=date(2026, 10, 31), answer_view_count=3)
        tip = self.article(TipsArticle, self.alias, day=date(2026, 10, 1), view_count=5)
        self.article(KnowledgeArticle, self.primary, day=date(2026, 11, 1), is_approved=False, answer_view_count=7)
        self.article(TipsArticle, self.primary, day=self.today, view_count=11)
        # All-time counts historically include future-created records, current month does not.
        self.article(TipsArticle, self.primary, day=date(2026, 11, 13), view_count=13)
        ArticleGood.objects.create(article=faq, user=self.staff)
        TipsGood.objects.create(tip=tip, user=self.staff)

    def test_summary_periods_preserve_monthly_approval_good_read_and_context_outputs(self):
        self.reset_period_articles()
        expected = {
            'all': (3, 3, 5, 2, 41, ['2026-11', '2026-10', '2026-09']),
            'current': (1, 1, 1, 0, 18, ['2026-11']),
            'previous': (1, 1, 2, 2, 8, ['2026-10']),
        }
        for period, (faq, tips, approved, good, read, months) in expected.items():
            with self.subTest(period=period):
                context, _ = self.context(views.SummaryView, params={'period': period})
                totals = context['summary_totals']
                self.assertEqual(context['selected_period'], period)
                self.assertEqual(context['sort_by'], 'no')
                self.assertEqual(totals['faq_post_count'], faq)
                self.assertEqual(totals['tips_post_count'], tips)
                self.assertEqual(totals['post_count_total'], faq + tips)
                self.assertEqual(totals['approved_count_total'], approved)
                self.assertEqual(totals['good_count_total'], good)
                self.assertEqual(totals['view_count_total'], read)
                self.assertEqual(totals['current_month_faq_post_count'], 1)
                self.assertEqual(totals['current_month_tips_post_count'], 1)
                self.assertEqual(totals['current_month_post_count_total'], 2)
                self.assertEqual(totals['current_month_approved_count_total'], 1)
                self.assertEqual(totals['current_month_faq_approved_count'], 0)
                self.assertEqual(totals['current_month_tips_approved_count'], 1)
                self.assertEqual([row['month'] for row in totals['monthly_totals']], months)
                for row in totals['monthly_totals']:
                    self.assertEqual(row['post_count_total'], row['faq_post_count'] + row['tips_post_count'])
                    self.assertEqual(row['post_count_total'], sum(
                        month['post_count_total']
                        for member in context['member_summaries'] for month in member['monthly_totals']
                        if month['month'] == row['month']
                    ))
                rows = context['member_summaries']
                self.assertEqual(context['chart_labels'], [row['name'] for row in rows])
                self.assertEqual(context['chart_post_counts'], [row['post_count_total'] for row in rows])
                self.assertEqual(context['chart_faq_post_counts'], [row['faq_post_count'] for row in rows])
                self.assertEqual(context['chart_tips_post_counts'], [row['tips_post_count'] for row in rows])
                self.assertEqual(context['internal_summary_totals']['post_count'], 6)
                self.assertEqual(context['customer_faq_good_total'], 1)
                self.assertEqual(context['customer_tips_good_total'], 1)
        print('Summary periods (FAQ/Tips): all=3/3, current=1/1, previous=1/1; current-month total=2.')

    def test_summary_sort_order_ties_and_invalid_parameters_remain_unchanged(self):
        self.reset_period_articles()
        # Tie on posting total: UID remains the tie-breaker, with numeric UID ordering.
        self.member('numeric-uid', '先頭', '2')
        self.member('nonnumeric-uid', '末尾', 'legacy')
        self.member('blank-uid', '空UID', None)
        for sort_by, expected in (
            ('no', ['先頭', 'ゼロ', '同名', '別名', '末尾', '空UID']),
            ('post_count', ['同名', '別名', '先頭', 'ゼロ', '末尾', '空UID']),
        ):
            context, _ = self.context(views.SummaryView, params={'sort_by': sort_by})
            self.assertEqual([row['name'] for row in context['member_summaries']], expected)
        self.article(TipsArticle, self.alias)
        context, _ = self.context(views.SummaryView, params={'period': 'previous', 'sort_by': 'post_count'})
        self.assertEqual([row['name'] for row in context['member_summaries']][:2], ['別名', '同名'])
        invalid, _ = self.context(views.SummaryView, params={'period': 'unknown', 'sort_by': 'unknown'})
        self.assertEqual(invalid['selected_period'], 'all')
        self.assertEqual(invalid['sort_by'], 'no')
        home, _ = self.context()
        self.assertEqual(home['home_member_posts'], self.posting_rows(invalid['member_summaries']))