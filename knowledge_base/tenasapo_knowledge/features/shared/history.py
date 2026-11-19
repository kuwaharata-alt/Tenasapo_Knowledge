"""View-history recording shared by legacy and feature views."""

from ...models import LoginHistory, ViewHistory
from ...utils import resolve_user_display_name


def client_ip_from_request(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def current_login_history(request):
    user = request.user
    if not user.is_authenticated:
        return None

    history_id = request.session.get('login_history_id')
    if history_id:
        history = LoginHistory.objects.filter(id=history_id, user=user).first()
        if history:
            return history

    history = LoginHistory.objects.filter(user=user, logged_out_at__isnull=True).first()
    if history:
        request.session['login_history_id'] = history.id
    return history


def record_view_history(
    request,
    page_name,
    search_query='',
    parent_category='',
    category='',
    path='',
):
    user = request.user
    if not user.is_authenticated:
        return

    login_history = current_login_history(request)
    resolved_path = path or request.path

    last_history = (
        ViewHistory.objects.filter(
            user=user,
            login_history=login_history,
        )
        .order_by('-viewed_at', '-id')
        .first()
    )
    if (
        last_history
        and last_history.page_name == page_name
        and last_history.path == resolved_path
        and last_history.search_query == search_query
        and last_history.parent_category == parent_category
        and last_history.category == category
    ):
        return

    ViewHistory.objects.create(
        login_history=login_history,
        user=user,
        username=resolve_user_display_name(user),
        page_name=page_name,
        path=resolved_path,
        search_query=search_query,
        parent_category=parent_category,
        category=category,
        ip_address=client_ip_from_request(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:1000],
    )