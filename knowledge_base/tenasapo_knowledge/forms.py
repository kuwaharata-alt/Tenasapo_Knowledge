import json

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from .models import (
    ConvenienceCategory,
    ConvenienceFeature,
    FAQCategory,
    Manual,
    RevisionHistory,
    default_expires_on,
)


TARGET_OS_VERSION_MAP = {
    'Windows PC': ['11', '10', '8.1', '8', '7'],
    'Windows Server': ['2025', '2022', '2019', '2016', '2012 R2', '2012'],
    'VMware': ['8.0', '7.0', '6.7'],
    'macOS': ['15 Sequoia', '14 Sonoma', '13 Ventura', '12 Monterey'],
    'Ubuntu': ['24.04', '22.04', '20.04', '18.04', '16.04'],
    'iOS': ['18', '17', '16'],
    'Android': ['15', '14', '13', '12', '11', '10'],
    'その他': ['指定なし'],
}
TARGET_OS_NAME_CHOICES = [('', '選択してください')] + [
    (name, name) for name in TARGET_OS_VERSION_MAP.keys()
]
TARGET_OS_CONDITION_CHOICES = (
    ('', '指定なし'),
    ('以降', '以降'),
    ('以前', '以前'),
)


def target_os_version_choices():
    seen = set()
    choices = [('', '選択してください')]
    for versions in TARGET_OS_VERSION_MAP.values():
        for version in versions:
            if version in seen:
                continue
            seen.add(version)
            choices.append((version, version))
    return choices


def build_target_os_value(name, version, condition):
    name = (name or '').strip()
    version = (version or '').strip()
    condition = (condition or '').strip()
    if name == 'その他' and version == '指定なし':
        version = ''
    if not name and not version:
        return ''
    parts = [part for part in [name, version, condition] if part]
    return ' '.join(parts)


def build_target_os_values(entries):
    values = []
    seen = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        value = build_target_os_value(
            entry.get('name'),
            entry.get('version'),
            entry.get('condition'),
        )
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return ', '.join(values)


def parse_target_os_value(value):
    raw = (value or '').strip()
    if not raw:
        return {'name': '', 'version': '', 'condition': ''}

    condition = ''
    for suffix in ('以降', '以前'):
        if raw.endswith(' ' + suffix):
            raw = raw[:-(len(suffix) + 1)].strip()
            condition = suffix
            break
        if raw.endswith(suffix):
            raw = raw[:-len(suffix)].strip()
            condition = suffix
            break

    for name, versions in TARGET_OS_VERSION_MAP.items():
        if raw == name:
            return {'name': name, 'version': '', 'condition': condition}
        if raw.startswith(name + ' '):
            version = raw[len(name):].strip()
            if version in versions:
                return {'name': name, 'version': version, 'condition': condition}
            return {'name': name, 'version': version, 'condition': condition}

    return {'name': '', 'version': raw, 'condition': condition}


def parse_target_os_values(value):
    values = []
    for item in (value or '').split(','):
        raw = item.strip()
        if not raw:
            continue
        values.append(parse_target_os_value(raw))
    return values


def parse_target_os_entries_json(raw_value):
    try:
        payload = json.loads(raw_value or '[]')
    except (TypeError, ValueError, json.JSONDecodeError):
        return []

    if not isinstance(payload, list):
        return []

    entries = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        entries.append(
            {
                'name': (item.get('name') or '').strip(),
                'version': (item.get('version') or '').strip(),
                'condition': (item.get('condition') or '').strip(),
            }
        )
    return entries


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data]
        cleaned_files = []
        for uploaded_file in files:
            if not uploaded_file:
                continue
            cleaned_file = super().clean(uploaded_file, initial)
            content_type = getattr(cleaned_file, 'content_type', '')
            if content_type and not content_type.startswith('image/'):
                raise forms.ValidationError('画像ファイルを選択してください。')
            cleaned_files.append(cleaned_file)
        return cleaned_files


