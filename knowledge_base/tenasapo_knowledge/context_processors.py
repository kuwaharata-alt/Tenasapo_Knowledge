from .utils import resolve_user_display_name


def user_display_name(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {
            'current_user_display_name': '',
            'current_user_is_customer': False,
        }

    profile = getattr(user, 'knowledge_profile', None)
    current_user_is_customer = False
    if profile is not None:
        current_user_is_customer = profile.user_type == 'customer'
    else:
        current_user_is_customer = user.groups.filter(name='カスタマー').exists()

    return {
        'current_user_display_name': resolve_user_display_name(user),
        'current_user_is_customer': current_user_is_customer,
    }
