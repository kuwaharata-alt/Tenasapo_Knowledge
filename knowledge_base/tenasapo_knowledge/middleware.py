from django.conf import settings
from django.contrib.auth.middleware import LoginRequiredMiddleware
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.sessions.middleware import SessionMiddleware


class SafeSessionMiddleware(SessionMiddleware):
    def process_response(self, request, response):
        try:
            return super().process_response(request, response)
        except SessionInterrupted:
            return response


class LoginRequiredExceptAssetsMiddleware(LoginRequiredMiddleware):
    def process_view(self, request, view_func, view_args, view_kwargs):
        path = request.path
        static_url = getattr(settings, 'STATIC_URL', '')
        media_url = getattr(settings, 'MEDIA_URL', '')

        if static_url and path.startswith(static_url):
            return None
        if media_url and path.startswith(media_url):
            return None

        return super().process_view(request, view_func, view_args, view_kwargs)
