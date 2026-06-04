from collections import Counter
import csv
from datetime import datetime
import io
import json

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.views import LoginView
from django.contrib.auth.models import Group
from django.contrib.auth.mixins import UserPassesTestMixin
from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.crypto import get_random_string
from django.utils import timezone
from django.views import View
from django.views.generic import FormView, ListView, TemplateView

from .forms import (
    CSVImportForm,
    FAQCategoryCreateForm,
    KnowledgeArticleCreateForm,
    ManualForm,
    TipsCreateForm,
    UserCreateForm,
    UserUpdateForm,
)
from .models import (
    ArticleGood,
    ArticleAttachment,
    Customer,
    FAQCategory,
    FAQParentCategorySetting,
    KnowledgeArticle,
    LoginHistory,
    Manual,
    TipsGood,
    TipsArticle,
    UserProfile,
    ViewHistory,
)


ADMIN_GROUP_NAME = getattr(
    settings,
    'USER_ROLE_ADMIN_NAME',
    getattr(settings, 'USER_GROUP_ADMIN_NAME', '管理者'),
)
SYSTENA_GROUP_NAME = getattr(
    settings,
    'USER_ROLE_SYSTENA_NAME',
    getattr(settings, 'USER_GROUP_SYSTENA_NAME', 'システナ'),
)
CUSTOMER_GROUP_NAME = getattr(
    settings,
    'USER_ROLE_CUSTOMER_NAME',
    getattr(settings, 'USER_GROUP_CUSTOMER_NAME', 'カスタマー'),
)
REVIEWER_GROUP_NAME = getattr(
    settings,
    'USER_ROLE_REVIEWER_NAME',
    getattr(settings, 'USER_GROUP_REVIEWER_NAME', 'レビュアー'),
)
FAQ_APPROVAL_ENABLED = getattr(settings, 'FAQ_APPROVAL_ENABLED', False)


class HomeRedirectLoginView(LoginView):
    def get_success_url(self):
        return reverse_lazy('home')


def in_group(user, group_name):
    return user.is_authenticated and user.groups.filter(name=group_name).exists()


def visible_to_any_account_filter():
    return Q(visible_to_customer=True) | Q(visible_to_systena=True)


def is_hidden_for_all_accounts(article_or_tip):
    return not article_or_tip.visible_to_customer and not article_or_tip.visible_to_systena


def can_republish_hidden_content(user):
    return (
        user.is_authenticated
        and (
            user.is_superuser
            or in_group(user, ADMIN_GROUP_NAME)
        )
    )


def profile_user_type_from_groups(group_names):
    if SYSTENA_GROUP_NAME in group_names or ADMIN_GROUP_NAME in group_names:
        return UserProfile.USER_TYPE_SYSTENA
    return UserProfile.USER_TYPE_CUSTOMER