class FAQCategoryCreateForm(forms.ModelForm):
    PARENT_CATEGORY_CHOICES = (
        ('サーバー', 'サーバー'),
        ('PC', 'PC'),
        ('Azure', 'Azure'),
        ('クラウドインフラ', 'クラウドインフラ'),
        ('Microsoft 365', 'Microsoft 365'),
        ('グループウェア', 'グループウェア'),
        ('ソフトウェア', 'ソフトウェア'),
        ('ハードウェア', 'ハードウェア'),
        ('周辺機器', '周辺機器'),
        ('運用・保守', '運用・保守'),
    )

    parent_name = forms.ChoiceField(
        label='大カテゴリ',
        choices=(),
        required=False,
    )
    new_parent_name = forms.CharField(
        label='大カテゴリ（新規）',
        required=False,
        max_length=120,
    )
    middle_name = forms.CharField(
        label='中カテゴリ',
        required=False,
        max_length=120,
    )
    class Meta:
        model = FAQCategory
        fields = ('parent_name', 'middle_name', 'child_name')
        labels = {
            'parent_name': '大カテゴリ',
            'middle_name': '中カテゴリ',
            'child_name': '小カテゴリ',
        }

    def __init__(self, *args, **kwargs):
        self.allow_new_parent_name = kwargs.pop('allow_new_parent_name', False)
        super().__init__(*args, **kwargs)
        existing_parent_names = list(
            dict.fromkeys(
                [name for name, _ in self.PARENT_CATEGORY_CHOICES]
                + list(FAQCategory.objects.values_list('parent_name', flat=True).distinct())
            )
        )
        candidates = [name for name in existing_parent_names if name]
        self.parent_name_candidates = candidates
        self.fields['parent_name'].choices = [('', '選択してください')] + [(name, name) for name in candidates]

        if self.instance and getattr(self.instance, 'pk', None):
            self.initial.setdefault('parent_name', self.instance.parent_name)

        if self.allow_new_parent_name:
            self.fields['new_parent_name'].help_text = '既存候補にない場合のみ入力してください。'
        else:
            self.fields['new_parent_name'].widget = forms.HiddenInput()

    def clean_parent_name(self):
        return self.cleaned_data.get('parent_name', '').strip()

    def clean_new_parent_name(self):
        return self.cleaned_data.get('new_parent_name', '').strip()

    def clean_middle_name(self):
        return self.cleaned_data.get('middle_name', '').strip()

    def clean_child_name(self):
        return self.cleaned_data['child_name'].strip()

    def clean(self):
        cleaned_data = super().clean()
        selected_parent = (cleaned_data.get('parent_name') or '').strip()
        new_parent = (cleaned_data.get('new_parent_name') or '').strip()

        if new_parent and not self.allow_new_parent_name:
            self.add_error('new_parent_name', '大カテゴリの新規作成権限がありません。')

        final_parent = new_parent if (self.allow_new_parent_name and new_parent) else selected_parent
        if not final_parent:
            self.add_error('parent_name', '大カテゴリを選択してください。')

        cleaned_data['parent_name'] = final_parent
        return cleaned_data


DEFAULT_QR_CATEGORY_HIERARCHY = [
    {
        'value': 'shortcut',
        'label': 'ショートカット',
        'children': [
            {
                'value': 'Winodws',
                'label': 'Winodws',
                'children': ['デスクトップ', '文書', 'ウィンドウ', 'ファイル操作'],
            },
            {
                'value': 'Office',
                'label': 'Office',
                'children': ['Word', 'Excel', 'PowerPoint', 'Outlook'],
            },
            {
                'value': 'Googleカレンダー',
                'label': 'Googleカレンダー',
                'children': ['基本操作', '予定管理'],
            },
            {
                'value': 'コントロールパネル',
                'label': 'コントロールパネル',
                'children': ['基本操作', 'プログラム', 'システム'],
            },
            {
                'value': 'Windowsの設定',
                'label': 'Windowsの設定',
                'children': ['基本操作', 'ネットワーク', 'アカウント'],
            },
        ],
    },
    {
        'value': 'command',
        'label': 'コマンド',
        'children': [
            {
                'value': 'Winodws',
                'label': 'Winodws',
                'children': ['ファイル操作', 'ネットワーク', 'システム'],
            },
            {
                'value': 'Office',
                'label': 'Office',
                'children': ['Word', 'Excel', 'PowerPoint', 'Outlook'],
            },
        ],
    },
]


