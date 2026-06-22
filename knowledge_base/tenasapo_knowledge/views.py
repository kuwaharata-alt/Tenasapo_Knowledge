from collections import Counter
from datetime import datetime, timedelta
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.views import LoginView
from django.contrib.auth.models import Group
from django.contrib.auth.mixins import UserPassesTestMixin
from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import HttpResponse, JsonResponse
from django.template import Context, Template
from django.shortcuts import get_object_or_404, redirect

from io import BytesIO
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os
import sys
from django.urls import reverse_lazy
from django.utils.crypto import get_random_string
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import FormView, ListView, TemplateView, UpdateView

from .forms import (
    ConvenienceCategoryCreateForm,
    ConvenienceFeatureCreateForm,
    FAQCategoryCreateForm,
    KnowledgeArticleCreateForm,
    ManualForm,
    get_qr_category_hierarchy,
    RevisionHistoryForm,
    parse_target_os_entries_json,
    parse_target_os_value,
    parse_target_os_values,
    TARGET_OS_VERSION_MAP,
    TipsCreateForm,
    UserCreateForm,
    UserUpdateForm,
)
from .models import (
    ConvenienceCategory,
    ConvenienceFeature,
    ConvenienceFavorite,
    ArticleFavorite,
    ArticleGood,
    ArticleAttachment,
    Customer,
    FAQCategory,
    KnowledgeArticle,
    KnowledgeArticleImageAttachment,
    LoginHistory,
    Manual,
    RevisionHistory,
    TipsFavorite,
    TipsGood,
    TipsArticle,
    TipsImageAttachment,
    UserProfile,
    ViewHistory,
)
from .utils import resolve_saved_or_user_display_name, resolve_user_display_name


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
PASSWORD_MANAGER_USERNAMES = {
    username.lower()
    for username in getattr(settings, 'PASSWORD_MANAGER_USERNAMES', ['Admin', 'SystenaAdmin'])
}
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
CONTRIBUTOR_GROUP_NAME = getattr(
    settings,
    'USER_ROLE_CONTRIBUTOR_NAME',
    getattr(settings, 'USER_GROUP_CONTRIBUTOR_NAME', '投稿者'),
)
DEMO_GROUP_NAME = getattr(
    settings,
    'USER_ROLE_DEMO_NAME',
    getattr(settings, 'USER_GROUP_DEMO_NAME', 'demo'),
)
DEMO_GROUP_ALIASES = {'demo', 'デモ'}
FAQ_APPROVAL_ENABLED = getattr(settings, 'FAQ_APPROVAL_ENABLED', False)
ACCOUNT_VIEW_MODE_SESSION_KEY = 'account_view_mode'
ACCOUNT_VIEW_MODE_DEMO = 'demo'
ACCOUNT_VIEW_MODE_CS = 'cs'
ACCOUNT_VIEW_MODES = {ACCOUNT_VIEW_MODE_DEMO, ACCOUNT_VIEW_MODE_CS}


class HomeRedirectLoginView(LoginView):
    def get_success_url(self):
        return reverse_lazy('home')


def get_forced_account_view_mode(user):
    mode = str(getattr(user, '_view_mode_override', '') or '').strip().lower()
    return mode if mode in ACCOUNT_VIEW_MODES else ''


def can_switch_account_view_mode(user):
    if not user.is_authenticated:
        return False

    profile = getattr(user, 'knowledge_profile', None)
    if profile is not None:
        return profile.user_type == UserProfile.USER_TYPE_SYSTENA

    return user.groups.filter(name=SYSTENA_GROUP_NAME).exists()


@login_required
@require_POST
def switch_account_view_mode(request):
    if not can_switch_account_view_mode(request.user):
        messages.warning(request, 'このアカウントでは表示切替できません。')
        request.session.pop(ACCOUNT_VIEW_MODE_SESSION_KEY, None)
        next_url = request.POST.get('next') or ''
        if next_url and url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect('home')

    requested_mode = str(request.POST.get('mode') or '').strip().lower()
    if requested_mode in ACCOUNT_VIEW_MODES:
        request.session[ACCOUNT_VIEW_MODE_SESSION_KEY] = requested_mode
        label = 'Demo' if requested_mode == ACCOUNT_VIEW_MODE_DEMO else 'CS'
        messages.info(request, f'{label}表示に切り替えました。')
    else:
        request.session.pop(ACCOUNT_VIEW_MODE_SESSION_KEY, None)
        messages.info(request, '通常表示に戻しました。')

    next_url = request.POST.get('next') or ''
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect('home')


def in_group(user, group_name):
    if not user.is_authenticated:
        return False

    forced_mode = get_forced_account_view_mode(user)
    if forced_mode in ACCOUNT_VIEW_MODES:
        return group_name == CUSTOMER_GROUP_NAME

    return user.groups.filter(name=group_name).exists()


def resolve_next_path(request, fallback_url_name, **fallback_kwargs):
    candidate = (request.POST.get('next') or request.GET.get('next') or '').strip()
    if candidate and url_has_allowed_host_and_scheme(
        url=candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    if fallback_kwargs:
        return reverse_lazy(fallback_url_name, kwargs=fallback_kwargs)
    return reverse_lazy(fallback_url_name)


def target_os_entries_for_form(form):
    raw_entries = form['target_os_entries'].value() if 'target_os_entries' in form.fields else ''
    entries = parse_target_os_entries_json(raw_entries)
    if entries:
        return entries

    legacy_value = ''
    if form.is_bound:
        legacy_value = (form.data.get(form.add_prefix('target_os')) or '').strip()
    else:
        legacy_value = (form.initial.get('target_os') or '').strip()
    if legacy_value:
        return parse_target_os_values(legacy_value)

    entry = {
        'name': (form['target_os_name'].value() or '').strip() if 'target_os_name' in form.fields else '',
        'version': (form['target_os_version'].value() or '').strip() if 'target_os_version' in form.fields else '',
        'condition': (form['target_os_condition'].value() or '').strip() if 'target_os_condition' in form.fields else '',
    }
    if entry['name'] or entry['version'] or entry['condition']:
        return [entry]
    return []


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


def can_reset_approval(user):
    return user.is_authenticated and user.username.lower() in PASSWORD_MANAGER_USERNAMES


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
        username=resolve_user_display_name(user),
        page_name=page_name,
        path=resolved_path,
        search_query=search_query,
        parent_category=parent_category,
        category=category,
        ip_address=client_ip_from_request(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:1000],
    )


def _render_preview_html(text, image_list=None):
    template = Template('{% load article_extras %}{{ text|render_inline_images:images }}')
    return template.render(Context({'text': text or '', 'images': image_list or []}))


class PreviewRenderView(View):
    def post(self, request, *args, **kwargs):
        if not can_edit_article(request.user):
            return JsonResponse({'ok': False, 'error': 'permission denied'}, status=403)

        try:
            payload = json.loads(request.body.decode('utf-8'))
        except (TypeError, ValueError, json.JSONDecodeError):
            return JsonResponse({'ok': False, 'error': 'invalid payload'}, status=400)

        preview_type = str(payload.get('type') or '').strip().lower()
        if preview_type == 'faq':
            question_html = _render_preview_html(payload.get('question', ''), [])
            answer_html = _render_preview_html(payload.get('answer', ''), [])
            return JsonResponse(
                {
                    'ok': True,
                    'question_html': question_html,
                    'answer_html': answer_html,
                }
            )

        if preview_type == 'tips':
            body_html = _render_preview_html(payload.get('body', ''), [])
            return JsonResponse(
                {
                    'ok': True,
                    'body_html': body_html,
                }
            )

        return JsonResponse({'ok': False, 'error': 'invalid type'}, status=400)


def is_reviewer_user(user):
    reviewer_group_names = {group_name for group_name in {REVIEWER_GROUP_NAME, 'レビュアー', '承認者'} if group_name}
    return user.is_authenticated and any(in_group(user, group_name) for group_name in reviewer_group_names)


def is_demo_user(user):
    if not user.is_authenticated:
        return False

    forced_mode = get_forced_account_view_mode(user)
    if forced_mode == ACCOUNT_VIEW_MODE_DEMO:
        return True
    if forced_mode == ACCOUNT_VIEW_MODE_CS:
        return False

    def normalize_group_name(name):
        return str(name or '').strip().casefold()

    demo_group_names = {
        normalize_group_name(group_name)
        for group_name in DEMO_GROUP_ALIASES
        if normalize_group_name(group_name)
    }
    if DEMO_GROUP_NAME:
        normalized_demo_group_name = normalize_group_name(DEMO_GROUP_NAME)
        if normalized_demo_group_name:
            demo_group_names.add(normalized_demo_group_name)

    user_group_names = {
        normalize_group_name(group_name)
        for group_name in user.groups.values_list('name', flat=True)
        if normalize_group_name(group_name)
    }

    return (
        bool(user_group_names & demo_group_names)
        and not (user.is_staff or user.is_superuser)
    )


def can_view_restricted_knowledge_content(user, article_or_tip):
    if not getattr(article_or_tip, 'standard_contract_only', False):
        return True

    if user.is_staff or user.is_superuser:
        return True

    if is_demo_user(user):
        return False

    if can_edit_article(user):
        return True

    return True


def can_user_access_article(user, article):
    if is_hidden_for_all_accounts(article):
        return False

    is_staff_user = user.is_staff or user.is_superuser
    is_systena = in_group(user, SYSTENA_GROUP_NAME)
    is_reviewer = is_reviewer_user(user)
    if is_staff_user:
        return True
    if is_reviewer:
        return True
    if is_systena:
        return article.visible_to_systena
    if FAQ_APPROVAL_ENABLED and not article.is_approved:
        return False
    return article.visible_to_customer


def can_user_access_tip(user, tip):
    if is_hidden_for_all_accounts(tip):
        return False

    is_staff_user = user.is_staff or user.is_superuser
    is_systena = in_group(user, SYSTENA_GROUP_NAME)
    is_reviewer = is_reviewer_user(user)
    if is_staff_user:
        return True
    if is_reviewer:
        return True
    if is_systena:
        return tip.visible_to_systena
    if FAQ_APPROVAL_ENABLED and not tip.is_approved:
        return False
    return tip.visible_to_customer


def can_approve_article(user):
    return is_reviewer_user(user)


def can_edit_article(user):
    if get_forced_account_view_mode(user) in ACCOUNT_VIEW_MODES:
        return False

    return (
        user.is_authenticated
        and (
            user.is_staff
            or user.is_superuser
            or in_group(user, ADMIN_GROUP_NAME)
            or is_reviewer_user(user)
        )
    )


def is_admin_account(user):
    return user.is_authenticated and user.username.lower() == 'admin'


def is_customer_user(user):
    forced_mode = get_forced_account_view_mode(user)
    if forced_mode in ACCOUNT_VIEW_MODES:
        return user.is_authenticated

    return (
        user.is_authenticated
        and in_group(user, CUSTOMER_GROUP_NAME)
        and not (user.is_staff or user.is_superuser)
    )


def can_use_favorite(user):
    return (
        user.is_authenticated
        and (
            user.is_staff
            or user.is_superuser
            or in_group(user, SYSTENA_GROUP_NAME)
            or in_group(user, CUSTOMER_GROUP_NAME)
        )
    )


def can_use_convenience_favorite(user):
    return can_use_favorite(user)


def approval_status_value(item):
    if item.is_approved:
        return 'approved'
    if (item.remand_reason or '').strip():
        return 'remanded'
    return 'registered'


def should_count_content_view(user, content_creator=None):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff or in_group(user, ADMIN_GROUP_NAME):
        return False
    if content_creator and getattr(content_creator, 'pk', None) == user.pk:
        return False
    return True


def active_until_filter(base_date=None):
    target_date = base_date or timezone.localdate()
    return Q(expires_on__isnull=True) | Q(expires_on__gte=target_date)


def is_recently_published(published_at, *, now=None, days=14):
    if not published_at:
        return False
    current_time = now or timezone.now()
    return published_at >= (current_time - timedelta(days=days))


def hidden_parent_category_names_for_customer():
    return set()


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
    parent_names.extend(FAQCategory.objects.values_list('parent_name', flat=True).distinct())
    return list(dict.fromkeys(parent_name for parent_name in parent_names if parent_name))


def build_parent_category_groups(
    *,
    user,
):
    hidden_parent_names = hidden_parent_category_names_for_customer() if is_customer_user(user) else set()
    parent_map = {}

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


def split_registered_and_unregistered_categories(category_text):
    category_names = ArticleListView.split_categories(category_text)
    registered_category_ids = []
    unregistered_categories = []

    for category_name in category_names:
        parent_name, middle_name, child_name = ArticleListView.split_category_parts(category_name)
        category = None
        if parent_name and child_name:
            category = FAQCategory.objects.filter(
                parent_name=parent_name,
                middle_name=middle_name,
                child_name=child_name,
            ).first()

        if category:
            registered_category_ids.append(category.id)
        else:
            unregistered_categories.append(category_name)

    return (
        list(dict.fromkeys(registered_category_ids)),
        ','.join(dict.fromkeys(unregistered_categories)),
    )


class HomeView(TemplateView):
    template_name = 'tenasapo_knowledge/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        is_admin = user.is_staff or user.is_superuser
        can_edit = can_edit_article(user)
        is_customer_home = is_customer_user(user) or is_demo_user(user)

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
        is_reviewer = is_reviewer_user(user)
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
        context['is_customer_home'] = is_customer_home
        menu_groups = [
            {
                'name': 'Knowledge',
                'icon': '📚',
                'items': [
                    {'label': 'FAQ', 'url_name': 'article_list'},
                    {'label': 'Tips', 'url_name': 'tip_list'},
                    {'label': 'クイックリファレンス', 'url_name': 'convenience_list'},
                ],
            },
            {'name': 'Input', 'icon': '✍️', 'items': []},
            {'name': 'Manual', 'icon': '📘', 'items': []},
            {'name': 'User', 'icon': '👥', 'items': []},
            {'name': 'Management', 'icon': '📊', 'items': []},
            {'name': 'History', 'icon': '🕒', 'items': []},
        ]
        if is_admin:
            menu_groups[1]['items'].extend(
                [
                    {'label': 'Knowledge登録', 'url_name': 'knowledge_input'},
                    {'label': 'レビュー', 'url_name': 'review_list'},
                    {'label': 'カテゴリ登録', 'url_name': 'category_create'},
                ]
            )
            menu_groups[2]['items'].append({'label': '運用マニュアル', 'url_name': 'manual_list'})
            menu_groups[3]['items'].append({'label': 'ユーザー一覧', 'url_name': 'user_list'})
            menu_groups[4]['items'].extend(
                [
                    {'label': 'アナライズ', 'url_name': 'summary'},
                    {'label': '記事管理', 'url_name': 'article_management'},
                ]
            )
            menu_groups[5]['items'].extend(
                [
                    {'label': '更新履歴', 'url_name': 'revision_history_list'},
                    {'label': 'ログイン履歴', 'url_name': 'login_history_list'},
                    {'label': '閲覧履歴', 'url_name': 'view_history_list'},
                ]
            )
        context['menu_groups'] = [group for group in menu_groups if group['items']]
        return context


