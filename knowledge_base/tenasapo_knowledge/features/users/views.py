"""Contract and demo account issuance, including collision handling."""

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import FormView

from ...models import Customer, UserProfile
from ...utils import resolve_user_display_name
from ..shared.access import ADMIN_GROUP_NAME, DEMO_GROUP_NAME, StaffRequiredMixin, resolve_next_path
from .forms import UserCreateForm, UserContractIssueForm, UserDemoIssueForm


class UserCreateView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/user_form.html'
    form_class = UserCreateForm
    success_url = reverse_lazy('user_list')

    def form_valid(self, form):
        User = get_user_model()
        email = (form.cleaned_data.get('email_addresses') or '').strip()
        selected_group_names = list(form.cleaned_data['groups'])
        user_type = form.cleaned_data['user_type']
        display_name = form.cleaned_data['display_name'] or form.cleaned_data['username']
        company_name = 'システナ' if user_type == 'systena' else form.cleaned_data['company_name']
        is_admin = (
            form.cleaned_data['role'] == UserCreateForm.ROLE_ADMIN
            or ADMIN_GROUP_NAME in selected_group_names
        )

        user = User.objects.create_user(
            username=form.cleaned_data['username'],
            password=form.cleaned_data['password'],
            email=email,
            is_staff=is_admin,
            is_superuser=is_admin,
        )

        group_objects = [
            Group.objects.get_or_create(name=group_name)[0]
            for group_name in selected_group_names
        ]
        user.groups.set(group_objects)

        UserProfile.objects.create(
            user=user,
            uid=form.cleaned_data.get('uid') or None,
            display_name=display_name,
            company_name=company_name,
            department=(form.cleaned_data.get('department') or '').strip(),
            user_type=user_type,
            email_addresses=email,
            note=form.cleaned_data['note'],
            exclude_from_analysis=form.cleaned_data.get('exclude_from_analysis', False),
        )

        customer, _ = Customer.objects.get_or_create(name=company_name)
        customer.users.add(user)

        messages.success(
            self.request,
            f'{resolve_user_display_name(user)}（{user.username}）を作成しました。',
            extra_tags='user',
        )
        return super().form_valid(form)


class UserIssueFormView(StaffRequiredMixin, FormView):
    template_name = 'tenasapo_knowledge/user_issue_form.html'
    success_url = reverse_lazy('user_list')
    is_demo = False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_demo'] = self.is_demo
        context['return_to'] = resolve_next_path(self.request, 'user_list')
        form = context['form']
        context['account_fields'] = [form[name] for name in (
            'display_name', 'email_addresses', 'company_name', 'department',
        ) if name in form.fields]
        # Read-only preview: issuance still resolves the number again, without reservation.
        number = (self._resolve_next_demo_number() if self.is_demo
                  else self._resolve_next_contract_number())
        uid = f'{number:06d}' if number is not None else ''
        email = '' if self.is_demo else str(form['email_addresses'].value() or '').strip()
        login = f'{self.demo_prefix}{uid}' if self.is_demo and uid else email
        context['issue_preview'] = {
            'uid': uid,
            'at_capacity': number is None,
            'login': login,
            'password': login if self.is_demo else (
                email.split('@', 1)[0].strip() if '@' in email else ''
            ),
        }
        return context

    def issuance_error(self, form, message):
        form.add_error(None, message)
        return self.form_invalid(form)


