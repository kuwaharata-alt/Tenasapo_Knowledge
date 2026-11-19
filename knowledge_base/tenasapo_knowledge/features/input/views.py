"""INPUT hub and QR registration/editing."""

import json
from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import FormView, TemplateView

from ...models import ConvenienceFeature
from ..shared.access import can_edit_article, is_admin_account
from ..shared.qr_categories import get_qr_category_hierarchy
from .forms import ConvenienceCategoryCreateForm, ConvenienceFeatureCreateForm


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