"""Scheduled PDF regressions: isolated DB, no live transports or image reads."""

from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from .chat_schedule import ChatNotificationSchedule, JAPAN_TIMEZONE
from .models import (
    ArticleAttachment, KnowledgeArticle, KnowledgeArticleImageAttachment,
    PostingQuota, TipsArticle, TipsImageAttachment, UserProfile,
)
from .scheduled_report_pdf import (
    RESTRICTED_CONTENT, build_scheduled_report_context, build_scheduled_report_pdf,
)
from .views import CONTRIBUTOR_GROUP_NAME, SummaryView


MODULE = 'tenasapo_knowledge.scheduled_report_pdf'
NOW = datetime(2026, 11, 13, 12, tzinfo=JAPAN_TIMEZONE)


class ScheduledReportPDFTests(TestCase):
    def setUp(self):
        self.guards = [self.enterContext(patch(
            name, side_effect=AssertionError('live network/image reads forbidden')))
            for name in ('urllib.request.urlopen', 'tenasapo_knowledge.notifications.urlopen',
                         'requests.sessions.Session.request', 'socket.socket.connect',
                         'socket.create_connection', 'http.client.HTTPConnection.connect',
                         'http.client.HTTPSConnection.connect',
                         'tenasapo_knowledge.posting_report_pdf._attachment_image')]
        self.actor = get_user_model().objects.create_user('pdf-staff', is_staff=True)
        self.schedule = ChatNotificationSchedule(
            target_month=date(2026, 11, 1), report_notice_at=NOW, updated_by=self.actor)

    def tearDown(self):
        for guard in self.guards:
            guard.assert_not_called()
        super().tearDown()

    def member(self, name, uid, *, excluded=False, contributor=True):
        user = get_user_model().objects.create_user(f'pdf-member-{uid}')
        UserProfile.objects.create(user=user, display_name=name, uid=f'{uid:06}',
                                   exclude_from_analysis=excluded)
        if contributor:
            group, _ = Group.objects.get_or_create(name=CONTRIBUTOR_GROUP_NAME)
            user.groups.add(group)
        return user

    def article(self, model=KnowledgeArticle, *, at=NOW, **overrides):
        values = dict(title='タイトル', body='秘密の本文', created_by=self.actor,
                      visible_to_customer=False, visible_to_systena=True,
                      is_published=False, is_approved=False)
        values.update(overrides)
        article = model.objects.create(**values)
        model.objects.filter(pk=article.pk).update(created_at=at)
        return article

    def context(self, now=NOW):
        return build_scheduled_report_context(self.schedule, now)

    def test_selected_previous_month_not_execution_month_and_monthly_quota(self):
        self.schedule.target_month = date(2026, 10, 1)
        user = self.member('対象者', 10)
        self.article(at=NOW.replace(month=10), created_by=user)
        self.article(created_by=user)
        PostingQuota.objects.create(effective_month=date(2026, 10, 1),
                                    faq_count=2, tips_count=1, alpha_count=3)
        PostingQuota.objects.create(effective_month=date(2026, 11, 1),
                                    faq_count=9, tips_count=9, alpha_count=9)
        context = self.context()
        self.assertEqual(context['report_month'], '2026-10')
        self.assertEqual(context['report_month_start'], date(2026, 10, 1))
        self.assertEqual(context['report_month_end'], date(2026, 10, 31))
        self.assertEqual(context['report_totals'], dict(total=1, faq=1, tips=0))
        self.assertEqual(context['report_member_totals']['post_count_total'], 1)
        self.assertEqual(context['report_monthly_target'], dict(faq=2, tips=1, alpha=3, total=6))
        half = context['report_half_year_target']
        self.assertEqual((half['start'], half['end'], half['actual']),
                         (date(2026, 4, 1), date(2026, 10, 31), 1))

    def test_jst_month_boundaries_and_notice_cutoff_override_ambient_timezone(self):
        user = self.member('境界', 10)
        start = datetime(2026, 11, 1, tzinfo=JAPAN_TIMEZONE)
        cutoff = NOW - timedelta(hours=1)
        self.schedule.report_notice_at = cutoff
        expected = []
        for at in (start - timedelta(microseconds=1), start, cutoff,
                   cutoff + timedelta(microseconds=1),
                   datetime(2026, 12, 1, tzinfo=JAPAN_TIMEZONE)):
            item = self.article(at=at, created_by=user)
            if start <= at <= cutoff:
                expected.append(item.pk)
        with timezone.override('UTC'):
            context = self.context()
            self.assertEqual(timezone.get_current_timezone_name(), 'UTC')
        self.assertEqual([row['id'] for row in context['report_articles']], expected[::-1])
        self.assertEqual(context['report_member_totals']['post_count_total'], 2)
        self.assertEqual(context['report_half_year_target']['actual'], 2)
        self.assertEqual(context['report_articles'][-1]['created_at'].day, 1)
        self.assertEqual(context['report_articles'][-1]['created_at'].utcoffset(), timedelta(hours=9))

    def test_execution_before_notice_uses_now_and_explicit_counter_arguments(self):
        self.schedule.report_notice_at = NOW + timedelta(days=1)
        self.article()
        self.article(at=NOW + timedelta(microseconds=1))
        with patch.object(SummaryView, 'member_post_counts',
                          wraps=SummaryView.member_post_counts) as counts:
            context = self.context()
        self.assertEqual(context['report_totals']['total'], 1)
        self.assertEqual(counts.call_args_list[0].kwargs,
                         dict(from_date=date(2026, 11, 1), to_date=date(2026, 11, 30), as_of=NOW))
        self.assertEqual(counts.call_args_list[1].kwargs,
                         dict(from_date=date(2026, 11, 1), to_date=date(2027, 3, 31), as_of=NOW))

    def test_member_saved_names_zero_hidden_admin_nonmember_and_visibility_rules(self):
        user = self.member('投稿者', 20)
        self.member('ゼロ', 10)
        hidden = self.member('非表示', 30, excluded=True)
        nonmember = self.member('対象外', 40, contributor=False)
        admin = self.member('admin', 50)
        self.article(created_by=user)
        self.article(TipsArticle, created_by=self.actor, created_by_name='投稿者')
        self.article(created_by=user, visible_to_systena=False, visible_to_demo=True)
        for author in (hidden, nonmember, admin):
            self.article(created_by=author)
        context = self.context()
        self.assertEqual([row['name'] for row in context['report_member_summaries']], ['ゼロ', '投稿者'])
        self.assertEqual([row['management_uid'] for row in context['report_member_summaries']],
                         ['000010', '000020'])
        self.assertEqual(context['report_member_totals'],
                         dict(faq_post_count=1, tips_post_count=1, post_count_total=2))
        self.assertEqual(context['report_half_year_target']['actual'], 2)
        self.assertEqual(context['report_totals']['total'], 5)

    def test_all_articles_over_40_and_same_screen_sort_without_publication_filters(self):
        for index in range(43):
            self.article(TipsArticle if index % 2 else KnowledgeArticle,
                         visible_to_customer=True, visible_to_systena=False,
                         expires_on=date(2020, 1, 1))
        context = self.context()
        rows = context['report_articles']
        self.assertEqual(len(rows), 43)
        self.assertEqual(context['report_totals'], dict(total=43, faq=22, tips=21))
        self.assertEqual(rows, sorted(rows, key=lambda row: (row['created_at'], row['kind'], row['id']),
                                      reverse=True))

    def test_half_year_boundaries_match_screen_including_january_and_cutoff(self):
        user = self.member('半期', 10)
        for at in (datetime(2026, 3, 31, tzinfo=JAPAN_TIMEZONE),
                   datetime(2026, 4, 1, tzinfo=JAPAN_TIMEZONE),
                   datetime(2026, 10, 31, tzinfo=JAPAN_TIMEZONE),
                   datetime(2026, 11, 1, tzinfo=JAPAN_TIMEZONE),
                   NOW, NOW + timedelta(microseconds=1)):
            self.article(at=at, created_by=user)
        for month, start, end, actual in (
            (date(2026, 10, 1), date(2026, 4, 1), date(2026, 10, 31), 2),
            (date(2027, 1, 1), date(2026, 11, 1), date(2027, 3, 31), 2),
        ):
            with self.subTest(month=month):
                self.schedule.target_month = month
                half = self.context()['report_half_year_target']
                self.assertEqual((half['start'], half['end'], half['actual']), (start, end, actual))
                self.assertEqual(half['faq'], actual)
                self.assertEqual(half['target'], 300)
                self.assertAlmostEqual(half['progress_percent'], actual / 300 * 100)
                self.assertFalse(half['achieved'])

    def test_missing_inactive_nonstaff_anonymous_and_synthetic_actors_fail_closed(self):
        actors = [None, AnonymousUser(), get_user_model()(username='unsaved', is_staff=True),
                  get_user_model()(pk=999, username='synthetic', is_staff=True),
                  get_user_model().objects.create_user('pdf-member'),
                  get_user_model().objects.create_user('pdf-inactive', is_staff=True, is_active=False)]
        for actor in actors:
            with self.subTest(actor=actor):
                # Set the relation cache to exercise even malformed callers without fake users in production.
                self.schedule._state.fields_cache['updated_by'] = actor
                with patch.object(SummaryView, 'member_post_counts') as counts:
                    with self.assertRaises(PermissionDenied):
                        self.context()
                    counts.assert_not_called()

    def test_active_nonstaff_superuser_is_allowed(self):
        self.schedule.updated_by = get_user_model().objects.create_user(
            'pdf-superuser', is_superuser=True, is_staff=False)
        self.assertEqual(self.context()['report_totals']['total'], 0)

    def test_naive_times_are_rejected(self):
        with self.assertRaises(ValueError):
            self.context(NOW.replace(tzinfo=None))
        self.schedule.report_notice_at = NOW.replace(tzinfo=None)
        with self.assertRaises(ValueError):
            self.context()

    def test_authorized_question_attachments_creator_and_reference_policy(self):
        user = self.member('表示名', 10)
        faq = self.article(created_by=user, summary='質問の概要')
        fallback = self.article(title='概要が空の質問')
        tips = self.article(TipsArticle)
        question = ArticleAttachment.objects.create(article=faq, placement='question', file='test/question.png')
        ArticleAttachment.objects.create(article=faq, placement='answer', file='test/other.png')
        answer = KnowledgeArticleImageAttachment.objects.create(article=faq, file='test/answer.png')
        image = TipsImageAttachment.objects.create(tip=tips, file='test/tips.png')
        links = [{'label': '参考', 'url': 'https://example.test/reference'}]
        KnowledgeArticle.objects.filter(pk=faq.pk).update(reference_links=links)
        context = self.context()
        rows = {(row['kind'], row['id']): row for row in context['report_articles']}
        row = rows['Q&A', faq.pk]
        self.assertEqual(row['question'], '質問の概要')
        self.assertEqual(row['creator_name'], '表示名')
        self.assertEqual(row['question_images'], [question])
        self.assertEqual(row['body_images'], [answer])
        self.assertEqual(row['reference_links'], links)
        self.assertEqual(rows['Q&A', fallback.pk]['question'], '概要が空の質問')
        self.assertEqual(rows['Tips', tips.pk]['body_images'], [image])
        self.assertEqual(rows['Tips', tips.pk]['question'], '')

    def test_real_actor_demo_override_restricts_question_body_images_and_references(self):
        faq = self.article(summary='秘密の質問', reference_links=['https://example.test/private'])
        ArticleAttachment.objects.create(article=faq, placement='question', file='test/question.png')
        KnowledgeArticleImageAttachment.objects.create(article=faq, file='test/answer.png')
        self.article(TipsArticle)
        self.actor._view_mode_override = 'demo'
        for row in self.context()['report_articles']:
            self.assertEqual(row['body'], RESTRICTED_CONTENT)
            self.assertEqual(row['question'], RESTRICTED_CONTENT if row['kind'] == 'Q&A' else '')
            self.assertEqual(row['question_images'], [])
            self.assertEqual(row['body_images'], [])
            self.assertEqual(row['reference_links'], [])

    def test_content_helper_denial_also_restricts_question(self):
        self.article(summary='秘密の質問')
        with patch(MODULE + '.can_view_restricted_knowledge_content', return_value=False) as policy:
            row = self.context()['report_articles'][0]
        self.assertIs(policy.call_args.args[0], self.actor)
        self.assertEqual(row['question'], RESTRICTED_CONTENT)
        self.assertEqual(row['body'], RESTRICTED_CONTENT)

    def test_actual_pdf_bytes_and_shared_renderer_without_request_or_view(self):
        user = self.member('投稿者', 10)
        self.article(created_by=user, summary='質問です', body='回答です')
        # The optional logo is omitted; there are no image fixtures to read.
        with patch('tenasapo_knowledge.posting_report_pdf.Path.is_file', return_value=False), \
             patch.object(SummaryView, 'get_context_data', side_effect=AssertionError('no view')), \
             patch.object(SummaryView, 'setup', side_effect=AssertionError('no request')):
            output = build_scheduled_report_pdf(self.schedule, NOW)
        self.assertIsInstance(output, bytes)
        self.assertTrue(output.startswith(b'%PDF-'))
        self.assertGreater(len(output), 3000)
        self.assertIn(b'/HeiseiKakuGo-W5', output)
        with timezone.override('UTC'), patch(MODULE + '.build_posting_report_pdf', return_value=b'pdf') as render:
            def render_in_jst(context):
                self.assertEqual(timezone.get_current_timezone(), JAPAN_TIMEZONE)
                self.assertEqual(context['report_month'], '2026-11')
                return b'pdf'
            render.side_effect = render_in_jst
            self.assertEqual(build_scheduled_report_pdf(self.schedule, NOW), b'pdf')
            render.assert_called_once()
            self.assertEqual(timezone.get_current_timezone_name(), 'UTC')