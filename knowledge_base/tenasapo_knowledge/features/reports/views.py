from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from django.views.generic import FormView

from ...models import PostingQuota
from ...posting_quotas import summary_filters
from ..shared.access import StaffRequiredMixin
from .forms import PostingQuotaForm


class PostingQuotaSettingsView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/posting_quota_settings.html'
    form_class = PostingQuotaForm
    http_method_names = ['get', 'post', 'head', 'options']

    def selected_month(self):
        raw = (self.request.POST.get('effective_month') if self.request.method == 'POST'
               else self.request.GET.get('month'))
        if raw and len(raw) == 7:
            try:
                month = PostingQuotaForm.base_fields['effective_month'].clean(raw)
                if month >= date(2026, 6, 1) and month.strftime('%Y-%m') == raw:
                    return month
            except ValidationError:
                pass
        return max(timezone.localdate().replace(day=1), date(2026, 6, 1))

    def get_initial(self):
        initial = super().get_initial()
        month = self.selected_month()
        initial['effective_month'] = month
        quota = PostingQuota.objects.filter(effective_month=month).first()
        if quota is not None:
            initial.update(faq_count=quota.faq_count, tips_count=quota.tips_count,
                           alpha_count=quota.alpha_count)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        month = self.selected_month()
        raw_year = self.request.GET.get('year', '')
        year = int(raw_year) if len(raw_year) == 4 and raw_year.isascii() and raw_year.isdigit() else month.year
        if not 2026 <= year <= 9999:
            year = month.year
        filters = summary_filters(self.request.GET)
        quotas = {q.effective_month: q for q in PostingQuota.objects.filter(
            effective_month__gte=date(2026, 6, 1), effective_month__lte=date(year, 12, 1),
        )}
        previous = [q for month, q in quotas.items() if month.year < year]
        running_total = sum(q.faq_count + q.tips_count + q.alpha_count for q in previous)
        configured_months = len(previous)
        current_month = timezone.localdate().replace(day=1)
        rows = []
        for number in range(1, 13):
            target_month = date(year, number, 1)
            if target_month < date(2026, 6, 1):
                continue
            quota = quotas.get(target_month)
            total = quota.faq_count + quota.tips_count + quota.alpha_count if quota is not None else None
            if total is not None:
                running_total += total
                configured_months += 1
            required_months = (year - 2026) * 12 + number - 6 + 1
            rows.append({
                'month': target_month, 'quota': quota,
                'total': total,
                'cumulative_total': running_total if configured_months == required_months else None,
                'is_current_month': target_month == current_month,
                'edit_query': urlencode({**filters, 'year': year, 'month': target_month.strftime('%Y-%m')}),
            })
        context.update(quota_rows=rows, selected_year=year,
                       quota_years=sorted({*range(2026, 2031), timezone.localdate().year, year}),
                       quota_filters=filters)
        context['summary_return_url'] = self.get_success_url()
        return context

    def get_success_url(self):
        return reverse('summary') + '?' + urlencode(summary_filters(self.request.GET))

    def form_valid(self, form):
        quota, created = PostingQuota.objects.update_or_create(
            effective_month=form.cleaned_data['effective_month'],
            defaults={
                'faq_count': form.cleaned_data['faq_count'],
                'tips_count': form.cleaned_data['tips_count'],
                'alpha_count': form.cleaned_data['alpha_count'],
                'updated_by': self.request.user,
            },
        )
        action = '登録' if created else '更新（同じ対象月の設定を上書き）'
        messages.success(self.request, f'{quota.effective_month:%Y年%m月}の投稿目標を{action}しました。他の月は変更していません。')
        return super().form_valid(form)