from django import forms

from ...models import Manual


class ManualForm(forms.ModelForm):
    description = forms.CharField(
        label='使い方・操作手順', required=True,
        widget=forms.Textarea(attrs={'rows': 18}),
    )

    class Meta:
        model = Manual
        fields = ('title', 'description', 'order', 'is_published')
        labels = {
            'title': 'タイトル',
            'description': '使い方・操作手順',
            'order': '表示順',
            'is_published': '公開',
        }