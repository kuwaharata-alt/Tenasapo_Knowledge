from .utils import resolve_user_display_name


ACCOUNT_VIEW_MODE_SESSION_KEY = 'account_view_mode'
ACCOUNT_VIEW_MODE_DEMO = 'demo'
ACCOUNT_VIEW_MODE_CS = 'cs'


def user_display_name(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {
            'current_user_display_name': '',
            'current_user_base_display_name': '',
            'current_user_is_customer': False,
            'current_user_view_mode': '',
        }

    view_mode = str(request.session.get(ACCOUNT_VIEW_MODE_SESSION_KEY) or '').strip().lower()
    if view_mode not in {ACCOUNT_VIEW_MODE_DEMO, ACCOUNT_VIEW_MODE_CS}:
        view_mode = ''

    profile = getattr(user, 'knowledge_profile', None)
    current_user_is_customer = False
    if view_mode in {ACCOUNT_VIEW_MODE_DEMO, ACCOUNT_VIEW_MODE_CS}:
        current_user_is_customer = True
    elif profile is not None:
        current_user_is_customer = profile.user_type == 'customer'
    else:
        current_user_is_customer = user.groups.filter(name='カスタマー').exists()

    base_display_name = resolve_user_display_name(user)
    mode_suffix = ''
    if view_mode == ACCOUNT_VIEW_MODE_DEMO:
        mode_suffix = ' (Demo)'
    elif view_mode == ACCOUNT_VIEW_MODE_CS:
        mode_suffix = ' (CS)'

    return {
        'current_user_display_name': f'{base_display_name}{mode_suffix}',
        'current_user_base_display_name': base_display_name,
        'current_user_is_customer': current_user_is_customer,
        'current_user_view_mode': view_mode,
    }
