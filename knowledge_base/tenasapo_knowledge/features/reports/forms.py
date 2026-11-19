from django import forms
from django.core.exceptions import ValidationError

from ...models import POSTING_QUOTA_START


class PostingQuotaForm(forms.Form):
    effective_month = forms.DateField(
        label='対象月', input_formats=['%Y-%m'],
        initial=POSTING_QUOTA_START,
        widget=forms.DateInput(format='%Y-%m', attrs={
            'type': 'month', 'min': '2026-06', 'class': 'form-control',
        }),
    )
    faq_count = forms.IntegerField(label='1人あたり Q&A月間件数', min_value=0, max_value=2147483647,
                                   widget=forms.NumberInput(attrs={'class': 'form-control'}))
    tips_count = forms.IntegerField(label='1人あたり Tips月間件数', min_value=0, max_value=2147483647,
                                    widget=forms.NumberInput(attrs={'class': 'form-control'}))
    alpha_count = forms.IntegerField(label='1人あたり ＋α月間件数', min_value=0, max_value=2147483647,
                                     initial=0, required=True,
                                     widget=forms.NumberInput(attrs={'class': 'form-control'}))

    def clean_effective_month(self):
        value = self.cleaned_data['effective_month']
        if value < POSTING_QUOTA_START:
            raise ValidationError('2026年6月以降を指定してください。')
        return value