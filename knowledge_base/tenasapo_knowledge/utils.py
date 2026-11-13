from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist


def can_view_view_history(request, manager_usernames=None):
    """Require effective admin flags and a configured upper-admin login, never a preview mode."""
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or not (user.is_staff or user.is_superuser):
        return False
    modes = (
        getattr(user, '_view_mode_override', ''),
        getattr(request, 'session', {}).get('account_view_mode', ''),
    )
    if any(str(mode or '').strip().lower() in {'demo', 'cs'} for mode in modes):
        return False
    if manager_usernames is None:
        manager_usernames = getattr(settings, 'PASSWORD_MANAGER_USERNAMES', ['Admin', 'SystenaAdmin'])
    return user.username.lower() in {name.lower() for name in manager_usernames}


def resolve_user_display_name(user, default=''):
    if not user:
        return default

    profile = None
    try:
        profile = getattr(user, 'knowledge_profile', None)
    except ObjectDoesNotExist:
        profile = None

    if profile:
        display_name = (profile.display_name or '').strip()
        if display_name:
            return display_name

    return (user.get_username() or '').strip() or default


def resolve_saved_or_user_display_name(saved_name, user, default=''):
    name = (saved_name or '').strip()
    resolved_name = resolve_user_display_name(user, default='') if user else ''
    login_id = (user.get_username() or '').strip() if user else ''

    if name:
        if login_id and name == login_id and resolved_name:
            return resolved_name
        return name

    if resolved_name:
        return resolved_name
    return default
