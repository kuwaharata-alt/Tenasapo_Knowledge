from collections import Counter
from datetime import datetime, timedelta
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
from django.template import Context, Template
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.crypto import get_random_string
from django.utils import timezone
from django.views import View
from django.views.generic import FormView, ListView, TemplateView

from .forms import (
    ConvenienceFeatureCreateForm,
    FAQCategoryCreateForm,
    KnowledgeArticleCreateForm,
    ManualForm,
    parse_target_os_entries_json,
    parse_target_os_value,
    parse_target_os_values,
    TARGET_OS_VERSION_MAP,
    TipsCreateForm,
    UserCreateForm,
    UserUpdateForm,
)
from .models import (
    ConvenienceFeature,
    ConvenienceFavorite,
    ArticleFavorite,
    ArticleGood,
    ArticleAttachment,
    Customer,
    FAQCategory,
    FAQParentCategorySetting,
    KnowledgeArticle,
    KnowledgeArticleImageAttachment,
    LoginHistory,
    Manual,
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
                    {'label': 'FAQ登録', 'url_name': 'article_create'},
                    {'label': 'Tips登録', 'url_name': 'tip_create'},
                    {'label': 'クイックリファレンス登録', 'url_name': 'convenience_create'},
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
                    {'label': 'ログイン履歴', 'url_name': 'login_history_list'},
                    {'label': '閲覧履歴', 'url_name': 'view_history_list'},
                ]
            )
        context['menu_groups'] = [group for group in menu_groups if group['items']]
        return context


