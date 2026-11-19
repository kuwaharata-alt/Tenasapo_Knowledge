from django import forms

from ...models import ConvenienceCategory, ConvenienceFeature
from ..shared.qr_categories import get_qr_category_hierarchy


class ConvenienceCategoryCreateForm(forms.ModelForm):
    reference_type = forms.ChoiceField(
        label='大カテゴリ',
        choices=(),
        required=False,
    )
    new_reference_type = forms.CharField(
        label='大カテゴリ（新規）',
        required=False,
        max_length=120,
    )
    category = forms.CharField(
        label='中カテゴリ',
        required=True,
        max_length=120,
    )
    middle_category = forms.CharField(
        label='小カテゴリ',
        required=False,
        max_length=120,
    )

    class Meta:
        model = ConvenienceCategory
        fields = ('reference_type', 'category', 'middle_category')

    def __init__(self, *args, **kwargs):
        self.allow_new_reference_type = kwargs.pop('allow_new_reference_type', False)
        super().__init__(*args, **kwargs)
        existing_reference_types = list(
            dict.fromkeys(
                [value for value, _ in ConvenienceFeature.TYPE_CHOICES]
                + list(ConvenienceCategory.objects.values_list('reference_type', flat=True).distinct())
            )
        )
        candidates = [item for item in existing_reference_types if item]
        self.fields['reference_type'].choices = [('', '選択してください')] + [(item, item) for item in candidates]

        if self.instance and getattr(self.instance, 'pk', None):
            self.initial.setdefault('reference_type', self.instance.reference_type)

        if self.allow_new_reference_type:
            self.fields['new_reference_type'].help_text = '既存候補にない場合のみ入力してください。'
        else:
            self.fields['new_reference_type'].widget = forms.HiddenInput()

    def clean_reference_type(self):
        return self.cleaned_data.get('reference_type', '').strip()

    def clean_new_reference_type(self):
        return self.cleaned_data.get('new_reference_type', '').strip()

    def clean_category(self):
        return self.cleaned_data.get('category', '').strip()

    def clean_middle_category(self):
        return self.cleaned_data.get('middle_category', '').strip()

    def clean(self):
        cleaned_data = super().clean()
        selected_reference_type = (cleaned_data.get('reference_type') or '').strip()
        new_reference_type = (cleaned_data.get('new_reference_type') or '').strip()

        if new_reference_type and not self.allow_new_reference_type:
            self.add_error('new_reference_type', '大カテゴリの新規作成権限がありません。')

        reference_type = new_reference_type or selected_reference_type
        category = (cleaned_data.get('category') or '').strip()
        middle_category = (cleaned_data.get('middle_category') or '').strip()

        if not reference_type:
            self.add_error('reference_type', '大カテゴリを選択してください。')
            cleaned_data['reference_type'] = ''
            return cleaned_data

        cleaned_data['reference_type'] = reference_type
        if reference_type and category:
            exists = ConvenienceCategory.objects.filter(
                reference_type=reference_type,
                category=category,
                middle_category=middle_category,
            )
            if self.instance.pk:
                exists = exists.exclude(pk=self.instance.pk)
            if exists.exists():
                raise forms.ValidationError('同じカテゴリ構成がすでに登録されています。')
        return cleaned_data


class ConvenienceFeatureCreateForm(forms.ModelForm):
    USAGE_FREQUENCY_CHOICES = getattr(
        ConvenienceFeature,
        'USAGE_FREQUENCY_CHOICES',
        (
            ('1', '1'),
            ('2', '2'),
            ('3', '3'),
            ('4', '4'),
            ('5', '5'),
        ),
    )

    reference_type = forms.CharField(
        label='大カテゴリ',
        max_length=20,
        widget=forms.HiddenInput(),
    )
    category = forms.CharField(
        label='中カテゴリ',
        max_length=120,
        widget=forms.HiddenInput(),
    )
    middle_category = forms.CharField(
        label='小カテゴリ',
        required=False,
        max_length=120,
        widget=forms.HiddenInput(),
    )
    usage_frequency = forms.ChoiceField(
        label='使用頻度',
        choices=USAGE_FREQUENCY_CHOICES,
        initial='3',
    )
    display_text = forms.CharField(
        label='内容',
        widget=forms.Textarea(attrs={'rows': 4}),
    )

    class Meta:
        model = ConvenienceFeature
        fields = ('reference_type', 'category', 'middle_category', 'shortcut_key', 'display_text', 'note', 'image')
        labels = {
            'reference_type': '大カテゴリ',
            'category': '中カテゴリ',
            'middle_category': '小カテゴリ',
            'usage_frequency': '使用頻度',
            'shortcut_key': 'ショートカットキー',
            'display_text': '内容',
            'note': '備考',
            'image': '画像',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def clean_reference_type(self):
        value = self.cleaned_data.get('reference_type', '').strip()
        valid_values = [item['value'] for item in get_qr_category_hierarchy()]
        if value not in valid_values:
            raise forms.ValidationError('大カテゴリを選択してください。')
        return value

    def clean_category(self):
        value = self.cleaned_data.get('category', '').strip()
        if not value:
            raise forms.ValidationError('中カテゴリを選択してください。')
        return value

    def clean(self):
        cleaned_data = super().clean()
        reference_type = (cleaned_data.get('reference_type') or '').strip()
        category = (cleaned_data.get('category') or '').strip()
        middle_category = (cleaned_data.get('middle_category') or '').strip()

        hierarchy = get_qr_category_hierarchy()
        big_item = next((item for item in hierarchy if item['value'] == reference_type), None)
        if not big_item:
            self.add_error('reference_type', '大カテゴリを選択してください。')
            return cleaned_data

        mid_item = next((item for item in big_item['children'] if item['value'] == category), None)
        if not mid_item:
            self.add_error('category', '中カテゴリを選択してください。')
            return cleaned_data

        small_values = list(mid_item.get('children', []))
        if small_values:
            if not middle_category:
                self.add_error('middle_category', '小カテゴリを選択してください。')
            elif middle_category not in small_values:
                self.add_error('middle_category', '小カテゴリの選択が不正です。')
        return cleaned_data