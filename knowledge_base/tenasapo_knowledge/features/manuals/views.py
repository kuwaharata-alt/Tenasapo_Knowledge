from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import FormView, ListView

from ...models import Manual
from ..shared.access import StaffRequiredMixin
from ..shared.history import record_view_history
from .forms import ManualForm


class ManualListView(StaffRequiredMixin, ListView):
    model = Manual
    template_name = 'tenasapo_knowledge/manual_list.html'
    context_object_name = 'manuals'

    def dispatch(self, request, *args, **kwargs):
        record_view_history(request, 'マニュアル一覧')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Manual.objects.all()

    def get_context_data(self, **kwargs):
        from ...manual_presentation import (
            MENU_ICONS, build_menu_groups, menu_definitions, parse_manual_body,
        )
        context = super().get_context_data(**kwargs)
        manuals = list(context['manuals'])
        keyed = {manual.menu_key: manual for manual in manuals if manual.menu_key}
        sections = []
        for definition in menu_definitions():
            manual = keyed.get(definition.menu_key)
            if manual is None:
                continue
            anchor = f'manual-{manual.menu_key}'
            sections.append({
                'manual': manual, 'anchor': anchor,
                'icon': MENU_ICONS[manual.menu_key],
                'blocks': parse_manual_body(manual.description),
                'menu_url': reverse(definition.url_name),
                'return_to': reverse('manual_list') + '#' + anchor,
            })
        supplemental = [
            {'manual': manual, 'anchor': f'manual-legacy-{manual.pk}',
             'icon': 'bi-journal-text', 'blocks': parse_manual_body(manual.description),
             'return_to': reverse('manual_list') + f'#manual-legacy-{manual.pk}'}
            for manual in manuals if not manual.menu_key and manual.description.strip()
        ]
        context['menu_sections'] = sections
        context['menu_groups'] = build_menu_groups(sections)
        context['supplemental_sections'] = supplemental
        context['manual_sections'] = sections + supplemental
        return context


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
        context['return_to'] = self.get_success_url()
        return context

    def get_success_url(self):
        candidate = self.request.POST.get('next') or self.request.GET.get('next') or ''
        if candidate and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return candidate
        return reverse('manual_list')

    def form_valid(self, form):
        manual = form.save()
        messages.success(self.request, f'マニュアル「{manual.title}」を更新しました。')
        return super().form_valid(form)


class ManualDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        manual = get_object_or_404(Manual, pk=pk)
        if manual.menu_key:
            messages.error(request, '固定メニューの操作手順は削除できません。編集で内容を更新してください。')
            return redirect('manual_list')
        title = manual.title
        manual.delete()
        messages.success(request, f'マニュアル「{title}」を削除しました。')
        return redirect('manual_list')