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
    if name:
        return name
    return resolve_user_display_name(user, default=default)
