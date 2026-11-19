"""Shared access policy. Do not import the legacy views module here."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme


ADMIN_GROUP_NAME = getattr(
    settings,
    'USER_ROLE_ADMIN_NAME',
    getattr(settings, 'USER_GROUP_ADMIN_NAME', '管理者'),
)
SYSTENA_GROUP_NAME = getattr(
    settings,
    'USER_ROLE_SYSTENA_NAME',
    getattr(settings, 'USER_GROUP_SYSTENA_NAME', 'システナ'),
)
CUSTOMER_GROUP_NAME = getattr(
    settings,
    'USER_ROLE_CUSTOMER_NAME',
    getattr(settings, 'USER_GROUP_CUSTOMER_NAME', 'カスタマー'),
)
REVIEWER_GROUP_NAME = getattr(
    settings,
    'USER_ROLE_REVIEWER_NAME',
    getattr(settings, 'USER_GROUP_REVIEWER_NAME', 'レビュアー'),
)
DEMO_GROUP_NAME = getattr(
    settings,
    'USER_ROLE_DEMO_NAME',
    getattr(settings, 'USER_GROUP_DEMO_NAME', 'demo'),
)
ACCOUNT_VIEW_MODE_SESSION_KEY = 'account_view_mode'
ACCOUNT_VIEW_MODE_DEMO = 'demo'
ACCOUNT_VIEW_MODE_CS = 'cs'
ACCOUNT_VIEW_MODES = {ACCOUNT_VIEW_MODE_DEMO, ACCOUNT_VIEW_MODE_CS}


def get_forced_account_view_mode(user):
    mode = str(getattr(user, '_view_mode_override', '') or '').strip().lower()
    return mode if mode in ACCOUNT_VIEW_MODES else ''


def in_group(user, group_name):
    if not user.is_authenticated:
        return False

    forced_mode = get_forced_account_view_mode(user)
    if forced_mode in ACCOUNT_VIEW_MODES:
        return group_name == CUSTOMER_GROUP_NAME

    return user.groups.filter(name=group_name).exists()


def is_reviewer_user(user):
    reviewer_group_names = {group_name for group_name in {REVIEWER_GROUP_NAME, 'レビュアー', '承認者'} if group_name}
    return user.is_authenticated and any(in_group(user, group_name) for group_name in reviewer_group_names)


def can_edit_article(user):
    if get_forced_account_view_mode(user) in ACCOUNT_VIEW_MODES:
        return False

    return (
        user.is_authenticated
        and (
            user.is_staff
            or user.is_superuser
            or in_group(user, ADMIN_GROUP_NAME)
            or is_reviewer_user(user)
        )
    )


def is_admin_account(user):
    return user.is_authenticated and user.username.lower() == 'admin'


def can_use_favorite(user):
    return (
        user.is_authenticated
        and (
            user.is_staff
            or user.is_superuser
            or in_group(user, SYSTENA_GROUP_NAME)
            or in_group(user, CUSTOMER_GROUP_NAME)
        )
    )


def can_use_convenience_favorite(user):
    return can_use_favorite(user)


def resolve_next_path(request, fallback_url_name, **fallback_kwargs):
    candidate = (request.POST.get('next') or request.GET.get('next') or '').strip()
    if candidate and url_has_allowed_host_and_scheme(
        url=candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    if fallback_kwargs:
        return reverse_lazy(fallback_url_name, kwargs=fallback_kwargs)
    return reverse_lazy(fallback_url_name)


class StaffRequiredMixin(UserPassesTestMixin):
    raise_exception = True

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and (user.is_staff or user.is_superuser)

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(self.request, 'このページを閲覧する権限がありません。')
            return redirect('article_list')
        return super().handle_no_permission()