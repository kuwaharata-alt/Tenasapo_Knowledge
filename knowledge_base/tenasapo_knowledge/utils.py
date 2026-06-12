from django.core.exceptions import ObjectDoesNotExist


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