def get_qr_category_hierarchy():
    categories = ConvenienceCategory.objects.order_by('reference_type', 'category', 'middle_category', 'id')
    if not categories.exists():
        return DEFAULT_QR_CATEGORY_HIERARCHY

    type_label_map = dict(ConvenienceFeature.TYPE_CHOICES)
    big_map = {}
    for item in categories:
        big_node = big_map.setdefault(
            item.reference_type,
            {
                'value': item.reference_type,
                'label': type_label_map.get(item.reference_type, item.reference_type),
                'children': {},
            },
        )
        mid_node = big_node['children'].setdefault(
            item.category,
            {
                'value': item.category,
                'label': item.category,
                'children': [],
            },
        )
        if item.middle_category and item.middle_category not in mid_node['children']:
            mid_node['children'].append(item.middle_category)

    hierarchy = []
    for big in big_map.values():
        hierarchy.append(
            {
                'value': big['value'],
                'label': big['label'],
                'children': list(big['children'].values()),
            }
        )
    return hierarchy


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
        label='得られる結果',
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


class RevisionHistoryForm(forms.ModelForm):
    class Meta:
        model = RevisionHistory
        fields = ('category', 'title', 'update_content')
        labels = {
            'category': 'カテゴリ',
            'title': 'タイトル',
            'update_content': '更新内容',
        }
        widgets = {
            'update_content': forms.Textarea(attrs={'rows': 4}),
        }


