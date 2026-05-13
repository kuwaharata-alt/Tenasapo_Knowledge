from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.models import Group
from django.contrib.auth.mixins import UserPassesTestMixin
from django.conf import settings
from django.db.models import Count, F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.crypto import get_random_string
from django.views import View
from django.views.generic import FormView, ListView
import json

from .forms import FAQCategoryCreateForm, KnowledgeArticleCreateForm, ManualForm, UserCreateForm, UserUpdateForm
from .models import (
    ArticleGood,
    ArticleAttachment,
    Customer,
    FAQCategory,
    KnowledgeArticle,
    LoginHistory,
    Manual,
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


def in_group(user, group_name):
    return user.is_authenticated and user.groups.filter(name=group_name).exists()


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
            or in_group(user, REVIEWER_GROUP_NAME)
        )
    )


def is_customer_user(user):
    return (
        user.is_authenticated
        and in_group(user, CUSTOMER_GROUP_NAME)
        and not (user.is_staff or user.is_superuser)
    )


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
        liked_article_ids = set(
            ArticleGood.objects.filter(
                user=self.request.user,
                article_id__in=[article.id for article in context['articles']],
            ).values_list('article_id', flat=True)
        )
        for article in context['articles']:
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
        if selected_category and not selected_parent:
            selected_parent = self.parent_category_name(selected_category)
        context['parent_categories'] = self.available_parent_category_groups()
        context['selected_parent_category'] = selected_parent
        context['selected_category'] = selected_category
        context['grouped_articles'] = self.group_articles(context['articles'], selected_parent)
        context['query'] = self.request.GET.get('q', '')
        return context

    @staticmethod
    def split_categories(value):
        return [category.strip() for category in (value or '').split(',') if category.strip()]

    @staticmethod
    def parent_category_name(category):
        if '/' in category:
            return category.split('/', 1)[0].strip()
        return category.strip() or '未分類'

    @classmethod
    def article_parent_categories(cls, article):
        parent_names = [
            cls.parent_category_name(category)
            for category in cls.split_categories(article.category)
        ]
        return list(dict.fromkeys(parent_names or ['未分類']))

    @classmethod
    def available_parent_categories(cls):
        parent_categories = [
            parent_name
            for parent_name, _ in FAQCategoryCreateForm.PARENT_CATEGORY_CHOICES
        ]
        parent_categories.extend(
            FAQCategory.objects.values_list('parent_name', flat=True).distinct()
        )
        for category_text in KnowledgeArticle.objects.filter(is_published=True).values_list('category', flat=True):
            parent_categories.extend(
                cls.parent_category_name(category)
                for category in cls.split_categories(category_text)
            )
        return list(dict.fromkeys(parent_categories))

    @classmethod
    def available_parent_category_groups(cls):
        child_categories = {}
        for parent_name in cls.available_parent_categories():
            child_categories.setdefault(parent_name, [])

        for category in FAQCategory.objects.order_by('parent_name', 'child_name'):
            child_categories.setdefault(category.parent_name, []).append(
                {
                    'name': category.child_name,
                    'full_name': category.full_name,
                }
            )

        for category_text in KnowledgeArticle.objects.filter(is_published=True).values_list('category', flat=True):
            for category_name in cls.split_categories(category_text):
                parent_name = cls.parent_category_name(category_name)
                child_name = category_name.split('/', 1)[1].strip() if '/' in category_name else category_name
                child_categories.setdefault(parent_name, []).append(
                    {
                        'name': child_name,
                        'full_name': category_name,
                    }
                )

        groups = []
        for parent_name, children in child_categories.items():
            unique_children = {}
            for child in children:
                unique_children[child['full_name']] = child
            groups.append(
                {
                    'name': parent_name,
                    'children': list(unique_children.values()),
                }
            )
        return groups

    @classmethod
    def group_articles(cls, articles, selected_parent=''):
        grouped_articles = []
        parent_categories = [selected_parent] if selected_parent else cls.available_parent_categories()

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
        article = KnowledgeArticle.objects.create(
            category=form.cleaned_data['category'],
            title=form.cleaned_data['question'],
            body=form.cleaned_data['answer'],
            is_approved=not FAQ_APPROVAL_ENABLED,
            visible_to_customer=form.cleaned_data['visible_to_customer'],
            visible_to_systena=form.cleaned_data['visible_to_systena'],
            source_published_at=form.cleaned_data['source_published_at'],
            created_by=self.request.user,
            created_by_name=self.request.user.get_username(),
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
            if '/' not in category_name:
                continue
            parent_name, child_name = category_name.split('/', 1)
            category = FAQCategory.objects.filter(
                parent_name=parent_name,
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
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'FAQ編集'
        context['submit_label'] = '更新'
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
        context['category_groups'] = KnowledgeArticleCreateView.category_groups(context['form'])
        return context

    def form_valid(self, form):
        self.article.category = form.cleaned_data['category']
        self.article.title = form.cleaned_data['question']
        self.article.body = form.cleaned_data['answer']
        self.article.visible_to_customer = form.cleaned_data['visible_to_customer']
        self.article.visible_to_systena = form.cleaned_data['visible_to_systena']
        self.article.source_published_at = form.cleaned_data['source_published_at']
        self.article.save(
            update_fields=[
                'category',
                'title',
                'body',
                'visible_to_customer',
                'visible_to_systena',
                'source_published_at',
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
            return redirect('article_edit', pk=article.id)

        if article.is_approved:
            messages.info(request, f'FAQ「{article.title}」は既に承認済みです。')
            return redirect('article_edit', pk=article.id)

        article.is_approved = True
        article.approved_by = request.user
        article.approved_by_name = request.user.get_username()
        article.save(update_fields=['is_approved', 'approved_by', 'approved_by_name', 'updated_at'])
        messages.success(request, f'FAQ「{article.title}」を承認しました。')
        return redirect('article_edit', pk=article.id)


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
        context['categories'] = FAQCategory.objects.all()
        return context

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
        context['categories'] = FAQCategory.objects.all()
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
        queryset = User.objects.select_related('knowledge_profile').prefetch_related('groups').order_by('username')

        query = self.request.GET.get('q')
        if query:
            queryset = queryset.filter(username__icontains=query)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['query'] = self.request.GET.get('q', '')
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
    context_object_name = 'view_histories'
    paginate_by = 50

    def get_queryset(self):
        queryset = ViewHistory.objects.select_related('user', 'login_history')
        query = self.request.GET.get('q', '').strip()
        if query:
            queryset = queryset.filter(username__icontains=query)
        return queryset.order_by('-login_history__logged_in_at', 'viewed_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['query'] = self.request.GET.get('q', '')
        grouped_histories = {}
        for history in context['view_histories']:
            login_history = history.login_history
            if not login_history:
                continue
            session_key = f'session-{login_history.id}'
            if session_key not in grouped_histories:
                grouped_histories[session_key] = {
                    'session': login_history,
                    'items': [],
                }
            grouped_histories[session_key]['items'].append(history)
        context['grouped_view_histories'] = list(grouped_histories.values())
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
            'username': self.user_obj.username,
            'company_name': profile.company_name if profile else '',
            'role': UserCreateForm.ROLE_ADMIN if self.user_obj.is_staff else UserCreateForm.ROLE_USER,
            'groups': list(self.user_obj.groups.values_list('name', flat=True)),
            'email_addresses': profile.email_addresses if profile else '',
            'note': profile.note if profile else '',
        }

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