def client_ip_from_request(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def current_login_history(request):
    user = request.user
    if not user.is_authenticated:
        return None

    history_id = request.session.get('login_history_id')
    if history_id:
        history = LoginHistory.objects.filter(id=history_id, user=user).first()
        if history:
            return history

    history = LoginHistory.objects.filter(user=user, logged_out_at__isnull=True).first()
    if history:
        request.session['login_history_id'] = history.id
    return history


def record_view_history(
    request,
    page_name,
    search_query='',
    parent_category='',
    category='',
    path='',
):
    user = request.user
    if not user.is_authenticated:
        return

    login_history = current_login_history(request)
    resolved_path = path or request.path

    last_history = (
        ViewHistory.objects.filter(
            user=user,
            login_history=login_history,
        )
        .order_by('-viewed_at', '-id')
        .first()
    )
    if (
        last_history
        and last_history.page_name == page_name
        and last_history.path == resolved_path
        and last_history.search_query == search_query
        and last_history.parent_category == parent_category
        and last_history.category == category
    ):
        return

    ViewHistory.objects.create(
        login_history=login_history,
        user=user,
        username=user.get_username(),
        page_name=page_name,
        path=resolved_path,
        search_query=search_query,
        parent_category=parent_category,
        category=category,
        ip_address=client_ip_from_request(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:1000],
    )


def can_user_access_article(user, article):
    if is_hidden_for_all_accounts(article):
        return False

    is_staff_user = user.is_staff or user.is_superuser
    is_systena_user = in_group(user, SYSTENA_GROUP_NAME)
    is_reviewer_user = in_group(user, REVIEWER_GROUP_NAME)
    if is_staff_user:
        return True
    if is_reviewer_user:
        return True
    if is_systena_user:
        return article.visible_to_systena
    if FAQ_APPROVAL_ENABLED and not article.is_approved:
        return False
    return article.visible_to_customer


def can_user_access_tip(user, tip):
    if is_hidden_for_all_accounts(tip):
        return False

    is_staff_user = user.is_staff or user.is_superuser
    is_systena_user = in_group(user, SYSTENA_GROUP_NAME)
    is_reviewer_user = in_group(user, REVIEWER_GROUP_NAME)
    if is_staff_user:
        return True
    if is_reviewer_user:
        return True
    if is_systena_user:
        return tip.visible_to_systena
    if FAQ_APPROVAL_ENABLED and not tip.is_approved:
        return False
    return tip.visible_to_customer


def can_approve_article(user):
    return (
        user.is_authenticated
        and in_group(user, REVIEWER_GROUP_NAME)
    )


def can_edit_article(user):
    return (
        user.is_authenticated
        and (
            user.is_staff
            or user.is_superuser
            or in_group(user, ADMIN_GROUP_NAME)
            or in_group(user, REVIEWER_GROUP_NAME)
        )
    )


def is_customer_user(user):
    return (
        user.is_authenticated
        and in_group(user, CUSTOMER_GROUP_NAME)
        and not (user.is_staff or user.is_superuser)
    )


def active_until_filter(base_date=None):
    target_date = base_date or timezone.localdate()
    return Q(expires_on__isnull=True) | Q(expires_on__gte=target_date)


def hidden_parent_category_names_for_customer():
    return set(
        FAQParentCategorySetting.objects.filter(visible_to_customer=False)
        .values_list('name', flat=True)
    )


def category_visible_to_customer_parent_settings(
    category_text,
    split_categories,
    parent_category_name,
    hidden_parent_names=None,
):
    hidden_parent_names = hidden_parent_names or hidden_parent_category_names_for_customer()
    if not hidden_parent_names:
        return True

    category_names = split_categories(category_text)
    if not category_names:
        return True

    parent_names = {
        parent_category_name(category_name)
        for category_name in category_names
    }
    return parent_names.isdisjoint(hidden_parent_names)


def filter_queryset_by_customer_parent_settings(
    queryset,
    user,
    split_categories,
    parent_category_name,
):
    if not is_customer_user(user):
        return queryset

    hidden_parent_names = hidden_parent_category_names_for_customer()
    if not hidden_parent_names:
        return queryset

    visible_ids = [
        item.id
        for item in queryset
        if category_visible_to_customer_parent_settings(
            item.category,
            split_categories,
            parent_category_name,
            hidden_parent_names,
        )
    ]
    return queryset.filter(id__in=visible_ids)


def ordered_parent_category_names(parent_choices):
    parent_names = [parent_name for parent_name, _ in parent_choices]
    parent_names.extend(FAQParentCategorySetting.objects.values_list('name', flat=True))
    parent_names.extend(FAQCategory.objects.values_list('parent_name', flat=True).distinct())
    return list(dict.fromkeys(parent_name for parent_name in parent_names if parent_name))


def build_parent_category_groups(
    *,
    user,
    category_texts,
    split_categories,
    split_category_parts,
    parent_choices,
):
    hidden_parent_names = hidden_parent_category_names_for_customer() if is_customer_user(user) else set()
    parent_map = {
        parent_name: {'direct_children': {}, 'middle_groups': {}}
        for parent_name in ordered_parent_category_names(parent_choices)
        if parent_name not in hidden_parent_names
    }

    for category in FAQCategory.objects.order_by('parent_name', 'middle_name', 'child_name'):
        parent_name = category.parent_name.strip()
        if not parent_name or parent_name in hidden_parent_names:
            continue

        node = parent_map.setdefault(parent_name, {'direct_children': {}, 'middle_groups': {}})
        if category.middle_name:
            middle_node = node['middle_groups'].setdefault(category.middle_name, {})
            middle_node[category.full_name] = {
                'name': category.child_name,
                'full_name': category.full_name,
            }
        else:
            node['direct_children'][category.full_name] = {
                'name': category.child_name,
                'full_name': category.full_name,
            }

    for category_text in category_texts:
        for category_name in split_categories(category_text):
            parent_name, middle_name, child_name = split_category_parts(category_name)
            if not parent_name or parent_name in hidden_parent_names:
                continue

            node = parent_map.setdefault(parent_name, {'direct_children': {}, 'middle_groups': {}})
            if not child_name:
                continue
            if middle_name:
                middle_node = node['middle_groups'].setdefault(middle_name, {})
                middle_node[category_name] = {
                    'name': child_name,
                    'full_name': category_name,
                }
            else:
                node['direct_children'][category_name] = {
                    'name': child_name,
                    'full_name': category_name,
                }

    groups = []
    for parent_name, node in parent_map.items():
        middle_groups = []
        for middle_name, children_map in node['middle_groups'].items():
            children = list(children_map.values())
            middle_groups.append(
                {
                    'name': middle_name,
                    'children': children,
                    'full_names': [child['full_name'] for child in children],
                }
            )

        groups.append(
            {
                'name': parent_name,
                'children': list(node['direct_children'].values()),
                'middle_groups': middle_groups,
            }
        )
    return groups


def resolve_category_browser_state(
    *,
    parent_categories,
    split_category_parts,
    selected_parent='',
    selected_middle='',
    selected_category='',
):
    if selected_category:
        inferred_parent, inferred_middle, _ = split_category_parts(selected_category)
        if inferred_parent and not selected_parent:
            selected_parent = inferred_parent
        if inferred_middle and not selected_middle:
            selected_middle = inferred_middle

    selected_parent_group = next(
        (group for group in parent_categories if group['name'] == selected_parent),
        None,
    )

    if selected_parent_group and selected_parent_group['middle_groups']:
        if not any(group['name'] == selected_middle for group in selected_parent_group['middle_groups']):
            selected_middle = selected_parent_group['middle_groups'][0]['name']

    selected_middle_group = None
    if selected_parent_group and selected_middle:
        selected_middle_group = next(
            (
                group
                for group in selected_parent_group['middle_groups']
                if group['name'] == selected_middle
            ),
            None,
        )

    if not selected_parent_group:
        selected_parent = ''
        selected_middle = ''

    return {
        'selected_parent': selected_parent,
        'selected_middle': selected_middle,
        'selected_parent_group': selected_parent_group,
        'selected_middle_group': selected_middle_group,
    }


class HomeView(TemplateView):
    template_name = 'tenasapo_knowledge/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        is_admin = user.is_staff or user.is_superuser
        can_edit = can_edit_article(user)

        # 最新FAQ（権限に応じてフィルタ）
        faq_qs = (
            KnowledgeArticle.objects.filter(is_published=True)
            .filter(active_until_filter())
            .filter(visible_to_any_account_filter())
        )
        tips_qs = (
            TipsArticle.objects.filter(is_published=True)
            .filter(active_until_filter())
            .filter(visible_to_any_account_filter())
        )
        is_systena = in_group(user, SYSTENA_GROUP_NAME)
        is_reviewer = in_group(user, REVIEWER_GROUP_NAME)
        if not is_admin and not is_systena and not is_reviewer:
            faq_qs = faq_qs.filter(visible_to_customer=True)
            tips_qs = tips_qs.filter(visible_to_customer=True)
            if FAQ_APPROVAL_ENABLED:
                faq_qs = faq_qs.filter(is_approved=True)
                tips_qs = tips_qs.filter(is_approved=True)
        faq_qs = filter_queryset_by_customer_parent_settings(
            faq_qs,
            user,
            ArticleListView.split_categories,
            ArticleListView.parent_category_name,
        )
        tips_qs = filter_queryset_by_customer_parent_settings(
            tips_qs,
            user,
            TipsListView.split_categories,
            TipsListView.parent_category_name,
        )
        context['recent_faqs'] = faq_qs.order_by('-updated_at')[:3]
        context['recent_tips'] = tips_qs.order_by('-updated_at')[:3]
        menu_groups = [
            {
                'name': 'Knowledge',
                'icon': '📚',
                'items': [
                    {'label': 'FAQ', 'url_name': 'article_list'},
                    {'label': 'Tips', 'url_name': 'tip_list'},
                ],
            },
            {'name': 'Input', 'icon': '✍️', 'items': []},
            {'name': 'Manual', 'icon': '📘', 'items': []},
            {'name': 'User', 'icon': '👥', 'items': []},
            {'name': 'History', 'icon': '🕒', 'items': []},
        ]
        if can_edit:
            menu_groups[1]['items'].extend(
                [
                    {'label': 'FAQ CSV一括登録', 'url_name': 'article_csv_import'},
                    {'label': 'Tips CSV一括登録', 'url_name': 'tip_csv_import'},
                ]
            )

        if is_admin:
            menu_groups[1]['items'].extend(
                [
                    {'label': 'FAQ登録', 'url_name': 'article_create'},
                    {'label': 'Tips登録', 'url_name': 'tip_create'},
                    {'label': 'カテゴリ登録', 'url_name': 'category_create'},
                ]
            )
            menu_groups[2]['items'].append({'label': '運用マニュアル', 'url_name': 'manual_list'})
            menu_groups[3]['items'].append({'label': 'ユーザー一覧', 'url_name': 'user_list'})
            menu_groups[4]['items'].extend(
                [
                    {'label': 'データ分析まとめ', 'url_name': 'summary'},
                    {'label': 'ログイン履歴', 'url_name': 'login_history_list'},
                    {'label': '閲覧履歴', 'url_name': 'view_history_list'},
                ]
            )
        context['menu_groups'] = [group for group in menu_groups if group['items']]
        return context


class ArticleListView(ListView):
    model = KnowledgeArticle
    template_name = 'tenasapo_knowledge/article_list.html'
    context_object_name = 'articles'

    def dispatch(self, request, *args, **kwargs):
        record_view_history(
            request,
            'FAQ一覧',
            search_query=request.GET.get('q', '')[:200],
            parent_category=request.GET.get('parent_category', '')[:120],
            category=request.GET.get('category', '')[:120],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = (
            KnowledgeArticle.objects.select_related('customer', 'created_by', 'approved_by')
            .prefetch_related('attachments')
            .filter(is_published=True)
            .filter(active_until_filter())
            .filter(visible_to_any_account_filter())
            .annotate(good_count=Count('goods'))
        )

        user = self.request.user
        is_systena_user = in_group(user, SYSTENA_GROUP_NAME)
        is_reviewer_user = in_group(user, REVIEWER_GROUP_NAME)
        if (
            not (user.is_authenticated and (user.is_staff or user.is_superuser))
            and not is_systena_user
            and not is_reviewer_user
        ):
            queryset = queryset.filter(visible_to_customer=True)
            if FAQ_APPROVAL_ENABLED:
                queryset = queryset.filter(is_approved=True)

        queryset = filter_queryset_by_customer_parent_settings(
            queryset,
            user,
            self.split_categories,
            self.parent_category_name,
        )

        query = self.request.GET.get('q')
        if query:
            queryset = queryset.filter(title__icontains=query)

        parent_category = self.request.GET.get('parent_category')
        category = self.request.GET.get('category')
        if category:
            matching_ids = [
                article.id
                for article in queryset
                if category in self.split_categories(article.category)
            ]
            queryset = queryset.filter(id__in=matching_ids)
        elif parent_category:
            matching_ids = [
                article.id
                for article in queryset
                if parent_category in self.article_parent_categories(article)
            ]
            queryset = queryset.filter(id__in=matching_ids)

        return queryset.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        can_view_approval_meta = in_group(self.request.user, SYSTENA_GROUP_NAME)
        context['can_use_good'] = is_customer_user(self.request.user)
        context['can_edit_article'] = can_edit_article(self.request.user)
        context['can_view_approval_meta'] = can_view_approval_meta

        visible_articles = list(context['articles'])
        if is_customer_user(self.request.user):
            hidden_parent_names = hidden_parent_category_names_for_customer()
            visible_articles = [
                article
                for article in visible_articles
                if category_visible_to_customer_parent_settings(
                    article.category,
                    self.split_categories,
                    self.parent_category_name,
                    hidden_parent_names,
                )
            ]
        context['articles'] = visible_articles

        liked_article_ids = set(
            ArticleGood.objects.filter(
                user=self.request.user,
                article_id__in=[article.id for article in visible_articles],
            ).values_list('article_id', flat=True)
        )
        for article in visible_articles:
            article.is_gooded = article.id in liked_article_ids
            article.creator_display_name = article.created_by_name or (
                article.created_by.get_username() if article.created_by else ''
            )
            article.approver_display_name = article.approved_by_name or (
                article.approved_by.get_username() if article.approved_by else ''
            )
            article.category_chips = self.split_categories(article.category)
            ordered_attachments = sorted(
                article.attachments.all(),
                key=lambda attachment: (attachment.uploaded_at, attachment.id),
            )
            article.question_images = [
                attachment
                for attachment in ordered_attachments
                if attachment.placement == ArticleAttachment.PLACEMENT_QUESTION
            ]
            article.answer_images = [
                attachment
                for attachment in ordered_attachments
                if attachment.placement == ArticleAttachment.PLACEMENT_ANSWER
            ]
            article.file_attachments = [
                attachment
                for attachment in ordered_attachments
                if attachment.placement == ArticleAttachment.PLACEMENT_ATTACHMENT
            ]
        selected_parent = self.request.GET.get('parent_category', '')
        selected_category = self.request.GET.get('category', '')
        parent_categories = self.available_parent_category_groups()
        if selected_category and not selected_parent:
            selected_parent = self.parent_category_name(selected_category)
        context['parent_categories'] = parent_categories
        context['selected_parent_category'] = selected_parent
        context['selected_category'] = selected_category
        context['grouped_articles'] = self.group_articles(
            visible_articles,
            selected_parent,
            [group['name'] for group in parent_categories],
        )
        context['query'] = self.request.GET.get('q', '')
        return context

    @staticmethod
    def split_categories(value):
        return [category.strip() for category in (value or '').split(',') if category.strip()]

    @staticmethod
    def split_category_parts(category):
        parts = [part.strip() for part in (category or '').split('/') if part.strip()]
        if len(parts) >= 3:
            return parts[0], parts[1], '/'.join(parts[2:])
        if len(parts) == 2:
            return parts[0], '', parts[1]
        if len(parts) == 1:
            return parts[0], '', ''
        return '未分類', '', ''

    @staticmethod
    def parent_category_name(category):
        parent_name, _, _ = ArticleListView.split_category_parts(category)
        return parent_name or '未分類'

    @classmethod
    def article_parent_categories(cls, article):
        parent_names = [
            cls.parent_category_name(category)
            for category in cls.split_categories(article.category)
        ]
        return list(dict.fromkeys(parent_names or ['未分類']))

    def navigation_category_texts(self):
        queryset = (
            KnowledgeArticle.objects.filter(is_published=True)
            .filter(active_until_filter())
            .filter(visible_to_any_account_filter())
        )
        user = self.request.user
        if is_customer_user(user):
            queryset = queryset.filter(visible_to_customer=True)
            if FAQ_APPROVAL_ENABLED:
                queryset = queryset.filter(is_approved=True)
            queryset = filter_queryset_by_customer_parent_settings(
                queryset,
                user,
                self.split_categories,
                self.parent_category_name,
            )
        return queryset.values_list('category', flat=True)

    def available_parent_category_groups(self):
        return build_parent_category_groups(
            user=self.request.user,
            category_texts=self.navigation_category_texts(),
            split_categories=self.split_categories,
            split_category_parts=self.split_category_parts,
            parent_choices=FAQCategoryCreateForm.PARENT_CATEGORY_CHOICES,
        )

    @classmethod
    def group_articles(cls, articles, selected_parent='', parent_categories=None):
        grouped_articles = []
        parent_categories = [selected_parent] if selected_parent else (parent_categories or [])

        for parent_category in parent_categories:
            matched_articles = [
                article
                for article in articles
                if parent_category in cls.article_parent_categories(article)
            ]
            if matched_articles:
                grouped_articles.append(
                    {
                        'parent_name': parent_category,
                        'articles': matched_articles,
                    }
                )
        return grouped_articles


class TipsListView(ListView):
    model = TipsArticle
    template_name = 'tenasapo_knowledge/tips_list.html'
    context_object_name = 'tips_list'

    def dispatch(self, request, *args, **kwargs):
        record_view_history(
            request,
            'Tips一覧',
            search_query=request.GET.get('q', '')[:200],
            parent_category=request.GET.get('parent_category', '')[:120],
            category=request.GET.get('category', '')[:120],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = (
            TipsArticle.objects.filter(is_published=True)
            .filter(active_until_filter())
            .filter(visible_to_any_account_filter())
            .annotate(good_count=Count('goods'))
        )

        user = self.request.user
        is_systena_user = in_group(user, SYSTENA_GROUP_NAME)
        is_reviewer_user = in_group(user, REVIEWER_GROUP_NAME)
        if (
            not (user.is_authenticated and (user.is_staff or user.is_superuser))
            and not is_systena_user
            and not is_reviewer_user
        ):
            queryset = queryset.filter(visible_to_customer=True)
            if FAQ_APPROVAL_ENABLED:
                queryset = queryset.filter(is_approved=True)

        queryset = filter_queryset_by_customer_parent_settings(
            queryset,
            user,
            self.split_categories,
            self.parent_category_name,
        )

        query = self.request.GET.get('q')
        if query:
            queryset = queryset.filter(title__icontains=query)

        parent_category = self.request.GET.get('parent_category')
        category = self.request.GET.get('category')
        if category:
            matching_ids = [
                tip.id
                for tip in queryset
                if category in self.split_categories(tip.category)
            ]
            queryset = queryset.filter(id__in=matching_ids)
        elif parent_category:
            matching_ids = [
                tip.id
                for tip in queryset
                if parent_category in [
                    self.parent_category_name(category_name)
                    for category_name in self.split_categories(tip.category)
                ]
            ]
            queryset = queryset.filter(id__in=matching_ids)

        return queryset.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        can_view_approval_meta = in_group(self.request.user, SYSTENA_GROUP_NAME)
        context['can_use_good'] = is_customer_user(self.request.user)
        context['can_edit_tip'] = can_edit_article(self.request.user)
        context['can_view_approval_meta'] = can_view_approval_meta

        visible_tips = list(context['tips_list'])
        if is_customer_user(self.request.user):
            hidden_parent_names = hidden_parent_category_names_for_customer()
            visible_tips = [
                tip
                for tip in visible_tips
                if category_visible_to_customer_parent_settings(
                    tip.category,
                    self.split_categories,
                    self.parent_category_name,
                    hidden_parent_names,
                )
            ]
        context['tips_list'] = visible_tips

        liked_tip_ids = set(
            TipsGood.objects.filter(
                user=self.request.user,
                tip_id__in=[tip.id for tip in visible_tips],
            ).values_list('tip_id', flat=True)
        )

        for tip in visible_tips:
            tip.is_gooded = tip.id in liked_tip_ids
            tip.creator_display_name = tip.created_by_name or (
                tip.created_by.get_username() if tip.created_by else ''
            )
            tip.approver_display_name = tip.approved_by_name or (
                tip.approved_by.get_username() if tip.approved_by else ''
            )
            tip.category_chips = self.split_categories(tip.category)

        selected_parent = self.request.GET.get('parent_category', '')
        selected_category = self.request.GET.get('category', '')
        parent_categories = self.available_parent_category_groups()
        if selected_category and not selected_parent:
            selected_parent = self.parent_category_name(selected_category)
        context['parent_categories'] = parent_categories
        context['selected_parent_category'] = selected_parent
        context['selected_category'] = selected_category
        context['grouped_tips'] = self.group_tips(
            visible_tips,
            selected_parent,
            [group['name'] for group in parent_categories],
        )
        context['query'] = self.request.GET.get('q', '')
        return context

    @staticmethod
    def split_categories(value):
        return [category.strip() for category in (value or '').split(',') if category.strip()]

    @staticmethod
    def split_category_parts(category):
        parts = [part.strip() for part in (category or '').split('/') if part.strip()]
        if len(parts) >= 3:
            return parts[0], parts[1], '/'.join(parts[2:])
        if len(parts) == 2:
            return parts[0], '', parts[1]
        if len(parts) == 1:
            return parts[0], '', ''
        return '未分類', '', ''

    @staticmethod
    def parent_category_name(category):
        parent_name, _, _ = TipsListView.split_category_parts(category)
        return parent_name or '未分類'

    @classmethod
    def tip_parent_categories(cls, tip):
        parent_names = [
            cls.parent_category_name(category)
            for category in cls.split_categories(tip.category)
        ]
        return list(dict.fromkeys(parent_names or ['未分類']))

    def navigation_category_texts(self):
        queryset = (
            TipsArticle.objects.filter(is_published=True)
            .filter(active_until_filter())
            .filter(visible_to_any_account_filter())
        )
        user = self.request.user
        if is_customer_user(user):
            queryset = queryset.filter(visible_to_customer=True)
            if FAQ_APPROVAL_ENABLED:
                queryset = queryset.filter(is_approved=True)
            queryset = filter_queryset_by_customer_parent_settings(
                queryset,
                user,
                self.split_categories,
                self.parent_category_name,
            )
        return queryset.values_list('category', flat=True)

    def available_parent_category_groups(self):
        return build_parent_category_groups(
            user=self.request.user,
            category_texts=self.navigation_category_texts(),
            split_categories=self.split_categories,
            split_category_parts=self.split_category_parts,
            parent_choices=FAQCategoryCreateForm.PARENT_CATEGORY_CHOICES,
        )

    @classmethod
    def group_tips(cls, tips, selected_parent='', parent_categories=None):
        grouped_tips = []
        parent_categories = [selected_parent] if selected_parent else (parent_categories or [])

        for parent_category in parent_categories:
            matched_tips = [
                tip
                for tip in tips
                if parent_category in cls.tip_parent_categories(tip)
            ]
            if matched_tips:
                grouped_tips.append(
                    {
                        'parent_name': parent_category,
                        'articles': matched_tips,
                    }
                )
        return grouped_tips


class TipsCreateView(FormView):
    template_name = 'tenasapo_knowledge/tips_form.html'
    form_class = TipsCreateForm
    success_url = reverse_lazy('tip_list')

    def dispatch(self, request, *args, **kwargs):
        if not (request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)):
            messages.error(request, 'このページを閲覧する権限がありません。')
            return redirect('tip_list')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'Tips登録'
        context['submit_label'] = '登録'
        context['can_csv_import'] = can_edit_article(self.request.user)
        context['category_groups'] = KnowledgeArticleCreateView.category_groups(context['form'])
        return context

    def form_valid(self, form):
        reference_links = []
        try:
            reference_links_json = self.request.POST.get('reference_links', '[]')
            reference_links = json.loads(reference_links_json)
        except (json.JSONDecodeError, TypeError):
            reference_links = []

        tip = TipsArticle.objects.create(
            title=form.cleaned_data['title'],
            target_os=form.cleaned_data['target_os'],
            category=form.cleaned_data['category'],
            body=form.cleaned_data['body'],
            source_published_at=form.cleaned_data['source_published_at'],
            expires_on=form.cleaned_data['expires_on'],
            is_approved=not FAQ_APPROVAL_ENABLED,
            visible_to_customer=form.cleaned_data['visible_to_customer'],
            visible_to_systena=form.cleaned_data['visible_to_systena'],
            created_by=self.request.user,
            created_by_name=self.request.user.get_username(),
            reference_links=reference_links,
        )
        pdf_file = form.cleaned_data.get('pdf_file')
        if pdf_file:
            tip.pdf_file = pdf_file
            tip.save(update_fields=['pdf_file'])
        messages.success(self.request, f'Tips「{tip.title}」を登録しました。')
        return super().form_valid(form)


class TipsUpdateView(FormView):
    template_name = 'tenasapo_knowledge/tips_form.html'
    form_class = TipsCreateForm
    success_url = reverse_lazy('tip_list')

    def dispatch(self, request, *args, **kwargs):
        if not can_edit_article(request.user):
            messages.error(request, 'この操作を実行する権限がありません。')
            return redirect('tip_list')
        self.tip = get_object_or_404(TipsArticle, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        category_names = ArticleListView.split_categories(self.tip.category)
        registered_category_ids = []
        for category_name in category_names:
            parent_name, middle_name, child_name = ArticleListView.split_category_parts(category_name)
            if not parent_name or not child_name:
                continue
            category = FAQCategory.objects.filter(
                parent_name=parent_name,
                middle_name=middle_name,
                child_name=child_name,
            ).first()
            if category:
                registered_category_ids.append(category.id)
        return {
            'registered_category': registered_category_ids,
            'category': self.tip.category,
            'title': self.tip.title,
            'target_os': self.tip.target_os,
            'body': self.tip.body,
            'source_published_at': self.tip.source_published_at,
            'expires_on': self.tip.expires_on,
            'visible_to_customer': self.tip.visible_to_customer,
            'visible_to_systena': self.tip.visible_to_systena,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'Tips編集'
        context['submit_label'] = '更新'
        context['can_csv_import'] = can_edit_article(self.request.user)
        context['tip'] = self.tip
        context['tip_approver_display_name'] = self.tip.approved_by_name or (
            self.tip.approved_by.get_username() if self.tip.approved_by else ''
        )
        context['approval_enabled'] = FAQ_APPROVAL_ENABLED
        context['can_approve_tip'] = can_approve_article(self.request.user)
        context['category_groups'] = KnowledgeArticleCreateView.category_groups(context['form'])
        context['tip_pdf_url'] = self.tip.pdf_file.url if self.tip.pdf_file else None
        context['tip_pdf_name'] = self.tip.pdf_file.name.split('/')[-1] if self.tip.pdf_file else None
        context['reference_links_json'] = json.dumps(self.tip.reference_links or [])
        return context

    def form_valid(self, form):
        was_hidden_for_all = is_hidden_for_all_accounts(self.tip)
        will_be_visible_for_any = (
            form.cleaned_data['visible_to_customer']
            or form.cleaned_data['visible_to_systena']
        )
        if (
            was_hidden_for_all
            and will_be_visible_for_any
            and not can_republish_hidden_content(self.request.user)
        ):
            error_message = '全ユーザー非表示のTipsを再公開できるのはSystenaAdminのみです。'
            form.add_error('visible_to_customer', error_message)
            form.add_error('visible_to_systena', error_message)
            return self.form_invalid(form)

        reference_links = []
        try:
            reference_links_json = self.request.POST.get('reference_links', '[]')
            reference_links = json.loads(reference_links_json)
        except (json.JSONDecodeError, TypeError):
            reference_links = []

        self.tip.title = form.cleaned_data['title']
        self.tip.target_os = form.cleaned_data['target_os']
        self.tip.category = form.cleaned_data['category']
        self.tip.body = form.cleaned_data['body']
        self.tip.source_published_at = form.cleaned_data['source_published_at']
        self.tip.expires_on = form.cleaned_data['expires_on']
        self.tip.visible_to_customer = form.cleaned_data['visible_to_customer']
        self.tip.visible_to_systena = form.cleaned_data['visible_to_systena']
        self.tip.reference_links = reference_links
        update_fields = [
            'title', 'target_os', 'category', 'body', 'source_published_at', 'expires_on',
            'visible_to_customer', 'visible_to_systena', 'reference_links', 'updated_at',
        ]
        if form.cleaned_data.get('clear_pdf') and self.tip.pdf_file:
            self.tip.pdf_file.delete(save=False)
            self.tip.pdf_file = None
            update_fields.append('pdf_file')
        elif form.cleaned_data.get('pdf_file'):
            if self.tip.pdf_file:
                self.tip.pdf_file.delete(save=False)
            self.tip.pdf_file = form.cleaned_data['pdf_file']
            update_fields.append('pdf_file')
        self.tip.save(update_fields=update_fields)
        messages.success(self.request, f'Tips「{self.tip.title}」を更新しました。')
        return super().form_valid(form)


class TipsApproveView(View):
    def post(self, request, pk):
        if not can_approve_article(request.user):
            messages.error(request, '承認操作を実行する権限がありません。')
            return redirect('tip_list')
        tip = get_object_or_404(TipsArticle, pk=pk)
        if not FAQ_APPROVAL_ENABLED:
            messages.info(request, '承認機能は無効です。')
            return redirect('tip_list')

        if tip.is_approved:
            messages.info(request, f'Tips「{tip.title}」は既に承認済みです。')
            return redirect('tip_list')

        tip.is_approved = True
        tip.approved_by = request.user
        tip.approved_by_name = request.user.get_username()
        tip.save(update_fields=['is_approved', 'approved_by', 'approved_by_name', 'updated_at'])
        messages.success(request, f'Tips「{tip.title}」を承認しました。')
        return redirect('tip_list')


class TipsDeleteView(View):
    def post(self, request, pk):
        if not (request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)):
            messages.error(request, 'この操作を実行する権限がありません。')
            return redirect('tip_list')
        tip = get_object_or_404(TipsArticle, pk=pk)
        title = tip.title
        if tip.pdf_file:
            tip.pdf_file.delete(save=False)
        tip.delete()
        messages.success(request, f'Tips「{title}」を削除しました。')
        return redirect('tip_list')


class FAQAnswerViewTrackView(View):
    def post(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({'detail': 'authentication required'}, status=401)

        try:
            payload = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            payload = {}

        article_id = payload.get('article_id')
        if not article_id:
            return JsonResponse({'detail': 'article_id is required'}, status=400)

        article = get_object_or_404(KnowledgeArticle, pk=article_id, is_published=True)
        if not can_user_access_article(request.user, article):
            return JsonResponse({'detail': 'forbidden'}, status=403)

        KnowledgeArticle.objects.filter(pk=article.pk).update(answer_view_count=F('answer_view_count') + 1)
        article.refresh_from_db(fields=['answer_view_count'])

        record_view_history(
            request,
            page_name=f'FAQ回答表示: {article.title}'[:200],
            search_query=str(payload.get('search_query', ''))[:200],
            parent_category=str(payload.get('parent_category', ''))[:120],
            category=str(payload.get('category', ''))[:120],
            path=str(payload.get('source_path') or request.path)[:255],
        )

        return JsonResponse({'ok': True, 'answer_view_count': article.answer_view_count})


class FAQGoodToggleView(View):
    def post(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({'detail': 'authentication required'}, status=401)
        if not is_customer_user(request.user):
            return JsonResponse({'detail': 'forbidden'}, status=403)

        try:
            payload = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            payload = {}

        article_id = payload.get('article_id')
        if not article_id:
            return JsonResponse({'detail': 'article_id is required'}, status=400)

        article = get_object_or_404(KnowledgeArticle, pk=article_id, is_published=True)
        if not can_user_access_article(request.user, article):
            return JsonResponse({'detail': 'forbidden'}, status=403)

        reaction, created = ArticleGood.objects.get_or_create(article=article, user=request.user)
        liked = True
        if not created:
            reaction.delete()
            liked = False

        good_count = ArticleGood.objects.filter(article=article).count()

        record_view_history(
            request,
            page_name=f'FAQグッド: {article.title}'[:200],
            search_query=str(payload.get('search_query', ''))[:200],
            parent_category=str(payload.get('parent_category', ''))[:120],
            category=str(payload.get('category', ''))[:120],
            path=str(payload.get('source_path') or request.path)[:255],
        )

        return JsonResponse({'ok': True, 'liked': liked, 'good_count': good_count})


class TipsGoodToggleView(View):
    def post(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({'detail': 'authentication required'}, status=401)
        if not is_customer_user(request.user):
            return JsonResponse({'detail': 'forbidden'}, status=403)

        try:
            payload = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            payload = {}

        tip_id = payload.get('tip_id')
        if not tip_id:
            return JsonResponse({'detail': 'tip_id is required'}, status=400)

        tip = get_object_or_404(TipsArticle, pk=tip_id, is_published=True)
        if not can_user_access_tip(request.user, tip):
            return JsonResponse({'detail': 'forbidden'}, status=403)

        reaction, created = TipsGood.objects.get_or_create(tip=tip, user=request.user)
        liked = True
        if not created:
            reaction.delete()
            liked = False

        good_count = TipsGood.objects.filter(tip=tip).count()

        record_view_history(
            request,
            page_name=f'Tipsグッド: {tip.title}'[:200],
            search_query=str(payload.get('search_query', ''))[:200],
            parent_category=str(payload.get('parent_category', ''))[:120],
            category=str(payload.get('category', ''))[:120],
            path=str(payload.get('source_path') or request.path)[:255],
        )

        return JsonResponse({'ok': True, 'liked': liked, 'good_count': good_count})


class StaffRequiredMixin(UserPassesTestMixin):
    raise_exception = True

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and (user.is_staff or user.is_superuser)

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(self.request, 'このページを閲覧する権限がありません。')
            return redirect('article_list')
        return super().handle_no_permission()


class SummaryView(StaffRequiredMixin, TemplateView):
    template_name = 'tenasapo_knowledge/summary.html'
    excluded_contributor_names = {'admin'}

    def dispatch(self, request, *args, **kwargs):
        record_view_history(request, 'データ分析まとめ')
        return super().dispatch(request, *args, **kwargs)

    @classmethod
    def resolve_contributor_name(cls, saved_name='', user=None):
        name = (saved_name or '').strip()
        if not name and user:
            name = user.get_username().strip()
        return name

    @classmethod
    def is_excluded_contributor(cls, contributor_name):
        return not contributor_name or contributor_name.lower() in cls.excluded_contributor_names

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        faq_articles = list(
            KnowledgeArticle.objects.select_related('created_by', 'approved_by')
            .filter(visible_to_any_account_filter())
            .annotate(good_count=Count('goods'))
            .order_by('-good_count', '-published_at', '-created_at')
        )
        tips_articles = list(
            TipsArticle.objects.select_related('created_by', 'approved_by')
            .filter(visible_to_any_account_filter())
            .annotate(good_count=Count('goods'))
            .order_by('-good_count', '-published_at', '-created_at')
        )

        post_counts = Counter()
        review_counts = Counter()
        like_counts = Counter()

        for article in faq_articles:
            creator_name = self.resolve_contributor_name(article.created_by_name, article.created_by)
            article.creator_display_name = creator_name or '-'
            if not self.is_excluded_contributor(creator_name):
                post_counts[creator_name] += 1
                like_counts[creator_name] += article.good_count

            reviewer_name = self.resolve_contributor_name(article.approved_by_name, article.approved_by)
            if article.is_approved and not self.is_excluded_contributor(reviewer_name):
                review_counts[reviewer_name] += 1

        for tip in tips_articles:
            creator_name = self.resolve_contributor_name(tip.created_by_name, tip.created_by)
            tip.creator_display_name = creator_name or '-'
            if not self.is_excluded_contributor(creator_name):
                post_counts[creator_name] += 1
                like_counts[creator_name] += tip.good_count

            reviewer_name = self.resolve_contributor_name(tip.approved_by_name, tip.approved_by)
            if tip.is_approved and not self.is_excluded_contributor(reviewer_name):
                review_counts[reviewer_name] += 1

        contributor_names = sorted(
            set(post_counts) | set(review_counts) | set(like_counts),
            key=lambda name: (-post_counts[name], -review_counts[name], -like_counts[name], name.lower()),
        )
        contributor_summaries = [
            {
                'name': name,
                'post_count': post_counts[name],
                'review_count': review_counts[name],
                'like_count': like_counts[name],
            }
            for name in contributor_names
        ]

        context['summary_totals'] = {
            'post_count': sum(post_counts.values()),
            'review_count': sum(review_counts.values()),
            'like_count': sum(like_counts.values()),
        }
        context['contributor_summaries'] = contributor_summaries
        context['chart_labels'] = [item['name'] for item in contributor_summaries]
        context['post_chart_data'] = [item['post_count'] for item in contributor_summaries]
        context['review_chart_data'] = [item['review_count'] for item in contributor_summaries]
        context['like_chart_data'] = [item['like_count'] for item in contributor_summaries]
        context['top_faq_articles'] = faq_articles[:5]
        context['top_tips_articles'] = tips_articles[:5]
        context['contributor_post_ranking'] = sorted(
            contributor_summaries,
            key=lambda item: (-item['post_count'], -item['review_count'], -item['like_count'], item['name'].lower()),
        )

        # ── 顧客タブ用データ ──────────────────────────────────────────────
        User = get_user_model()
        customer_users = User.objects.filter(groups__name=CUSTOMER_GROUP_NAME)
        customer_user_ids = list(customer_users.values_list('id', flat=True))

        # カスタマーユーザーのアクセス数（ユーザー別）
        customer_access_per_user = (
            ViewHistory.objects.filter(user_id__in=customer_user_ids)
            .values('username')
            .annotate(access_count=Count('id'))
            .order_by('-access_count')
        )
        context['customer_access_per_user'] = list(customer_access_per_user)
        context['customer_total_access'] = sum(r['access_count'] for r in context['customer_access_per_user'])

        # カテゴリへのアクセス数（大カテゴリ別）
        category_access_qs = (
            ViewHistory.objects.filter(user_id__in=customer_user_ids)
            .exclude(parent_category='')
            .values('parent_category', 'category')
            .annotate(access_count=Count('id'))
            .order_by('parent_category', 'category')
        )
        category_access_list = list(category_access_qs)
        # 大カテゴリ別にグループ化
        category_groups_map = {}
        for row in category_access_list:
            pc = row['parent_category']
            if pc not in category_groups_map:
                category_groups_map[pc] = {'total': 0, 'children': []}
            category_groups_map[pc]['total'] += row['access_count']
            if row['category']:
                category_groups_map[pc]['children'].append({
                    'category': row['category'],
                    'access_count': row['access_count'],
                })
        context['customer_category_access'] = [
            {'parent_category': k, 'total': v['total'], 'children': v['children']}
            for k, v in sorted(category_groups_map.items(), key=lambda x: -x[1]['total'])
        ]

        # 回答へのアクセス数（FAQ回答表示）
        answer_access_qs = (
            ViewHistory.objects.filter(
                user_id__in=customer_user_ids,
                page_name__startswith='FAQ回答表示:',
            )
            .values('page_name')
            .annotate(access_count=Count('id'))
            .order_by('-access_count')
        )
        context['customer_answer_access'] = [
            {
                'title': row['page_name'].removeprefix('FAQ回答表示:').strip(),
                'access_count': row['access_count'],
            }
            for row in answer_access_qs
        ]
        context['customer_answer_total_access'] = sum(r['access_count'] for r in context['customer_answer_access'])

        # Goodボタン数（FAQナレッジ一覧、good_count > 0 のもの含む全件）
        faq_with_goods = [
            a for a in faq_articles if a.good_count > 0
        ]
        tips_with_goods = [
            t for t in tips_articles if t.good_count > 0
        ]
        context['customer_faq_goods'] = faq_with_goods
        context['customer_tips_goods'] = tips_with_goods
        context['customer_faq_good_total'] = sum(a.good_count for a in faq_with_goods)
        context['customer_tips_good_total'] = sum(t.good_count for t in tips_with_goods)

        return context


class ArticleEditorRequiredMixin(UserPassesTestMixin):
    raise_exception = True

    def test_func(self):
        return can_edit_article(self.request.user)

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(self.request, 'この操作を実行する権限がありません。')
            return redirect('article_list')
        return super().handle_no_permission()


class ArticleApprovalRequiredMixin(UserPassesTestMixin):
    raise_exception = True

    def test_func(self):
        return can_approve_article(self.request.user)

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(self.request, '承認操作を実行する権限がありません。')
            return redirect('article_list')
        return super().handle_no_permission()


class CSVImportBaseView(UserPassesTestMixin, FormView):
    raise_exception = True
    template_name = 'tenasapo_knowledge/csv_import.html'
    form_class = CSVImportForm
    required_headers = ()
    optional_headers = ()
    success_url = reverse_lazy('home')
    form_title = ''
    submit_label = '一括登録'
    back_url_name = 'home'
    import_target_label = ''

    def test_func(self):
        return can_edit_article(self.request.user)

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(self.request, 'この操作を実行する権限がありません。')
            return redirect(self.back_url_name)
        return super().handle_no_permission()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = self.form_title
        context['submit_label'] = self.submit_label
        context['back_url_name'] = self.back_url_name
        context['required_headers'] = self.required_headers
        context['optional_headers'] = self.optional_headers
        return context

    @staticmethod
    def parse_bool(raw_value, default=True):
        value = (raw_value or '').strip().lower()
        if not value:
            return default
        if value in {'1', 'true', 't', 'yes', 'y', 'on'}:
            return True
        if value in {'0', 'false', 'f', 'no', 'n', 'off'}:
            return False
        raise ValueError('true/false または 1/0 を指定してください。')

    @staticmethod
    def parse_optional_date(raw_value):
        value = (raw_value or '').strip()
        if not value:
            return None
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError as exc:
            raise ValueError('YYYY-MM-DD 形式で指定してください。') from exc

    @staticmethod
    def read_csv_rows(uploaded_file):
        raw_content = uploaded_file.read()
        text = None
        for encoding in ('utf-8-sig', 'cp932', 'utf-8'):
            try:
                text = raw_content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError('CSVの文字コードは UTF-8 または Shift_JIS(cp932) を使用してください。')

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError('ヘッダー行がありません。')

        reader.fieldnames = [(header or '').strip() for header in reader.fieldnames]
        rows = []
        for line_no, row in enumerate(reader, start=2):
            normalized = {
                (key or '').strip(): (value or '').strip()
                for key, value in row.items()
            }
            if not any(normalized.values()):
                continue
            rows.append((line_no, normalized))
        return reader.fieldnames, rows

    def validate_headers(self, headers):
        missing_headers = [header for header in self.required_headers if header not in headers]
        if missing_headers:
            missing_text = ', '.join(missing_headers)
            raise ValueError(f'必須ヘッダーが不足しています: {missing_text}')

    def form_valid(self, form):
        try:
            headers, rows = self.read_csv_rows(form.cleaned_data['csv_file'])
            self.validate_headers(headers)
        except ValueError as exc:
            form.add_error('csv_file', str(exc))
            return self.form_invalid(form)

        if not rows:
            form.add_error('csv_file', '登録対象のデータ行がありません。')
            return self.form_invalid(form)

        try:
            created_count = self.import_rows(rows)
        except ValueError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        messages.success(self.request, f'{self.import_target_label}を {created_count} 件一括登録しました。')
        return super().form_valid(form)

    def import_rows(self, rows):
        raise NotImplementedError


class KnowledgeArticleCSVImportView(CSVImportBaseView):
    success_url = reverse_lazy('article_list')
    form_title = 'FAQ CSV一括登録'
    back_url_name = 'article_list'
    import_target_label = 'FAQ'
    required_headers = ('question', 'answer', 'category')
    optional_headers = (
        'visible_to_customer',
        'visible_to_systena',
        'source_published_at',
        'expires_on',
    )

    def import_rows(self, rows):
        payloads = []
        errors = []

        for line_no, row in rows:
            row_errors = []
            question = row.get('question', '').strip()
            answer = row.get('answer', '').strip()
            category = row.get('category', '').strip()

            if not question:
                row_errors.append(f'{line_no}行目: question は必須です。')
            if not answer:
                row_errors.append(f'{line_no}行目: answer は必須です。')
            if not category:
                row_errors.append(f'{line_no}行目: category は必須です。')
            if row_errors:
                errors.extend(row_errors)
                continue

            try:
                visible_to_customer = self.parse_bool(row.get('visible_to_customer'), default=True)
                visible_to_systena = self.parse_bool(row.get('visible_to_systena'), default=True)
                source_published_at = self.parse_optional_date(row.get('source_published_at'))
                expires_on = self.parse_optional_date(row.get('expires_on'))
            except ValueError as exc:
                errors.append(f'{line_no}行目: {exc}')
                continue

            payloads.append(
                {
                    'category': category,
                    'title': question,
                    'body': answer,
                    'visible_to_customer': visible_to_customer,
                    'visible_to_systena': visible_to_systena,
                    'source_published_at': source_published_at,
                    'expires_on': expires_on,
                }
            )

        if errors:
            raise ValueError(' / '.join(errors[:10]))

        with transaction.atomic():
            for payload in payloads:
                KnowledgeArticle.objects.create(
                    **payload,
                    is_approved=not FAQ_APPROVAL_ENABLED,
                    created_by=self.request.user,
                    created_by_name=self.request.user.get_username(),
                )

        return len(payloads)


class TipsCSVImportView(CSVImportBaseView):
    success_url = reverse_lazy('tip_list')
    form_title = 'Tips CSV一括登録'
    back_url_name = 'tip_list'
    import_target_label = 'Tips'
    required_headers = ('title', 'target_os', 'body', 'category')
    optional_headers = (
        'visible_to_customer',
        'visible_to_systena',
        'source_published_at',
        'expires_on',
    )

    def import_rows(self, rows):
        payloads = []
        errors = []

        for line_no, row in rows:
            row_errors = []
            title = row.get('title', '').strip()
            target_os = row.get('target_os', '').strip()
            body = row.get('body', '').strip()
            category = row.get('category', '').strip()

            if not title:
                row_errors.append(f'{line_no}行目: title は必須です。')
            if not target_os:
                row_errors.append(f'{line_no}行目: target_os は必須です。')
            if not body:
                row_errors.append(f'{line_no}行目: body は必須です。')
            if not category:
                row_errors.append(f'{line_no}行目: category は必須です。')
            if row_errors:
                errors.extend(row_errors)
                continue

            try:
                visible_to_customer = self.parse_bool(row.get('visible_to_customer'), default=True)
                visible_to_systena = self.parse_bool(row.get('visible_to_systena'), default=True)
                source_published_at = self.parse_optional_date(row.get('source_published_at'))
                expires_on = self.parse_optional_date(row.get('expires_on'))
            except ValueError as exc:
                errors.append(f'{line_no}行目: {exc}')
                continue

            payloads.append(
                {
                    'title': title,
                    'target_os': target_os,
                    'body': body,
                    'category': category,
                    'visible_to_customer': visible_to_customer,
                    'visible_to_systena': visible_to_systena,
                    'source_published_at': source_published_at,
                    'expires_on': expires_on,
                }
            )

        if errors:
            raise ValueError(' / '.join(errors[:10]))

        with transaction.atomic():
            for payload in payloads:
                TipsArticle.objects.create(
                    **payload,
                    is_approved=not FAQ_APPROVAL_ENABLED,
                    created_by=self.request.user,
                    created_by_name=self.request.user.get_username(),
                )

        return len(payloads)


class KnowledgeArticleCreateView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/article_form.html'
    form_class = KnowledgeArticleCreateForm
    success_url = reverse_lazy('article_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'FAQ登録'
        context['submit_label'] = '登録'
        context['can_csv_import'] = can_edit_article(self.request.user)
        context['category_groups'] = self.category_groups(context['form'])
        return context

    @staticmethod
    def category_groups(form):
        selected_values = {str(value) for value in form['registered_category'].value() or []}
        groups = []
        for parent_name, _ in FAQCategoryCreateForm.PARENT_CATEGORY_CHOICES:
            categories = [
                category
                for category in form.fields['registered_category'].queryset
                if category.parent_name == parent_name
            ]
            if categories:
                groups.append(
                    {
                        'parent_name': parent_name,
                        'categories': categories,
                        'selected_values': selected_values,
                    }
                )
        return groups

    def form_valid(self, form):
        reference_links = []
        try:
            reference_links_json = self.request.POST.get('reference_links', '[]')
            reference_links = json.loads(reference_links_json)
        except (json.JSONDecodeError, TypeError):
            reference_links = []
        
        article = KnowledgeArticle.objects.create(
            category=form.cleaned_data['category'],
            title=form.cleaned_data['question'],
            body=form.cleaned_data['answer'],
            is_approved=not FAQ_APPROVAL_ENABLED,
            visible_to_customer=form.cleaned_data['visible_to_customer'],
            visible_to_systena=form.cleaned_data['visible_to_systena'],
            source_published_at=form.cleaned_data['source_published_at'],
            expires_on=form.cleaned_data['expires_on'],
            created_by=self.request.user,
            created_by_name=self.request.user.get_username(),
            reference_links=reference_links,
        )
        self.save_inline_images(article, form)
        messages.success(self.request, f'FAQ「{article.title}」を登録しました。')
        return super().form_valid(form)

    @staticmethod
    def save_inline_images(article, form):
        placements = (
            ('question_images', ArticleAttachment.PLACEMENT_QUESTION),
            ('answer_images', ArticleAttachment.PLACEMENT_ANSWER),
        )
        for field_name, placement in placements:
            for uploaded_file in form.cleaned_data.get(field_name, []):
                ArticleAttachment.objects.create(
                    article=article,
                    file=uploaded_file,
                    placement=placement,
                    display_name=uploaded_file.name,
                )


class KnowledgeArticleUpdateView(ArticleEditorRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/article_form.html'
    form_class = KnowledgeArticleCreateForm
    success_url = reverse_lazy('article_list')

    def dispatch(self, request, *args, **kwargs):
        self.article = get_object_or_404(KnowledgeArticle, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        category_names = ArticleListView.split_categories(self.article.category)
        registered_category_ids = []
        for category_name in category_names:
            parent_name, middle_name, child_name = ArticleListView.split_category_parts(category_name)
            if not parent_name or not child_name:
                continue
            category = FAQCategory.objects.filter(
                parent_name=parent_name,
                middle_name=middle_name,
                child_name=child_name,
            ).first()
            if category:
                registered_category_ids.append(category.id)
        return {
            'registered_category': registered_category_ids,
            'category': self.article.category,
            'question': self.article.title,
            'answer': self.article.body,
            'visible_to_customer': self.article.visible_to_customer,
            'visible_to_systena': self.article.visible_to_systena,
            'source_published_at': self.article.source_published_at,
            'expires_on': self.article.expires_on,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'FAQ編集'
        context['submit_label'] = '更新'
        context['can_csv_import'] = can_edit_article(self.request.user)
        context['article'] = self.article
        context['article_approver_display_name'] = self.article.approved_by_name or (
            self.article.approved_by.get_username() if self.article.approved_by else ''
        )
        context['approval_enabled'] = FAQ_APPROVAL_ENABLED
        context['can_approve_article'] = can_approve_article(self.request.user)
        context['question_images'] = self.article.attachments.filter(
            placement=ArticleAttachment.PLACEMENT_QUESTION
        ).order_by('uploaded_at', 'id')
        context['answer_images'] = self.article.attachments.filter(
            placement=ArticleAttachment.PLACEMENT_ANSWER
        ).order_by('uploaded_at', 'id')
        context['reference_links_json'] = json.dumps(self.article.reference_links or [])
        context['category_groups'] = KnowledgeArticleCreateView.category_groups(context['form'])
        return context

    def form_valid(self, form):
        was_hidden_for_all = is_hidden_for_all_accounts(self.article)
        will_be_visible_for_any = (
            form.cleaned_data['visible_to_customer']
            or form.cleaned_data['visible_to_systena']
        )
        if (
            was_hidden_for_all
            and will_be_visible_for_any
            and not can_republish_hidden_content(self.request.user)
        ):
            error_message = '全ユーザー非表示のFAQを再公開できるのはSystenaAdminのみです。'
            form.add_error('visible_to_customer', error_message)
            form.add_error('visible_to_systena', error_message)
            return self.form_invalid(form)

        reference_links = []
        try:
            reference_links_json = self.request.POST.get('reference_links', '[]')
            reference_links = json.loads(reference_links_json)
        except (json.JSONDecodeError, TypeError):
            reference_links = []

        self.article.category = form.cleaned_data['category']
        self.article.title = form.cleaned_data['question']
        self.article.body = form.cleaned_data['answer']
        self.article.visible_to_customer = form.cleaned_data['visible_to_customer']
        self.article.visible_to_systena = form.cleaned_data['visible_to_systena']
        self.article.source_published_at = form.cleaned_data['source_published_at']
        self.article.expires_on = form.cleaned_data['expires_on']
        self.article.reference_links = reference_links
        self.article.save(
            update_fields=[
                'category',
                'title',
                'body',
                'visible_to_customer',
                'visible_to_systena',
                'source_published_at',
                'expires_on',
                'reference_links',
                'updated_at',
            ]
        )
        KnowledgeArticleCreateView.save_inline_images(self.article, form)
        messages.success(self.request, f'FAQ「{self.article.title}」を更新しました。')
        return super().form_valid(form)


class KnowledgeArticleApproveView(ArticleApprovalRequiredMixin, View):
    def post(self, request, pk):
        article = get_object_or_404(KnowledgeArticle, pk=pk)
        if not FAQ_APPROVAL_ENABLED:
            messages.info(request, '承認機能は無効です。')
            return redirect('article_list')

        if article.is_approved:
            messages.info(request, f'FAQ「{article.title}」は既に承認済みです。')
            return redirect('article_list')

        article.is_approved = True
        article.approved_by = request.user
        article.approved_by_name = request.user.get_username()
        article.save(update_fields=['is_approved', 'approved_by', 'approved_by_name', 'updated_at'])
        messages.success(request, f'FAQ「{article.title}」を承認しました。')
        return redirect('article_list')


class ArticleAttachmentDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        attachment = get_object_or_404(ArticleAttachment, pk=pk)
        article_id = attachment.article_id
        attachment.file.delete(save=False)
        attachment.delete()
        messages.success(request, '画像を削除しました。')
        return redirect('article_edit', pk=article_id)


class KnowledgeArticleDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        article = get_object_or_404(KnowledgeArticle, pk=pk)
        title = article.title
        for attachment in article.attachments.all():
            attachment.file.delete(save=False)
        article.delete()
        messages.success(request, f'FAQ「{title}」を削除しました。')
        return redirect('article_list')


class FAQCategoryCreateView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/category_form.html'
    form_class = FAQCategoryCreateForm
    success_url = reverse_lazy('category_create')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'カテゴリ登録'
        context['submit_label'] = '登録'
        context['categories'] = self.categories_with_parent_visibility()
        context['category_browser'] = self.category_browser_data()
        context['category_browser_json'] = json.dumps(context['category_browser'], ensure_ascii=False)
        return context

    @staticmethod
    def categories_with_parent_visibility():
        parent_category_visibility = {
            setting.name: setting.visible_to_customer
            for setting in FAQParentCategorySetting.objects.all()
        }
        categories = list(FAQCategory.objects.all())
        for item in categories:
            item.parent_visible_to_customer = parent_category_visibility.get(item.parent_name, True)
        return categories

    @staticmethod
    def category_browser_data():
        categories = FAQCategory.objects.order_by('parent_name', 'middle_name', 'child_name')
        visibility_map = {
            item['name']: item['visible_to_customer']
            for item in FAQParentCategorySetting.objects.values('name', 'visible_to_customer')
        }

        parent_map = {}
        for category in categories:
            parent_node = parent_map.setdefault(
                category.parent_name,
                {
                    'name': category.parent_name,
                    'visible_to_customer': visibility_map.get(category.parent_name, True),
                    'direct_children': [],
                    'middles': {},
                },
            )
            if category.middle_name:
                middle_node = parent_node['middles'].setdefault(
                    category.middle_name,
                    {
                        'name': category.middle_name,
                        'children': [],
                    },
                )
                middle_node['children'].append(
                    {
                        'id': category.id,
                        'name': category.child_name,
                    }
                )
            else:
                parent_node['direct_children'].append(
                    {
                        'id': category.id,
                        'name': category.child_name,
                    }
                )

        browser = []
        for parent in parent_map.values():
            parent['middles'] = list(parent['middles'].values())
            browser.append(parent)
        return browser

    def form_valid(self, form):
        category = form.save()
        messages.success(self.request, f'カテゴリ「{category.full_name}」を登録しました。')
        return super().form_valid(form)


class FAQCategoryUpdateView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/category_form.html'
    form_class = FAQCategoryCreateForm
    success_url = reverse_lazy('category_create')

    def dispatch(self, request, *args, **kwargs):
        self.category = get_object_or_404(FAQCategory, pk=kwargs['pk'])
        self.old_full_name = self.category.full_name
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.category
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'カテゴリ修正'
        context['submit_label'] = '更新'
        context['category'] = self.category
        context['categories'] = FAQCategoryCreateView.categories_with_parent_visibility()
        context['category_browser'] = FAQCategoryCreateView.category_browser_data()
        context['category_browser_json'] = json.dumps(context['category_browser'], ensure_ascii=False)
        return context

    def form_valid(self, form):
        category = form.save()
        self.update_article_category_names(self.old_full_name, category.full_name)
        messages.success(self.request, f'カテゴリ「{category.full_name}」を更新しました。')
        return super().form_valid(form)

    @staticmethod
    def update_article_category_names(old_full_name, new_full_name):
        if old_full_name == new_full_name:
            return

        articles = KnowledgeArticle.objects.filter(category__icontains=old_full_name)
        for article in articles:
            categories = ArticleListView.split_categories(article.category)
            updated_categories = [
                new_full_name if category == old_full_name else category
                for category in categories
            ]
            if updated_categories != categories:
                article.category = ','.join(updated_categories)
                article.save(update_fields=['category', 'updated_at'])

        tips_articles = TipsArticle.objects.filter(category__icontains=old_full_name)
        for tip in tips_articles:
            categories = TipsListView.split_categories(tip.category)
            updated_categories = [
                new_full_name if category == old_full_name else category
                for category in categories
            ]
            if updated_categories != categories:
                tip.category = ','.join(updated_categories)
                tip.save(update_fields=['category', 'updated_at'])


class UserCreateView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/user_form.html'
    form_class = UserCreateForm
    success_url = reverse_lazy('user_list')

    def form_valid(self, form):
        User = get_user_model()
        emails = UserCreateForm.normalized_emails(form.cleaned_data['email_addresses'])
        selected_group_names = list(form.cleaned_data['groups'])
        is_admin = (
            form.cleaned_data['role'] == UserCreateForm.ROLE_ADMIN
            or ADMIN_GROUP_NAME in selected_group_names
        )

        user = User.objects.create_user(
            username=form.cleaned_data['username'],
            password=form.cleaned_data['password'],
            email=emails[0] if emails else '',
            is_staff=is_admin,
            is_superuser=is_admin,
        )

        group_objects = [
            Group.objects.get_or_create(name=group_name)[0]
            for group_name in selected_group_names
        ]
        user.groups.set(group_objects)

        UserProfile.objects.create(
            user=user,
            uid=form.cleaned_data.get('uid') or None,
            company_name=form.cleaned_data['company_name'],
            user_type=profile_user_type_from_groups(selected_group_names),
            email_addresses='\n'.join(emails),
            note=form.cleaned_data['note'],
        )

        customer, _ = Customer.objects.get_or_create(name=form.cleaned_data['company_name'])
        customer.users.add(user)

        messages.success(self.request, f'{user.username} を作成しました。')
        return super().form_valid(form)


class UserListView(StaffRequiredMixin, ListView):
    template_name = 'tenasapo_knowledge/user_list.html'
    context_object_name = 'users'
    paginate_by = 20

    def get_queryset(self):
        User = get_user_model()
        queryset = User.objects.select_related('knowledge_profile').prefetch_related('groups').order_by(
            'knowledge_profile__uid', 'username'
        )

        query = self.request.GET.get('q', '').strip()
        if query:
            queryset = queryset.filter(
                Q(username__icontains=query) |
                Q(knowledge_profile__uid__icontains=query)
            )

        role = self.request.GET.get('role', '').strip()
        if role == '__none__':
            queryset = queryset.filter(groups__isnull=True)
        elif role:
            queryset = queryset.filter(groups__name=role)

        return queryset.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['query'] = self.request.GET.get('q', '')
        context['selected_role'] = self.request.GET.get('role', '')
        context['roles'] = getattr(settings, 'USER_ROLES', getattr(settings, 'USER_GROUPS', []))
        return context


class UserPasswordResetView(StaffRequiredMixin, View):
    def post(self, request, pk):
        User = get_user_model()
        user = get_object_or_404(User, pk=pk)
        reset_mode = request.POST.get('reset_mode', 'random')

        if reset_mode == 'manual':
            temporary_password = request.POST.get('new_password', '').strip()
            if not temporary_password:
                messages.error(request, '手動設定するパスワードを入力してください。')
                return redirect('user_list')
            message = f'{user.username} のパスワードを手動設定しました。'
        else:
            temporary_password = get_random_string(
                12,
                allowed_chars='abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789',
            )
            message = (
                f'{user.username} のパスワードをランダム生成しました。'
                f'一時パスワード: {temporary_password}'
            )

        user.set_password(temporary_password)
        user.save(update_fields=['password'])
        if user == request.user:
            update_session_auth_hash(request, user)

        messages.warning(request, message)
        return redirect('user_list')


class LoginHistoryListView(StaffRequiredMixin, ListView):
    template_name = 'tenasapo_knowledge/login_history_list.html'
    context_object_name = 'login_histories'
    paginate_by = 50

    def get_queryset(self):
        queryset = LoginHistory.objects.select_related('user')
        query = self.request.GET.get('q', '').strip()
        if query:
            queryset = queryset.filter(username__icontains=query)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['query'] = self.request.GET.get('q', '')
        return context


class ViewHistoryListView(StaffRequiredMixin, ListView):
    template_name = 'tenasapo_knowledge/view_history_list.html'
    context_object_name = 'users'
    paginate_by = 20

    def get_queryset(self):
        User = get_user_model()
        queryset = User.objects.all().order_by('username')
        query = self.request.GET.get('q', '').strip()
        if query:
            queryset = queryset.filter(username__icontains=query)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['query'] = self.request.GET.get('q', '')

        users = list(context['users'])
        user_ids = [user.id for user in users]

        login_histories_by_user = {user.id: [] for user in users}
        view_histories_by_user = {user.id: [] for user in users}

        if user_ids:
            login_histories = LoginHistory.objects.filter(user_id__in=user_ids).order_by('-logged_in_at')
            view_histories = ViewHistory.objects.filter(user_id__in=user_ids).order_by('-viewed_at')

            for history in login_histories:
                login_histories_by_user.setdefault(history.user_id, []).append(history)
            for history in view_histories:
                view_histories_by_user.setdefault(history.user_id, []).append(history)

        context['grouped_user_histories'] = [
            {
                'user': user,
                'login_histories': login_histories_by_user.get(user.id, []),
                'view_histories': view_histories_by_user.get(user.id, []),
            }
            for user in users
        ]
        return context


class UserUpdateView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/user_form.html'
    form_class = UserUpdateForm
    success_url = reverse_lazy('user_list')

    def dispatch(self, request, *args, **kwargs):
        User = get_user_model()
        self.user_obj = get_object_or_404(User, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        profile = getattr(self.user_obj, 'knowledge_profile', None)
        return {
            'uid': profile.uid if profile else '',
            'username': self.user_obj.username,
            'company_name': profile.company_name if profile else '',
            'role': UserCreateForm.ROLE_ADMIN if self.user_obj.is_staff else UserCreateForm.ROLE_USER,
            'groups': list(self.user_obj.groups.values_list('name', flat=True)),
            'email_addresses': profile.email_addresses if profile else '',
            'note': profile.note if profile else '',
        }

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form._current_user_pk = self.user_obj.pk
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_edit'] = True
        context['user_obj'] = self.user_obj
        return context

    def form_valid(self, form):
        User = get_user_model()
        emails = UserCreateForm.normalized_emails(form.cleaned_data['email_addresses'])
        selected_group_names = list(form.cleaned_data['groups'])
        is_admin = (
            form.cleaned_data['role'] == UserCreateForm.ROLE_ADMIN
            or ADMIN_GROUP_NAME in selected_group_names
        )

        # ユーザー情報を更新
        self.user_obj.is_staff = is_admin
        self.user_obj.is_superuser = is_admin
        self.user_obj.email = emails[0] if emails else ''
        
        # パスワードが設定されている場合のみ更新
        password = form.cleaned_data.get('password', '').strip()
        if password:
            self.user_obj.set_password(password)
            if self.user_obj == self.request.user:
                update_session_auth_hash(self.request, self.user_obj)
        
        self.user_obj.save()

        group_objects = [
            Group.objects.get_or_create(name=group_name)[0]
            for group_name in selected_group_names
        ]
        self.user_obj.groups.set(group_objects)

        # プロフィールを更新
        profile, _ = UserProfile.objects.get_or_create(user=self.user_obj)
        profile.uid = form.cleaned_data.get('uid') or None
        profile.company_name = form.cleaned_data['company_name']
        profile.user_type = profile_user_type_from_groups(selected_group_names)
        profile.email_addresses = '\n'.join(emails)
        profile.note = form.cleaned_data['note']
        profile.save()

        # 顧客を更新
        customer, _ = Customer.objects.get_or_create(name=form.cleaned_data['company_name'])
        if self.user_obj not in customer.users.all():
            customer.users.add(self.user_obj)

        messages.success(self.request, f'{self.user_obj.username} を更新しました。')
        return super().form_valid(form)


class UserDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        User = get_user_model()
        user = get_object_or_404(User, pk=pk)
        
        # 削除対象がリクエスト者本人でないことを確認
        if user == request.user:
            messages.error(request, '自分自身を削除することはできません。')
            return redirect('user_list')
        
        username = user.username
        user.delete()
        messages.success(request, f'{username} を削除しました。')
        return redirect('user_list')


# ──────────────────────────────────────────
# Manual views
# ──────────────────────────────────────────

class ManualListView(StaffRequiredMixin, ListView):
    model = Manual
    template_name = 'tenasapo_knowledge/manual_list.html'
    context_object_name = 'manuals'

    def dispatch(self, request, *args, **kwargs):
        record_view_history(request, 'マニュアル一覧')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Manual.objects.all()


class ManualDetailView(StaffRequiredMixin, View):
    def get(self, request, pk):
        from django.shortcuts import render
        manual = get_object_or_404(Manual, pk=pk)
        record_view_history(request, f'マニュアル詳細: {manual.title}')
        return render(request, 'tenasapo_knowledge/manual_detail.html', {'manual': manual})

class ManualCreateView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/manual_form.html'
    form_class = ManualForm
    success_url = reverse_lazy('manual_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'マニュアル登録'
        context['submit_label'] = '登録'
        return context

    def form_valid(self, form):
        manual = form.save(commit=False)
        manual.created_by = self.request.user
        manual.save()
        messages.success(self.request, f'マニュアル「{manual.title}」を登録しました。')
        return super().form_valid(form)


class ManualUpdateView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/manual_form.html'
    form_class = ManualForm
    success_url = reverse_lazy('manual_list')

    def dispatch(self, request, *args, **kwargs):
        self.manual = get_object_or_404(Manual, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.manual
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'マニュアル編集'
        context['submit_label'] = '更新'
        context['manual'] = self.manual
        return context

    def form_valid(self, form):
        manual = form.save()
        messages.success(self.request, f'マニュアル「{manual.title}」を更新しました。')
        return super().form_valid(form)


class ManualDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        manual = get_object_or_404(Manual, pk=pk)
        title = manual.title
        manual.pdf_file.delete(save=False)
        manual.delete()
        messages.success(request, f'マニュアル「{title}」を削除しました。')
        return redirect('manual_list')