class KnowledgeArticleCreateForm(forms.Form):
    registered_category = forms.ModelMultipleChoiceField(
        label='カテゴリ',
        queryset=FAQCategory.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='カテゴリを選択してください。',
    )
    title = forms.CharField(
        label='タイトル',
        max_length=200,
    )
    target_os_name = forms.ChoiceField(
        label='OS',
        choices=TARGET_OS_NAME_CHOICES,
        required=True,
    )
    target_os_version = forms.ChoiceField(
        label='バージョン',
        choices=(),
        required=False,
    )
    target_os_condition = forms.ChoiceField(
        label='条件',
        choices=TARGET_OS_CONDITION_CHOICES,
        required=False,
    )
    target_os_entries = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )
    question = forms.CharField(
        label='質問',
        required=True,
        widget=forms.Textarea(attrs={'rows': 8}),
    )
    answer = forms.CharField(
        label='回答',
        required=True,
        widget=forms.Textarea(attrs={'rows': 8}),
    )
    question_images = MultipleImageField(
        label='質問画像',
        required=False,
        widget=MultipleFileInput(attrs={'multiple': True, 'accept': 'image/*'}),
        help_text='質問に差し込む画像を選択し、「本文へ挿入」ボタンで挿入位置を指定してください。',
    )
    answer_images = MultipleImageField(
        label='回答画像',
        required=False,
        widget=MultipleFileInput(attrs={'multiple': True, 'accept': 'image/*'}),
        help_text='回答に差し込む画像を選択し、「本文へ挿入」ボタンで挿入位置を指定してください。',
    )
    visible_to_customer = forms.BooleanField(
        label='カスタマーユーザーに表示する',
        required=False,
        initial=True,
    )
    visible_to_systena = forms.BooleanField(
        label='システナユーザーに表示する',
        required=False,
        initial=True,
    )
    source_published_at = forms.DateField(
        label='ソース公開日',
        required=True,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    expires_on = forms.DateField(
        label='掲載期限',
        required=False,
        initial=default_expires_on,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['registered_category'].queryset = FAQCategory.objects.order_by(
            'parent_name',
            'middle_name',
            'child_name',
        )
        self.fields['target_os_version'].choices = target_os_version_choices()

    def clean_expires_on(self):
        return self.cleaned_data.get('expires_on') or default_expires_on()

    def clean(self):
        cleaned_data = super().clean()
        registered_categories = cleaned_data.get('registered_category')
        categories = []
        if registered_categories:
            categories.extend(cat.full_name for cat in registered_categories)
        if categories:
            cleaned_data['category'] = ','.join(dict.fromkeys(categories))
        else:
            self.add_error('registered_category', 'カテゴリを選択してください。')

        target_os_entries = parse_target_os_entries_json(cleaned_data.get('target_os_entries'))
        if target_os_entries:
            cleaned_data['target_os'] = build_target_os_values(target_os_entries)
        else:
            cleaned_data['target_os'] = build_target_os_value(
                cleaned_data.get('target_os_name'),
                cleaned_data.get('target_os_version'),
                cleaned_data.get('target_os_condition'),
            )

        legacy_target_os = (self.data.get(self.add_prefix('target_os')) or '').strip()
        if not cleaned_data['target_os'] and legacy_target_os:
            cleaned_data['target_os'] = build_target_os_values(parse_target_os_values(legacy_target_os))
        return cleaned_data


class TipsCreateForm(forms.Form):
    registered_category = forms.ModelMultipleChoiceField(
        label='カテゴリ',
        queryset=FAQCategory.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='カテゴリを選択してください。',
    )
    title = forms.CharField(
        label='タイトル',
        max_length=200,
    )
    target_os_name = forms.ChoiceField(
        label='OS',
        choices=TARGET_OS_NAME_CHOICES,
        required=True,
    )
    target_os_version = forms.ChoiceField(
        label='バージョン',
        choices=(),
        required=False,
    )
    target_os_condition = forms.ChoiceField(
        label='条件',
        choices=TARGET_OS_CONDITION_CHOICES,
        required=False,
    )
    target_os_entries = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )
    body = forms.CharField(
        label='内容',
        required=True,
        widget=forms.Textarea(attrs={'rows': 10}),
    )
    tips_images = MultipleImageField(
        label='本文画像',
        required=False,
        widget=MultipleFileInput(attrs={'multiple': True, 'accept': 'image/*'}),
        help_text='本文に差し込む画像を選択し、「本文へ挿入」ボタンで挿入位置を指定してください。',
    )
    visible_to_customer = forms.BooleanField(
        label='カスタマーユーザーに表示する',
        required=False,
        initial=True,
    )
    visible_to_systena = forms.BooleanField(
        label='システナユーザーに表示する',
        required=False,
        initial=True,
    )
    source_published_at = forms.DateField(
        label='ソース公開日',
        required=True,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    pdf_file = forms.FileField(
        label='マニュアル',
        required=False,
        help_text='PDFファイルをアップロードすると一覧画面からポップアップでマニュアルを閲覧できます。',
    )
    clear_pdf = forms.BooleanField(
        label='マニュア ルを削除する',
        required=False,
    )
    expires_on = forms.DateField(
        label='掲載期限',
        required=False,
        initial=default_expires_on,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['registered_category'].queryset = FAQCategory.objects.order_by(
            'parent_name',
            'middle_name',
            'child_name',
        )
        self.fields['target_os_version'].choices = target_os_version_choices()

    def clean_expires_on(self):
        return self.cleaned_data.get('expires_on') or default_expires_on()

    def clean(self):
        cleaned_data = super().clean()
        registered_categories = cleaned_data.get('registered_category')
        categories = []
        if registered_categories:
            categories.extend(cat.full_name for cat in registered_categories)
        if categories:
            cleaned_data['category'] = ','.join(dict.fromkeys(categories))
        else:
            self.add_error('registered_category', 'カテゴリを選択してください。')

        target_os_entries = parse_target_os_entries_json(cleaned_data.get('target_os_entries'))
        if target_os_entries:
            cleaned_data['target_os'] = build_target_os_values(target_os_entries)
        else:
            cleaned_data['target_os'] = build_target_os_value(
                cleaned_data.get('target_os_name'),
                cleaned_data.get('target_os_version'),
                cleaned_data.get('target_os_condition'),
            )

        legacy_target_os = (self.data.get(self.add_prefix('target_os')) or '').strip()
        if not cleaned_data['target_os'] and legacy_target_os:
            cleaned_data['target_os'] = build_target_os_values(parse_target_os_values(legacy_target_os))
        return cleaned_data


class UserCreateForm(forms.Form):
    ROLE_ADMIN = 'admin'
    ROLE_USER = 'user'
    ROLE_CHOICES = (
        (ROLE_USER, 'ユーザー'),
        (ROLE_ADMIN, '管理者'),
    )

    uid = forms.CharField(
        label='管理番号',
        max_length=6,
        required=True,
        help_text='数字6桁で入力してください。',
    )
    username = forms.CharField(label='ログインID', max_length=150)
    display_name = forms.CharField(label='ユーザー名', max_length=150, required=False)
    password = forms.CharField(label='パスワード', widget=forms.PasswordInput)
    company_name = forms.CharField(label='会社名', max_length=120, required=False)
    role = forms.ChoiceField(label='権限', choices=ROLE_CHOICES)
    user_type = forms.ChoiceField(
        label='ユーザー区分',
        required=True,
        choices=[
            ('systena', 'システナ'),
            ('customer', 'カスタマー'),
        ],
    )
    groups = forms.MultipleChoiceField(
        label='所属役割',
        required=False,
        choices=(),
        widget=forms.CheckboxSelectMultiple,
    )
    email_addresses = forms.CharField(
        label='メールアドレス（複数）',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        help_text='改行、カンマ、セミコロンで複数入力できます。',
    )
    note = forms.CharField(
        label='備考',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_user_pk = None
        role_names = getattr(settings, 'USER_ROLES', getattr(settings, 'USER_GROUPS', []))
        self.fields['groups'].choices = [(name, name) for name in role_names]

    def clean_uid(self):
        value = self.cleaned_data.get('uid', '').strip()
        if not value:
            raise forms.ValidationError('この項目は必須です。')
        if not value.isdigit() or len(value) != 6:
            raise forms.ValidationError('数字6桁で入力してください。')
        from .models import UserProfile
        if UserProfile.objects.filter(uid=value).exists():
            raise forms.ValidationError('この管理番号は既に使用されています。')
        return value

    def clean_username(self):
        username = self.cleaned_data['username']
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('このログインIDはすでに使われています。')
        return username

    def clean_display_name(self):
        display_name = (self.cleaned_data.get('display_name') or '').strip()
        if display_name:
            return display_name
        return self.cleaned_data.get('username', '').strip()

    def clean_company_name(self):
        user_type = self.cleaned_data.get('user_type')
        company_name = (self.cleaned_data.get('company_name') or '').strip()
        if user_type == 'systena':
            return 'システナ'
        if not company_name:
            raise forms.ValidationError('この項目は必須です。')
        return company_name

    def clean_email_addresses(self):
        value = self.cleaned_data.get('email_addresses', '')
        emails = self.normalized_emails(value)
        errors = []
        for email in emails:
            try:
                validate_email(email)
            except ValidationError:
                errors.append(email)
        if errors:
            raise forms.ValidationError(
                'メールアドレスの形式が正しくありません: ' + ', '.join(errors)
            )
        return '\n'.join(emails)

    @staticmethod
    def normalized_emails(value):
        separators = [',', ';', '、']
        normalized = value or ''
        for separator in separators:
            normalized = normalized.replace(separator, '\n')
        return [email.strip() for email in normalized.splitlines() if email.strip()]


class UserUpdateForm(forms.Form):
    ROLE_ADMIN = 'admin'
    ROLE_USER = 'user'
    ROLE_CHOICES = (
        (ROLE_USER, 'ユーザー'),
        (ROLE_ADMIN, '管理者'),
    )

    uid = forms.CharField(
        label='管理番号',
        max_length=6,
        required=True,
        help_text='数字6桁で入力してください。',
    )
    username = forms.CharField(label='ログインID', max_length=150, disabled=True)
    display_name = forms.CharField(label='ユーザー名', max_length=150, required=False)
    password = forms.CharField(label='パスワード', widget=forms.PasswordInput, required=False)
    company_name = forms.CharField(label='会社名', max_length=120, required=False)
    role = forms.ChoiceField(label='権限', choices=ROLE_CHOICES)
    user_type = forms.ChoiceField(
        label='ユーザー区分',
        required=True,
        choices=[
            ('systena', 'システナ'),
            ('customer', 'カスタマー'),
        ],
    )
    groups = forms.MultipleChoiceField(
        label='所属役割',
        required=False,
        choices=(),
        widget=forms.CheckboxSelectMultiple,
    )
    email_addresses = forms.CharField(
        label='メールアドレス（複数）',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        help_text='改行、カンマ、セミコロンで複数入力できます。',
    )
    note = forms.CharField(
        label='備考',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_user_pk = None
        role_names = getattr(settings, 'USER_ROLES', getattr(settings, 'USER_GROUPS', []))
        self.fields['groups'].choices = [(name, name) for name in role_names]

    def clean_uid(self):
        value = self.cleaned_data.get('uid', '').strip()
        if not value:
            raise forms.ValidationError('この項目は必須です。')
        if not value.isdigit() or len(value) != 6:
            raise forms.ValidationError('数字6桁で入力してください。')
        from .models import UserProfile
        # 自分自身のIDは除外してユニーク確認
        qs = UserProfile.objects.filter(uid=value)
        if self._current_user_pk:
            qs = qs.exclude(user__pk=self._current_user_pk)
        if qs.exists():
            raise forms.ValidationError('この管理番号は既に使用されています。')
        return value

    def clean_email_addresses(self):
        value = self.cleaned_data.get('email_addresses', '')
        emails = UserCreateForm.normalized_emails(value)
        errors = []
        for email in emails:
            try:
                validate_email(email)
            except ValidationError:
                errors.append(email)
        if errors:
            raise forms.ValidationError(
                'メールアドレスの形式が正しくありません: ' + ', '.join(errors)
            )
        return '\n'.join(emails)

    def clean_display_name(self):
        display_name = (self.cleaned_data.get('display_name') or '').strip()
        if display_name:
            return display_name
        return (self.cleaned_data.get('username') or '').strip()

    def clean_company_name(self):
        user_type = self.cleaned_data.get('user_type')
        company_name = (self.cleaned_data.get('company_name') or '').strip()
        if user_type == 'systena':
            return 'システナ'
        if not company_name:
            raise forms.ValidationError('この項目は必須です。')
        return company_name


class ManualForm(forms.ModelForm):
    class Meta:
        model = Manual
        fields = ('title', 'description', 'pdf_file', 'order', 'is_published')
        labels = {
            'title': 'タイトル',
            'description': '説明',
            'pdf_file': 'PDFファイル',
            'order': '表示順',
            'is_published': '公開',
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def clean_pdf_file(self):
        f = self.cleaned_data.get('pdf_file')
        if f:
            name = getattr(f, 'name', '')
            if not name.lower().endswith('.pdf'):
                raise forms.ValidationError('PDFファイルを選択してください。')
        return f