class ConvenienceListView(TemplateView):
    template_name = 'tenasapo_knowledge/convenience_list.html'

    def dispatch(self, request, *args, **kwargs):
        record_view_history(request, 'QR一覧')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tab = (self.request.GET.get('tab') or ConvenienceFeature.TYPE_SHORTCUT).strip().lower()
        if tab not in {ConvenienceFeature.TYPE_SHORTCUT, ConvenienceFeature.TYPE_COMMAND}:
            tab = ConvenienceFeature.TYPE_SHORTCUT
        sort_mode = (self.request.GET.get('sort') or 'frequency').strip().lower()
        if sort_mode not in {'frequency', 'favorite'}:
            sort_mode = 'frequency'

        features = list(ConvenienceFeature.objects.filter(reference_type=tab))
        favorite_ids = set()
        can_use_convenience_favorite_flag = can_use_convenience_favorite(self.request.user)
        if can_use_convenience_favorite_flag:
            favorite_ids = set(
                ConvenienceFavorite.objects.filter(user=self.request.user, feature__in=features)
                .values_list('feature_id', flat=True)
            )
        for feature in features:
            try:
                frequency = int(feature.usage_frequency or '0')
            except (TypeError, ValueError):
                frequency = 0
            frequency = max(0, min(5, frequency))
            feature.usage_frequency_value = frequency
            feature.usage_frequency_stars = ('★' * frequency) + ('☆' * (5 - frequency))
            feature.is_favorited = feature.id in favorite_ids

        if sort_mode == 'favorite' and can_use_convenience_favorite_flag:
            features.sort(
                key=lambda feature: (
                    not feature.is_favorited,
                    -feature.usage_frequency_value,
                    feature.category or '',
                    feature.display_text or '',
                    feature.id,
                )
            )
        else:
            sort_mode = 'frequency'
            features.sort(
                key=lambda feature: (
                    -feature.usage_frequency_value,
                    feature.category or '',
                    feature.display_text or '',
                    feature.id,
                )
            )

        available_categories = list(dict.fromkeys(feature.category for feature in features if feature.category))
        selected_category = (self.request.GET.get('category') or '').strip()
        if selected_category and selected_category not in available_categories:
            selected_category = ''

        filtered_features = [
            feature for feature in features
            if not selected_category or feature.category == selected_category
        ]

        group_map = {}
        for feature in filtered_features:
            group_name = (feature.middle_category or '未分類').strip() or '未分類'
            group_map.setdefault(group_name, []).append(feature)

        groups = [
            {
                'middle_name': middle_name,
                'features': items,
            }
            for middle_name, items in group_map.items()
        ]

        context['category_groups'] = [
            {
                'middle_name': group['middle_name'],
                'features': group['features'],
            }
            for group in groups
        ]
        context['active_tab'] = tab
        context['available_categories'] = available_categories
        context['selected_category'] = selected_category
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
            queryset = queryset.filter(
                Q(title__icontains=query)
                | Q(summary__icontains=query)
                | Q(target_os__icontains=query)
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
        can_view_approval_meta = in_group(self.request.user, SYSTENA_GROUP_NAME)
        context['can_use_good'] = is_customer_user(self.request.user)
        context['can_use_favorite'] = can_use_favorite(self.request.user)
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
        favorite_article_ids = set(
            ArticleFavorite.objects.filter(
                user=self.request.user,
                article_id__in=[article.id for article in visible_articles],
            ).values_list('article_id', flat=True)
        )
        for article in visible_articles:
            article.is_gooded = article.id in liked_article_ids
            article.is_favorited = article.id in favorite_article_ids
            article.creator_display_name = resolve_saved_or_user_display_name(
                article.created_by_name,
                article.created_by,
            )
            article.approver_display_name = resolve_saved_or_user_display_name(
                article.approved_by_name,
                article.approved_by,
            )
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
        parent_counts, category_counts = self.category_count_maps(visible_articles)
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
        context['all_count'] = len(visible_articles)
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
        can_view_approval_meta = in_group(self.request.user, SYSTENA_GROUP_NAME)
        context['can_use_good'] = is_customer_user(self.request.user)
        context['can_use_favorite'] = can_use_favorite(self.request.user)
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
        favorite_tip_ids = set(
            TipsFavorite.objects.filter(
                user=self.request.user,
                tip_id__in=[tip.id for tip in visible_tips],
            ).values_list('tip_id', flat=True)
        )

        for tip in visible_tips:
            tip.is_gooded = tip.id in liked_tip_ids
            tip.is_favorited = tip.id in favorite_tip_ids
            tip.creator_display_name = resolve_saved_or_user_display_name(
                tip.created_by_name,
                tip.created_by,
            )
            tip.approver_display_name = resolve_saved_or_user_display_name(
                tip.approved_by_name,
                tip.approved_by,
            )
            tip.category_chips = list(dict.fromkeys(self.split_categories(tip.category)))
            tip.target_os_chips = parse_target_os_values(tip.target_os)
            tip.inline_images = sorted(
                tip.images.all(),
                key=lambda image: (image.uploaded_at, image.id),
            )

        selected_parent = self.request.GET.get('parent_category', '')
        selected_category = self.request.GET.get('category', '')
        parent_categories = self.available_parent_category_groups()
        parent_counts, category_counts = self.category_count_maps(visible_tips)
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
        context['all_count'] = len(visible_tips)
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
        context['category_groups'] = KnowledgeArticleCreateView.category_groups(context['form'])
        context['category_browser'] = FAQCategoryCreateView.category_browser_data()
        context['category_browser_json'] = json.dumps(context['category_browser'], ensure_ascii=False)
        context['registered_category_values_json'] = json.dumps(
            KnowledgeArticleCreateView.selected_registered_category_values(context['form']),
            ensure_ascii=False,
        )
        context['target_os_version_map_json'] = json.dumps(TARGET_OS_VERSION_MAP, ensure_ascii=False)
        context['target_os_entries_json'] = json.dumps(target_os_entries_for_form(context['form']), ensure_ascii=False)
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
        registered_category_ids, unregistered_category_text = split_registered_and_unregistered_categories(
            self.tip.category
        )
        parsed_target_os = parse_target_os_value(self.tip.target_os)
        return {
            'registered_category': registered_category_ids,
            'category': unregistered_category_text,
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
        context['tip'] = self.tip
        context['tip_approver_display_name'] = resolve_saved_or_user_display_name(
            self.tip.approved_by_name,
            self.tip.approved_by,
        )
        context['approval_enabled'] = FAQ_APPROVAL_ENABLED
        context['can_approve_tip'] = can_approve_article(self.request.user)
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
        tip.approved_by_name = resolve_user_display_name(request.user)
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
        current_month_key = today.strftime('%Y-%m')

        faq_qs = (
            KnowledgeArticle.objects.select_related('created_by')
            .filter(visible_to_any_account_filter())
            .annotate(good_count=Count('goods'))
        )
        tips_qs = (
            TipsArticle.objects.select_related('created_by')
            .filter(visible_to_any_account_filter())
            .annotate(good_count=Count('goods'))
        )

        if from_date:
            faq_qs = faq_qs.filter(created_at__date__gte=from_date)
            tips_qs = tips_qs.filter(created_at__date__gte=from_date)
        if to_date:
            faq_qs = faq_qs.filter(created_at__date__lte=to_date)
            tips_qs = tips_qs.filter(created_at__date__lte=to_date)

        faq_articles = list(faq_qs.order_by('-created_at'))
        tips_articles = list(tips_qs.order_by('-created_at'))

        User = get_user_model()
        summary_users = User.objects.filter(
            Q(is_staff=True)
            | Q(is_superuser=True)
            | Q(groups__name=SYSTENA_GROUP_NAME)
            | Q(groups__name=ADMIN_GROUP_NAME)
        ).distinct().prefetch_related('knowledge_profile').order_by('id')

        member_map = {}
        member_monthly_map = {}
        member_order_map = {}

        def ensure_member(name, member_id=None):
            node = member_map.get(name)
            if node is None:
                if member_id is None:
                    member_id = member_order_map.get(name, 10**9)
                node = {
                    'member_id': member_id,
                    'name': name,
                    'faq_post_count': 0,
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
                    'faq_post_count': 0,
                    'tips_post_count': 0,
                    'good_count_total': 0,
                    'view_count_total': 0,
                }
                monthly[month_key] = month_node
            return month_node

        for user in summary_users:
            display_name = resolve_user_display_name(user).strip()
            if self.is_excluded_contributor(display_name):
                continue
            if display_name not in member_order_map:
                member_order_map[display_name] = user.id
            ensure_member(display_name, member_order_map[display_name])

        for article in faq_articles:
            creator_name = self.resolve_contributor_name(article.created_by_name, article.created_by)
            if self.is_excluded_contributor(creator_name):
                continue
            creator_id = member_order_map.get(creator_name)
            if creator_id is None and article.created_by_id:
                creator_id = article.created_by_id
                member_order_map[creator_name] = creator_id
            member = ensure_member(creator_name, creator_id)
            month_key = article.created_at.strftime('%Y-%m')
            month_node = ensure_member_month(creator_name, month_key)
            member['faq_post_count'] += 1
            member['good_count_total'] += article.good_count
            member['view_count_total'] += article.answer_view_count
            month_node['faq_post_count'] += 1
            month_node['good_count_total'] += article.good_count
            month_node['view_count_total'] += article.answer_view_count

        for tip in tips_articles:
            creator_name = self.resolve_contributor_name(tip.created_by_name, tip.created_by)
            if self.is_excluded_contributor(creator_name):
                continue
            creator_id = member_order_map.get(creator_name)
            if creator_id is None and tip.created_by_id:
                creator_id = tip.created_by_id
                member_order_map[creator_name] = creator_id
            member = ensure_member(creator_name, creator_id)
            month_key = tip.created_at.strftime('%Y-%m')
            month_node = ensure_member_month(creator_name, month_key)
            member['tips_post_count'] += 1
            member['good_count_total'] += tip.good_count
            member['view_count_total'] += tip.view_count
            month_node['tips_post_count'] += 1
            month_node['good_count_total'] += tip.good_count
            month_node['view_count_total'] += tip.view_count

        member_summaries = list(member_map.values())
        for item in member_summaries:
            current_month = member_monthly_map.get(item['name'], {}).get(current_month_key, {})
            item['current_month_faq_post_count'] = current_month.get('faq_post_count', 0)
            item['current_month_tips_post_count'] = current_month.get('tips_post_count', 0)
            item['current_month_post_count_total'] = (
                item['current_month_faq_post_count'] + item['current_month_tips_post_count']
            )
            item['post_count_total'] = item['faq_post_count'] + item['tips_post_count']
            monthly_totals = list(member_monthly_map.get(item['name'], {}).values())
            for month_item in monthly_totals:
                month_item['post_count_total'] = month_item['faq_post_count'] + month_item['tips_post_count']
            monthly_totals.sort(key=lambda month_item: month_item['month'], reverse=True)
            item['monthly_totals'] = monthly_totals

        member_summaries.sort(key=lambda item: (item['member_id'], item['name'].lower()))

        context['selected_period'] = selected_period
        context['member_summaries'] = member_summaries
        context['summary_totals'] = {
            'faq_post_count': sum(item['faq_post_count'] for item in member_summaries),
            'tips_post_count': sum(item['tips_post_count'] for item in member_summaries),
            'good_count_total': sum(item['good_count_total'] for item in member_summaries),
            'view_count_total': sum(item['view_count_total'] for item in member_summaries),
            'post_count_total': sum(item['post_count_total'] for item in member_summaries),
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


class KnowledgeArticleCreateView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/article_form.html'
    form_class = KnowledgeArticleCreateForm
    success_url = reverse_lazy('article_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'FAQ登録'
        context['submit_label'] = '登録'
        context['category_groups'] = self.category_groups(context['form'])
        context['category_browser'] = FAQCategoryCreateView.category_browser_data()
        context['category_browser_json'] = json.dumps(context['category_browser'], ensure_ascii=False)
        context['registered_category_values_json'] = json.dumps(
            self.selected_registered_category_values(context['form']),
            ensure_ascii=False,
        )
        context['target_os_version_map_json'] = json.dumps(TARGET_OS_VERSION_MAP, ensure_ascii=False)
        context['target_os_entries_json'] = json.dumps(target_os_entries_for_form(context['form']), ensure_ascii=False)
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
        registered_category_ids, unregistered_category_text = split_registered_and_unregistered_categories(
            self.article.category
        )
        parsed_target_os = parse_target_os_value(self.article.target_os)
        return {
            'registered_category': registered_category_ids,
            'category': unregistered_category_text,
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
        context['article'] = self.article
        context['article_approver_display_name'] = resolve_saved_or_user_display_name(
            self.article.approved_by_name,
            self.article.approved_by,
        )
        context['approval_enabled'] = FAQ_APPROVAL_ENABLED
        context['can_approve_article'] = can_approve_article(self.request.user)
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
            return redirect('article_list')

        if article.is_approved:
            messages.info(request, f'FAQ「{article.title}」は既に承認済みです。')
            return redirect('article_list')

        article.is_approved = True
        article.approved_by = request.user
        article.approved_by_name = resolve_user_display_name(request.user)
        article.save(update_fields=['is_approved', 'approved_by', 'approved_by_name', 'updated_at'])
        messages.success(request, f'FAQ「{article.title}」を承認しました。')
        return redirect('article_list')


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
            display_name=form.cleaned_data['display_name'],
            company_name=form.cleaned_data['company_name'],
            user_type=profile_user_type_from_groups(selected_group_names),
            email_addresses='\n'.join(emails),
            note=form.cleaned_data['note'],
        )

        customer, _ = Customer.objects.get_or_create(name=form.cleaned_data['company_name'])
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
            'display_name': profile.display_name if profile else self.user_obj.username,
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
        profile.display_name = form.cleaned_data['display_name']
        profile.company_name = form.cleaned_data['company_name']
        profile.user_type = profile_user_type_from_groups(selected_group_names)
        profile.email_addresses = '\n'.join(emails)
        profile.note = form.cleaned_data['note']
        profile.save()

        # 顧客を更新
        customer, _ = Customer.objects.get_or_create(name=form.cleaned_data['company_name'])
        if self.user_obj not in customer.users.all():
            customer.users.add(self.user_obj)

        messages.success(self.request, f'{resolve_user_display_name(self.user_obj)}（{self.user_obj.username}）を更新しました。')
        return super().form_valid(form)


class UserDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        User = get_user_model()
        user = get_object_or_404(User, pk=pk)
        
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

        # システナ・管理者ユーザーの表示名リストを構築（プルダウン用）
        User = get_user_model()
        systena_users = User.objects.filter(
            Q(is_staff=True)
            | Q(is_superuser=True)
            | Q(groups__name=SYSTENA_GROUP_NAME)
            | Q(groups__name=ADMIN_GROUP_NAME)
        ).distinct().select_related().prefetch_related('knowledge_profile')
        author_choices = []
        seen_names = set()
        for u in systena_users.order_by('username'):
            name = resolve_user_display_name(u)
            if name and name not in seen_names:
                author_choices.append({'id': u.id, 'name': name})
                seen_names.add(name)
        author_choices.sort(key=lambda item: item['id'])

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
