from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.mixins import UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.crypto import get_random_string
from django.views import View
from django.views.generic import FormView, ListView

from .forms import FAQCategoryCreateForm, KnowledgeArticleCreateForm, ManualForm, UserCreateForm
from .models import ArticleAttachment, Customer, FAQCategory, KnowledgeArticle, Manual, UserProfile


class ArticleListView(ListView):
    model = KnowledgeArticle
    template_name = 'tenasapo_knowledge/article_list.html'
    context_object_name = 'articles'

    def get_queryset(self):
        queryset = (
            KnowledgeArticle.objects.select_related('customer', 'created_by')
            .prefetch_related('attachments')
            .filter(is_published=True)
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
        for article in context['articles']:
            article.category_chips = self.split_categories(article.category)
            article.question_images = [
                attachment
                for attachment in article.attachments.all()
                if attachment.placement == ArticleAttachment.PLACEMENT_QUESTION
            ]
            article.answer_images = [
                attachment
                for attachment in article.attachments.all()
                if attachment.placement == ArticleAttachment.PLACEMENT_ANSWER
            ]
            article.file_attachments = [
                attachment
                for attachment in article.attachments.all()
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


class StaffRequiredMixin(UserPassesTestMixin):
    raise_exception = True

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and (user.is_staff or user.is_superuser)


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
            created_by=self.request.user,
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


class KnowledgeArticleUpdateView(StaffRequiredMixin, FormView):
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
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_title'] = 'FAQ編集'
        context['submit_label'] = '更新'
        context['article'] = self.article
        context['question_images'] = self.article.attachments.filter(
            placement=ArticleAttachment.PLACEMENT_QUESTION
        )
        context['answer_images'] = self.article.attachments.filter(
            placement=ArticleAttachment.PLACEMENT_ANSWER
        )
        context['category_groups'] = KnowledgeArticleCreateView.category_groups(context['form'])
        return context

    def form_valid(self, form):
        self.article.category = form.cleaned_data['category']
        self.article.title = form.cleaned_data['question']
        self.article.body = form.cleaned_data['answer']
        self.article.save(update_fields=['category', 'title', 'body', 'updated_at'])
        KnowledgeArticleCreateView.save_inline_images(self.article, form)
        messages.success(self.request, f'FAQ「{self.article.title}」を更新しました。')
        return super().form_valid(form)


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
        is_admin = form.cleaned_data['role'] == UserCreateForm.ROLE_ADMIN

        user = User.objects.create_user(
            username=form.cleaned_data['username'],
            password=form.cleaned_data['password'],
            email=emails[0] if emails else '',
            is_staff=is_admin,
            is_superuser=is_admin,
        )
        UserProfile.objects.create(
            user=user,
            company_name=form.cleaned_data['company_name'],
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
        queryset = User.objects.select_related('knowledge_profile').order_by('username')

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


# ──────────────────────────────────────────
# Manual views
# ──────────────────────────────────────────

class ManualListView(StaffRequiredMixin, ListView):
    model = Manual
    template_name = 'tenasapo_knowledge/manual_list.html'
    context_object_name = 'manuals'

    def get_queryset(self):
        return Manual.objects.all()


class ManualDetailView(StaffRequiredMixin, View):
    def get(self, request, pk):
        from django.shortcuts import render
        manual = get_object_or_404(Manual, pk=pk)
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
