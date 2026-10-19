from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.middleware import LoginRequiredMiddleware
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.sessions.middleware import SessionMiddleware
from django.shortcuts import redirect
from django.urls import reverse


ACCOUNT_VIEW_MODE_SESSION_KEY = 'account_view_mode'
ACCOUNT_VIEW_MODES = {'demo', 'cs'}
PUBLIC_AUTH_PATH_PREFIXES = (
    '/accounts/login/',
    '/accounts/logout/',
    '/accounts/password-reset/',
    '/accounts/reset/',
)


class SafeSessionMiddleware(SessionMiddleware):
    def process_response(self, request, response):
        try:
            return super().process_response(request, response)
        except SessionInterrupted:
            return response


class LoginRequiredExceptAssetsMiddleware(LoginRequiredMiddleware):
    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, 'user', None)
        if user and getattr(user, 'is_authenticated', False):
            mode = str(request.session.get(ACCOUNT_VIEW_MODE_SESSION_KEY) or '').strip().lower()
            effective_mode = mode if mode in ACCOUNT_VIEW_MODES else ''
            setattr(user, '_view_mode_override', effective_mode)
            if effective_mode:
                setattr(user, '_original_is_staff', user.is_staff)
                setattr(user, '_original_is_superuser', user.is_superuser)
                user.is_staff = False
                user.is_superuser = False

        path = request.path
        static_url = getattr(settings, 'STATIC_URL', '')
        media_url = getattr(settings, 'MEDIA_URL', '')

        if static_url and path.startswith(static_url):
            return None
        if media_url and path.startswith(media_url):
            return None
        for prefix in PUBLIC_AUTH_PATH_PREFIXES:
            if path.startswith(prefix):
                return None

        if user and getattr(user, 'is_authenticated', False):
            from .views import is_demo_account_expired, should_force_password_change

            if is_demo_account_expired(user):
                logout(request)
                messages.error(request, 'デモアカウントの有効期限が切れています。管理者へ連絡してください。')
                return redirect('login')

            password_change_path = reverse('password_change')
            if should_force_password_change(user) and not path.startswith(password_change_path):
                return redirect(password_change_path)

        return super().process_view(request, view_func, view_args, view_kwargs)
