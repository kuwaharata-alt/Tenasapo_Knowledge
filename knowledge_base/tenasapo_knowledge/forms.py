import json

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from .models import ConvenienceFeature, FAQCategory, FAQParentCategorySetting, Manual, default_expires_on


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
        choices=PARENT_CATEGORY_CHOICES,
    )
    middle_name = forms.CharField(
        label='中カテゴリ',
        required=False,
        max_length=120,
    )
    visible_to_customer = forms.BooleanField(
        label='大カテゴリをカスタマーユーザーに表示する',
        required=False,
        initial=True,
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
        super().__init__(*args, **kwargs)
        choice_map = dict(self.PARENT_CATEGORY_CHOICES)
        parent_choices = list(self.PARENT_CATEGORY_CHOICES)
        for parent_name in FAQCategory.objects.values_list('parent_name', flat=True).distinct():
            if parent_name and parent_name not in choice_map:
                parent_choices.append((parent_name, parent_name))
        self.fields['parent_name'].choices = parent_choices

        current_parent_name = ''
        if self.is_bound:
            current_parent_name = (self.data.get(self.add_prefix('parent_name')) or '').strip()
        elif self.instance and getattr(self.instance, 'pk', None):
            current_parent_name = self.instance.parent_name
        else:
            current_parent_name = (self.initial.get('parent_name') or '').strip()

        if current_parent_name:
            setting = FAQParentCategorySetting.objects.filter(name=current_parent_name).first()
            self.fields['visible_to_customer'].initial = (
                setting.visible_to_customer if setting else True
            )

    def clean_middle_name(self):
        return self.cleaned_data.get('middle_name', '').strip()

    def clean_child_name(self):
        return self.cleaned_data['child_name'].strip()

    def save(self, commit=True):
        category = super().save(commit=commit)
        if commit:
            self.save_parent_setting()
        return category

    def save_parent_setting(self):
        parent_name = self.cleaned_data.get('parent_name', '').strip()
        if not parent_name:
            return None
        setting, _ = FAQParentCategorySetting.objects.update_or_create(
            name=parent_name,
            defaults={'visible_to_customer': self.cleaned_data.get('visible_to_customer', True)},
        )
        return setting


class ConvenienceFeatureCreateForm(forms.ModelForm):
    CATEGORY_CHOICES = (
        ('Winodws', 'Winodws'),
        ('Office', 'Office'),
        ('Googleカレンダー', 'Googleカレンダー'),
        ('コントロールパネル', 'コントロールパネル'),
        ('Windowsの設定', 'Windowsの設定'),
    )
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

    reference_type = forms.ChoiceField(
        label='タブ種別',
        choices=ConvenienceFeature.TYPE_CHOICES,
        initial=ConvenienceFeature.TYPE_SHORTCUT,
    )

    category = forms.ChoiceField(
        label='大カテゴリ',
        choices=CATEGORY_CHOICES,
    )
    middle_category = forms.CharField(
        label='中カテゴリ',
        required=True,
        max_length=120,
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
            'reference_type': 'タブ種別',
            'category': '大カテゴリ',
            'middle_category': '中カテゴリ',
            'usage_frequency': '使用頻度',
            'shortcut_key': 'ショートカットキー / コマンド',
            'display_text': '内容',
            'note': '備考',
            'image': '画像',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class KnowledgeArticleCreateForm(forms.Form):
    registered_category = forms.ModelMultipleChoiceField(
        label='登録済みカテゴリ',
        queryset=FAQCategory.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='カテゴリ登録済みの場合はこちらから選択してください。',
    )
    category = forms.CharField(
        label='カテゴリ',
        max_length=180,
        required=False,
        help_text='未登録カテゴリを使う場合は「大カテゴリ/中カテゴリ/小カテゴリ」で入力してください。',
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

    def clean_category(self):
        return self.cleaned_data.get('category', '').strip()

    def clean_expires_on(self):
        return self.cleaned_data.get('expires_on') or default_expires_on()

    def clean(self):
        cleaned_data = super().clean()
        registered_categories = cleaned_data.get('registered_category')
        category = cleaned_data.get('category')
        categories = []
        if registered_categories:
            categories.extend(category.full_name for category in registered_categories)
        if category:
            categories.extend(
                category_name.strip()
                for category_name in category.split(',')
                if category_name.strip()
            )
        if categories:
            cleaned_data['category'] = ','.join(dict.fromkeys(categories))
        else:
            self.add_error('category', 'カテゴリを選択するか入力してください。')

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
        label='登録済みカテゴリ',
        queryset=FAQCategory.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='カテゴリ登録済みの場合はこちらから選択してください。',
    )
    category = forms.CharField(
        label='カテゴリ',
        max_length=180,
        required=False,
        help_text='未登録カテゴリを使う場合は「大カテゴリ/中カテゴリ/小カテゴリ」で入力してください。',
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

    def clean_category(self):
        return self.cleaned_data.get('category', '').strip()

    def clean_expires_on(self):
        return self.cleaned_data.get('expires_on') or default_expires_on()

    def clean(self):
        cleaned_data = super().clean()
        registered_categories = cleaned_data.get('registered_category')
        category = cleaned_data.get('category')
        categories = []
        if registered_categories:
            categories.extend(category.full_name for category in registered_categories)
        if category:
            categories.extend(
                category_name.strip()
                for category_name in category.split(',')
                if category_name.strip()
            )
        if categories:
            cleaned_data['category'] = ','.join(dict.fromkeys(categories))
        else:
            self.add_error('category', 'カテゴリを選択するか入力してください。')

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
        label='ユーザーID',
        max_length=6,
        required=False,
        help_text='数字6桁で入力してください（省略可）。',
    )
    username = forms.CharField(label='ユーザー名', max_length=150)
    password = forms.CharField(label='パスワード', widget=forms.PasswordInput)
    company_name = forms.CharField(label='会社名', max_length=120)
    role = forms.ChoiceField(label='権限', choices=ROLE_CHOICES)
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
            return None
        if not value.isdigit() or len(value) != 6:
            raise forms.ValidationError('ユーザーIDは数字6桁で入力してください。')
        from .models import UserProfile
        if UserProfile.objects.filter(uid=value).exists():
            raise forms.ValidationError('このユーザーIDはすでに使われています。')
        return value

    def clean_username(self):
        username = self.cleaned_data['username']
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('このユーザー名はすでに使われています。')
        return username

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
        label='ユーザーID',
        max_length=6,
        required=False,
        help_text='数字6桁で入力してください（省略可）。',
    )
    username = forms.CharField(label='ユーザー名', max_length=150, disabled=True)
    password = forms.CharField(label='パスワード', widget=forms.PasswordInput, required=False)
    company_name = forms.CharField(label='会社名', max_length=120)
    role = forms.ChoiceField(label='権限', choices=ROLE_CHOICES)
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
            return None
        if not value.isdigit() or len(value) != 6:
            raise forms.ValidationError('ユーザーIDは数字6桁で入力してください。')
        from .models import UserProfile
        # 自分自身のIDは除外してユニーク確認
        qs = UserProfile.objects.filter(uid=value)
        if self._current_user_pk:
            qs = qs.exclude(user__pk=self._current_user_pk)
        if qs.exists():
            raise forms.ValidationError('このユーザーIDはすでに使われています。')
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