class KnowledgeInputHubView(TemplateView):
    template_name = 'tenasapo_knowledge/knowledge_input.html'

    def dispatch(self, request, *args, **kwargs):
        if not can_edit_article(request.user):
            messages.error(request, 'このページを閲覧する権限がありません。')
            return redirect('article_list')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_tab = (self.request.GET.get('tab') or 'faq').strip().lower()
        if selected_tab not in {'faq', 'tips', 'qr'}:
            selected_tab = 'faq'
        context['selected_tab'] = selected_tab
        return context


class ConvenienceListView(TemplateView):
    template_name = 'tenasapo_knowledge/convenience_list.html'

    def dispatch(self, request, *args, **kwargs):
        record_view_history(request, 'QR一覧')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sort_mode = (self.request.GET.get('sort') or 'frequency').strip().lower()
        if sort_mode not in {'frequency', 'favorite'}:
            sort_mode = 'frequency'
        query = self.request.GET.get('q', '').strip()
        selected_big = (self.request.GET.get('big_category') or '').strip()
        selected_mid = (self.request.GET.get('category') or '').strip()
        selected_small = (self.request.GET.get('small_category') or '').strip()

        all_features = list(ConvenienceFeature.objects.all())
        favorite_ids = set()
        can_use_convenience_favorite_flag = can_use_convenience_favorite(self.request.user)
        if can_use_convenience_favorite_flag:
            favorite_ids = set(
                ConvenienceFavorite.objects.filter(user=self.request.user, feature__in=all_features)
                .values_list('feature_id', flat=True)
            )
        for feature in all_features:
            try:
                frequency = int(feature.usage_frequency or '0')
            except (TypeError, ValueError):
                frequency = 0
            frequency = max(0, min(5, frequency))
            feature.usage_frequency_value = frequency
            feature.usage_frequency_stars = ('★' * frequency) + ('☆' * (5 - frequency))
            feature.is_favorited = feature.id in favorite_ids

        if sort_mode == 'favorite' and can_use_convenience_favorite_flag:
            all_features.sort(
                key=lambda f: (
                    not f.is_favorited,
                    -f.usage_frequency_value,
                    f.reference_type or '',
                    f.category or '',
                    f.middle_category or '',
                    f.display_text or '',
                    f.id,
                )
            )
        else:
            sort_mode = 'frequency'
            all_features.sort(
                key=lambda f: (
                    -f.usage_frequency_value,
                    f.reference_type or '',
                    f.category or '',
                    f.middle_category or '',
                    f.display_text or '',
                    f.id,
                )
            )

        # サイドバー階層データを構築（QR_CATEGORY_HIERARCHY を骨格に DB のカウントを付与）
        existing_big_vals = set(f.reference_type for f in all_features)
        sidebar_hierarchy = []
        for big_item in get_qr_category_hierarchy():
            big_val = big_item['value']
            big_features = [f for f in all_features if f.reference_type == big_val]
            # DBに存在するが階層未登録の中カテゴリも追加
            registered_mid_vals = {c['value'] for c in big_item['children']}
            extra_mid_vals = set(f.category for f in big_features if f.category and f.category not in registered_mid_vals)
            mid_list = list(big_item['children']) + [{'value': v, 'label': v, 'children': []} for v in sorted(extra_mid_vals)]
            mid_entries = []
            for mid_item in mid_list:
                mid_val = mid_item['value']
                mid_features = [f for f in big_features if f.category == mid_val]
                registered_small_vals = list(mid_item.get('children', []))
                extra_small_vals = [
                    v for v in dict.fromkeys(f.middle_category for f in mid_features if f.middle_category)
                    if v not in registered_small_vals
                ]
                small_entries = []
                for small_val in registered_small_vals + extra_small_vals:
                    small_features = [f for f in mid_features if f.middle_category == small_val]
                    small_entries.append({'value': small_val, 'label': small_val, 'count': len(small_features)})
                mid_entries.append({
                    'value': mid_val,
                    'label': mid_item['label'],
                    'count': len(mid_features),
                    'children': small_entries,
                })
            sidebar_hierarchy.append({
                'value': big_val,
                'label': big_item['label'],
                'count': len(big_features),
                'children': mid_entries,
            })

        # 選択値のバリデーション
        all_big_vals = [item['value'] for item in sidebar_hierarchy]
        if selected_big and selected_big not in all_big_vals:
            selected_big = ''
        if selected_big:
            big_entry = next((b for b in sidebar_hierarchy if b['value'] == selected_big), None)
            all_mid_vals = [m['value'] for m in big_entry['children']] if big_entry else []
            if selected_mid and selected_mid not in all_mid_vals:
                selected_mid = ''
                selected_small = ''
        else:
            if selected_mid:
                selected_mid = ''
            selected_small = ''

        # フィルタリング
        filtered_features = list(all_features)
        if selected_big:
            filtered_features = [f for f in filtered_features if f.reference_type == selected_big]
        if selected_mid:
            filtered_features = [f for f in filtered_features if f.category == selected_mid]
        if selected_small:
            filtered_features = [f for f in filtered_features if f.middle_category == selected_small]

        if query:
            query_lower = query.lower()
            filtered_features = [
                f for f in filtered_features
                if query_lower in (f.display_text or '').lower()
                or query_lower in (f.shortcut_key or '').lower()
                or query_lower in (f.note or '').lower()
            ]

        # 小カテゴリでグループ化
        group_map = {}
        for feature in filtered_features:
            group_name = (feature.middle_category or '（なし）').strip() or '（なし）'
            group_map.setdefault(group_name, []).append(feature)

        category_groups = [
            {'middle_name': middle_name, 'features': items}
            for middle_name, items in group_map.items()
        ]

        context['category_groups'] = category_groups
        context['sidebar_hierarchy'] = sidebar_hierarchy
        context['selected_big'] = selected_big
        context['selected_category'] = selected_mid
        context['selected_small'] = selected_small
        context['query'] = query
        context['can_create_convenience'] = (
            self.request.user.is_authenticated
            and (self.request.user.is_staff or self.request.user.is_superuser)
        )
        context['can_edit_convenience'] = context['can_create_convenience']
        context['can_use_convenience_favorite'] = can_use_convenience_favorite_flag
        context['sort_mode'] = sort_mode
        return context


class ConvenienceCreateView(FormView):
    template_name = 'tenasapo_knowledge/convenience_form.html'
    form_class = ConvenienceFeatureCreateForm
    success_url = reverse_lazy('convenience_list')

    def dispatch(self, request, *args, **kwargs):
        if not (request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)):
            messages.error(request, 'このページを閲覧する権限がありません。')
            return redirect('convenience_list')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'QR登録'
        context['submit_label'] = '登録'
        context['qr_category_hierarchy_json'] = json.dumps(get_qr_category_hierarchy(), ensure_ascii=False)
        context['category_create_url'] = f"{reverse_lazy('category_create')}?tab=qr&{urlencode({'next': self.request.get_full_path()})}"
        return context

    def form_valid(self, form):
        feature = ConvenienceFeature.objects.create(
            reference_type=form.cleaned_data['reference_type'],
            category=form.cleaned_data['category'],
            middle_category=form.cleaned_data['middle_category'],
            usage_frequency=form.cleaned_data['usage_frequency'],
            shortcut_key=form.cleaned_data['shortcut_key'],
            display_text=form.cleaned_data['display_text'],
            note=form.cleaned_data['note'],
            image=form.cleaned_data.get('image'),
        )
        messages.success(self.request, f'QR「{feature.display_text}」を登録しました。')
        return super().form_valid(form)