class UserDemoIssueView(UserIssueFormView):
    success_url = reverse_lazy('user_list')
    is_demo = True
    demo_prefix = 'CSDemo'
    demo_number_start = 900001
    demo_number_end = 999999

    def get_form_class(self):
        return UserDemoIssueForm

    @classmethod
    def _iter_used_demo_numbers(cls):
        User = get_user_model()

        usernames = User.objects.filter(username__startswith=f'{cls.demo_prefix}90').values_list('username', flat=True)
        for username in usernames:
            suffix = (username or '')[len(cls.demo_prefix):]
            if len(suffix) == 6 and suffix.isdigit() and suffix.startswith('90'):
                number = int(suffix)
                if cls.demo_number_start <= number <= cls.demo_number_end:
                    yield number

        uids = UserProfile.objects.filter(uid__startswith='90').values_list('uid', flat=True)
        for uid in uids:
            normalized_uid = (uid or '').strip()
            if len(normalized_uid) == 6 and normalized_uid.isdigit() and normalized_uid.startswith('90'):
                number = int(normalized_uid)
                if cls.demo_number_start <= number <= cls.demo_number_end:
                    yield number

    @classmethod
    def _resolve_next_demo_number(cls):
        used_numbers = set(cls._iter_used_demo_numbers())
        for number in range(cls.demo_number_start, cls.demo_number_end + 1):
            if number not in used_numbers:
                return number
        return None

    def form_valid(self, form):
        request = self.request
        next_path = resolve_next_path(request, 'user_list')
        company_name = form.cleaned_data['company_name']

        demo_group, _ = Group.objects.get_or_create(name=DEMO_GROUP_NAME)

        for _ in range(10):
            number = self._resolve_next_demo_number()
            if number is None:
                return self.issuance_error(form, 'デモアカウント番号の発行上限（999999）に達しました。')

            uid = f'{number:06d}'
            username = f'{self.demo_prefix}{uid}'
            temporary_password = username
            demo_account_expires_at = timezone.now() + timedelta(days=30)

            try:
                with transaction.atomic():
                    User = get_user_model()
                    user = User.objects.create_user(
                        username=username,
                        password=temporary_password,
                        email='',
                        is_staff=False,
                        is_superuser=False,
                    )
                    user.groups.set([demo_group])

                    UserProfile.objects.create(
                        user=user,
                        uid=uid,
                        display_name='Demoアカウント',
                        company_name=company_name,
                        user_type=UserProfile.USER_TYPE_CUSTOMER,
                        email_addresses='',
                        note='',
                        exclude_from_analysis=True,
                        demo_account_expires_at=demo_account_expires_at,
                    )

                    customer, _ = Customer.objects.get_or_create(name=company_name)
                    customer.users.add(user)
            except IntegrityError:
                continue

            messages.success(
                request,
                (
                    f'デモアカウントを発行しました。'
                    f'管理番号: {uid} / ログインID: {username} / '
                    'ユーザー名: Demoアカウント / 初期パスワード: ログインIDと同じ'
                ),
                extra_tags='user',
            )
            request.session['issued_demo_account'] = {
                'login_id': username,
                'password': temporary_password,
            }
            return redirect(next_path)

        return self.issuance_error(form, 'デモアカウント発行時に競合が発生しました。再度お試しください。')


class UserContractIssueView(UserIssueFormView):
    success_url = reverse_lazy('user_list')
    contract_prefix = 'CSUser'
    contract_number_start = 800001
    contract_number_end = 899999

    def get_form_class(self):
        return UserContractIssueForm

    @classmethod
    def _iter_used_contract_numbers(cls):
        User = get_user_model()

        usernames = User.objects.filter(username__startswith=f'{cls.contract_prefix}80').values_list('username', flat=True)
        for username in usernames:
            suffix = (username or '')[len(cls.contract_prefix):]
            if len(suffix) == 6 and suffix.isdigit() and suffix.startswith('80'):
                number = int(suffix)
                if cls.contract_number_start <= number <= cls.contract_number_end:
                    yield number

        uids = UserProfile.objects.filter(uid__startswith='80').values_list('uid', flat=True)
        for uid in uids:
            normalized_uid = (uid or '').strip()
            if len(normalized_uid) == 6 and normalized_uid.isdigit() and normalized_uid.startswith('80'):
                number = int(normalized_uid)
                if cls.contract_number_start <= number <= cls.contract_number_end:
                    yield number

    @classmethod
    def _resolve_next_contract_number(cls):
        used_numbers = set(cls._iter_used_contract_numbers())
        for number in range(cls.contract_number_start, cls.contract_number_end + 1):
            if number not in used_numbers:
                return number
        return None

    def form_valid(self, form):
        request = self.request
        next_path = resolve_next_path(request, 'user_list')
        display_name = form.cleaned_data['display_name']
        company_name = form.cleaned_data['company_name']
        department = form.cleaned_data['department']
        email = form.cleaned_data['email_addresses']
        note = form.cleaned_data['note']

        login_id = email
        initial_password = (email.split('@', 1)[0] if '@' in email else '').strip()

        User = get_user_model()

        for _ in range(10):
            number = self._resolve_next_contract_number()
            if number is None:
                return self.issuance_error(form, '契約アカウント番号の発行上限（899999）に達しました。')

            uid = f'{number:06d}'
            username = login_id

            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=username,
                        password=initial_password,
                        email=email,
                        is_staff=False,
                        is_superuser=False,
                    )

                    UserProfile.objects.create(
                        user=user,
                        uid=uid,
                        display_name=display_name,
                        company_name=company_name,
                        department=department,
                        user_type=UserProfile.USER_TYPE_CUSTOMER,
                        email_addresses=email,
                        note=note,
                        exclude_from_analysis=True,
                    )

                    customer, _ = Customer.objects.get_or_create(name=company_name)
                    customer.users.add(user)
            except IntegrityError:
                continue

            messages.success(
                request,
                (
                    f'契約アカウントを発行しました。'
                    f'管理番号: {uid} / ログインID: {username} / '
                    f'ユーザー名: {display_name} / '
                    '初期パスワード: メールアドレスの@前'
                ),
                extra_tags='user',
            )
            return redirect(next_path)

        return self.issuance_error(form, '契約アカウント発行時に競合が発生しました。再度お試しください。')