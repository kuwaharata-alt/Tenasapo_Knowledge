from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from .models import FAQCategory, Manual


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
        ('PC', 'PC'),
        ('サーバー', 'サーバー'),
        ('ネットワーク', 'ネットワーク'),
        ('アプリ', 'アプリ'),
        ('その他', 'その他'),
    )

    parent_name = forms.ChoiceField(
        label='大カテゴリ',
        choices=PARENT_CATEGORY_CHOICES,
    )

    class Meta:
        model = FAQCategory
        fields = ('parent_name', 'child_name')
        labels = {
            'parent_name': '大カテゴリ',
            'child_name': '小カテゴリ',
        }

    def clean_child_name(self):
        return self.cleaned_data['child_name'].strip()


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
        max_length=120,
        required=False,
        help_text='未登録カテゴリを使う場合は「大カテゴリ/小カテゴリ」で入力してください。',
    )
    question = forms.CharField(
        label='質問',
        max_length=200,
        widget=forms.Textarea(attrs={'rows': 8}),
        help_text='本文内で <image1> のように書くと指定番号の画像を挿入できます（<image> は順番に挿入）。',
    )
    answer = forms.CharField(
        label='回答',
        widget=forms.Textarea(attrs={'rows': 8}),
        help_text='本文内で <image1> のように書くと指定番号の画像を挿入できます（<image> は順番に挿入）。',
    )
    question_images = MultipleImageField(
        label='質問画像',
        required=False,
        widget=MultipleFileInput(attrs={'multiple': True, 'accept': 'image/*'}),
        help_text='アップロード順で1枚目が <image1>、2枚目が <image2> です。',
    )
    answer_images = MultipleImageField(
        label='回答画像',
        required=False,
        widget=MultipleFileInput(attrs={'multiple': True, 'accept': 'image/*'}),
        help_text='アップロード順で1枚目が <image1>、2枚目が <image2> です。',
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
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['registered_category'].queryset = FAQCategory.objects.order_by(
            'parent_name',
            'child_name',
        )

    def clean_category(self):
        return self.cleaned_data.get('category', '').strip()

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
        return cleaned_data


class UserCreateForm(forms.Form):
    ROLE_ADMIN = 'admin'
    ROLE_USER = 'user'
    ROLE_CHOICES = (
        (ROLE_USER, 'ユーザー'),
        (ROLE_ADMIN, '管理者'),
    )

    username = forms.CharField(label='ユーザー名', max_length=150)
    password = forms.CharField(label='パスワード', widget=forms.PasswordInput)
    company_name = forms.CharField(label='会社名', max_length=120)
    role = forms.ChoiceField(label='権限', choices=ROLE_CHOICES)
    groups = forms.MultipleChoiceField(
        label='所属グループ',
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
        group_names = getattr(settings, 'USER_GROUPS', [])
        self.fields['groups'].choices = [(name, name) for name in group_names]

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

    username = forms.CharField(label='ユーザー名', max_length=150, disabled=True)
    password = forms.CharField(label='パスワード', widget=forms.PasswordInput, required=False)
    company_name = forms.CharField(label='会社名', max_length=120)
    role = forms.ChoiceField(label='権限', choices=ROLE_CHOICES)
    groups = forms.MultipleChoiceField(
        label='所属グループ',
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
        group_names = getattr(settings, 'USER_GROUPS', [])
        self.fields['groups'].choices = [(name, name) for name in group_names]

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