class ConvenienceUpdateView(FormView):
    template_name = 'tenasapo_knowledge/convenience_form.html'
    form_class = ConvenienceFeatureCreateForm
    success_url = reverse_lazy('convenience_list')

    def dispatch(self, request, *args, **kwargs):
        if not (request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)):
            messages.error(request, 'このページを閲覧する権限がありません。')
            return redirect('convenience_list')
        self.feature = get_object_or_404(ConvenienceFeature, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {
            'reference_type': self.feature.reference_type,
            'category': self.feature.category,
            'middle_category': self.feature.middle_category,
            'usage_frequency': self.feature.usage_frequency,
            'shortcut_key': self.feature.shortcut_key,
            'display_text': self.feature.display_text,
            'note': self.feature.note,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'QR編集'
        context['submit_label'] = '更新'
        context['feature'] = self.feature
        context['qr_category_hierarchy_json'] = json.dumps(get_qr_category_hierarchy(), ensure_ascii=False)
        context['category_create_url'] = f"{reverse_lazy('category_create')}?tab=qr&{urlencode({'next': self.request.get_full_path()})}"
        return context

    def form_valid(self, form):
        self.feature.reference_type = form.cleaned_data['reference_type']
        self.feature.category = form.cleaned_data['category']
        self.feature.middle_category = form.cleaned_data['middle_category']
        self.feature.usage_frequency = form.cleaned_data['usage_frequency']
        self.feature.shortcut_key = form.cleaned_data['shortcut_key']
        self.feature.display_text = form.cleaned_data['display_text']
        self.feature.note = form.cleaned_data['note']
        if form.cleaned_data.get('image'):
            if self.feature.image:
                self.feature.image.delete(save=False)
            self.feature.image = form.cleaned_data['image']
        self.feature.save()
        messages.success(self.request, f'QR「{self.feature.display_text}」を更新しました。')
        return super().form_valid(form)


class ConvenienceCategoryCreateView(FormView):
    template_name = 'tenasapo_knowledge/category_form.html'
    form_class = ConvenienceCategoryCreateForm
    success_url = reverse_lazy('category_create')

    def dispatch(self, request, *args, **kwargs):
        if not (request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)):
            messages.error(request, 'このページを閲覧する権限がありません。')
            return redirect('convenience_list')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['allow_new_reference_type'] = is_admin_account(self.request.user)
        return kwargs

    def _resolve_return_to_url(self):
        candidate = (self.request.POST.get('next') or self.request.GET.get('next') or '').strip()
        if candidate and url_has_allowed_host_and_scheme(
            url=candidate,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return candidate
        return ''

    def get_success_url(self):
        return self._resolve_return_to_url() or f"{self.success_url}?tab=qr"

    @staticmethod
    def category_browser_data():
        hierarchy = get_qr_category_hierarchy()
        browser = []
        for big in hierarchy:
            browser.append(
                {
                    'name': big['label'],
                    'value': big['value'],
                    'direct_children': [
                        {
                            'id': f"{big['value']}::{mid['value']}",
                            'name': mid['label'],
                        }
                        for mid in big.get('children', [])
                    ],
                    'middles': [
                        {
                            'name': mid['label'],
                            'children': [
                                {
                                    'id': f"{big['value']}::{mid['value']}::{small}",
                                    'name': small,
                                }
                                for small in mid.get('children', [])
                            ],
                        }
                        for mid in big.get('children', [])
                    ],
                }
            )
        return browser

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'カテゴリ登録'
        context['submit_label'] = '登録'
        context['return_to'] = self._resolve_return_to_url()
        context['category_type_tab'] = 'qr'
        context['category_browser'] = self.category_browser_data()
        context['category_browser_json'] = json.dumps(context['category_browser'], ensure_ascii=False)
        return context

    def form_valid(self, form):
        category = form.save()
        messages.success(
            self.request,
            f'QRカテゴリ「{category.reference_type} / {category.category}{(" / " + category.middle_category) if category.middle_category else ""}」を登録しました。',
        )
        return super().form_valid(form)


class ConvenienceFavoriteToggleView(View):
    def post(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({'detail': 'authentication required'}, status=401)
        if not can_use_convenience_favorite(request.user):
            return JsonResponse({'detail': 'forbidden'}, status=403)

        try:
            payload = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            payload = {}

        feature_id = payload.get('feature_id')
        if not feature_id:
            return JsonResponse({'detail': 'feature_id is required'}, status=400)

        feature = get_object_or_404(ConvenienceFeature, pk=feature_id)
        favorite, created = ConvenienceFavorite.objects.get_or_create(feature=feature, user=request.user)
        favorited = True
        if not created:
            favorite.delete()
            favorited = False

        return JsonResponse({'ok': True, 'favorited': favorited})


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
            .prefetch_related('attachments', 'images')
            .filter(is_published=True)
            .filter(active_until_filter())
            .filter(visible_to_any_account_filter())
            .annotate(good_count=Count('goods'))
        )

        user = self.request.user
        is_systena_user = in_group(user, SYSTENA_GROUP_NAME)
        is_reviewer_group_user = is_reviewer_user(user)
        if (
            not (user.is_authenticated and (user.is_staff or user.is_superuser))
            and not is_systena_user
            and not is_reviewer_group_user
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

        parent_category = self.request.GET.get('parent_category')
        category = self.request.GET.get('category')
        favorite_only = str(self.request.GET.get('favorite_only', '')).lower() in {'1', 'true', 'on'}
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

        if favorite_only:
            if can_use_favorite(self.request.user):
                queryset = queryset.filter(favorites__user=self.request.user)
            else:
                queryset = queryset.none()

        return queryset.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        can_view_approval_meta = can_edit_article(self.request.user) or in_group(self.request.user, SYSTENA_GROUP_NAME)
        context['is_demo_user'] = is_demo_user(self.request.user)
        context['can_use_good'] = is_customer_user(self.request.user)
        context['can_use_favorite'] = can_use_favorite(self.request.user)
        context['can_edit_article'] = can_edit_article(self.request.user)
        context['can_approve_article'] = can_approve_article(self.request.user)
        context['approval_enabled'] = FAQ_APPROVAL_ENABLED
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
        favorite_article_ids = set(
            ArticleFavorite.objects.filter(
                user=self.request.user,
                article_id__in=[article.id for article in visible_articles],
            ).values_list('article_id', flat=True)
        )
        for article in visible_articles:
            article.is_gooded = article.id in liked_article_ids
            article.is_favorited = article.id in favorite_article_ids
            article.is_new_badge = is_recently_published(article.published_at)
            article.can_view_content = can_view_restricted_knowledge_content(self.request.user, article)
            article.creator_display_name = resolve_saved_or_user_display_name(
                article.created_by_name,
                article.created_by,
            )
            article.approver_display_name = resolve_saved_or_user_display_name(
                article.approved_by_name,
                article.approved_by,
            )
            article.approval_status = approval_status_value(article)
            article.category_chips = list(dict.fromkeys(self.split_categories(article.category)))
            article.target_os_chips = parse_target_os_values(article.target_os)
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
        all_category_texts = list(self.navigation_category_texts())
        parent_counts, category_counts = self.category_count_maps_from_texts(all_category_texts)
        for parent_category in parent_categories:
            parent_category['count'] = parent_counts.get(parent_category.get('name', ''), 0)

            for child_category in parent_category.get('children', []):
                child_category['count'] = category_counts.get(child_category.get('full_name', ''), 0)

            for middle_group in parent_category.get('middle_groups', []):
                middle_group['count'] = sum(
                    category_counts.get(full_name, 0)
                    for full_name in middle_group.get('full_names', [])
                )
                for child_category in middle_group.get('children', []):
                    child_category['count'] = category_counts.get(child_category.get('full_name', ''), 0)
        if selected_category and not selected_parent:
            selected_parent = self.parent_category_name(selected_category)
        context['parent_categories'] = parent_categories
        context['selected_parent_category'] = selected_parent
        context['selected_category'] = selected_category
        context['all_count'] = len(all_category_texts)
        context['grouped_articles'] = self.group_articles(
            visible_articles,
            selected_parent,
            [group['name'] for group in parent_categories],
        )
        context['query'] = self.request.GET.get('q', '')
        context['favorite_only'] = str(self.request.GET.get('favorite_only', '')).lower() in {'1', 'true', 'on'}
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

    @classmethod
    def category_count_maps(cls, articles):
        parent_counts = {}
        category_counts = {}
        for article in articles:
            seen_categories = set()
            seen_parents = set()
            for category_name in cls.split_categories(article.category):
                if category_name and category_name not in seen_categories:
                    category_counts[category_name] = category_counts.get(category_name, 0) + 1
                    seen_categories.add(category_name)

                parent_name = cls.parent_category_name(category_name)
                if parent_name and parent_name not in seen_parents:
                    parent_counts[parent_name] = parent_counts.get(parent_name, 0) + 1
                    seen_parents.add(parent_name)
        return parent_counts, category_counts

    @classmethod
    def category_count_maps_from_texts(cls, category_texts):
        parent_counts = {}
        category_counts = {}
        for category_text in category_texts:
            seen_categories = set()
            seen_parents = set()
            for category_name in cls.split_categories(category_text):
                if category_name and category_name not in seen_categories:
                    category_counts[category_name] = category_counts.get(category_name, 0) + 1
                    seen_categories.add(category_name)

                parent_name = cls.parent_category_name(category_name)
                if parent_name and parent_name not in seen_parents:
                    parent_counts[parent_name] = parent_counts.get(parent_name, 0) + 1
                    seen_parents.add(parent_name)
        return parent_counts, category_counts

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
        )

    @classmethod
    def group_articles(cls, articles, selected_parent='', parent_categories=None):
        if not selected_parent:
            return [{'parent_name': '', 'articles': list(articles)}] if articles else []

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
            TipsArticle.objects.prefetch_related('images').filter(is_published=True)
            .filter(active_until_filter())
            .filter(visible_to_any_account_filter())
            .annotate(good_count=Count('goods'))
        )

        user = self.request.user
        is_systena_user = in_group(user, SYSTENA_GROUP_NAME)
        is_reviewer_group_user = is_reviewer_user(user)
        if (
            not (user.is_authenticated and (user.is_staff or user.is_superuser))
            and not is_systena_user
            and not is_reviewer_group_user
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
        favorite_only = str(self.request.GET.get('favorite_only', '')).lower() in {'1', 'true', 'on'}
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

        if favorite_only:
            if can_use_favorite(self.request.user):
                queryset = queryset.filter(favorites__user=self.request.user)
            else:
                queryset = queryset.none()

        return queryset.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        can_view_approval_meta = can_edit_article(self.request.user) or in_group(self.request.user, SYSTENA_GROUP_NAME)
        context['is_demo_user'] = is_demo_user(self.request.user)
        context['can_use_good'] = is_customer_user(self.request.user)
        context['can_use_favorite'] = can_use_favorite(self.request.user)
        context['can_edit_tip'] = can_edit_article(self.request.user)
        context['can_approve_tip'] = can_approve_article(self.request.user)
        context['approval_enabled'] = FAQ_APPROVAL_ENABLED
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
        favorite_tip_ids = set(
            TipsFavorite.objects.filter(
                user=self.request.user,
                tip_id__in=[tip.id for tip in visible_tips],
            ).values_list('tip_id', flat=True)
        )

        for tip in visible_tips:
            tip.is_gooded = tip.id in liked_tip_ids
            tip.is_favorited = tip.id in favorite_tip_ids
            tip.is_new_badge = is_recently_published(tip.published_at)
            tip.can_view_content = can_view_restricted_knowledge_content(self.request.user, tip)
            tip.creator_display_name = resolve_saved_or_user_display_name(
                tip.created_by_name,
                tip.created_by,
            )
            tip.approver_display_name = resolve_saved_or_user_display_name(
                tip.approved_by_name,
                tip.approved_by,
            )
            tip.approval_status = approval_status_value(tip)
            tip.category_chips = list(dict.fromkeys(self.split_categories(tip.category)))
            tip.target_os_chips = parse_target_os_values(tip.target_os)
            tip.inline_images = sorted(
                tip.images.all(),
                key=lambda image: (image.uploaded_at, image.id),
            )

        selected_parent = self.request.GET.get('parent_category', '')
        selected_category = self.request.GET.get('category', '')
        parent_categories = self.available_parent_category_groups()
        all_category_texts = list(self.navigation_category_texts())
        parent_counts, category_counts = self.category_count_maps_from_texts(all_category_texts)
        for parent_category in parent_categories:
            parent_category['count'] = parent_counts.get(parent_category.get('name', ''), 0)

            for child_category in parent_category.get('children', []):
                child_category['count'] = category_counts.get(child_category.get('full_name', ''), 0)

            for middle_group in parent_category.get('middle_groups', []):
                middle_group['count'] = sum(
                    category_counts.get(full_name, 0)
                    for full_name in middle_group.get('full_names', [])
                )
                for child_category in middle_group.get('children', []):
                    child_category['count'] = category_counts.get(child_category.get('full_name', ''), 0)
        if selected_category and not selected_parent:
            selected_parent = self.parent_category_name(selected_category)
        context['parent_categories'] = parent_categories
        context['selected_parent_category'] = selected_parent
        context['selected_category'] = selected_category
        context['all_count'] = len(all_category_texts)
        context['grouped_tips'] = self.group_tips(
            visible_tips,
            selected_parent,
            [group['name'] for group in parent_categories],
        )
        context['query'] = self.request.GET.get('q', '')
        context['favorite_only'] = str(self.request.GET.get('favorite_only', '')).lower() in {'1', 'true', 'on'}
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

    @classmethod
    def category_count_maps(cls, tips):
        parent_counts = {}
        category_counts = {}
        for tip in tips:
            seen_categories = set()
            seen_parents = set()
            for category_name in cls.split_categories(tip.category):
                if category_name and category_name not in seen_categories:
                    category_counts[category_name] = category_counts.get(category_name, 0) + 1
                    seen_categories.add(category_name)

                parent_name = cls.parent_category_name(category_name)
                if parent_name and parent_name not in seen_parents:
                    parent_counts[parent_name] = parent_counts.get(parent_name, 0) + 1
                    seen_parents.add(parent_name)
        return parent_counts, category_counts

    @classmethod
    def category_count_maps_from_texts(cls, category_texts):
        parent_counts = {}
        category_counts = {}
        for category_text in category_texts:
            seen_categories = set()
            seen_parents = set()
            for category_name in cls.split_categories(category_text):
                if category_name and category_name not in seen_categories:
                    category_counts[category_name] = category_counts.get(category_name, 0) + 1
                    seen_categories.add(category_name)

                parent_name = cls.parent_category_name(category_name)
                if parent_name and parent_name not in seen_parents:
                    parent_counts[parent_name] = parent_counts.get(parent_name, 0) + 1
                    seen_parents.add(parent_name)
        return parent_counts, category_counts

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
        )

    @classmethod
    def group_tips(cls, tips, selected_parent='', parent_categories=None):
        if not selected_parent:
            return [{'parent_name': '', 'articles': list(tips)}] if tips else []

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
        context['category_create_url'] = f"{reverse_lazy('category_create')}?{urlencode({'next': self.request.get_full_path()})}"
        context['category_groups'] = KnowledgeArticleCreateView.category_groups(context['form'])
        context['category_browser'] = FAQCategoryCreateView.category_browser_data()
        context['category_browser_json'] = json.dumps(context['category_browser'], ensure_ascii=False)
        context['registered_category_values_json'] = json.dumps(
            KnowledgeArticleCreateView.selected_registered_category_values(context['form']),
            ensure_ascii=False,
        )
        context['target_os_version_map_json'] = json.dumps(TARGET_OS_VERSION_MAP, ensure_ascii=False)
        context['target_os_entries_json'] = json.dumps(target_os_entries_for_form(context['form']), ensure_ascii=False)
        context['is_demo_user'] = is_demo_user(self.request.user)
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
            created_by_name=resolve_user_display_name(self.request.user),
            reference_links=reference_links,
        )
        pdf_file = form.cleaned_data.get('pdf_file')
        if pdf_file:
            tip.pdf_file = pdf_file
            tip.save(update_fields=['pdf_file'])
        self.save_inline_images(tip, form)
        messages.success(self.request, f'Tips「{tip.title}」を登録しました。')
        return super().form_valid(form)

    @staticmethod
    def save_inline_images(tip, form):
        for uploaded_file in form.cleaned_data.get('tips_images', []):
            TipsImageAttachment.objects.create(
                tip=tip,
                file=uploaded_file,
                display_name=uploaded_file.name,
            )


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
        registered_category_ids, _ = split_registered_and_unregistered_categories(
            self.tip.category
        )
        parsed_target_os = parse_target_os_value(self.tip.target_os)
        return {
            'registered_category': registered_category_ids,
            'title': self.tip.title,
            'target_os_entries': json.dumps(parse_target_os_values(self.tip.target_os), ensure_ascii=False),
            'target_os_name': parsed_target_os['name'],
            'target_os_version': parsed_target_os['version'],
            'target_os_condition': parsed_target_os['condition'],
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
        context['category_create_url'] = f"{reverse_lazy('category_create')}?{urlencode({'next': self.request.get_full_path()})}"
        context['tip'] = self.tip
        context['tip_approver_display_name'] = resolve_saved_or_user_display_name(
            self.tip.approved_by_name,
            self.tip.approved_by,
        )
        context['approval_enabled'] = FAQ_APPROVAL_ENABLED
        context['can_approve_tip'] = can_approve_article(self.request.user)
        context['can_remand_tip'] = can_approve_article(self.request.user)
        context['can_reset_tip_approval'] = can_reset_approval(self.request.user)
        context['tip_approval_status'] = approval_status_value(self.tip)
        context['category_groups'] = KnowledgeArticleCreateView.category_groups(context['form'])
        context['category_browser'] = FAQCategoryCreateView.category_browser_data()
        context['category_browser_json'] = json.dumps(context['category_browser'], ensure_ascii=False)
        context['registered_category_values_json'] = json.dumps(
            KnowledgeArticleCreateView.selected_registered_category_values(context['form']),
            ensure_ascii=False,
        )
        context['target_os_version_map_json'] = json.dumps(TARGET_OS_VERSION_MAP, ensure_ascii=False)
        context['target_os_entries_json'] = json.dumps(target_os_entries_for_form(context['form']), ensure_ascii=False)
        context['tip_pdf_url'] = self.tip.pdf_file.url if self.tip.pdf_file else None
        context['tip_pdf_name'] = self.tip.pdf_file.name.split('/')[-1] if self.tip.pdf_file else None
        context['tip_images'] = self.tip.images.all().order_by('uploaded_at', 'id')
        context['reference_links_json'] = json.dumps(self.tip.reference_links or [])
        candidate = (self.request.POST.get('next') or self.request.GET.get('next') or '').strip()
        if candidate and url_has_allowed_host_and_scheme(
            url=candidate,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            context['return_to'] = candidate
        else:
            context['return_to'] = ''
        context['is_demo_user'] = is_demo_user(self.request.user)
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
        TipsCreateView.save_inline_images(self.tip, form)
        messages.success(self.request, f'Tips「{self.tip.title}」を更新しました。')
        return super().form_valid(form)


class TipsApproveView(View):
    def post(self, request, pk):
        if not can_approve_article(request.user):
            messages.error(request, '承認操作を実行する権限がありません。')
            return redirect(resolve_next_path(request, 'tip_list'))
        tip = get_object_or_404(TipsArticle, pk=pk)
        if not FAQ_APPROVAL_ENABLED:
            messages.info(request, '承認機能は無効です。')
            return redirect(resolve_next_path(request, 'tip_list'))

        if tip.is_approved:
            messages.info(request, f'Tips「{tip.title}」は既に承認済みです。')
            return redirect(resolve_next_path(request, 'tip_list'))

        standard_contract_only_raw = str(request.POST.get('standard_contract_only', '1')).strip().lower()
        standard_contract_only = standard_contract_only_raw in {'1', 'true', 'on', 'yes'}
        visible_to_customer_raw = str(request.POST.get('visible_to_customer', '1')).strip().lower()
        visible_to_customer = visible_to_customer_raw in {'1', 'true', 'on', 'yes'}

        tip.is_approved = True
        tip.standard_contract_only = standard_contract_only
        tip.visible_to_customer = visible_to_customer
        tip.approved_by = request.user
        tip.approved_by_name = resolve_user_display_name(request.user)
        tip.remand_reason = ''
        tip.save(update_fields=['is_approved', 'standard_contract_only', 'visible_to_customer', 'approved_by', 'approved_by_name', 'remand_reason', 'updated_at'])
        messages.success(request, f'Tips「{tip.title}」を承認しました。')
        return redirect(resolve_next_path(request, 'tip_list'))


class TipsRemandView(View):
    def post(self, request, pk):
        if not can_approve_article(request.user):
            messages.error(request, '承認操作を実行する権限がありません。')
            return redirect(resolve_next_path(request, 'tip_list'))
        tip = get_object_or_404(TipsArticle, pk=pk)
        if not FAQ_APPROVAL_ENABLED:
            messages.info(request, '承認機能は無効です。')
            return redirect(resolve_next_path(request, 'tip_list'))

        reason = (request.POST.get('remand_reason') or '').strip() or '差戻し'

        tip.is_approved = False
        tip.approved_by = None
        tip.approved_by_name = ''
        tip.remand_reason = reason
        tip.save(update_fields=['is_approved', 'approved_by', 'approved_by_name', 'remand_reason', 'updated_at'])
        messages.success(request, f'Tips「{tip.title}」を差し戻しました。')
        return redirect(resolve_next_path(request, 'tip_list'))


class TipsApprovalResetView(View):
    def post(self, request, pk):
        if not can_reset_approval(request.user):
            messages.error(request, '承認リセットを実行する権限がありません。')
            return redirect(resolve_next_path(request, 'tip_list'))

        tip = get_object_or_404(TipsArticle, pk=pk)
        tip.is_approved = False
        tip.approved_by = None
        tip.approved_by_name = ''
        tip.remand_reason = ''
        tip.save(update_fields=['is_approved', 'approved_by', 'approved_by_name', 'remand_reason', 'updated_at'])
        messages.success(request, f'Tips「{tip.title}」の承認をリセットしました。')
        return redirect(resolve_next_path(request, 'tip_edit', pk=pk))


class TipsDeleteView(View):
    def post(self, request, pk):
        if not (request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)):
            messages.error(request, 'この操作を実行する権限がありません。')
            return redirect('tip_list')
        tip = get_object_or_404(TipsArticle, pk=pk)
        title = tip.title
        if tip.pdf_file:
            tip.pdf_file.delete(save=False)
        for image in tip.images.all():
            image.file.delete(save=False)
        tip.delete()
        messages.success(request, f'Tips「{title}」を削除しました。')
        return redirect('tip_list')


class TipsImageAttachmentDeleteView(View):
    def post(self, request, pk):
        if not can_edit_article(request.user):
            messages.error(request, 'この操作を実行する権限がありません。')
            return redirect('tip_list')

        image = get_object_or_404(TipsImageAttachment, pk=pk)
        tip_id = image.tip_id
        image.file.delete(save=False)
        image.delete()
        messages.success(request, 'Tips画像を削除しました。')
        return redirect('tip_edit', pk=tip_id)


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

        if should_count_content_view(request.user, article.created_by):
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


class TipViewTrackView(View):
    def post(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({'detail': 'authentication required'}, status=401)

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

        if should_count_content_view(request.user, tip.created_by):
            TipsArticle.objects.filter(pk=tip.pk).update(view_count=F('view_count') + 1)
            tip.refresh_from_db(fields=['view_count'])

        record_view_history(
            request,
            page_name=f'Tips表示: {tip.title}'[:200],
            search_query=str(payload.get('search_query', ''))[:200],
            parent_category=str(payload.get('parent_category', ''))[:120],
            category=str(payload.get('category', ''))[:120],
            path=str(payload.get('source_path') or request.path)[:255],
        )

        return JsonResponse({'ok': True, 'view_count': tip.view_count})


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


class FAQFavoriteToggleView(View):
    def post(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({'detail': 'authentication required'}, status=401)
        if not can_use_favorite(request.user):
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

        favorite, created = ArticleFavorite.objects.get_or_create(article=article, user=request.user)
        favorited = True
        if not created:
            favorite.delete()
            favorited = False

        record_view_history(
            request,
            page_name=f'FAQお気に入り: {article.title}'[:200],
            search_query=str(payload.get('search_query', ''))[:200],
            parent_category=str(payload.get('parent_category', ''))[:120],
            category=str(payload.get('category', ''))[:120],
            path=str(payload.get('source_path') or request.path)[:255],
        )

        return JsonResponse({'ok': True, 'favorited': favorited})


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


class TipsFavoriteToggleView(View):
    def post(self, request):
        if not request.user.is_authenticated:
            return JsonResponse({'detail': 'authentication required'}, status=401)
        if not can_use_favorite(request.user):
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

        favorite, created = TipsFavorite.objects.get_or_create(tip=tip, user=request.user)
        favorited = True
        if not created:
            favorite.delete()
            favorited = False

        record_view_history(
            request,
            page_name=f'Tipsお気に入り: {tip.title}'[:200],
            search_query=str(payload.get('search_query', ''))[:200],
            parent_category=str(payload.get('parent_category', ''))[:120],
            category=str(payload.get('category', ''))[:120],
            path=str(payload.get('source_path') or request.path)[:255],
        )

        return JsonResponse({'ok': True, 'favorited': favorited})


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


def _can_manage_all_users(user):
    """Admin/SystenaAdmin のアカウント名のみ、他ユーザー管理を許可する。"""
    return user.is_authenticated and user.username.lower() in PASSWORD_MANAGER_USERNAMES


def _can_manage_target_user(actor, target_user):
    return actor.is_authenticated and (
        _can_manage_all_users(actor) or actor.pk == getattr(target_user, 'pk', None)
    )


class StaffOrSelfRequiredMixin(UserPassesTestMixin):
    """Admin/SystenaAdmin、またはログイン中の本人のみアクセスを許可するMixin。"""

    raise_exception = True

    def test_func(self):
        user = self.request.user
        if not user.is_authenticated:
            return False
        if _can_manage_all_users(user):
            return True
        # URLに pk が含まれる場合のみ本人チェックを行う
        pk = self.kwargs.get('pk')
        return pk is not None and user.pk == pk

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
        return resolve_saved_or_user_display_name(saved_name, user, default='').strip()

    @classmethod
    def is_excluded_contributor(cls, contributor_name):
        return not contributor_name or contributor_name.lower() in cls.excluded_contributor_names

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        selected_period = (self.request.GET.get('period') or 'all').strip().lower()
        if selected_period not in {'all', 'current', 'previous'}:
            selected_period = 'all'

        from_date = None
        to_date = None
        today = timezone.localdate()
        if selected_period == 'current':
            from_date = today.replace(day=1)
            to_date = today
        elif selected_period == 'previous':
            first_day_this_month = today.replace(day=1)
            last_day_previous_month = first_day_this_month - timedelta(days=1)
            from_date = last_day_previous_month.replace(day=1)
            to_date = last_day_previous_month
        current_month_start = today.replace(day=1)

        base_faq_qs = (
            KnowledgeArticle.objects.select_related('created_by')
            .filter(visible_to_any_account_filter())
            .annotate(good_count=Count('goods'))
        )
        base_tips_qs = (
            TipsArticle.objects.select_related('created_by')
            .filter(visible_to_any_account_filter())
            .annotate(good_count=Count('goods'))
        )

        faq_qs = base_faq_qs
        tips_qs = base_tips_qs

        if from_date:
            faq_qs = faq_qs.filter(created_at__date__gte=from_date)
            tips_qs = tips_qs.filter(created_at__date__gte=from_date)
        if to_date:
            faq_qs = faq_qs.filter(created_at__date__lte=to_date)
            tips_qs = tips_qs.filter(created_at__date__lte=to_date)

        faq_articles = list(faq_qs.order_by('-created_at'))
        tips_articles = list(tips_qs.order_by('-created_at'))
        current_month_faq_articles = list(
            base_faq_qs.filter(
                created_at__date__gte=current_month_start,
                created_at__date__lte=today,
            ).order_by('-created_at')
        )
        current_month_tips_articles = list(
            base_tips_qs.filter(
                created_at__date__gte=current_month_start,
                created_at__date__lte=today,
            ).order_by('-created_at')
        )

        User = get_user_model()
        summary_users = User.objects.filter(
            groups__name=CONTRIBUTOR_GROUP_NAME
        ).distinct().prefetch_related('knowledge_profile').order_by('knowledge_profile__uid', 'id')

        member_map = {}
        member_monthly_map = {}
        member_order_map = {}
        member_current_month_map = {}
        summary_monthly_map = {}

        def ensure_member(name, member_id=None, management_uid=''):
            node = member_map.get(name)
            if node is None:
                if member_id is None:
                    member_id = member_order_map.get(name, 10**9)
                node = {
                    'member_id': member_id,
                    'management_uid': management_uid,
                    'name': name,
                    'approved_count_total': 0,
                    'faq_approved_count': 0,
                    'faq_post_count': 0,
                    'tips_approved_count': 0,
                    'tips_post_count': 0,
                    'good_count_total': 0,
                    'view_count_total': 0,
                }
                member_map[name] = node
            return node

        def ensure_member_month(name, month_key):
            monthly = member_monthly_map.setdefault(name, {})
            month_node = monthly.get(month_key)
            if month_node is None:
                month_node = {
                    'month': month_key,
                    'approved_count_total': 0,
                    'faq_approved_count': 0,
                    'faq_post_count': 0,
                    'tips_approved_count': 0,
                    'tips_post_count': 0,
                    'good_count_total': 0,
                    'view_count_total': 0,
                }
                monthly[month_key] = month_node
            return month_node

        def ensure_current_month(name):
            node = member_current_month_map.get(name)
            if node is None:
                node = {
                    'approved_count_total': 0,
                    'faq_approved_count': 0,
                    'faq_post_count': 0,
                    'tips_approved_count': 0,
                    'tips_post_count': 0,
                }
                member_current_month_map[name] = node
            return node

        def ensure_summary_month(month_key):
            node = summary_monthly_map.get(month_key)
            if node is None:
                node = {
                    'month': month_key,
                    'approved_count_total': 0,
                    'faq_approved_count': 0,
                    'faq_post_count': 0,
                    'tips_approved_count': 0,
                    'tips_post_count': 0,
                    'good_count_total': 0,
                    'view_count_total': 0,
                }
                summary_monthly_map[month_key] = node
            return node

        for user in summary_users:
            display_name = resolve_user_display_name(user).strip()
            if self.is_excluded_contributor(display_name):
                continue
            if display_name not in member_order_map:
                profile = getattr(user, 'knowledge_profile', None)
                uid_value = (getattr(profile, 'uid', '') or '').strip()
                uid_order = int(uid_value) if uid_value.isdigit() else 10**9 + user.id
                member_order_map[display_name] = uid_order
            profile = getattr(user, 'knowledge_profile', None)
            ensure_member(
                display_name,
                user.id,
                (getattr(profile, 'uid', '') or '').strip(),
            )

        for article in faq_articles:
            creator_name = self.resolve_contributor_name(article.created_by_name, article.created_by)
            if self.is_excluded_contributor(creator_name):
                continue
            if creator_name not in member_map:
                continue
            member = ensure_member(creator_name)
            month_key = article.created_at.strftime('%Y-%m')
            month_node = ensure_member_month(creator_name, month_key)
            summary_month = ensure_summary_month(month_key)
            member['faq_post_count'] += 1
            if article.is_approved:
                member['approved_count_total'] += 1
                member['faq_approved_count'] += 1
                month_node['approved_count_total'] += 1
                month_node['faq_approved_count'] += 1
                summary_month['approved_count_total'] += 1
                summary_month['faq_approved_count'] += 1
            member['good_count_total'] += article.good_count
            member['view_count_total'] += article.answer_view_count
            month_node['faq_post_count'] += 1
            month_node['good_count_total'] += article.good_count
            month_node['view_count_total'] += article.answer_view_count
            summary_month['faq_post_count'] += 1
            summary_month['good_count_total'] += article.good_count
            summary_month['view_count_total'] += article.answer_view_count

        for tip in tips_articles:
            creator_name = self.resolve_contributor_name(tip.created_by_name, tip.created_by)
            if self.is_excluded_contributor(creator_name):
                continue
            if creator_name not in member_map:
                continue
            member = ensure_member(creator_name)
            month_key = tip.created_at.strftime('%Y-%m')
            month_node = ensure_member_month(creator_name, month_key)
            summary_month = ensure_summary_month(month_key)
            member['tips_post_count'] += 1
            if tip.is_approved:
                member['approved_count_total'] += 1
                member['tips_approved_count'] += 1
                month_node['approved_count_total'] += 1
                month_node['tips_approved_count'] += 1
                summary_month['approved_count_total'] += 1
                summary_month['tips_approved_count'] += 1
            member['good_count_total'] += tip.good_count
            member['view_count_total'] += tip.view_count
            month_node['tips_post_count'] += 1
            month_node['good_count_total'] += tip.good_count
            month_node['view_count_total'] += tip.view_count
            summary_month['tips_post_count'] += 1
            summary_month['good_count_total'] += tip.good_count
            summary_month['view_count_total'] += tip.view_count

        for article in current_month_faq_articles:
            creator_name = self.resolve_contributor_name(article.created_by_name, article.created_by)
            if self.is_excluded_contributor(creator_name):
                continue
            if creator_name not in member_map:
                continue
            current_month = ensure_current_month(creator_name)
            current_month['faq_post_count'] += 1
            if article.is_approved:
                current_month['approved_count_total'] += 1
                current_month['faq_approved_count'] += 1

        for tip in current_month_tips_articles:
            creator_name = self.resolve_contributor_name(tip.created_by_name, tip.created_by)
            if self.is_excluded_contributor(creator_name):
                continue
            if creator_name not in member_map:
                continue
            current_month = ensure_current_month(creator_name)
            current_month['tips_post_count'] += 1
            if tip.is_approved:
                current_month['approved_count_total'] += 1
                current_month['tips_approved_count'] += 1

        member_summaries = list(member_map.values())
        for item in member_summaries:
            current_month = ensure_current_month(item['name'])
            item['current_month_faq_approved_count'] = current_month.get('faq_approved_count', 0)
            item['current_month_faq_post_count'] = current_month.get('faq_post_count', 0)
            item['current_month_tips_approved_count'] = current_month.get('tips_approved_count', 0)
            item['current_month_tips_post_count'] = current_month.get('tips_post_count', 0)
            item['current_month_post_count_total'] = (
                item['current_month_faq_post_count'] + item['current_month_tips_post_count']
            )
            item['current_month_approved_count_total'] = current_month.get('approved_count_total', 0)
            item['post_count_total'] = item['faq_post_count'] + item['tips_post_count']
            monthly_totals = list(member_monthly_map.get(item['name'], {}).values())
            for month_item in monthly_totals:
                month_item['post_count_total'] = month_item['faq_post_count'] + month_item['tips_post_count']
            monthly_totals.sort(key=lambda month_item: month_item['month'], reverse=True)
            item['monthly_totals'] = monthly_totals

        member_summaries.sort(
            key=lambda item: (
                member_order_map.get(item['name'], 10**9),
                item['management_uid'] or '999999',
                item['name'].lower(),
            )
        )

        context['selected_period'] = selected_period
        context['member_summaries'] = member_summaries
        summary_monthly_totals = list(summary_monthly_map.values())
        for month_item in summary_monthly_totals:
            month_item['post_count_total'] = month_item['faq_post_count'] + month_item['tips_post_count']
        summary_monthly_totals.sort(key=lambda month_item: month_item['month'], reverse=True)
        context['summary_totals'] = {
            'approved_count_total': sum(item['approved_count_total'] for item in member_summaries),
            'faq_approved_count': sum(item['faq_approved_count'] for item in member_summaries),
            'faq_post_count': sum(item['faq_post_count'] for item in member_summaries),
            'tips_approved_count': sum(item['tips_approved_count'] for item in member_summaries),
            'tips_post_count': sum(item['tips_post_count'] for item in member_summaries),
            'good_count_total': sum(item['good_count_total'] for item in member_summaries),
            'view_count_total': sum(item['view_count_total'] for item in member_summaries),
            'current_month_faq_approved_count': sum(
                item['current_month_faq_approved_count'] for item in member_summaries
            ),
            'current_month_faq_post_count': sum(item['current_month_faq_post_count'] for item in member_summaries),
            'current_month_tips_approved_count': sum(
                item['current_month_tips_approved_count'] for item in member_summaries
            ),
            'current_month_tips_post_count': sum(item['current_month_tips_post_count'] for item in member_summaries),
            'current_month_approved_count_total': sum(
                item['current_month_approved_count_total'] for item in member_summaries
            ),
            'current_month_post_count_total': sum(item['current_month_post_count_total'] for item in member_summaries),
            'post_count_total': sum(item['post_count_total'] for item in member_summaries),
            'monthly_totals': summary_monthly_totals,
        }
        context['chart_labels'] = [item['name'] for item in member_summaries]
        context['chart_post_counts'] = [item['post_count_total'] for item in member_summaries]
        context['chart_faq_post_counts'] = [item['faq_post_count'] for item in member_summaries]
        context['chart_tips_post_counts'] = [item['tips_post_count'] for item in member_summaries]

        # ── 社内タブ用（旧集計） ──────────────────────────────────────────────
        internal_faq_articles = list(
            KnowledgeArticle.objects.select_related('created_by', 'approved_by')
            .filter(visible_to_any_account_filter())
            .annotate(good_count=Count('goods'))
            .order_by('-good_count', '-published_at', '-created_at')
        )
        internal_tips_articles = list(
            TipsArticle.objects.select_related('created_by', 'approved_by')
            .filter(visible_to_any_account_filter())
            .annotate(good_count=Count('goods'))
            .order_by('-good_count', '-published_at', '-created_at')
        )

        internal_post_counts = Counter()
        internal_review_counts = Counter()
        internal_like_counts = Counter()

        for article in internal_faq_articles:
            creator_name = self.resolve_contributor_name(article.created_by_name, article.created_by)
            article.creator_display_name = creator_name or '-'
            if not self.is_excluded_contributor(creator_name):
                internal_post_counts[creator_name] += 1
                internal_like_counts[creator_name] += article.good_count

            reviewer_name = self.resolve_contributor_name(article.approved_by_name, article.approved_by)
            if article.is_approved and not self.is_excluded_contributor(reviewer_name):
                internal_review_counts[reviewer_name] += 1

        for tip in internal_tips_articles:
            creator_name = self.resolve_contributor_name(tip.created_by_name, tip.created_by)
            tip.creator_display_name = creator_name or '-'
            if not self.is_excluded_contributor(creator_name):
                internal_post_counts[creator_name] += 1
                internal_like_counts[creator_name] += tip.good_count

            reviewer_name = self.resolve_contributor_name(tip.approved_by_name, tip.approved_by)
            if tip.is_approved and not self.is_excluded_contributor(reviewer_name):
                internal_review_counts[reviewer_name] += 1

        internal_contributor_names = sorted(
            set(internal_post_counts) | set(internal_review_counts) | set(internal_like_counts),
            key=lambda name: (
                -internal_post_counts[name],
                -internal_review_counts[name],
                -internal_like_counts[name],
                name.lower(),
            ),
        )
        internal_contributor_summaries = [
            {
                'name': name,
                'post_count': internal_post_counts[name],
                'review_count': internal_review_counts[name],
                'like_count': internal_like_counts[name],
            }
            for name in internal_contributor_names
        ]

        context['internal_summary_totals'] = {
            'post_count': sum(internal_post_counts.values()),
            'review_count': sum(internal_review_counts.values()),
            'like_count': sum(internal_like_counts.values()),
        }
        context['internal_contributor_summaries'] = internal_contributor_summaries
        context['internal_chart_labels'] = [item['name'] for item in internal_contributor_summaries]
        context['internal_post_chart_data'] = [item['post_count'] for item in internal_contributor_summaries]
        context['internal_review_chart_data'] = [item['review_count'] for item in internal_contributor_summaries]
        context['internal_like_chart_data'] = [item['like_count'] for item in internal_contributor_summaries]
        context['internal_top_faq_articles'] = internal_faq_articles[:5]
        context['internal_top_tips_articles'] = internal_tips_articles[:5]
        context['internal_contributor_post_ranking'] = sorted(
            internal_contributor_summaries,
            key=lambda item: (
                -item['post_count'],
                -item['review_count'],
                -item['like_count'],
                item['name'].lower(),
            ),
        )

        # ── 顧客タブ用（旧集計） ──────────────────────────────────────────────
        User = get_user_model()
        customer_users = User.objects.filter(groups__name=CUSTOMER_GROUP_NAME)
        customer_user_ids = list(customer_users.values_list('id', flat=True))

        customer_access_per_user = (
            ViewHistory.objects.filter(user_id__in=customer_user_ids)
            .values('username')
            .annotate(access_count=Count('id'))
            .order_by('-access_count')
        )
        context['customer_access_per_user'] = list(customer_access_per_user)
        context['customer_total_access'] = sum(r['access_count'] for r in context['customer_access_per_user'])

        category_access_qs = (
            ViewHistory.objects.filter(user_id__in=customer_user_ids)
            .exclude(parent_category='')
            .values('parent_category', 'category')
            .annotate(access_count=Count('id'))
            .order_by('parent_category', 'category')
        )
        category_access_list = list(category_access_qs)
        category_groups_map = {}
        for row in category_access_list:
            parent_category = row['parent_category']
            if parent_category not in category_groups_map:
                category_groups_map[parent_category] = {'total': 0, 'children': []}
            category_groups_map[parent_category]['total'] += row['access_count']
            if row['category']:
                category_groups_map[parent_category]['children'].append(
                    {
                        'category': row['category'],
                        'access_count': row['access_count'],
                    }
                )
        context['customer_category_access'] = [
            {'parent_category': key, 'total': value['total'], 'children': value['children']}
            for key, value in sorted(category_groups_map.items(), key=lambda item: -item[1]['total'])
        ]

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

        faq_with_goods = [
            article for article in internal_faq_articles if article.good_count > 0
        ]
        tips_with_goods = [
            tip for tip in internal_tips_articles if tip.good_count > 0
        ]
        context['customer_faq_goods'] = faq_with_goods
        context['customer_tips_goods'] = tips_with_goods
        context['customer_faq_good_total'] = sum(article.good_count for article in faq_with_goods)
        context['customer_tips_good_total'] = sum(tip.good_count for tip in tips_with_goods)

        return context


class SummaryPDFView(StaffRequiredMixin, View):
    """アナライズレポートをPDFで出力するビュー"""
    
    # クラス変数：フォント名
    japanese_font_name = 'Helvetica'  # デフォルト

    @classmethod
    def register_japanese_font(cls):
        """日本語フォント登録"""
        if cls.japanese_font_name != 'Helvetica':
            return  # 既に登録済み
        
        # Windows フォントパス
        font_paths = [
            'C:\\Windows\\Fonts\\msgothic.ttc',  # MS Pゴシック
            'C:\\Windows\\Fonts\\meiryo.ttc',    # メイリオ
            '/System/Library/Fonts/ヒラギノ角ゴシック W9.ttc',  # macOS
            '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',  # Linux
        ]
        
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    font_name = 'Japanese'
                    pdfmetrics.registerFont(TTFont(font_name, font_path))
                    cls.japanese_font_name = font_name
                    return
                except Exception as e:
                    continue
    
    @classmethod
    def resolve_contributor_name(cls, saved_name='', user=None):
        return resolve_saved_or_user_display_name(saved_name, user, default='').strip()

    @classmethod
    def is_excluded_contributor(cls, contributor_name):
        excluded = {'admin'}
        return not contributor_name or contributor_name.lower() in excluded

    def get_summary_data(self):
        """SummaryViewと同じロジックでデータを生成"""
        selected_period = 'all'
        from_date = None
        to_date = None
        today = timezone.localdate()
        current_month_start = today.replace(day=1)

        base_faq_qs = (
            KnowledgeArticle.objects.select_related('created_by')
            .filter(visible_to_any_account_filter())
            .annotate(good_count=Count('goods'))
        )
        base_tips_qs = (
            TipsArticle.objects.select_related('created_by')
            .filter(visible_to_any_account_filter())
            .annotate(good_count=Count('goods'))
        )

        faq_qs = base_faq_qs
        tips_qs = base_tips_qs

        if from_date:
            faq_qs = faq_qs.filter(created_at__date__gte=from_date)
            tips_qs = tips_qs.filter(created_at__date__gte=from_date)
        if to_date:
            faq_qs = faq_qs.filter(created_at__date__lte=to_date)
            tips_qs = tips_qs.filter(created_at__date__lte=to_date)

        faq_articles = list(faq_qs.order_by('-created_at'))
        tips_articles = list(tips_qs.order_by('-created_at'))
        current_month_faq_articles = list(
            base_faq_qs.filter(
                created_at__date__gte=current_month_start,
                created_at__date__lte=today,
            ).order_by('-created_at')
        )
        current_month_tips_articles = list(
            base_tips_qs.filter(
                created_at__date__gte=current_month_start,
                created_at__date__lte=today,
            ).order_by('-created_at')
        )

        User = get_user_model()
        summary_users = User.objects.filter(
            groups__name=CONTRIBUTOR_GROUP_NAME
        ).distinct().prefetch_related('knowledge_profile').order_by('knowledge_profile__uid', 'id')

        member_map = {}
        member_current_month_map = {}
        member_order_map = {}

        def ensure_member(name, member_id=None, management_uid=''):
            node = member_map.get(name)
            if node is None:
                if member_id is None:
                    member_id = member_order_map.get(name, 10**9)
                node = {
                    'member_id': member_id,
                    'management_uid': management_uid,
                    'name': name,
                    'approved_count_total': 0,
                    'faq_approved_count': 0,
                    'faq_post_count': 0,
                    'tips_approved_count': 0,
                    'tips_post_count': 0,
                    'good_count_total': 0,
                    'view_count_total': 0,
                }
                member_map[name] = node
            return node

        def ensure_current_month(name):
            node = member_current_month_map.get(name)
            if node is None:
                node = {
                    'approved_count_total': 0,
                    'faq_approved_count': 0,
                    'faq_post_count': 0,
                    'tips_approved_count': 0,
                    'tips_post_count': 0,
                }
                member_current_month_map[name] = node
            return node

        for user in summary_users:
            display_name = resolve_user_display_name(user).strip()
            if self.is_excluded_contributor(display_name):
                continue
            if display_name not in member_order_map:
                profile = getattr(user, 'knowledge_profile', None)
                uid_value = (getattr(profile, 'uid', '') or '').strip()
                uid_order = int(uid_value) if uid_value.isdigit() else 10**9 + user.id
                member_order_map[display_name] = uid_order
            profile = getattr(user, 'knowledge_profile', None)
            ensure_member(
                display_name,
                user.id,
                (getattr(profile, 'uid', '') or '').strip(),
            )

        for article in faq_articles:
            creator_name = self.resolve_contributor_name(article.created_by_name, article.created_by)
            if self.is_excluded_contributor(creator_name):
                continue
            if creator_name not in member_map:
                continue
            member = ensure_member(creator_name)
            member['faq_post_count'] += 1
            if article.is_approved:
                member['approved_count_total'] += 1
                member['faq_approved_count'] += 1
            member['good_count_total'] += article.good_count
            member['view_count_total'] += article.answer_view_count

        for tip in tips_articles:
            creator_name = self.resolve_contributor_name(tip.created_by_name, tip.created_by)
            if self.is_excluded_contributor(creator_name):
                continue
            if creator_name not in member_map:
                continue
            member = ensure_member(creator_name)
            member['tips_post_count'] += 1
            if tip.is_approved:
                member['approved_count_total'] += 1
                member['tips_approved_count'] += 1
            member['good_count_total'] += tip.good_count
            member['view_count_total'] += tip.view_count

        for article in current_month_faq_articles:
            creator_name = self.resolve_contributor_name(article.created_by_name, article.created_by)
            if self.is_excluded_contributor(creator_name):
                continue
            if creator_name not in member_map:
                continue
            current_month = ensure_current_month(creator_name)
            current_month['faq_post_count'] += 1
            if article.is_approved:
                current_month['approved_count_total'] += 1
                current_month['faq_approved_count'] += 1

        for tip in current_month_tips_articles:
            creator_name = self.resolve_contributor_name(tip.created_by_name, tip.created_by)
            if self.is_excluded_contributor(creator_name):
                continue
            if creator_name not in member_map:
                continue
            current_month = ensure_current_month(creator_name)
            current_month['tips_post_count'] += 1
            if tip.is_approved:
                current_month['approved_count_total'] += 1
                current_month['tips_approved_count'] += 1

        member_summaries = list(member_map.values())
        for item in member_summaries:
            current_month = ensure_current_month(item['name'])
            item['current_month_faq_approved_count'] = current_month.get('faq_approved_count', 0)
            item['current_month_faq_post_count'] = current_month.get('faq_post_count', 0)
            item['current_month_tips_approved_count'] = current_month.get('tips_approved_count', 0)
            item['current_month_tips_post_count'] = current_month.get('tips_post_count', 0)
            item['current_month_post_count_total'] = (
                item['current_month_faq_post_count'] + item['current_month_tips_post_count']
            )
            item['current_month_approved_count_total'] = current_month.get('approved_count_total', 0)
            item['post_count_total'] = item['faq_post_count'] + item['tips_post_count']

        member_summaries.sort(
            key=lambda item: (
                member_order_map.get(item['name'], 10**9),
                item['management_uid'] or '999999',
                item['name'].lower(),
            )
        )

        summary_totals = {
            'approved_count_total': sum(item['approved_count_total'] for item in member_summaries),
            'faq_approved_count': sum(item['faq_approved_count'] for item in member_summaries),
            'faq_post_count': sum(item['faq_post_count'] for item in member_summaries),
            'tips_approved_count': sum(item['tips_approved_count'] for item in member_summaries),
            'tips_post_count': sum(item['tips_post_count'] for item in member_summaries),
            'good_count_total': sum(item['good_count_total'] for item in member_summaries),
            'view_count_total': sum(item['view_count_total'] for item in member_summaries),
            'current_month_faq_approved_count': sum(
                item['current_month_faq_approved_count'] for item in member_summaries
            ),
            'current_month_faq_post_count': sum(item['current_month_faq_post_count'] for item in member_summaries),
            'current_month_tips_approved_count': sum(
                item['current_month_tips_approved_count'] for item in member_summaries
            ),
            'current_month_tips_post_count': sum(item['current_month_tips_post_count'] for item in member_summaries),
            'current_month_approved_count_total': sum(
                item['current_month_approved_count_total'] for item in member_summaries
            ),
            'current_month_post_count_total': sum(item['current_month_post_count_total'] for item in member_summaries),
            'post_count_total': sum(item['post_count_total'] for item in member_summaries),
        }

        return {
            'member_summaries': member_summaries,
            'summary_totals': summary_totals,
        }

    def get(self, request, *args, **kwargs):
        """PDFレポートを生成"""
        # 日本語フォント登録
        self.register_japanese_font()
        
        data = self.get_summary_data()
        member_summaries = data['member_summaries']
        summary_totals = data['summary_totals']

        # PDFを生成（縦向きA4）
        buffer = BytesIO()
        page_size = A4
        doc = SimpleDocTemplate(buffer, pagesize=page_size, rightMargin=1*cm, leftMargin=1*cm, topMargin=1.5*cm, bottomMargin=1*cm)
        
        elements = []
        styles = getSampleStyleSheet()
        font_name = self.japanese_font_name  # ローカル変数として保存
        
        # タイトル
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=20,
            textColor=colors.HexColor('#1a1a1a'),
            spaceAfter=6,
            alignment=1,  # 中央揃え
            fontName=font_name,
        )
        title = Paragraph('データ分析レポート', title_style)
        elements.append(title)

        today = timezone.localdate()
        date_style = ParagraphStyle(
            'DateStyle',
            parent=styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#666666'),
            alignment=1,  # 中央揃え
            spaceAfter=8,
            fontName=font_name,
        )
        date_para = Paragraph(f'出力日: {today.strftime("%Y年%m月%d日")}', date_style)
        elements.append(date_para)
        elements.append(Spacer(1, 0.2*cm))

        # サマリセクション
        summary_heading_style = ParagraphStyle(
            'SummaryHeading',
            parent=styles['Heading2'],
            fontSize=12,
            textColor=colors.HexColor('#000000'),
            spaceAfter=6,
            fontName=font_name,
        )
        summary_heading = Paragraph('総数サマリ', summary_heading_style)
        elements.append(summary_heading)

        # サマリテーブル
        summary_data = [
            ['項目', '総数'],
            ['投稿数（合計）', str(summary_totals['post_count_total'])],
            ['FAQ投稿数', str(summary_totals['faq_post_count'])],
            ['FAQ承認数', str(summary_totals['faq_approved_count'])],
            ['Tips投稿数', str(summary_totals['tips_post_count'])],
            ['Tips承認数', str(summary_totals['tips_approved_count'])],
            ['承認数（合計）', str(summary_totals['approved_count_total'])],
            ['Good総数', str(summary_totals['good_count_total'])],
            ['閲覧数', str(summary_totals['view_count_total'])],
            ['当月投稿数', str(summary_totals['current_month_post_count_total'])],
            ['当月承認数', str(summary_totals['current_month_approved_count_total'])],
        ]

        summary_table = Table(summary_data, colWidths=[4.5*cm, 2*cm])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), font_name),
            ('FONTNAME', (0, 1), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F0F0F0')]),
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 0.3*cm))

        # メンバー別セクション
        member_heading = Paragraph('メンバー別投稿数', summary_heading_style)
        elements.append(member_heading)

        # メンバーテーブル
        member_data = [
            ['メンバー', '投稿数', 'FAQ', 'Tips', '承認数', 'Good数', '閲覧数'],
        ]

        for member in member_summaries:
            member_data.append([
                member['name'],
                str(member['post_count_total']),
                str(member['faq_post_count']),
                str(member['tips_post_count']),
                str(member['approved_count_total']),
                str(member['good_count_total']),
                str(member['view_count_total']),
            ])

        member_table = Table(member_data, colWidths=[3.2*cm, 1.6*cm, 1.6*cm, 1.6*cm, 1.6*cm, 1.6*cm, 1.6*cm])
        member_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), font_name),
            ('FONTNAME', (0, 1), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F0F0F0')]),
            ('LEFTPADDING', (0, 0), (-1, -1), 2),
            ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ]))
        elements.append(member_table)

        # PDFを構築
        doc.build(elements)
        
        # レスポンスを返す
        buffer.seek(0)
        response = HttpResponse(buffer.read(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="analysis_report_{today.strftime("%Y%m%d")}.pdf"'
        return response


class ArticleEditorRequiredMixin(UserPassesTestMixin):
    raise_exception = True

    def test_func(self):
        return can_edit_article(self.request.user)

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(self.request, 'この操作を実行する権限がありません。')
            return redirect('article_list')
        return super().handle_no_permission()



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


class KnowledgeArticleCreateView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/article_form.html'
    form_class = KnowledgeArticleCreateForm
    success_url = reverse_lazy('article_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'FAQ登録'
        context['submit_label'] = '登録'
        context['category_create_url'] = f"{reverse_lazy('category_create')}?{urlencode({'next': self.request.get_full_path()})}"
        context['category_groups'] = self.category_groups(context['form'])
        context['category_browser'] = FAQCategoryCreateView.category_browser_data()
        context['category_browser_json'] = json.dumps(context['category_browser'], ensure_ascii=False)
        context['registered_category_values_json'] = json.dumps(
            self.selected_registered_category_values(context['form']),
            ensure_ascii=False,
        )
        context['target_os_version_map_json'] = json.dumps(TARGET_OS_VERSION_MAP, ensure_ascii=False)
        context['target_os_entries_json'] = json.dumps(target_os_entries_for_form(context['form']), ensure_ascii=False)
        context['is_demo_user'] = is_demo_user(self.request.user)
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

    @staticmethod
    def selected_registered_category_values(form):
        return [str(value) for value in (form['registered_category'].value() or [])]

    def form_valid(self, form):
        reference_links = []
        try:
            reference_links_json = self.request.POST.get('reference_links', '[]')
            reference_links = json.loads(reference_links_json)
        except (json.JSONDecodeError, TypeError):
            reference_links = []
        
        article = KnowledgeArticle.objects.create(
            category=form.cleaned_data['category'],
            title=form.cleaned_data['title'],
            target_os=form.cleaned_data['target_os'],
            summary=form.cleaned_data['question'],
            body=form.cleaned_data['answer'],
            is_approved=not FAQ_APPROVAL_ENABLED,
            visible_to_customer=form.cleaned_data['visible_to_customer'],
            visible_to_systena=form.cleaned_data['visible_to_systena'],
            source_published_at=form.cleaned_data['source_published_at'],
            expires_on=form.cleaned_data['expires_on'],
            created_by=self.request.user,
            created_by_name=resolve_user_display_name(self.request.user),
            reference_links=reference_links,
        )
        self.save_question_images(article, form)
        self.save_answer_images(article, form)
        messages.success(self.request, f'FAQ「{article.title}」を登録しました。')
        return super().form_valid(form)

    @staticmethod
    def save_question_images(article, form):
        for uploaded_file in form.cleaned_data.get('question_images', []):
            ArticleAttachment.objects.create(
                article=article,
                file=uploaded_file,
                placement=ArticleAttachment.PLACEMENT_QUESTION,
                display_name=uploaded_file.name,
            )

    @staticmethod
    def save_answer_images(article, form):
        for uploaded_file in form.cleaned_data.get('answer_images', []):
            KnowledgeArticleImageAttachment.objects.create(
                article=article,
                file=uploaded_file,
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
        registered_category_ids, _ = split_registered_and_unregistered_categories(
            self.article.category
        )
        parsed_target_os = parse_target_os_value(self.article.target_os)
        return {
            'registered_category': registered_category_ids,
            'title': self.article.title,
            'target_os_entries': json.dumps(parse_target_os_values(self.article.target_os), ensure_ascii=False),
            'target_os_name': parsed_target_os['name'],
            'target_os_version': parsed_target_os['version'],
            'target_os_condition': parsed_target_os['condition'],
            'question': self.article.summary or self.article.title,
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
        context['category_create_url'] = f"{reverse_lazy('category_create')}?{urlencode({'next': self.request.get_full_path()})}"
        context['article'] = self.article
        context['article_approver_display_name'] = resolve_saved_or_user_display_name(
            self.article.approved_by_name,
            self.article.approved_by,
        )
        context['approval_enabled'] = FAQ_APPROVAL_ENABLED
        context['can_approve_article'] = can_approve_article(self.request.user)
        context['can_remand_article'] = can_approve_article(self.request.user)
        context['can_reset_article_approval'] = can_reset_approval(self.request.user)
        context['article_approval_status'] = approval_status_value(self.article)
        context['question_images'] = self.article.attachments.filter(
            placement=ArticleAttachment.PLACEMENT_QUESTION
        ).order_by('uploaded_at', 'id')
        context['answer_images'] = self.article.images.all().order_by('uploaded_at', 'id')
        context['reference_links_json'] = json.dumps(self.article.reference_links or [])
        context['category_groups'] = KnowledgeArticleCreateView.category_groups(context['form'])
        context['category_browser'] = FAQCategoryCreateView.category_browser_data()
        context['category_browser_json'] = json.dumps(context['category_browser'], ensure_ascii=False)
        context['registered_category_values_json'] = json.dumps(
            KnowledgeArticleCreateView.selected_registered_category_values(context['form']),
            ensure_ascii=False,
        )
        context['target_os_version_map_json'] = json.dumps(TARGET_OS_VERSION_MAP, ensure_ascii=False)
        context['target_os_entries_json'] = json.dumps(target_os_entries_for_form(context['form']), ensure_ascii=False)
        candidate = (self.request.POST.get('next') or self.request.GET.get('next') or '').strip()
        if candidate and url_has_allowed_host_and_scheme(
            url=candidate,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            context['return_to'] = candidate
        else:
            context['return_to'] = ''
        context['is_demo_user'] = is_demo_user(self.request.user)
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
        self.article.title = form.cleaned_data['title']
        self.article.target_os = form.cleaned_data['target_os']
        self.article.summary = form.cleaned_data['question']
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
                'target_os',
                'summary',
                'body',
                'visible_to_customer',
                'visible_to_systena',
                'source_published_at',
                'expires_on',
                'reference_links',
                'updated_at',
            ]
        )
        KnowledgeArticleCreateView.save_question_images(self.article, form)
        KnowledgeArticleCreateView.save_answer_images(self.article, form)
        messages.success(self.request, f'FAQ「{self.article.title}」を更新しました。')
        return super().form_valid(form)


class KnowledgeArticleApproveView(ArticleApprovalRequiredMixin, View):
    def post(self, request, pk):
        article = get_object_or_404(KnowledgeArticle, pk=pk)
        if not FAQ_APPROVAL_ENABLED:
            messages.info(request, '承認機能は無効です。')
            return redirect(resolve_next_path(request, 'article_list'))

        if article.is_approved:
            messages.info(request, f'FAQ「{article.title}」は既に承認済みです。')
            return redirect(resolve_next_path(request, 'article_list'))

        standard_contract_only_raw = str(request.POST.get('standard_contract_only', '1')).strip().lower()
        standard_contract_only = standard_contract_only_raw in {'1', 'true', 'on', 'yes'}
        visible_to_customer_raw = str(request.POST.get('visible_to_customer', '1')).strip().lower()
        visible_to_customer = visible_to_customer_raw in {'1', 'true', 'on', 'yes'}

        article.is_approved = True
        article.standard_contract_only = standard_contract_only
        article.visible_to_customer = visible_to_customer
        article.approved_by = request.user
        article.approved_by_name = resolve_user_display_name(request.user)
        article.remand_reason = ''
        article.save(update_fields=['is_approved', 'standard_contract_only', 'visible_to_customer', 'approved_by', 'approved_by_name', 'remand_reason', 'updated_at'])
        messages.success(request, f'FAQ「{article.title}」を承認しました。')
        return redirect(resolve_next_path(request, 'article_list'))


class KnowledgeArticleApprovalResetView(View):
    def post(self, request, pk):
        if not can_reset_approval(request.user):
            messages.error(request, '承認リセットを実行する権限がありません。')
            return redirect(resolve_next_path(request, 'article_list'))

        article = get_object_or_404(KnowledgeArticle, pk=pk)
        article.is_approved = False
        article.approved_by = None
        article.approved_by_name = ''
        article.remand_reason = ''
        article.save(update_fields=['is_approved', 'approved_by', 'approved_by_name', 'remand_reason', 'updated_at'])
        messages.success(request, f'FAQ「{article.title}」の承認をリセットしました。')
        return redirect(resolve_next_path(request, 'article_edit', pk=pk))


class KnowledgeArticleRemandView(ArticleApprovalRequiredMixin, View):
    def post(self, request, pk):
        article = get_object_or_404(KnowledgeArticle, pk=pk)
        if not FAQ_APPROVAL_ENABLED:
            messages.info(request, '承認機能は無効です。')
            return redirect(resolve_next_path(request, 'article_list'))

        reason = (request.POST.get('remand_reason') or '').strip() or '差戻し'

        article.is_approved = False
        article.approved_by = None
        article.approved_by_name = ''
        article.remand_reason = reason
        article.save(update_fields=['is_approved', 'approved_by', 'approved_by_name', 'remand_reason', 'updated_at'])
        messages.success(request, f'FAQ「{article.title}」を差し戻しました。')
        return redirect(resolve_next_path(request, 'article_list'))


class KnowledgeArticleImageAttachmentDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        image = get_object_or_404(KnowledgeArticleImageAttachment, pk=pk)
        article_id = image.article_id
        image.file.delete(save=False)
        image.delete()
        messages.success(request, '画像を削除しました。')
        return redirect('article_edit', pk=article_id)


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
        for image in article.images.all():
            image.file.delete(save=False)
        article.delete()
        messages.success(request, f'FAQ「{title}」を削除しました。')
        return redirect('article_list')


class FAQCategoryCreateView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/category_form.html'
    form_class = FAQCategoryCreateForm
    success_url = reverse_lazy('category_create')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        user = self.request.user
        kwargs['allow_new_parent_name'] = is_admin_account(user)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['can_use_qr_tab'] = True
        tab = (self.request.GET.get('tab') or self.request.POST.get('tab') or 'faq').strip().lower()
        if tab == 'qr':
            qr_view = ConvenienceCategoryCreateView()
            qr_view.request = self.request
            qr_view.kwargs = getattr(self, 'kwargs', {})
            qr_view.args = getattr(self, 'args', ())
            qr_context = qr_view.get_context_data(**kwargs)
            context.update(qr_context)
            context['form'] = kwargs.get('form') or ConvenienceCategoryCreateForm(
                self.request.POST or None,
                allow_new_reference_type=is_admin_account(self.request.user),
            )
            context['category_type_tab'] = 'qr'
            return context

        context['form_title'] = 'カテゴリ登録'
        context['submit_label'] = '登録'
        context['category_type_tab'] = 'faq'
        context['return_to'] = self._resolve_return_to_url()
        context['categories'] = self.categories_with_parent_visibility()
        context['category_browser'] = self.category_browser_data()
        context['category_browser_json'] = json.dumps(context['category_browser'], ensure_ascii=False)
        return context

    def _resolve_return_to_url(self):
        candidate = (self.request.POST.get('next') or self.request.GET.get('next') or '').strip()
        if candidate and url_has_allowed_host_and_scheme(
            url=candidate,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return candidate
        return ''

    def get_success_url(self):
        tab = (self.request.POST.get('tab') or self.request.GET.get('tab') or 'faq').strip().lower()
        if tab == 'qr':
            return ConvenienceCategoryCreateView()._resolve_return_to_url.__get__(self, FAQCategoryCreateView)() or f"{self.success_url}?tab=qr"
        return self._resolve_return_to_url() or str(self.success_url)

    @staticmethod
    def categories_with_parent_visibility():
        categories = list(FAQCategory.objects.all())
        for item in categories:
            item.parent_visible_to_customer = True
        return categories

    @staticmethod
    def category_browser_data():
        categories = FAQCategory.objects.order_by('parent_name', 'middle_name', 'child_name')

        parent_map = {}
        for category in categories:
            parent_node = parent_map.setdefault(
                category.parent_name,
                {
                    'name': category.parent_name,
                    'visible_to_customer': True,
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
        tab = (self.request.POST.get('tab') or self.request.GET.get('tab') or 'faq').strip().lower()
        if tab == 'qr':
            qr_form = ConvenienceCategoryCreateForm(
                self.request.POST,
                allow_new_reference_type=is_admin_account(self.request.user),
            )
            if qr_form.is_valid():
                category = qr_form.save()
                messages.success(
                    self.request,
                    f'QRカテゴリ「{category.reference_type} / {category.category}{(" / " + category.middle_category) if category.middle_category else ""}」を登録しました。',
                )
                return redirect(self.get_success_url())
            return self.form_invalid(qr_form)

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
        user = self.request.user
        kwargs['allow_new_parent_name'] = is_admin_account(user)
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
        user_type = form.cleaned_data['user_type']
        display_name = form.cleaned_data['display_name'] or form.cleaned_data['username']
        company_name = 'システナ' if user_type == 'systena' else form.cleaned_data['company_name']
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
            display_name=display_name,
            company_name=company_name,
            user_type=user_type,
            email_addresses='\n'.join(emails),
            note=form.cleaned_data['note'],
        )

        customer, _ = Customer.objects.get_or_create(name=company_name)
        customer.users.add(user)

        messages.success(self.request, f'{resolve_user_display_name(user)}（{user.username}）を作成しました。')
        return super().form_valid(form)


class UserListView(StaffRequiredMixin, ListView):
    template_name = 'tenasapo_knowledge/user_list.html'
    context_object_name = 'users'
    paginate_by = 20

    def get_queryset(self):
        User = get_user_model()
        queryset = User.objects.select_related('knowledge_profile').prefetch_related('groups').order_by(
            'knowledge_profile__uid', 'knowledge_profile__display_name', 'username'
        )

        query = self.request.GET.get('q', '').strip()
        if query:
            queryset = queryset.filter(
                Q(username__icontains=query) |
                Q(knowledge_profile__display_name__icontains=query) |
                Q(knowledge_profile__uid__icontains=query)
            )

        role = self.request.GET.get('role', '').strip()
        if role == '__none__':
            queryset = queryset.filter(groups__isnull=True)
        elif role:
            queryset = queryset.filter(groups__name=role)

        authority = self.request.GET.get('authority', '').strip()
        if authority == 'admin':
            queryset = queryset.filter(Q(is_staff=True) | Q(is_superuser=True))
        elif authority == 'user':
            queryset = queryset.filter(is_staff=False, is_superuser=False)

        user_type = self.request.GET.get('user_type', '').strip()
        if user_type:
            queryset = queryset.filter(knowledge_profile__user_type=user_type)

        return queryset.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['query'] = self.request.GET.get('q', '')
        context['selected_role'] = self.request.GET.get('role', '')
        context['selected_authority'] = self.request.GET.get('authority', '')
        context['selected_user_type'] = self.request.GET.get('user_type', '')
        context['roles'] = getattr(settings, 'USER_ROLES', getattr(settings, 'USER_GROUPS', []))
        context['can_manage_all_users'] = _can_manage_all_users(self.request.user)
        return context


class UserDetailView(StaffRequiredMixin, TemplateView):
    template_name = 'tenasapo_knowledge/user_detail.html'

    def dispatch(self, request, *args, **kwargs):
        User = get_user_model()
        self.user_obj = get_object_or_404(User, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role_names = list(getattr(settings, 'USER_ROLES', getattr(settings, 'USER_GROUPS', [])))
        user_group_names = set(self.user_obj.groups.values_list('name', flat=True))
        context['user_obj'] = self.user_obj
        context['profile'] = getattr(self.user_obj, 'knowledge_profile', None)
        context['visible_roles'] = [name for name in role_names if name in user_group_names]
        context['can_edit'] = _can_manage_all_users(self.request.user) or self.user_obj.pk == self.request.user.pk
        return context


class UserPasswordResetView(StaffOrSelfRequiredMixin, View):
    def post(self, request, pk):
        User = get_user_model()
        user = get_object_or_404(User, pk=pk)
        if not _can_manage_target_user(request.user, user):
            messages.error(request, '他のユーザーのパスワードを変更する権限がありません。')
            return redirect('user_list')
        reset_mode = request.POST.get('reset_mode', 'random')

        if reset_mode == 'manual':
            temporary_password = request.POST.get('new_password', '').strip()
            if not temporary_password:
                messages.error(request, '手動設定するパスワードを入力してください。')
                return redirect('user_list')
            message = f'{resolve_user_display_name(user)}（{user.username}）のパスワードを手動設定しました。'
        else:
            temporary_password = get_random_string(
                12,
                allowed_chars='abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789',
            )
            message = (
                f'{resolve_user_display_name(user)}（{user.username}）のパスワードをランダム生成しました。'
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
            queryset = queryset.filter(
                Q(username__icontains=query)
                | Q(user__username__icontains=query)
                | Q(user__knowledge_profile__display_name__icontains=query)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['query'] = self.request.GET.get('q', '')
        return context


class RevisionHistoryCreateView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/revision_history_form.html'
    form_class = RevisionHistoryForm
    success_url = reverse_lazy('revision_history_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = '更新履歴登録'
        context['submit_label'] = '登録'
        context['is_edit'] = False
        return context

    def form_valid(self, form):
        revision = form.save(commit=False)
        revision.updated_by_user = self.request.user
        revision.updated_by_name = self.request.user.username
        revision.save()
        messages.success(self.request, '更新履歴を登録しました。')
        return super().form_valid(form)


class RevisionHistoryUpdateView(StaffRequiredMixin, UpdateView):
    template_name = 'tenasapo_knowledge/revision_history_form.html'
    model = RevisionHistory
    form_class = RevisionHistoryForm
    success_url = reverse_lazy('revision_history_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = '更新履歴修正'
        context['submit_label'] = '修正'
        context['is_edit'] = True
        return context

    def form_valid(self, form):
        revision = form.save(commit=False)
        revision.updated_by_user = self.request.user
        revision.updated_by_name = self.request.user.username
        revision.save()
        messages.success(self.request, '更新履歴を修正しました。')
        return super().form_valid(form)


class RevisionHistoryDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        revision = get_object_or_404(RevisionHistory, pk=pk)
        revision.delete()
        messages.success(request, '更新履歴を削除しました。')
        return redirect('revision_history_list')


class RevisionHistoryListView(StaffRequiredMixin, ListView):
    template_name = 'tenasapo_knowledge/revision_history_list.html'
    model = RevisionHistory
    context_object_name = 'revision_histories'
    paginate_by = 50

    def dispatch(self, request, *args, **kwargs):
        record_view_history(request, '更新履歴')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return RevisionHistory.objects.select_related('updated_by_user').all()


class ViewHistoryListView(StaffRequiredMixin, ListView):
    template_name = 'tenasapo_knowledge/view_history_list.html'
    context_object_name = 'users'
    paginate_by = 20

    def get_queryset(self):
        User = get_user_model()
        queryset = User.objects.select_related('knowledge_profile').all().order_by(
            'knowledge_profile__display_name',
            'username',
        )
        query = self.request.GET.get('q', '').strip()
        if query:
            queryset = queryset.filter(
                Q(username__icontains=query) |
                Q(knowledge_profile__display_name__icontains=query)
            )
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


class UserUpdateView(StaffOrSelfRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/user_form.html'
    form_class = UserUpdateForm
    success_url = reverse_lazy('user_list')

    def dispatch(self, request, *args, **kwargs):
        User = get_user_model()
        self.user_obj = get_object_or_404(User, pk=kwargs['pk'])
        if not _can_manage_target_user(request.user, self.user_obj):
            messages.error(request, '他のユーザーを編集する権限がありません。')
            return redirect('user_list')
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        profile = getattr(self.user_obj, 'knowledge_profile', None)
        return {
            'uid': profile.uid if profile else '',
            'username': self.user_obj.username,
            'display_name': profile.display_name if profile else self.user_obj.username,
            'company_name': profile.company_name if profile else '',
            'role': UserCreateForm.ROLE_ADMIN if self.user_obj.is_staff else UserCreateForm.ROLE_USER,
            'user_type': profile.user_type if profile else 'customer',
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
        context['return_to'] = self._resolve_return_to_url()
        return context

    def _resolve_return_to_url(self):
        candidate = (self.request.POST.get('next') or self.request.GET.get('next') or '').strip()
        scroll_value = (self.request.POST.get('scroll') or self.request.GET.get('scroll') or '').strip()
        if candidate and url_has_allowed_host_and_scheme(
            url=candidate,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            if scroll_value.isdigit():
                split_result = urlsplit(candidate)
                query_items = [
                    (key, value)
                    for key, value in parse_qsl(split_result.query, keep_blank_values=True)
                    if key != 'scroll'
                ]
                query_items.append(('scroll', scroll_value))
                candidate = urlunsplit(
                    (
                        split_result.scheme,
                        split_result.netloc,
                        split_result.path,
                        urlencode(query_items, doseq=True),
                        split_result.fragment,
                    )
                )
            return candidate
        return str(self.success_url)

    def get_success_url(self):
        return self._resolve_return_to_url()

    def form_valid(self, form):
        User = get_user_model()
        emails = UserCreateForm.normalized_emails(form.cleaned_data['email_addresses'])
        selected_group_names = list(form.cleaned_data['groups'])
        user_type = form.cleaned_data['user_type']
        display_name = form.cleaned_data['display_name'] or self.user_obj.username
        company_name = 'システナ' if user_type == 'systena' else form.cleaned_data['company_name']
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
        profile.display_name = display_name
        profile.company_name = company_name
        profile.user_type = user_type
        profile.email_addresses = '\n'.join(emails)
        profile.note = form.cleaned_data['note']
        profile.save()

        # 顧客を更新
        customer, _ = Customer.objects.get_or_create(name=company_name)
        if self.user_obj not in customer.users.all():
            customer.users.add(self.user_obj)

        messages.success(self.request, f'{resolve_user_display_name(self.user_obj)}（{self.user_obj.username}）を更新しました。')
        return super().form_valid(form)


class UserDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        User = get_user_model()
        user = get_object_or_404(User, pk=pk)
        
        # Admin/SystenaAdminのみ削除可能
        if not _can_manage_all_users(request.user):
            messages.error(request, '他のユーザーを削除する権限がありません。')
            return redirect('user_list')
        
        # 削除対象がリクエスト者本人でないことを確認
        if user == request.user:
            messages.error(request, '自分自身を削除することはできません。')
            return redirect('user_list')
        
        username = resolve_user_display_name(user) or user.username
        login_id = user.username
        user.delete()
        messages.success(request, f'{username}（{login_id}）を削除しました。')
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


class ReviewListView(TemplateView):
    template_name = 'tenasapo_knowledge/review_list.html'

    def dispatch(self, request, *args, **kwargs):
        if not can_edit_article(request.user):
            messages.error(request, 'このページを閲覧する権限がありません。')
            return redirect('home')
        record_view_history(request, 'レビュー一覧')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        review_type = (self.request.GET.get('type') or '').strip().lower()
        if review_type not in {'faq', 'tips'}:
            review_type = ''

        review_items = []

        # フィルター条件を取得
        # 作成者リストを構築（フィルター用）
        creator_list = []
        seen_creator_ids = set()
        
        # FAQの作成者を取得
        faq_creators = KnowledgeArticle.objects.filter(
            created_by__isnull=False
        ).values_list('created_by__id', 'created_by__first_name', 'created_by__last_name', 
                      'created_by__username', 'created_by_name').distinct()
        for creator_id, first_name, last_name, username, saved_name in faq_creators:
            if creator_id not in seen_creator_ids:
                display_name = saved_name if saved_name else f"{first_name} {last_name}".strip() or username
                creator_list.append({'id': creator_id, 'name': display_name})
                seen_creator_ids.add(creator_id)
        
        # Tipsの作成者を取得
        tips_creators = TipsArticle.objects.filter(
            created_by__isnull=False
        ).values_list('created_by__id', 'created_by__first_name', 'created_by__last_name',
                      'created_by__username', 'created_by_name').distinct()
        for creator_id, first_name, last_name, username, saved_name in tips_creators:
            if creator_id not in seen_creator_ids:
                display_name = saved_name if saved_name else f"{first_name} {last_name}".strip() or username
                creator_list.append({'id': creator_id, 'name': display_name})
                seen_creator_ids.add(creator_id)
        
        # 作成者リストをソート
        creator_list.sort(key=lambda x: x['name'])

        status_filter = (self.request.GET.get('status') or '').strip().lower()
        creator_filter = (self.request.GET.get('creator_id') or '').strip()
        date_from = (self.request.GET.get('date_from') or '').strip()
        date_to = (self.request.GET.get('date_to') or '').strip()
        sort_by = (self.request.GET.get('sort') or 'created_at').strip().lower()
        sort_dir = (self.request.GET.get('sort_dir') or 'desc').strip().lower()

        date_from_value = None
        date_to_value = None
        if date_from:
            try:
                date_from_value = datetime.strptime(date_from, '%Y-%m-%d').date()
            except ValueError:
                date_from = ''
        if date_to:
            try:
                date_to_value = datetime.strptime(date_to, '%Y-%m-%d').date()
            except ValueError:
                date_to = ''
        
        # ソート条件の検証
        if sort_by not in {'created_at', 'updated_at', 'title'}:
            sort_by = 'created_at'
        if sort_dir not in {'asc', 'desc'}:
            sort_dir = 'desc'
        
        if review_type != 'tips':
            faq_qs = (
                KnowledgeArticle.objects
                .select_related('created_by', 'approved_by')
                .prefetch_related('attachments', 'images')
                .order_by('-created_at')
            )
            for article in faq_qs:
                ordered_attachments = sorted(
                    article.attachments.all(),
                    key=lambda attachment: (attachment.uploaded_at, attachment.id),
                )
                question_images = [
                    attachment
                    for attachment in ordered_attachments
                    if attachment.placement == ArticleAttachment.PLACEMENT_QUESTION
                ]
                answer_images = [
                    attachment
                    for attachment in ordered_attachments
                    if attachment.placement == ArticleAttachment.PLACEMENT_ANSWER
                ]
                review_items.append(
                    {
                        'type': 'faq',
                        'id': article.id,
                        'title': article.title,
                        'creator_id': article.created_by_id,
                        'creator_display_name': resolve_saved_or_user_display_name(
                            article.created_by_name,
                            article.created_by,
                        ),
                        'created_at': article.created_at,
                        'updated_at': article.updated_at,
                        'approval_status': approval_status_value(article),
                        'remand_reason': article.remand_reason,
                        'standard_contract_only': article.standard_contract_only,
                        'visible_to_customer': article.visible_to_customer,
                        'approve_url_name': 'article_approve',
                        'remand_url_name': 'article_remand',
                        'edit_url_name': 'article_edit',
                        'question': article.summary,
                        'answer': article.body,
                        'question_images': question_images,
                        'answer_images': answer_images,
                    }
                )

        if review_type != 'faq':
            tips_qs = (
                TipsArticle.objects
                .select_related('created_by', 'approved_by')
                .prefetch_related('images')
                .order_by('-created_at')
            )
            for tip in tips_qs:
                review_items.append(
                    {
                        'type': 'tips',
                        'id': tip.id,
                        'title': tip.title,
                        'creator_id': tip.created_by_id,
                        'creator_display_name': resolve_saved_or_user_display_name(
                            tip.created_by_name,
                            tip.created_by,
                        ),
                        'created_at': tip.created_at,
                        'updated_at': tip.updated_at,
                        'approval_status': approval_status_value(tip),
                        'remand_reason': tip.remand_reason,
                        'standard_contract_only': tip.standard_contract_only,
                        'visible_to_customer': tip.visible_to_customer,
                        'approve_url_name': 'tip_approve',
                        'remand_url_name': 'tip_remand',
                        'edit_url_name': 'tip_edit',
                        'body': tip.body,
                        'inline_images': sorted(
                            tip.images.all(),
                            key=lambda image: (image.uploaded_at, image.id),
                        ),
                    }
                )

        # フィルター処理
        filtered_items = []
        for item in review_items:
            if status_filter and item['approval_status'] != status_filter:
                continue
            if creator_filter and str(item.get('creator_id') or '') != creator_filter:
                continue
            created_date = item['created_at'].date()
            if date_from_value and created_date < date_from_value:
                continue
            if date_to_value and created_date > date_to_value:
                continue
            filtered_items.append(item)

        # ソート処理
        reverse = (sort_dir == 'desc')
        filtered_items.sort(key=lambda item: item.get(sort_by, ''), reverse=reverse)

        review_items = filtered_items

        def build_sort_url(target_sort):
            params = self.request.GET.copy()
            if sort_by == target_sort:
                params['sort_dir'] = 'asc' if sort_dir == 'desc' else 'desc'
            else:
                params['sort_dir'] = 'desc' if target_sort == 'created_at' else 'asc'
            params['sort'] = target_sort
            return f"?{params.urlencode()}"

        context['review_type'] = review_type
        context['status_filter'] = status_filter
        context['creator_filter'] = creator_filter
        context['date_from'] = date_from
        context['date_to'] = date_to
        context['sort_by'] = sort_by
        context['sort_dir'] = sort_dir
        context['sort_title_url'] = build_sort_url('title')
        context['sort_created_at_url'] = build_sort_url('created_at')
        context['creator_list'] = creator_list
        context['review_items'] = review_items
        context['can_approve_review'] = can_approve_article(self.request.user)
        context['use_edit_button_in_review_list'] = can_reset_approval(self.request.user)
        context['return_to'] = self.request.get_full_path()
        return context


class ArticleManagementView(TemplateView):
    template_name = 'tenasapo_knowledge/article_management.html'

    def dispatch(self, request, *args, **kwargs):
        if not (
            request.user.is_authenticated
            and (
                request.user.is_staff
                or request.user.is_superuser
                or in_group(request.user, ADMIN_GROUP_NAME)
            )
        ):
            messages.error(request, 'このページを閲覧する権限がありません。')
            return redirect('home')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        article_type = (self.request.GET.get('article_type') or '').strip()
        author_filter = (self.request.GET.get('author') or '').strip()
        status_filter = (self.request.GET.get('status') or '').strip()
        date_from = (self.request.GET.get('date_from') or '').strip()
        date_to = (self.request.GET.get('date_to') or '').strip()
        sort_by = (self.request.GET.get('sort') or 'created_at').strip()
        sort_dir = (self.request.GET.get('dir') or 'desc').strip()

        # 投稿者ロールのユーザー表示名リストを構築（プルダウン用）
        User = get_user_model()
        contributor_users = User.objects.filter(
            groups__name=CONTRIBUTOR_GROUP_NAME
        ).distinct().prefetch_related('knowledge_profile').order_by('knowledge_profile__uid', 'id')
        author_choices = []
        seen_names = set()
        for u in contributor_users:
            name = resolve_user_display_name(u)
            if name and name not in seen_names:
                author_choices.append({'id': u.id, 'name': name})
                seen_names.add(name)

        combined = []

        if article_type != 'tips':
            faq_qs = (
                KnowledgeArticle.objects
                .select_related('created_by', 'approved_by', 'customer')
                .prefetch_related('attachments', 'images')
                .annotate(good_count=Count('goods'))
                .order_by('-created_at')
            )
            for article in faq_qs:
                creator_name = resolve_saved_or_user_display_name(
                    article.created_by_name, article.created_by
                )
                ordered_attachments = sorted(
                    article.attachments.all(),
                    key=lambda a: (a.uploaded_at, a.id),
                )
                question_images = [
                    a for a in ordered_attachments
                    if a.placement == ArticleAttachment.PLACEMENT_QUESTION
                ]
                body_images = sorted(
                    article.images.all(),
                    key=lambda img: (img.uploaded_at, img.id),
                )
                file_attachments = [
                    a for a in ordered_attachments
                    if a.placement == ArticleAttachment.PLACEMENT_ATTACHMENT
                ]

                combined.append({
                    'type': 'faq',
                    'id': article.id,
                    'title': article.title,
                    'creator_display_name': creator_name,
                    'created_at': article.created_at,
                    'updated_at': article.updated_at,
                    'is_published': article.is_published,
                    'is_approved': article.is_approved,
                    'good_count': article.good_count,
                    'view_count': article.answer_view_count,
                    'category_chips': list(dict.fromkeys(
                        ArticleListView.split_categories(article.category)
                    )),
                    'obj': article,
                    'question_images': question_images,
                    'body_images': body_images,
                    'file_attachments': file_attachments,
                    'inline_images': [],
                })

        if article_type != 'faq':
            tips_qs = (
                TipsArticle.objects
                .select_related('created_by', 'approved_by')
                .prefetch_related('images')
                .annotate(good_count=Count('goods'))
                .order_by('-created_at')
            )
            for tip in tips_qs:
                creator_name = resolve_saved_or_user_display_name(
                    tip.created_by_name, tip.created_by
                )
                inline_images = sorted(
                    tip.images.all(),
                    key=lambda img: (img.uploaded_at, img.id),
                )

                combined.append({
                    'type': 'tips',
                    'id': tip.id,
                    'title': tip.title,
                    'creator_display_name': creator_name,
                    'created_at': tip.created_at,
                    'updated_at': tip.updated_at,
                    'is_published': tip.is_published,
                    'is_approved': tip.is_approved,
                    'good_count': tip.good_count,
                    'view_count': tip.view_count,
                    'category_chips': list(dict.fromkeys(
                        TipsListView.split_categories(tip.category)
                    )),
                    'obj': tip,
                    'question_images': [],
                    'body_images': [],
                    'file_attachments': [],
                    'inline_images': inline_images,
                })

        # 作成者フィルター（プルダウン値と完全一致）
        if author_filter:
            combined = [
                item for item in combined
                if item['creator_display_name'] == author_filter
            ]

        # 公開状態フィルター
        if status_filter == 'published':
            combined = [
                item for item in combined
                if item['is_published'] and item['is_approved']
            ]
        elif status_filter == 'unpublished':
            combined = [
                item for item in combined
                if not (item['is_published'] and item['is_approved'])
            ]

        # 期間フィルター（created_at ベース）
        if date_from:
            try:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d').date()
                combined = [
                    item for item in combined
                    if item['obj'].created_at.date() >= date_from_obj
                ]
            except ValueError:
                pass

        if date_to:
            try:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d').date()
                combined = [
                    item for item in combined
                    if item['obj'].created_at.date() <= date_to_obj
                ]
            except ValueError:
                pass

        # 閲覧数内訳（システナ/カスタマー）を計算
        faq_page_names = {
            f"FAQ回答表示: {item['title']}"[:200]
            for item in combined
            if item['type'] == 'faq'
        }
        tips_page_names = {
            f"Tips表示: {item['title']}"[:200]
            for item in combined
            if item['type'] == 'tips'
        }
        target_page_names = faq_page_names | tips_page_names

        creator_by_page_name = {}
        for item in combined:
            page_name = (
                f"FAQ回答表示: {item['title']}"[:200]
                if item['type'] == 'faq'
                else f"Tips表示: {item['title']}"[:200]
            )
            creator_by_page_name.setdefault(page_name, item['obj'].created_by)

        view_breakdown_by_page_name = {
            page_name: {'systena': 0, 'customer': 0}
            for page_name in target_page_names
        }
        if target_page_names:
            histories = (
                ViewHistory.objects
                .filter(page_name__in=target_page_names)
                .select_related('user')
                .prefetch_related('user__groups')
            )
            for history in histories:
                user = history.user
                if not user:
                    continue
                page_name = history.page_name
                if page_name not in view_breakdown_by_page_name:
                    continue
                content_creator = creator_by_page_name.get(page_name)
                if not should_count_content_view(user, content_creator):
                    continue
                if in_group(user, SYSTENA_GROUP_NAME):
                    view_breakdown_by_page_name[page_name]['systena'] += 1
                else:
                    view_breakdown_by_page_name[page_name]['customer'] += 1

        for item in combined:
            page_name = (
                f"FAQ回答表示: {item['title']}"[:200]
                if item['type'] == 'faq'
                else f"Tips表示: {item['title']}"[:200]
            )
            breakdown = view_breakdown_by_page_name.get(page_name, {'systena': 0, 'customer': 0})
            systena_count = breakdown['systena']
            customer_count = breakdown['customer']
            total_view_count = item.get('view_count') or 0
            resolved_total = systena_count + customer_count
            if total_view_count > resolved_total:
                customer_count += (total_view_count - resolved_total)

            item['view_count_systena'] = systena_count
            item['view_count_customer'] = customer_count

        # ソート
        reverse = (sort_dir != 'asc')
        if sort_by == 'good_count':
            combined.sort(key=lambda item: item['good_count'], reverse=reverse)
        elif sort_by == 'view_count':
            combined.sort(key=lambda item: (item['view_count'] or 0), reverse=reverse)
        else:
            combined.sort(key=lambda item: item['created_at'], reverse=reverse)

        context['articles'] = combined
        context['article_type'] = article_type
        context['author_filter'] = author_filter
        context['author_choices'] = author_choices
        context['status_filter'] = status_filter
        context['date_from'] = date_from
        context['date_to'] = date_to
        context['sort_by'] = sort_by
        context['sort_dir'] = sort_dir
        context['can_edit'] = can_edit_article(self.request.user)
        return context

