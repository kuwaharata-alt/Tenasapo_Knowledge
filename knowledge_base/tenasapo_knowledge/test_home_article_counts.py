"""Home totals must match unfiltered FAQ/Tips lists and their visibility rules."""

from datetime import date, timedelta
from html.parser import HTMLParser
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from . import views
from .models import KnowledgeArticle, TipsArticle


class HomeCountSpanParser(HTMLParser):
    """Collect count spans without depending on attribute order or extra classes."""

    def __init__(self):
        super().__init__()
        self.spans = []
        self.active_spans = []

    def handle_starttag(self, tag, attrs):
        if tag == 'span':
            node = {'classes': set(dict(attrs).get('class', '').split()), 'text': ''}
            self.spans.append(node)
            self.active_spans.append(node)

    def handle_endtag(self, tag):
        if tag == 'span' and self.active_spans:
            self.active_spans.pop()

    def handle_data(self, data):
        for node in self.active_spans:
            node['text'] += data


class HomeArticleCountsTests(TestCase):
    today = date(2026, 11, 11)
    article_specs = (
        ('faq', KnowledgeArticle, views.ArticleListView, 'article_list',
         'home_faq_count', 'recent_faqs', 'home-faq-count'),
        ('tips', TipsArticle, views.TipsListView, 'tip_list',
         'home_tips_count', 'recent_tips', 'home-tips-count'),
    )
    roles = ('customer', 'demo', 'systena', 'reviewer', 'staff', 'superuser')
    customer_states = {'customer_demo', 'customer_only', 'expires_today', 'no_expiry'}
    demo_states = customer_states
    internal_states = customer_states | {'demo_only', 'pending', 'systena_only'}

    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.users = {}
        group_names = {
            'customer': views.CUSTOMER_GROUP_NAME,
            'demo': views.DEMO_GROUP_NAME,
            'systena': views.SYSTENA_GROUP_NAME,
            'reviewer': views.REVIEWER_GROUP_NAME,
        }
        for role in cls.roles:
            user = users.objects.create_user(
                username=f'home-count-{role}',
                is_staff=role == 'staff',
                # Deliberately test is_superuser independently of is_staff.
                is_superuser=role == 'superuser',
            )
            if role in group_names:
                group, _ = Group.objects.get_or_create(name=group_names[role])
                user.groups.add(group)
            cls.users[role] = user

        cls.items = {}
        states = {
            'customer_demo': {},
            'customer_only': {'visible_to_demo': False, 'visible_to_systena': False},
            'demo_only': {'visible_to_customer': False},
            'pending': {'is_approved': False},
            'unpublished': {'is_published': False},
            'expired': {'expires_on': cls.today - timedelta(days=1)},
            'expires_today': {'expires_on': cls.today},
            'no_expiry': {'expires_on': None},
            # Demo alone must not override both customer/systena flags being off.
            'hidden': {'visible_to_customer': False, 'visible_to_systena': False},
            'systena_only': {'visible_to_customer': False, 'visible_to_demo': False},
        }
        updated_at = timezone.now()
        for kind, model, *_ in cls.article_specs:
            cls.items[kind] = {}
            for index, (state, overrides) in enumerate(states.items()):
                values = {
                    'title': f'{kind} {state}', 'body': '本文', 'category': 'PC/設定',
                    'is_published': True, 'is_approved': True,
                    'visible_to_customer': True, 'visible_to_systena': True,
                    'visible_to_demo': True,
                    'expires_on': cls.today + timedelta(days=1),
                }
                values.update(overrides)
                item = model.objects.create(**values)
                # Unique timestamps make the latest-three assertions deterministic.
                model.objects.filter(pk=item.pk).update(
                    updated_at=updated_at + timedelta(minutes=index),
                )
                cls.items[kind][state] = item

        # Different FAQ/Tips totals also catch accidentally swapped context values.
        cls.items['tips']['tips_extra'] = TipsArticle.objects.create(
            title='tips extra', body='追加本文', category='PC/設定',
            is_published=True, is_approved=True,
            visible_to_customer=True, visible_to_systena=True, visible_to_demo=True,
            expires_on=None,
        )

    def setUp(self):
        self.factory = RequestFactory()
        self.enterContext(patch.object(views.timezone, 'localdate', return_value=self.today))

    def request_for(self, user, url_name='home', params=None):
        request = self.factory.get(reverse(url_name), params or {})
        request.user = user
        request.session = {}
        mode = getattr(user, '_view_mode_override', '')
        if mode:
            request.session[views.ACCOUNT_VIEW_MODE_SESSION_KEY] = mode
        return request

    def home_context(self, user, params=None):
        request = self.request_for(user, params=params)
        view = views.HomeView()
        view.setup(request)
        # Do not dispatch: login LP/first-registration redirects are not under test.
        return view.get_context_data(), request

    def list_queryset(self, user, view_class, url_name, params=None):
        view = view_class()
        view.setup(self.request_for(user, url_name, params))
        return view.get_queryset()

    def expected_ids(self, kind, audience, approval_enabled):
        # Explicit fixture expectations, independent of the shared visibility helper.
        states = set({
            'customer': self.customer_states,
            'demo': self.demo_states,
            'internal': self.internal_states,
        }[audience])
        if audience != 'internal' and not approval_enabled:
            states.add('pending')
        if kind == 'tips':
            states.add('tips_extra')
        return {self.items[kind][state].pk for state in states}

    def assert_counts_and_visibility(self, user, audience, approval_enabled):
        context, _ = self.home_context(user)
        for kind, model, view_class, url_name, count_key, recent_key, _ in self.article_specs:
            with self.subTest(kind=kind):
                queryset = self.list_queryset(user, view_class, url_name)
                expected = self.expected_ids(kind, audience, approval_enabled)
                self.assertEqual(set(queryset.values_list('pk', flat=True)), expected)
                self.assertIs(type(context[count_key]), int)
                self.assertEqual(context[count_key], len(expected))
                self.assertEqual(context[count_key], queryset.count())
                expected_recent = list(
                    model.objects.filter(pk__in=expected).order_by('-updated_at')
                    .values_list('pk', flat=True)[:3]
                )
                self.assertEqual([item.pk for item in context[recent_key]], expected_recent)
                self.assertEqual(len(context[recent_key]), 3)
                self.assertGreaterEqual(context[count_key], 3)

    def test_role_fixtures_use_real_group_and_demo_detection(self):
        predicates = {
            'customer': views.is_customer_user,
            'demo': views.is_demo_user,
            'systena': lambda user: views.in_group(user, views.SYSTENA_GROUP_NAME),
            'reviewer': views.is_reviewer_user,
        }
        for role, predicate in predicates.items():
            with self.subTest(role=role):
                self.assertTrue(predicate(self.users[role]))
        for role in self.roles:
            with self.subTest(role=role):
                self.assertEqual(views.is_demo_user(self.users[role]), role == 'demo')

    def test_home_counts_match_unsearched_lists_for_every_role_and_approval_setting(self):
        for approval_enabled in (True, False):
            with patch.object(views, 'FAQ_APPROVAL_ENABLED', approval_enabled):
                for role in self.roles:
                    with self.subTest(approval_enabled=approval_enabled, role=role):
                        audience = role if role in ('customer', 'demo') else 'internal'
                        self.assert_counts_and_visibility(self.users[role], audience, approval_enabled)

    def test_forced_modes_use_customer_visibility_without_staff_or_group_bypass(self):
        for approval_enabled in (True, False):
            with patch.object(views, 'FAQ_APPROVAL_ENABLED', approval_enabled):
                for role in self.roles:
                    for mode in (views.ACCOUNT_VIEW_MODE_CS, views.ACCOUNT_VIEW_MODE_DEMO):
                        with self.subTest(approval_enabled=approval_enabled, role=role, mode=mode):
                            # Fresh instances prevent mode/flag mutations leaking into other cases.
                            user = get_user_model().objects.get(pk=self.users[role].pk)
                            user._view_mode_override = mode
                            user._original_is_staff = user.is_staff
                            user._original_is_superuser = user.is_superuser
                            user.is_staff = False
                            user.is_superuser = False
                            self.assertTrue(views.is_customer_user(user))
                            self.assertEqual(views.is_demo_user(user), mode == views.ACCOUNT_VIEW_MODE_DEMO)
                            self.assertFalse(views.can_edit_article(user))
                            self.assertFalse(views.in_group(user, views.SYSTENA_GROUP_NAME))
                            self.assertFalse(views.is_reviewer_user(user))
                            audience = 'demo' if mode == views.ACCOUNT_VIEW_MODE_DEMO else 'customer'
                            self.assert_counts_and_visibility(user, audience, approval_enabled)

    def test_home_search_does_not_change_totals_or_latest_three(self):
        for approval_enabled in (True, False):
            with patch.object(views, 'FAQ_APPROVAL_ENABLED', approval_enabled):
                for role in self.roles:
                    user = self.users[role]
                    baseline, _ = self.home_context(user)
                    for query in ('customer_demo', 'no-matching-home-count-article'):
                        with self.subTest(approval_enabled=approval_enabled, role=role, query=query):
                            context, request = self.home_context(user, {'q': query})
                            for _, _, view_class, url_name, count_key, recent_key, _ in self.article_specs:
                                unsearched = self.list_queryset(user, view_class, url_name)
                                searched = self.list_queryset(user, view_class, url_name, {'q': query})
                                self.assertEqual(searched.count(), 1 if query == 'customer_demo' else 0)
                                self.assertEqual(context[count_key], unsearched.count())
                                self.assertEqual(context[count_key], baseline[count_key])
                                self.assertEqual(
                                    [item.pk for item in context[recent_key]],
                                    [item.pk for item in baseline[recent_key]],
                                )
                            self.assertEqual(request.GET['q'], query)

    def assert_rendered_counts(self, context, request):
        html = render_to_string(views.HomeView.template_name, context, request=request)
        parser = HomeCountSpanParser()
        parser.feed(html)
        for *_, count_key, recent_key, css_class in self.article_specs:
            with self.subTest(css_class=css_class):
                spans = [node for node in parser.spans if css_class in node['classes']]
                self.assertEqual(len(spans), 1, f'Expected one span.{css_class}')
                self.assertEqual(' '.join(spans[0]['text'].split()), f'{context[count_key]} 件')
        return html

    def test_template_renders_each_nonzero_total_not_latest_three_length(self):
        with patch.object(views, 'FAQ_APPROVAL_ENABLED', True):
            context, request = self.home_context(self.users['customer'])
            self.assertEqual(context['home_faq_count'], 4)
            self.assertEqual(context['home_tips_count'], 5)
            self.assert_rendered_counts(context, request)

    def test_template_renders_zero_counts_when_no_visible_articles_remain(self):
        # Keep rows present but all hidden, including from privileged accounts.
        for _, model, *_ in self.article_specs:
            model.objects.update(visible_to_customer=False, visible_to_systena=False)
        for approval_enabled in (True, False):
            with patch.object(views, 'FAQ_APPROVAL_ENABLED', approval_enabled):
                for role in self.roles:
                    with self.subTest(approval_enabled=approval_enabled, role=role):
                        user = self.users[role]
                        context, request = self.home_context(user)
                        for _, _, view_class, url_name, count_key, recent_key, _ in self.article_specs:
                            self.assertEqual(context[count_key], 0)
                            self.assertEqual(self.list_queryset(user, view_class, url_name).count(), 0)
                            self.assertEqual(list(context[recent_key]), [])
                        html = self.assert_rendered_counts(context, request)
                        self.assertIn('最新情報はありません。', html)