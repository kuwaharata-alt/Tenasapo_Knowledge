"""Posting targets use an isolated test database; no mail, Chat or Google calls."""

from datetime import date
from importlib import import_module
import json
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import Client, TestCase
from django.urls import reverse

from . import views
from .models import KnowledgeArticle, PostingQuota, TipsArticle, UserProfile
from .posting_quotas import posting_targets, quota_progress


class PostingTargetCalculationTests(TestCase):
    def quota(self, month, faq, tips, alpha=0):
        return PostingQuota.objects.create(effective_month=month, faq_count=faq, tips_count=tips, alpha_count=alpha)

    def test_unset_and_future_only_are_not_zero(self):
        self.assertIsNone(posting_targets(date(2026, 11, 1), 'all')['chart']['value'])
        self.quota(date(2027, 1, 1), 8, 9)
        targets = posting_targets(date(2026, 11, 1), 'all')
        self.assertIsNone(targets['monthly'])
        self.assertIsNone(targets['cumulative'])
        self.assertEqual(targets['missing_months'], [f'2026-{month:02}' for month in range(6, 12)])

    def test_exact_months_across_year_and_future_excluded(self):
        # Deliberately insert out of order. June-Dec: 3*2 + 5*5; Jan-Feb: 7*2.
        self.quota(date(2027, 1, 1), 6, 1)
        self.quota(date(2027, 2, 1), 6, 1, 2)
        for month in range(8, 13):
            self.quota(date(2026, month, 1), 4, 1)
        self.quota(date(2026, 6, 1), 2, 1)
        self.quota(date(2026, 7, 1), 2, 1)
        self.quota(date(2027, 3, 1), 999, 999)
        targets = posting_targets(date(2027, 2, 28), 'all')
        self.assertEqual(targets['target_month'], date(2027, 2, 1))
        self.assertEqual(targets['monthly'], {'faq': 6, 'tips': 1, 'alpha': 2, 'total': 9})
        self.assertEqual(targets['cumulative'], {'faq': 36, 'tips': 9, 'alpha': 2, 'total': 47})
        self.assertEqual(targets['chart']['value'], 47)
        self.assertEqual(targets['missing_months'], [])
        for period in ('current', 'previous'):
            self.assertEqual(posting_targets(date(2027, 2, 1), period)['chart']['value'], 9)

    def test_incomplete_cumulative_never_exposes_partial_target_but_monthly_works(self):
        self.quota(date(2026, 8, 1), 3, 4)
        self.quota(date(2026, 11, 1), 3, 4)
        targets = posting_targets(date(2026, 11, 1), 'all')
        self.assertEqual(targets['monthly']['total'], 7)
        self.assertEqual(targets['missing_months'], ['2026-06', '2026-07', '2026-09', '2026-10'])
        self.assertIsNone(targets['cumulative'])
        self.assertIsNone(targets['chart']['value'])
        self.assertEqual(posting_targets(date(2026, 11, 1), 'current')['chart']['value'], 7)

    def test_zero_is_explicit_and_missing_month_does_not_inherit(self):
        self.quota(date(2026, 6, 1), 0, 0)
        targets = posting_targets(date(2026, 7, 1), 'all')
        self.assertIsNone(targets['monthly'])
        self.assertIsNone(targets['cumulative'])
        self.assertEqual(targets['missing_months'], ['2026-07'])
        self.quota(date(2026, 7, 1), 0, 0)
        targets = posting_targets(date(2026, 7, 1), 'all')
        self.assertEqual(targets['cumulative'], {'faq': 0, 'tips': 0, 'alpha': 0, 'total': 0})
        self.assertEqual(targets['chart']['value'], 0)
        self.quota(date(2026, 8, 1), 2, 3)
        self.quota(date(2026, 9, 1), 0, 0)
        targets = posting_targets(date(2026, 9, 1), 'all')
        self.assertEqual(targets['monthly']['total'], 0)
        self.assertEqual(targets['cumulative']['total'], 5)

    def test_initial_range_boundaries_and_month_after_range(self):
        for year in range(2026, 2031):
            for month in range(6 if year == 2026 else 1, 13):
                self.quota(date(year, month, 1), 1, 1)
        self.assertEqual(PostingQuota.objects.count(), 55)
        self.assertEqual(posting_targets(date(2026, 6, 30), 'all')['cumulative']['total'], 2)
        self.assertEqual(posting_targets(date(2030, 12, 31), 'all')['cumulative'],
                         {'faq': 55, 'tips': 55, 'alpha': 0, 'total': 110})
        targets = posting_targets(date(2031, 1, 1), 'current')
        self.assertIsNone(targets['monthly'])
        self.assertEqual(targets['missing_months'], ['2031-01'])

    def test_target_before_start_is_safe(self):
        self.quota(date(2026, 6, 1), 3, 4)
        for period in ('all', 'current', 'previous'):
            targets = posting_targets(date(2026, 5, 1), period)
            self.assertTrue(targets['before_start'])
            self.assertIsNone(targets['monthly'])
            self.assertIsNone(targets['chart']['value'])
            self.assertEqual(targets['missing_months'], [])

    def test_model_and_database_constraints_and_migration(self):
        for month, faq, tips in ((date(2026, 5, 1), 0, 0), (date(2026, 6, 2), 0, 0),
                                 (date(2026, 6, 1), -1, 0), (date(2026, 6, 1), 0, -1)):
            with self.subTest(month=month, faq=faq, tips=tips):
                with self.assertRaises(ValidationError):
                    PostingQuota(effective_month=month, faq_count=faq, tips_count=tips).full_clean()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.quota(month, faq, tips)
        self.quota(date(2026, 6, 1), 0, 0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.quota(date(2026, 6, 1), 1, 1)
        loader = MigrationLoader(None)
        migration = loader.disk_migrations[('tenasapo_knowledge', '0078_posting_quota')]
        self.assertIn(('tenasapo_knowledge', '0077_q_and_a_display_labels'), migration.dependencies)

    def test_alpha_model_and_database_constraints(self):
        quota = PostingQuota(effective_month=date(2026, 6, 1), faq_count=1, tips_count=1)
        self.assertEqual(quota.alpha_count, 0)
        quota.alpha_count = -1
        with self.assertRaises(ValidationError):
            quota.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            quota.save()


class QuotaProgressTests(TestCase):
    def test_independent_minima_and_common_bucket_without_double_credit(self):
        target = {'faq': 1, 'tips': 1, 'alpha': 1, 'total': 3}
        for faq, tips, achieved, filled, faq_missing, tips_missing in (
            (2, 1, True, 1, 0, 0), (1, 2, True, 1, 0, 0),
            (3, 0, False, 1, 0, 1), (0, 3, False, 1, 1, 0),
            (1, 1, False, 0, 0, 0), (0, 0, False, 0, 1, 1),
            (50, 50, True, 1, 0, 0),
        ):
            with self.subTest(faq=faq, tips=tips):
                progress = quota_progress(faq, tips, target)
                self.assertEqual(progress['achieved'], achieved)
                self.assertEqual(progress['alpha_fulfilled'], filled)
                self.assertEqual(progress['alpha_missing'], 1 - filled)
                self.assertEqual(progress['faq_missing'], faq_missing)
                self.assertEqual(progress['tips_missing'], tips_missing)
        self.assertIsNone(quota_progress(100, 100, None))
        self.assertTrue(quota_progress(0, 0, {'faq': 0, 'tips': 0, 'alpha': 0, 'total': 0})['achieved'])
        self.assertFalse(quota_progress(1, 0, {'faq': 1, 'tips': 1, 'alpha': 0, 'total': 2})['achieved'])
        progress = quota_progress(2, 2, {'faq': 1, 'tips': 1, 'alpha': 3, 'total': 5})
        self.assertEqual(progress['alpha_fulfilled'], 2)
        self.assertFalse(progress['achieved'])


class QuotaMaterializationTests(TestCase):
    def materialize(self):
        loader = MigrationLoader(None)
        key = ('tenasapo_knowledge', '0079_monthly_posting_quota_alpha')
        self.assertIn(('tenasapo_knowledge', '0078_posting_quota'), loader.disk_migrations[key].dependencies)
        historical_apps = loader.project_state([key]).apps
        migration = import_module('.migrations.0079_monthly_posting_quota_alpha', 'tenasapo_knowledge')
        migration.materialize_months(historical_apps, Mock(connection=Mock(alias='default')))

    def test_empty_history_does_not_seed(self):
        self.materialize()
        self.assertFalse(PostingQuota.objects.exists())

    def test_historical_next_change_is_materialized_without_overwriting(self):
        user = get_user_model().objects.create_user('quota-history-editor')
        first = PostingQuota.objects.create(effective_month=date(2026, 8, 1), faq_count=3, tips_count=2, updated_by=user)
        changed = PostingQuota.objects.create(effective_month=date(2027, 1, 1), faq_count=0, tips_count=4)
        last = PostingQuota.objects.create(effective_month=date(2030, 12, 1), faq_count=1, tips_count=1)
        outside = PostingQuota.objects.create(effective_month=date(2031, 2, 1), faq_count=8, tips_count=9)
        before = list(PostingQuota.objects.values())
        self.materialize()
        self.assertFalse(PostingQuota.objects.filter(effective_month__lt=first.effective_month).exists())
        self.assertEqual(PostingQuota.objects.count(), 54)  # 53 range months plus untouched future record.
        inherited = PostingQuota.objects.get(effective_month=date(2026, 12, 1))
        self.assertEqual((inherited.faq_count, inherited.tips_count, inherited.alpha_count), (3, 2, 0))
        self.assertEqual((inherited.updated_by_id, inherited.updated_at), (user.pk, first.updated_at))
        self.assertEqual(PostingQuota.objects.get(effective_month=date(2027, 2, 1)).tips_count, changed.tips_count)
        self.assertEqual(PostingQuota.objects.get(pk=last.pk).faq_count, 1)
        self.assertFalse(PostingQuota.objects.filter(effective_month=date(2031, 1, 1)).exists())
        self.assertEqual(list(PostingQuota.objects.filter(pk__in=[row['id'] for row in before]).values()), before)
        self.materialize()
        self.assertEqual(PostingQuota.objects.count(), 54)
        self.assertEqual(PostingQuota.objects.get(pk=outside.pk).tips_count, 9)

    def test_june_history_materializes_all_55_months(self):
        PostingQuota.objects.create(effective_month=date(2026, 6, 1), faq_count=3, tips_count=2)
        self.materialize()
        self.assertEqual(PostingQuota.objects.count(), 55)
        self.assertEqual(PostingQuota.objects.get(effective_month=date(2030, 12, 1)).faq_count, 3)


class PostingQuotaSettingsTests(TestCase):
    def test_running_total_crosses_year_and_highlights_only_current_month(self):
        for month in range(6, 13):
            PostingQuota.objects.create(effective_month=date(2026, month, 1),
                                        faq_count=1, tips_count=1, alpha_count=0)
        PostingQuota.objects.create(effective_month=date(2027, 1, 1),
                                    faq_count=1, tips_count=1, alpha_count=2)
        with patch.object(views.timezone, 'localdate', return_value=date(2026, 11, 12)):
            response = self.client.get(self.url, {'year': '2026'})
            rows = response.context['quota_rows']
            self.assertEqual([row['cumulative_total'] for row in rows], [2, 4, 6, 8, 10, 12, 14])
            self.assertEqual([row['month'] for row in rows if row['is_current_month']], [date(2026, 11, 1)])
            self.assertContains(response, 'aria-current="date"', count=1)
            self.assertContains(response, 'class="table-warning"', count=1)
            self.assertContains(response, '>総計</th>')
            response = self.client.get(self.url, {'year': '2027'})
            self.assertEqual(response.context['quota_rows'][0]['cumulative_total'], 18)
            self.assertIsNone(response.context['quota_rows'][1]['cumulative_total'])
            self.assertNotContains(response, 'aria-current="date"')

    def test_running_total_distinguishes_zero_and_missing_months(self):
        PostingQuota.objects.create(effective_month=date(2026, 6, 1), faq_count=0, tips_count=0)
        PostingQuota.objects.create(effective_month=date(2026, 8, 1), faq_count=1, tips_count=1)
        response = self.client.get(self.url, {'year': '2026'})
        rows = response.context['quota_rows']
        self.assertEqual(rows[0]['cumulative_total'], 0)
        self.assertIsNone(rows[1]['cumulative_total'])
        self.assertIsNone(rows[2]['cumulative_total'])
        self.assertContains(response, '未確定')

    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user('quota-staff', is_staff=True)
        cls.superuser = get_user_model().objects.create_user('quota-super', is_superuser=True)
        cls.customer = get_user_model().objects.create_user('quota-customer')

    def setUp(self):
        self.url = reverse('posting_quota_settings')
        self.client.force_login(self.staff)
        self.enterContext(patch.object(views, 'should_force_password_change', return_value=False))
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('external HTTP')))
        self.enterContext(patch('requests.sessions.Session.request', side_effect=AssertionError('external HTTP')))
        self.enterContext(patch('django.core.mail.send_mail', side_effect=AssertionError('external mail')))

    def data(self, **kwargs):
        return {'effective_month': '2026-06', 'faq_count': '2', 'tips_count': '1', 'alpha_count': '0', **kwargs}

    def test_get_has_no_invented_counts_and_defaults_to_current_year(self):
        with patch.object(views.timezone, 'localdate', return_value=date(2026, 11, 12)):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="2026-11"')
        self.assertEqual(response.context['selected_year'], 2026)
        self.assertEqual(len(response.context['quota_rows']), 7)
        self.assertIsNone(response.context['form']['faq_count'].value())
        self.assertIsNone(response.context['form']['tips_count'].value())
        self.assertFalse(PostingQuota.objects.exists())
        self.assertContains(response, '同じ対象月で保存すると')

    def test_form_invalid_values_and_bound_input_preserved(self):
        for field, value in (('faq_count', '-1'), ('tips_count', '-1'), ('faq_count', '1.5'),
                             ('tips_count', ''), ('faq_count', '2147483648'),
                             ('alpha_count', '-1'), ('alpha_count', '1.5'),
                             ('alpha_count', '2147483648'), ('alpha_count', ''),
                             ('effective_month', '2026-05'), ('effective_month', '2026-13'),
                             ('effective_month', 'not-a-month'), ('effective_month', '2026-06-15')):
            with self.subTest(field=field, value=value):
                response = self.client.post(self.url, self.data(**{field: value}))
                self.assertEqual(response.status_code, 200)
                form = response.context['form']
                self.assertTrue(form.is_bound)
                self.assertIn(field, form.errors)
                self.assertEqual(form[field].value(), value)
                self.assertFalse(PostingQuota.objects.exists())

    def test_post_persists_updates_same_month_and_allows_future_zero(self):
        response = self.client.post(self.url, self.data())
        self.assertEqual(response.status_code, 302)
        quota = PostingQuota.objects.get()
        self.assertEqual((quota.effective_month, quota.faq_count, quota.tips_count, quota.updated_by),
                         (date(2026, 6, 1), 2, 1, self.staff))
        old_updated = quota.updated_at
        response = self.client.post(self.url, self.data(faq_count='5'))
        quota.refresh_from_db()
        self.assertEqual(PostingQuota.objects.count(), 1)
        self.assertEqual(quota.faq_count, 5)
        self.assertGreaterEqual(quota.updated_at, old_updated)
        self.assertTrue(any('同じ対象月' in str(message) for message in response.wsgi_request._messages))
        self.client.post(self.url, self.data(effective_month='2029-01', faq_count='0', tips_count='0'))
        self.assertEqual(PostingQuota.objects.count(), 2)
        rows = self.client.get(self.url, {'year': '2029'}).context['quota_rows']
        self.assertEqual(rows[0]['quota'].effective_month, date(2029, 1, 1))
        self.assertEqual(rows[0]['total'], 0)

    def test_selected_month_loads_counts_and_edit_does_not_propagate(self):
        june = PostingQuota.objects.create(effective_month=date(2026, 6, 1), faq_count=1, tips_count=1)
        july = PostingQuota.objects.create(effective_month=date(2026, 7, 1), faq_count=3, tips_count=2, alpha_count=4)
        response = self.client.get(self.url, {'month': '2026-07', 'period': 'previous', 'sort_by': 'post_count', 'include_hidden': '1'})
        self.assertEqual(response.context['form'].initial,
                         {'effective_month': date(2026, 7, 1), 'faq_count': 3, 'tips_count': 2, 'alpha_count': 4})
        self.assertEqual(response.context['selected_year'], 2026)
        self.assertContains(response, 'period=previous&amp;sort_by=post_count&amp;include_hidden=1&amp;year=2026&amp;month=2026-07')
        self.client.post(self.url, self.data(faq_count='7', alpha_count='2'))
        june.refresh_from_db()
        july.refresh_from_db()
        self.assertEqual((june.faq_count, june.alpha_count), (7, 2))
        self.assertEqual((july.faq_count, july.tips_count, july.alpha_count), (3, 2, 4))
        self.assertEqual(PostingQuota.objects.count(), 2)
        self.assertIsNone(posting_targets(date(2026, 8, 1), 'current')['monthly'])

    def test_year_month_query_validation_and_future_years_never_write(self):
        with patch.object(views.timezone, 'localdate', return_value=date(2026, 11, 12)):
            for query in ({'year': '2030'}, {'year': '2035'}, {'year': '99999999999'},
                          {'year': '2025'}, {'year': '<script>'}, {'month': '2026-05'},
                          {'month': '2026-13'}, {'month': '2026-06-15'}, {'month': 'evil'}):
                with self.subTest(query=query):
                    response = self.client.get(self.url, query)
                    self.assertEqual(response.status_code, 200)
                    expected = int(query['year']) if query.get('year') in {'2030', '2035'} else 2026
                    self.assertEqual(response.context['selected_year'], expected)
                    self.assertEqual(response.context['form'].initial['effective_month'], date(2026, 11, 1))
        self.assertFalse(PostingQuota.objects.exists())

    def test_missing_alpha_is_required_and_invalid_bound_selection_stays_visible(self):
        data = self.data(effective_month='2029-03', alpha_count='-4')
        response = self.client.post(self.url + '?year=2029&period=previous', data)
        self.assertEqual(response.context['selected_year'], 2029)
        self.assertEqual(response.context['form']['alpha_count'].value(), '-4')
        self.assertContains(response, 'value="2029-03"')
        del data['alpha_count']
        response = self.client.post(self.url, data)
        self.assertIn('alpha_count', response.context['form'].errors)
        self.assertFalse(PostingQuota.objects.exists())

    def test_csrf_required_for_post(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        self.assertEqual(client.post(self.url, self.data()).status_code, 403)
        page = client.get(self.url)
        self.assertContains(page, 'csrfmiddlewaretoken')
        response = client.post(self.url, self.data(), HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PostingQuota.objects.count(), 1)

    def test_same_staff_permissions_and_effective_preview_flags(self):
        self.client.logout()
        for method in ('get', 'post'):
            self.assertNotEqual(getattr(self.client, method)(self.url, self.data()).status_code, 200)
        self.client.force_login(self.customer)
        for method in ('get', 'post'):
            response = getattr(self.client, method)(self.url, self.data())
            self.assertRedirects(response, reverse('article_list'), fetch_redirect_response=False)
        for mode in ('cs', 'demo'):
            self.client.force_login(self.staff)
            session = self.client.session
            session['account_view_mode'] = mode
            session.save()
            for method in ('get', 'post'):
                response = getattr(self.client, method)(self.url, self.data())
                self.assertRedirects(response, reverse('article_list'), fetch_redirect_response=False)
        self.assertFalse(PostingQuota.objects.exists())
        self.client.force_login(self.superuser)
        session = self.client.session
        session.pop('account_view_mode', None)
        session.save()
        self.assertEqual(self.client.post(self.url, self.data()).status_code, 302)
        self.assertEqual(PostingQuota.objects.get().updated_by, self.superuser)

    def test_fixed_return_route_preserves_only_validated_filters(self):
        url = self.url + '?period=previous&sort_by=post_count&include_hidden=1&next=https://evil.example/'
        response = self.client.post(url, self.data())
        returned = urlsplit(response.url)
        self.assertEqual(returned.path, reverse('summary'))
        self.assertEqual(returned.netloc, '')
        self.assertEqual(parse_qs(returned.query), {
            'period': ['previous'], 'sort_by': ['post_count'], 'include_hidden': ['1'],
        })
        response = self.client.post(self.url + '?period=evil&sort_by=evil&include_hidden=evil', self.data())
        self.assertEqual(parse_qs(urlsplit(response.url).query), {
            'period': ['all'], 'sort_by': ['no'], 'include_hidden': ['0'],
        })


class SummaryPostingTargetTests(TestCase):
    def test_half_year_progress_and_total_achievement_at_target_boundary(self):
        for count, percent, achieved in ((0, 0, False), (299, 299 / 3, False),
                                         (300, 100, True), (301, 301 / 3, True)):
            with self.subTest(count=count):
                KnowledgeArticle.objects.filter(title='半期進捗テスト').delete()
                KnowledgeArticle.objects.bulk_create([
                    KnowledgeArticle(title='半期進捗テスト', body='本文', created_by=self.member,
                                     visible_to_customer=True) for _ in range(count)
                ])
                KnowledgeArticle.objects.filter(title='半期進捗テスト').update(
                    created_at='2026-11-10T12:00:00+09:00')
                response = self.client.get(reverse('summary'))
                target = response.context['half_year_target']
                self.assertEqual(target['actual'], count)
                self.assertAlmostEqual(target['progress_percent'], percent)
                self.assertEqual(target['achieved'], achieved)
                self.assertContains(response, 'data-half-year-achievement')
                self.assertContains(response, 'data-half-year-progress')
                self.assertContains(response, f'{count} / 300 件')

    def test_performance_tables_separate_counts_achievement_and_details(self):
        response = self.client.get(reverse('summary'), {'period': 'previous'})
        html = response.content.decode()
        total = html.split('id="summary-total-table"', 1)[1].split('</thead>', 1)[0]
        member = html.split('id="summary-member-table"', 1)[1].split('</thead>', 1)[0]
        self.assertEqual(total.count('scope="col"'), 6)
        self.assertEqual(member.count('scope="col"'), 6)
        self.assertNotIn('承認数', member)
        self.assertNotIn('当月件数', member)
        self.assertIn('目標達成', member)
        self.assertContains(response, 'colspan="6"')
        self.assertContains(response, '2026/10/01〜2026/10/31の投稿実績')
        self.assertContains(response, '現在の承認済み件数')
        self.assertContains(response, '当月実績（表示期間とは別）')
        self.assertContains(response, '月別内訳（表示期間内）')
        posting = html.split('id="summary-pane"', 1)[1].split('id="internal-pane"', 1)[0]
        self.assertNotIn('Good', posting)
        self.assertNotIn('閲覧', posting)
        self.assertIn('Q&amp;A Goodボタン 総数', html)
        self.assertIn('回答 総閲覧数', html)
        for row in html.split('<tr data-summary-main-row>')[1:]:
            row = row.split('</tr>', 1)[0]
            self.assertNotIn('table-danger', row)
            self.assertNotIn('text-danger', row)
            self.assertNotIn('承認', row)
        self.assertContains(response, 'popover="auto" id="quota-popup-1"')
        self.assertContains(response, 'aria-expanded="false" aria-controls="summaryMonthly"')

    def test_summary_member_names_are_escaped_in_tables_and_detail_buttons(self):
        profile = self.member.knowledge_profile
        profile.display_name = '<img src=x onerror=alert(1)>'
        profile.save(update_fields=['display_name'])
        response = self.client.get(reverse('summary'))
        self.assertNotContains(response, '<img src=x onerror=alert(1)>')
        self.assertContains(response, '&lt;img src=x onerror=alert(1)&gt;')

    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user('summary-quota-staff', is_staff=True)
        cls.member = get_user_model().objects.create_user('summary-quota-member')
        group, _ = Group.objects.get_or_create(name=views.CONTRIBUTOR_GROUP_NAME)
        cls.member.groups.add(group)
        UserProfile.objects.create(user=cls.member, display_name='投稿メンバー', uid='009999')
        for month in range(6, 11):
            PostingQuota.objects.create(effective_month=date(2026, month, 1), faq_count=2, tips_count=1)
        PostingQuota.objects.create(effective_month=date(2026, 11, 1), faq_count=4, tips_count=2, alpha_count=1)
        PostingQuota.objects.create(effective_month=date(2026, 12, 1), faq_count=999, tips_count=999)
        # Legacy all-period scope must continue including older actual posts.
        for model in (KnowledgeArticle, TipsArticle):
            article = model.objects.create(title='以前の投稿', body='本文', created_by=cls.member,
                                           visible_to_customer=True)
            model.objects.filter(pk=article.pk).update(created_at='2026-05-15T12:00:00+09:00')

    def setUp(self):
        self.client.force_login(self.staff)
        self.enterContext(patch.object(views, 'should_force_password_change', return_value=False))
        self.enterContext(patch.object(views.timezone, 'localdate', return_value=date(2026, 11, 12)))

    def test_target_card_contains_settings_metrics_and_collapsed_help(self):
        response = self.client.get(reverse('summary'), {
            'period': 'current', 'sort_by': 'post_count', 'include_hidden': '1',
        })
        html = response.content.decode()
        card = html.split('<section class="card shadow-sm mb-4 posting-target-card"', 1)[1].split('</section>', 1)[0]
        header = html.split('<div class="page-title-panel', 1)[1].split('<div class="tab-content"', 1)[0]
        self.assertNotIn('投稿目標設定', header)
        self.assertIn('投稿目標設定', card)
        self.assertIn('?period=current&amp;sort_by=post_count&amp;include_hidden=1', card)
        self.assertNotIn('posting-target-breakdown', card)
        self.assertEqual(card.count('対象期間：'), 3)
        self.assertEqual(card.count('is-selected'), 1)
        self.assertIn('aria-label="投稿数 0 件 / 必要件数 7 件"', card)
        self.assertIn('aria-label="投稿数 0 件 / 必要件数 22 件"', card)
        self.assertNotIn('1人あたりの投稿目標', card)
        self.assertEqual(card.count('（1人あたり）'), 2)
        self.assertIn('半期目標（全メンバー）', card)
        self.assertLess(card.index('半期目標（全メンバー）'), card.index('累積目標（1人あたり）'))
        self.assertLess(card.index('累積目標（1人あたり）'), card.index('月間目標（1人あたり）'))
        self.assertNotIn('内訳：投稿数 / 目標件数', card)
        self.assertNotIn('共通追加目標', card)
        self.assertEqual(card.count('gap-2 border rounded-3 bg-body p-3'), 3)
        self.assertEqual(card.count('border rounded-3 bg-body px-3 py-2 flex-grow-1'), 6)
        self.assertIn('Q&amp;A <strong>0</strong> / 4 件', card)
        self.assertIn('Tips <strong>0</strong> / 2 件', card)
        self.assertIn('Q&amp;A <strong>0</strong> / 14 件', card)
        self.assertIn('Tips <strong>0</strong> / 7 件', card)
        help_markup = card.split('<details ', 1)[1].split('</details>', 1)[0]
        self.assertNotIn('open', help_markup.split('>', 1)[0])
        self.assertIn('aria-label="投稿目標の説明を開閉"', card)
        self.assertEqual(card.count('aria-label="投稿実績の内訳"'), 3)
        self.assertIn('基本件数を超えた投稿', help_markup)
        self.assertIn('PDF・ホーム・チャット通知', help_markup)
        self.assertNotIn('alpha', card)

    def test_personal_counts_use_login_owner_and_target_period_not_registry_or_saved_name(self):
        for model, owner, timestamp in (
            (KnowledgeArticle, self.staff, '2026-05-31T23:59:59+09:00'),
            (TipsArticle, self.staff, '2026-06-01T00:00:00+09:00'),
            (KnowledgeArticle, self.staff, '2026-10-31T23:59:59+09:00'),
            (TipsArticle, self.staff, '2026-11-01T00:00:00+09:00'),
            (KnowledgeArticle, self.staff, '2026-11-13T00:00:00+09:00'),
            (KnowledgeArticle, self.member, '2026-11-01T00:00:00+09:00'),
        ):
            article = model.objects.create(title='本人集計', body='本文', created_by=owner,
                                           created_by_name='別の保存名', visible_to_customer=True)
            model.objects.filter(pk=article.pk).update(created_at=timestamp)
        for period, monthly, cumulative, end in (
            ('all', 1, 3, date(2026, 11, 30)),
            ('current', 1, 3, date(2026, 11, 30)),
            ('previous', 1, 2, date(2026, 10, 31)),
        ):
            with self.subTest(period=period):
                response = self.client.get(reverse('summary'), {'period': period})
                self.assertEqual(response.context['personal_posting_counts'],
                                 {'monthly': monthly, 'cumulative': cumulative})
                self.assertEqual(response.context['posting_month_end'], end)
                breakdown = response.context['personal_posting_breakdown']
                self.assertEqual(breakdown['monthly']['faq'] + breakdown['monthly']['tips'], monthly)
                self.assertEqual(breakdown['cumulative']['faq'] + breakdown['cumulative']['tips'], cumulative)
                self.assertEqual(breakdown['monthly'], {'faq': 1, 'tips': 0} if period == 'previous' else {'faq': 0, 'tips': 1})
                self.assertContains(response, f'投稿数 {monthly} 件 / 必要件数')

    def test_summary_monthly_vs_cumulative_payload_scope_and_filter_links(self):
        for period, target, month, posts in (('all', 22, 11, 2), ('current', 7, 11, 0), ('previous', 3, 10, 0)):
            with self.subTest(period=period):
                response = self.client.get(reverse('summary'), {
                    'period': period, 'sort_by': 'post_count', 'include_hidden': '1',
                })
                self.assertEqual(response.status_code, 200)
                targets = response.context['posting_targets']
                self.assertEqual(targets['target_month'], date(2026, month, 1))
                self.assertEqual(targets['chart']['value'], target)
                self.assertEqual(response.context['summary_totals']['post_count_total'], posts)
                html = response.content.decode()
                payload = html.split('<script id="summary-posting-target" type="application/json">')[1].split('</script>')[0]
                self.assertEqual(json.loads(payload)['value'], target)
                for linked_period in ('all', 'current', 'previous'):
                    self.assertContains(response, f'?period={linked_period}&amp;sort_by=post_count&amp;include_hidden=1')
                self.assertContains(response, '>月間目標')
                self.assertContains(response, '>累積目標')
                self.assertContains(response, '＋α')
                self.assertEqual(response.context['member_summaries'][0]['quota_progress']['alpha_target'], 0 if period == 'previous' else 1)
                if period == 'all':
                    self.assertContains(response, '2026年6月より前の投稿も含みます')

    def test_half_year_target_boundaries_and_selected_target_month(self):
        for today, period, expected in (
            (date(2027, 3, 12), 'current', '下半期（11月〜3月）'),
            (date(2027, 4, 12), 'current', '上半期（4月〜10月）'),
            (date(2026, 10, 12), 'current', '上半期（4月〜10月）'),
            (date(2026, 11, 12), 'current', '下半期（11月〜3月）'),
            (date(2027, 1, 12), 'current', '下半期（11月〜3月）'),
            (date(2026, 11, 12), 'previous', '上半期（4月〜10月）'),
        ):
            with self.subTest(today=today, period=period):
                with patch.object(views.timezone, 'localdate', return_value=today):
                    response = self.client.get(reverse('summary'), {'period': period})
                html = response.content.decode()
                card = html.split('id="posting-half-year-target"', 1)[1].split('</div>\n                    </div>', 1)[0]
                self.assertIn('/ 必要件数 300 件"', card)
                self.assertEqual(response.context['half_year_target']['label'], expected)
                self.assertEqual(html.count('class="col-12 col-lg-4"'), 3)

    def test_half_year_actual_counts_boundaries_local_dates_and_year_rollover(self):
        # JST midnight boundaries: two posts belong to each half, not the adjacent one.
        for model, timestamp in (
            (KnowledgeArticle, '2026-03-31T23:59:59+09:00'),
            (KnowledgeArticle, '2026-04-01T00:00:00+09:00'),
            (TipsArticle, '2026-10-31T23:59:59+09:00'),
            (KnowledgeArticle, '2026-11-01T00:00:00+09:00'),
            (TipsArticle, '2027-03-31T23:59:59+09:00'),
            (TipsArticle, '2027-04-01T00:00:00+09:00'),
        ):
            article = model.objects.create(title='半期境界', body='本文', created_by=self.member,
                                           visible_to_customer=True, is_approved=False)
            model.objects.filter(pk=article.pk).update(created_at=timestamp)
        for today, period, start, end, expected in (
            (date(2026, 11, 12), 'previous', date(2026, 4, 1), date(2026, 10, 31), 4),
            (date(2026, 11, 12), 'current', date(2026, 11, 1), date(2027, 3, 31), 1),
            (date(2027, 3, 31), 'all', date(2026, 11, 1), date(2027, 3, 31), 2),
            (date(2027, 4, 12), 'previous', date(2026, 11, 1), date(2027, 3, 31), 2),
            (date(2027, 4, 12), 'current', date(2027, 4, 1), date(2027, 10, 31), 1),
        ):
            with self.subTest(today=today, period=period):
                with patch.object(views.timezone, 'localdate', return_value=today):
                    response = self.client.get(reverse('summary'), {'period': period})
                half = response.context['half_year_target']
                self.assertEqual((half['start'], half['end'], half['actual']), (start, end, expected))
                self.assertEqual(half['faq'] + half['tips'], expected)
                self.assertContains(response, '/ 300 件')

    def test_half_year_actual_respects_member_filters_and_is_not_monthly_total(self):
        hidden = get_user_model().objects.create_user('half-hidden')
        hidden.groups.add(Group.objects.get(name=views.CONTRIBUTOR_GROUP_NAME))
        UserProfile.objects.create(user=hidden, display_name='非表示メンバー', uid='009998',
                                   exclude_from_analysis=True)
        for user, saved_name, visible in (
            (self.member, '', True), (hidden, '', True), (self.staff, '', True),
            (self.member, '名簿外の保存名', True), (self.member, '', False),
        ):
            article = KnowledgeArticle.objects.create(
                title='半期集計対象', body='本文', created_by=user, created_by_name=saved_name,
                visible_to_customer=visible, visible_to_systena=False, visible_to_demo=False,
            )
            KnowledgeArticle.objects.filter(pk=article.pk).update(created_at='2026-12-01T12:00:00+09:00')
        with patch.object(views.timezone, 'localdate', return_value=date(2027, 1, 12)):
            for period in ('all', 'current', 'previous'):
                response = self.client.get(reverse('summary'), {'period': period})
                self.assertEqual(response.context['half_year_target']['actual'], 1)
                response = self.client.get(reverse('summary'), {'period': period, 'include_hidden': '1'})
                self.assertEqual(response.context['half_year_target']['actual'], 2)
        KnowledgeArticle.objects.all().delete()
        TipsArticle.objects.all().delete()
        response = self.client.get(reverse('summary'))
        self.assertEqual(response.context['half_year_target']['actual'], 0)
        self.assertContains(response, 'class="posting-target-total">0</span>')

    def test_summary_zero_unset_incomplete_and_before_start_explanations(self):
        PostingQuota.objects.all().delete()
        response = self.client.get(reverse('summary'))
        self.assertIsNone(response.context['posting_target_chart']['value'])
        self.assertContains(response, '投稿目標を設定してください')
        self.assertContains(response, '2026-06')
        PostingQuota.objects.create(effective_month=date(2026, 8, 1), faq_count=0, tips_count=0)
        response = self.client.get(reverse('summary'))
        self.assertIsNone(response.context['posting_target_chart']['value'])
        self.assertContains(response, '未確定')
        response = self.client.get(reverse('summary'), {'period': 'current'})
        self.assertIsNone(response.context['posting_target_chart']['value'])
        self.assertIsNone(response.context['member_summaries'][0]['quota_progress'])
        PostingQuota.objects.create(effective_month=date(2026, 11, 1), faq_count=0, tips_count=0)
        response = self.client.get(reverse('summary'), {'period': 'current'})
        self.assertEqual(response.context['posting_target_chart']['value'], 0)
        self.assertContains(response, '/ 必要件数 0 件')
        with patch.object(views.timezone, 'localdate', return_value=date(2026, 6, 12)):
            response = self.client.get(reverse('summary'), {'period': 'previous'})
        self.assertIsNone(response.context['posting_target_chart']['value'])
        self.assertContains(response, '対象月は開始前')

    def test_member_progress_uses_selected_actual_scope_and_common_bucket(self):
        PostingQuota.objects.filter(effective_month=date(2026, 11, 1)).update(faq_count=1, tips_count=1, alpha_count=1)
        for model in (KnowledgeArticle, KnowledgeArticle, TipsArticle):
            article = model.objects.create(title='当月の投稿', body='本文', created_by=self.member, visible_to_customer=True)
            model.objects.filter(pk=article.pk).update(created_at='2026-11-10T12:00:00+09:00')
        response = self.client.get(reverse('summary'), {'period': 'current'})
        row = response.context['member_summaries'][0]
        self.assertEqual((row['faq_post_count'], row['tips_post_count']), (2, 1))
        self.assertTrue(row['quota_progress']['achieved'])
        self.assertEqual(row['quota_progress']['alpha_fulfilled'], 1)
        self.assertContains(response, '＋α 1/1')
        response = self.client.get(reverse('summary'), {'period': 'previous'})
        row = response.context['member_summaries'][0]
        self.assertEqual(row['post_count_total'], 0)
        self.assertFalse(row['quota_progress']['achieved'])
        response = self.client.get(reverse('summary'), {'period': 'all'})
        row = response.context['member_summaries'][0]
        self.assertEqual(row['post_count_total'], 5)  # Includes legacy May posts, as before.
        self.assertFalse(row['quota_progress']['achieved'])