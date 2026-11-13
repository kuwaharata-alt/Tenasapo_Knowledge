"""Demo lists customer articles but omits FAQ answers and Tips content from HTML."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from . import views
from .models import KnowledgeArticle, TipsArticle


class DemoKnowledgeVisibilityTests(TestCase):
    message = 'この記事の閲覧は制限されています。テナサポStandardのご契約をご検討ください。'

    def setUp(self):
        self.factory = RequestFactory()
        self.demo = get_user_model().objects.create_user(username='demo-visibility')
        self.customer = get_user_model().objects.create_user(username='customer-visibility')
        for user, name in ((self.demo, views.DEMO_GROUP_NAME),
                           (self.customer, views.CUSTOMER_GROUP_NAME)):
            group, _ = Group.objects.get_or_create(name=name)
            user.groups.add(group)
        self.enterContext(patch.object(views, 'FAQ_APPROVAL_ENABLED', True))
        self.faq = KnowledgeArticle.objects.create(
            title='FAQ public title', summary='PRIVATE_QUESTION_TOKEN', body='PUBLIC_ANSWER_TOKEN',
            is_published=True, is_approved=True, visible_to_customer=True,
            visible_to_demo=False, standard_contract_only=False, expires_on=None,
        )
        self.tip = TipsArticle.objects.create(
            title='Tips public title', body='PRIVATE_TIPS_TOKEN',
            is_published=True, is_approved=True, visible_to_customer=True,
            visible_to_demo=False, standard_contract_only=False, expires_on=None,
        )

    def render_view(self, view_class, url_name, user, *, item=None, params=None):
        kwargs = {'management_code': item.management_code} if item else {}
        request = self.factory.get(reverse(url_name, kwargs=kwargs), params or {})
        request.user = user
        request.session = {}
        response = view_class.as_view()(request, **kwargs)
        self.assertEqual(response.status_code, 200)
        return response.context_data, render_to_string(view_class.template_name, response.context_data, request=request)

    def test_demo_sees_customer_articles_even_when_demo_flag_is_off(self):
        for model, view, name, key, item in self.list_specs():
            with self.subTest(model=model.__name__):
                context, _ = self.render_view(view, name, self.demo)
                self.assertEqual([row.pk for row in context[key]], [item.pk])

    def list_specs(self):
        return ((KnowledgeArticle, views.ArticleListView, 'article_list', 'articles', self.faq),
                (TipsArticle, views.TipsListView, 'tip_list', 'tips_list', self.tip))

    def test_demo_question_and_tips_body_are_not_in_list_html(self):
        _, faq_html = self.render_view(views.ArticleListView, 'article_list', self.demo)
        self.assertIn('PRIVATE_QUESTION_TOKEN', faq_html)
        self.assertNotIn('PUBLIC_ANSWER_TOKEN', faq_html)
        self.assertIn(self.message, faq_html)
        _, tips_html = self.render_view(views.TipsListView, 'tip_list', self.demo)
        self.assertNotIn('PRIVATE_TIPS_TOKEN', tips_html)
        self.assertIn(self.message, tips_html)

    def test_demo_detail_has_same_restrictions(self):
        _, html = self.render_view(views.KnowledgeArticleDetailView, 'article_detail', self.demo, item=self.faq)
        self.assertIn('PRIVATE_QUESTION_TOKEN', html)
        self.assertNotIn('PUBLIC_ANSWER_TOKEN', html)
        self.assertIn(self.message, html)
        _, html = self.render_view(views.TipsArticleDetailView, 'tip_detail', self.demo, item=self.tip)
        self.assertNotIn('PRIVATE_TIPS_TOKEN', html)
        self.assertIn(self.message, html)

    def test_regular_customer_content_is_unchanged(self):
        _, html = self.render_view(views.ArticleListView, 'article_list', self.customer)
        self.assertIn('PRIVATE_QUESTION_TOKEN', html)
        self.assertIn('PUBLIC_ANSWER_TOKEN', html)
        _, html = self.render_view(views.TipsListView, 'tip_list', self.customer)
        self.assertIn('PRIVATE_TIPS_TOKEN', html)

    def test_demo_keeps_answer_label_with_restriction_message(self):
        for view, name, item in (
            (views.ArticleListView, 'article_list', None),
            (views.KnowledgeArticleDetailView, 'article_detail', self.faq),
        ):
            with self.subTest(page=name):
                _, html = self.render_view(view, name, self.demo, item=item)
                self.assertRegex(
                    html,
                    r'<span class="badge text-bg-secondary flex-shrink-0">A</span>\s*'
                    r'<div class="flex-grow-1 faq-body-indent">\s*'
                    r'<div class="alert alert-warning mb-0">' + self.message + r'</div>',
                )
                self.assertIn('PRIVATE_QUESTION_TOKEN', html)
                self.assertNotIn('PUBLIC_ANSWER_TOKEN', html)

    def test_hidden_content_is_not_searchable_by_demo(self):
        for view, name, key, token in (
            (views.ArticleListView, 'article_list', 'articles', 'PUBLIC_ANSWER_TOKEN'),
            (views.TipsListView, 'tip_list', 'tips_list', 'PRIVATE_TIPS_TOKEN'),
        ):
            context, _ = self.render_view(view, name, self.demo, params={'q': token})
            self.assertEqual(list(context[key]), [])

    def test_demo_search_still_matches_titles(self):
        for _, view, name, key, item in self.list_specs():
            context, _ = self.render_view(view, name, self.demo, params={'q': item.title})
            self.assertEqual([row.pk for row in context[key]], [item.pk])

    def test_demo_can_search_visible_question(self):
        context, _ = self.render_view(views.ArticleListView, 'article_list', self.demo,
                                      params={'q': 'PRIVATE_QUESTION_TOKEN'})
        self.assertEqual([row.pk for row in context['articles']], [self.faq.pk])

    def test_visibility_safety_conditions_remain(self):
        for model, view, name, key, item in self.list_specs():
            for overrides in ({'is_approved': False}, {'is_published': False},
                              {'visible_to_customer': False},
                              {'expires_on': timezone.localdate() - timedelta(days=1)}):
                model.objects.create(title='Excluded article', body='EXCLUDED_TOKEN',
                                     **{**{'is_approved': True, 'is_published': True,
                                          'visible_to_customer': True, 'expires_on': None}, **overrides})
            context, html = self.render_view(view, name, self.demo)
            self.assertEqual([row.pk for row in context[key]], [item.pk])
            self.assertNotIn('EXCLUDED_TOKEN', html)

    def test_faq_standard_answer_remains_restricted(self):
        self.faq.standard_contract_only = True
        self.faq.save(update_fields=['standard_contract_only'])
        _, html = self.render_view(views.ArticleListView, 'article_list', self.demo)
        self.assertIn('PRIVATE_QUESTION_TOKEN', html)
        self.assertNotIn('PUBLIC_ANSWER_TOKEN', html)
        self.assertIn(self.message, html)