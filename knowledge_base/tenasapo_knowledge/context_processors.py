from .utils import resolve_user_display_name


def user_display_name(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {'current_user_display_name': ''}

    return {
        'current_user_display_name': resolve_user_display_name(user),
    }
