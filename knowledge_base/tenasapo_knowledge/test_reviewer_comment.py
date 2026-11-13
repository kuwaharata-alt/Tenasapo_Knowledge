"""FAQ/Tips reviewer comments are separate from AI reviews and remand reasons."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .models import KnowledgeArticle, TipsArticle
from .views import ReviewListView


class ReviewerCommentTests(TestCase):
    article_types = (
        ('faq', KnowledgeArticle, 'article'),
        ('tips', TipsArticle, 'tip'),
    )

    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.reviewer = users.objects.create_user(username='comment-reviewer')
        group, _ = Group.objects.get_or_create(name='レビュアー')
        cls.reviewer.groups.add(group)
        cls.other = users.objects.create_user(username='comment-other')
        cls.manager = users.objects.create_user(username='comment-manager')

    def setUp(self):
        # Views cache these settings at import time; override the cached values.
        approval = patch('tenasapo_knowledge.views.FAQ_APPROVAL_ENABLED', True)
        approval.start()
        self.addCleanup(approval.stop)
        managers = patch(
            'tenasapo_knowledge.views.PASSWORD_MANAGER_USERNAMES',
            {'comment-manager'},
        )
        managers.start()
        self.addCleanup(managers.stop)
        self.client.force_login(self.reviewer)

    def make_article(self, model, **overrides):
        return model.objects.create(**{
            'title': 'コメント検証',
            'body': '本文',
            'is_approved': False,
            'reviewer_comment': '既存の承認者コメント',
            'ai_review': '既存のAIレビュー',
            'remand_reason': '既存の差戻し理由',
            **overrides,
        })

    def post_action(self, prefix, action, article, data=None):
        response = self.client.post(
            reverse(f'{prefix}_{action}', args=[article.pk]), data or {},
        )
        # No creator means no mail-draft redirect; do not follow the list page.
        self.assertRedirects(
            response, reverse(f'{prefix}_list'), fetch_redirect_response=False,
        )
        article.refresh_from_db()
        return response

    def test_reviewer_comment_defaults_to_empty(self):
        for kind, model, _ in self.article_types:
            with self.subTest(kind=kind):
                article = model.objects.create(title='初期値', body='本文')
                article.refresh_from_db()
                self.assertEqual(article.reviewer_comment, '')

    def test_approve_saves_stripped_reviewer_comment(self):
        for kind, model, prefix in self.article_types:
            with self.subTest(kind=kind):
                article = self.make_article(model)
                self.post_action(prefix, 'approve', article, {
                    'reviewer_comment': ' \n 承認コメント\n本文 \t ',
                })
                self.assertEqual(article.reviewer_comment, '承認コメント\n本文')
                self.assertTrue(article.is_approved)
                self.assertEqual(article.approved_by_id, self.reviewer.pk)
                self.assertEqual(article.remand_reason, '')

    def test_remand_saves_stripped_reviewer_comment(self):
        for kind, model, prefix in self.article_types:
            with self.subTest(kind=kind):
                article = self.make_article(
                    model, is_approved=True, is_corrected=True,
                    approved_by=self.reviewer, approved_by_name='レビュアー',
                )
                self.post_action(prefix, 'remand', article, {
                    'reviewer_comment': ' \n 差戻しコメント\n本文 \t ',
                    'remand_reason': ' 修正が必要 ',
                })
                self.assertEqual(article.reviewer_comment, '差戻しコメント\n本文')
                self.assertEqual(article.remand_reason, '修正が必要')
                self.assertFalse(article.is_approved)
                self.assertFalse(article.is_corrected)
                self.assertIsNone(article.approved_by_id)
                self.assertEqual(article.approved_by_name, '')

    def test_reviewer_comment_and_ai_review_are_independent(self):
        for kind, model, prefix in self.article_types:
            for action in ('approve', 'remand'):
                with self.subTest(kind=kind, action=action):
                    article = self.make_article(model)
                    self.post_action(prefix, action, article, {
                        'reviewer_comment': ' 人間のコメント ',
                        'ai_review': ' AIのレビュー ',
                        'remand_reason': ' 差戻し理由 ',
                    })
                    self.assertEqual(article.reviewer_comment, '人間のコメント')
                    self.assertEqual(article.ai_review, 'AIのレビュー')
                    self.assertEqual(
                        article.remand_reason,
                        '差戻し理由' if action == 'remand' else '',
                    )

    def test_missing_reviewer_comment_preserves_existing_value(self):
        for kind, model, prefix in self.article_types:
            for action in ('approve', 'remand'):
                with self.subTest(kind=kind, action=action):
                    article = self.make_article(model)
                    self.post_action(prefix, action, article, {'ai_review': 'AI更新'})
                    self.assertEqual(article.reviewer_comment, '既存の承認者コメント')
                    self.assertEqual(article.ai_review, 'AI更新')

    def test_empty_reviewer_comment_clears_existing_value(self):
        for kind, model, prefix in self.article_types:
            for action in ('approve', 'remand'):
                for comment in ('', ' \t\r\n '):
                    with self.subTest(kind=kind, action=action, comment=comment):
                        article = self.make_article(model)
                        self.post_action(prefix, action, article, {
                            'reviewer_comment': comment, 'ai_review': 'AIは別欄',
                        })
                        self.assertEqual(article.reviewer_comment, '')
                        self.assertEqual(article.ai_review, 'AIは別欄')

    def test_non_reviewer_cannot_change_reviewer_comment(self):
        self.client.force_login(self.other)
        for kind, model, prefix in self.article_types:
            for action in ('approve', 'remand'):
                with self.subTest(kind=kind, action=action):
                    article = self.make_article(model, is_corrected=True)
                    original_updated_at = article.updated_at
                    self.post_action(prefix, action, article, {
                        'reviewer_comment': '不正な更新',
                        'ai_review': '不正なAI更新',
                        'remand_reason': '不正な差戻し',
                    })
                    self.assertEqual(article.reviewer_comment, '既存の承認者コメント')
                    self.assertEqual(article.ai_review, '既存のAIレビュー')
                    self.assertEqual(article.remand_reason, '既存の差戻し理由')
                    self.assertFalse(article.is_approved)
                    self.assertTrue(article.is_corrected)
                    self.assertIsNone(article.approved_by_id)
                    self.assertEqual(article.updated_at, original_updated_at)

    def test_approval_reset_clears_reviewer_comment(self):
        self.client.force_login(self.manager)
        for kind, model, prefix in self.article_types:
            with self.subTest(kind=kind):
                article = self.make_article(
                    model, is_approved=True, is_corrected=True,
                    approved_by=self.reviewer, approved_by_name='レビュアー',
                )
                response = self.client.post(
                    reverse(f'{prefix}_approval_reset', args=[article.pk]),
                    {'reviewer_comment': 'リセットでは保存しない'},
                )
                self.assertRedirects(
                    response, reverse(f'{prefix}_edit', args=[article.pk]),
                    fetch_redirect_response=False,
                )
                article.refresh_from_db()
                self.assertEqual(article.reviewer_comment, '')
                self.assertEqual(article.ai_review, '')
                self.assertEqual(article.remand_reason, '')
                self.assertFalse(article.is_approved)
                self.assertFalse(article.is_corrected)
                self.assertIsNone(article.approved_by_id)
                self.assertEqual(article.approved_by_name, '')

    def test_reviewer_without_reset_permission_cannot_clear_comment(self):
        for kind, model, prefix in self.article_types:
            with self.subTest(kind=kind):
                article = self.make_article(
                    model, is_approved=True, approved_by=self.reviewer,
                )
                original_updated_at = article.updated_at
                self.post_action(prefix, 'approval_reset', article)
                self.assertEqual(article.reviewer_comment, '既存の承認者コメント')
                self.assertEqual(article.ai_review, '既存のAIレビュー')
                self.assertEqual(article.remand_reason, '既存の差戻し理由')
                self.assertTrue(article.is_approved)
                self.assertEqual(article.approved_by_id, self.reviewer.pk)
                self.assertEqual(article.updated_at, original_updated_at)

    def test_review_list_items_include_reviewer_comment(self):
        expected = {}
        for kind, model, _ in self.article_types:
            article = self.make_article(model, reviewer_comment=f'{kind}のコメント')
            expected[(kind, article.pk)] = article.reviewer_comment

        request = RequestFactory().get(reverse('review_list'))
        request.user = self.reviewer
        view = ReviewListView()
        view.setup(request)
        context = view.get_context_data()

        self.assertEqual({
            (item['type'], item['id']): item['reviewer_comment']
            for item in context['review_items']
        }, expected)