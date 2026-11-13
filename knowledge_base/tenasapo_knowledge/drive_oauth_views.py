"""Explicit staff-only Drive consent; never modifies the login identity."""

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_POST

from .chat_schedule import current_chat_environment
from .drive_oauth import DriveOAuthError, SESSION_KEY, handle_callback, make_auth_request
from .drive_oauth_models import DriveOAuthConnection


def _staff(user):
    return user.is_authenticated and user.is_active and user.is_staff


@never_cache
@user_passes_test(_staff)
@require_POST
@csrf_protect
def drive_oauth_connect(request):
    try:
        return redirect(make_auth_request(request))
    except DriveOAuthError as error:
        messages.error(request, str(error))
        return redirect('chat_schedule_settings')


@sensitive_variables()
@never_cache
@user_passes_test(_staff)
@require_GET
def drive_oauth_callback(request):
    try:
        handle_callback(request)
        messages.success(request, 'Google Driveと連携しました。このアカウントで保存した予約からPDFを保存できます。')
    except DriveOAuthError as error:
        messages.error(request, str(error))
    response = redirect('chat_schedule_settings')
    response['Referrer-Policy'] = 'no-referrer'
    return response


@sensitive_post_parameters()
@never_cache
@user_passes_test(_staff)
@require_POST
@csrf_protect
def drive_oauth_disconnect(request):
    DriveOAuthConnection.objects.filter(actor=request.user, environment=current_chat_environment()).delete()
    request.session.pop(SESSION_KEY, None)
    messages.success(request, 'この環境のDrive連携を解除しました。Google側の許可取消はGoogleアカウントの設定で行えます。')
    return redirect('chat_schedule_settings')