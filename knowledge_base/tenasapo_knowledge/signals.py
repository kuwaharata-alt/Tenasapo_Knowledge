from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.utils import timezone
from django.dispatch import receiver

from .models import LoginHistory
from .utils import resolve_user_display_name


def client_ip_from_request(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


@receiver(user_logged_in)
def record_login_history(sender, request, user, **kwargs):
    LoginHistory.objects.filter(
        user=user,
        logged_out_at__isnull=True,
    ).update(logged_out_at=timezone.now())

    history = LoginHistory.objects.create(
        user=user,
        username=resolve_user_display_name(user),
        ip_address=client_ip_from_request(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:1000],
    )
    request.session['login_history_id'] = history.id


@receiver(user_logged_out)
def record_logout_history(sender, request, user, **kwargs):
    if request is None:
        return

    history_id = request.session.get('login_history_id')
    if history_id:
        history = LoginHistory.objects.filter(id=history_id).first()
        if history and history.logged_out_at is None:
            history.logged_out_at = timezone.now()
            history.save(update_fields=['logged_out_at'])
    elif user and user.is_authenticated:
        history = LoginHistory.objects.filter(user=user, logged_out_at__isnull=True).first()
        if history:
            history.logged_out_at = timezone.now()
            history.save(update_fields=['logged_out_at'])
