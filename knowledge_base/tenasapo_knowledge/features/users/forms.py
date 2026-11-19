from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email


class UserDemoIssueForm(forms.Form):
    company_name = forms.CharField(
        label='会社名', max_length=120,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        error_messages={'required': '会社名を入力してください。'},
    )


class UserContractIssueForm(forms.Form):
    display_name = forms.CharField(
        label='ユーザー名', max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        error_messages={'required': 'ユーザー名を入力してください。'},
    )
    company_name = forms.CharField(
        label='会社名', max_length=120,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        error_messages={'required': '会社名を入力してください。'},
    )
    department = forms.CharField(
        label='所属部署', max_length=255, required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    email_addresses = forms.CharField(
        # The entire email becomes auth.User.username (maximum 150 characters).
        label='メールアドレス', max_length=150,
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
        help_text='ログインに使用するメールアドレスを1件入力してください。',
        error_messages={'required': 'メールアドレスを入力してください。'},
    )
    note = forms.CharField(
        label='備考', required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
    )

    def clean_email_addresses(self):
        email = self.cleaned_data['email_addresses'].strip()
        try:
            validate_email(email)
        except ValidationError:
            raise forms.ValidationError('メールアドレスの形式が正しくありません。')
        if not email.split('@', 1)[0].strip():
            raise forms.ValidationError('メールアドレスから初期パスワードを生成できませんでした。')
        if get_user_model().objects.filter(username=email).exists():
            raise forms.ValidationError(
                'ログインID（メールアドレス）が既に使用されています。別のメールアドレスを指定してください。'
            )
        return email


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
        required=False,
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
        label='メールアドレス',
        required=False,
        max_length=255,
        help_text='ログインに使用するメールアドレスを1件入力してください。',
    )
    department = forms.CharField(label='所属部署', max_length=255, required=False)
    note = forms.CharField(
        label='備考',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )
    exclude_from_analysis = forms.BooleanField(
        label='アナライズから除外する',
        required=False,
        initial=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_user_pk = None
        role_names = getattr(settings, 'USER_ROLES', getattr(settings, 'USER_GROUPS', []))
        self.fields['groups'].choices = [(name, name) for name in role_names]
        self.fields['role'].initial = self.ROLE_ADMIN
        if not self.initial.get('groups'):
            self.initial['groups'] = ['投稿者']

    def clean_uid(self):
        value = self.cleaned_data.get('uid', '').strip()
        if not value:
            return ''
        if not value.isdigit() or len(value) != 6:
            raise forms.ValidationError('数字6桁で入力してください。')
        from ...models import UserProfile
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

    def clean(self):
        cleaned_data = super().clean()
        user_type = cleaned_data.get('user_type')
        company_name = (cleaned_data.get('company_name') or '').strip()
        if user_type == 'systena':
            cleaned_data['company_name'] = 'システナ'
        elif user_type == 'customer' and 'company_name' not in self.errors:
            if not company_name:
                self.add_error('company_name', 'この項目は必須です。')
            else:
                cleaned_data['company_name'] = company_name
        return cleaned_data

    def clean_email_addresses(self):
        value = (self.cleaned_data.get('email_addresses') or '').strip()
        if not value:
            return ''
        try:
            validate_email(value)
        except ValidationError:
            raise forms.ValidationError('メールアドレスの形式が正しくありません。')
        return value

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
        required=False,
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
        label='メールアドレス',
        required=False,
        max_length=255,
        help_text='ログインに使用するメールアドレスを1件入力してください。',
    )
    department = forms.CharField(label='所属部署', max_length=255, required=False)
    note = forms.CharField(
        label='備考',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
    )
    skip_login_lp = forms.BooleanField(
        label='次回ログイン時はLPを表示しない',
        required=False,
    )
    exclude_from_analysis = forms.BooleanField(
        label='アナライズから除外する',
        required=False,
        initial=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_user_pk = None
        role_names = getattr(settings, 'USER_ROLES', getattr(settings, 'USER_GROUPS', []))
        self.fields['groups'].choices = [(name, name) for name in role_names]

    def clean_uid(self):
        value = self.cleaned_data.get('uid', '').strip()
        if not value:
            return ''
        if not value.isdigit() or len(value) != 6:
            raise forms.ValidationError('数字6桁で入力してください。')
        from ...models import UserProfile
        # 自分自身のIDは除外してユニーク確認
        qs = UserProfile.objects.filter(uid=value)
        if self._current_user_pk:
            qs = qs.exclude(user__pk=self._current_user_pk)
        if qs.exists():
            raise forms.ValidationError('この管理番号は既に使用されています。')
        return value

    def clean_email_addresses(self):
        value = (self.cleaned_data.get('email_addresses') or '').strip()
        if not value:
            return ''
        try:
            validate_email(value)
        except ValidationError:
            raise forms.ValidationError('メールアドレスの形式が正しくありません。')
        return value

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