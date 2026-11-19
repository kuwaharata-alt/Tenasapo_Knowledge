"""顧客申請の管理画面。外部通信を含むPOSTはURL側でnon_atomicにする。"""

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_GET, require_POST

from . import customer_applications as service
from . import customer_application_google as google
from .customer_application_models import CustomerApplication


def _integration_context(user):
    backend = getattr(settings, 'CUSTOMER_APPLICATION_BACKEND', 'oauth')
    context = {'application_backend': backend, 'gas_ready': False,
               'gas_mailbox': '', 'gas_actor_id': None, 'gas_is_operator': False,
               'gas_error': '',
               'configured': bool(getattr(settings, 'CUSTOMER_APPLICATION_PUBLIC_URL', ''))}
    if backend == 'gas':
        from .customer_application_gas import gas_configuration, GasError
        try:
            config = gas_configuration()
            service._public_origin()
            context.update(gas_ready=True, gas_mailbox=config['mailbox'],
                           gas_actor_id=config['actor'].pk,
                           gas_is_operator=(user.pk == config['actor'].pk))
        except (GasError, service.ApplicationError):
            context['gas_error'] = 'GAS連携の環境変数・固定実行ユーザー・公開URLを確認してください。'
    return context


def application_admin(view):
    @login_required
    @never_cache
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


@application_admin
@require_GET
def application_list(request):
    status = request.GET.get('status', 'pending')
    if status not in {'', *CustomerApplication.Status.values}:
        status = 'pending'
    items = CustomerApplication.objects.defer('invitation_token')
    counts = dict(CustomerApplication.objects.values_list('status').annotate(total=Count('pk')))
    if status:
        items = items.filter(status=status)
    page = Paginator(items, 25).get_page(request.GET.get('page'))
    configured = bool(getattr(settings, 'CUSTOMER_APPLICATION_PUBLIC_URL', ''))
    connection = None
    connection_error = ''
    integration = _integration_context(request.user)
    if integration['application_backend'] == 'oauth':
        try:
            connection = google.get_connection(request.user)
        except google.CustomerGoogleError as exc:
            connection_error = str(exc)
    return render(request, 'tenasapo_knowledge/customer_application_list.html', {
        'page_obj': page, 'selected_status': status,
        'status_choices': CustomerApplication.Status.choices,
        'pending_count': counts.get('pending', 0),
        'approved_count': counts.get('approved', 0),
        'rejected_count': counts.get('rejected', 0), 'configured': configured,
        'google_connection': connection, 'google_connection_error': connection_error,
        **integration,
    })


@application_admin
@require_GET
def application_detail(request, pk):
    item = get_object_or_404(
        CustomerApplication.objects.defer('invitation_token').select_related('reviewed_by'), pk=pk,
    )
    return render(request, 'tenasapo_knowledge/customer_application_detail.html', {
        'application': item, 'is_draft_owner': item.reviewed_by_id == request.user.pk,
        **_integration_context(request.user),
    })


@application_admin
@require_POST
def application_import(request):
    if getattr(settings, 'CUSTOMER_APPLICATION_BACKEND', 'oauth') != 'oauth':
        messages.info(request, '回答の取込はGASのsyncNexusApplicationsまたは定期実行から行います。')
        return redirect('customer_application_list')
    try:
        count = service.import_applications(request.user)
    except service.ApplicationError as exc:
        messages.error(request, str(exc) + ' 途中まで取り込まれた申請は保存済みです。')
    else:
        messages.success(request, f'{count}件の新しい申請を取り込みました。最大2,000行ずつ取得します。')
    return redirect('customer_application_list')


@application_admin
@require_POST
def application_approve(request, pk):
    get_object_or_404(CustomerApplication, pk=pk)
    try:
        item = service.approve_application(pk, request.user)
    except service.ApplicationError as exc:
        approved = CustomerApplication.objects.filter(pk=pk, status='approved').exists()
        prefix = 'ユーザー作成・承認は完了しています。下書きの状態を確認してください。' if approved else ''
        messages.error(request, prefix + str(exc))
    else:
        if item.draft_id:
            messages.success(request, '承認済みです。案内メールの下書きを承認者のGmailで確認してください。')
        else:
            if getattr(settings, 'CUSTOMER_APPLICATION_BACKEND', 'oauth') == 'gas':
                messages.success(request, 'ユーザーを作成しました。GASの次回実行でGmail下書きを処理します。')
            else:
                messages.warning(request, '承認済みです。下書き作成を再試行してください。')
    return redirect('customer_application_detail', pk=pk)


@application_admin
@require_POST
def application_reject(request, pk):
    get_object_or_404(CustomerApplication, pk=pk)
    changed = CustomerApplication.objects.filter(pk=pk, status='pending').update(
        status='rejected', reviewed_by=request.user, reviewed_at=timezone.now(),
    )
    if changed:
        messages.success(request, '申請を却下しました。ユーザー作成・メール通知は行っていません。')
    else:
        messages.warning(request, 'この申請は処理済みです。状態を確認してください。')
    return redirect('customer_application_detail', pk=pk)


@application_admin
@require_POST
def application_retry_draft(request, pk):
    get_object_or_404(CustomerApplication, pk=pk)
    try:
        service.create_invitation_draft(pk, request.user)
    except service.ApplicationError as exc:
        messages.error(request, str(exc))
    else:
        if getattr(settings, 'CUSTOMER_APPLICATION_BACKEND', 'oauth') == 'gas':
            messages.info(request, 'GASの次回実行で処理・照合します。作成試行済みの下書きは新規作成しません。')
        else:
            messages.success(request, '案内メールの下書きを承認者のGmailで確認してください。')
    return redirect('customer_application_detail', pk=pk)


@application_admin
@require_POST
def google_connect(request):
    if getattr(settings, 'CUSTOMER_APPLICATION_BACKEND', 'oauth') != 'oauth':
        messages.info(request, '現在はGAS連携方式です。Googleの追加OAuth認可は使用しません。')
        return redirect('customer_application_list')
    try:
        return redirect(google.make_auth_request(request))
    except google.CustomerGoogleError as exc:
        messages.error(request, str(exc))
        return redirect('customer_application_list')


@sensitive_variables()
@application_admin
@require_GET
def google_callback(request):
    if getattr(settings, 'CUSTOMER_APPLICATION_BACKEND', 'oauth') != 'oauth':
        response = redirect('customer_application_list')
        response['Referrer-Policy'] = 'no-referrer'
        return response
    try:
        google.handle_callback(request)
        messages.success(request, '申請管理用のGoogleアクセスを許可しました。回答の取込・Gmail下書きを利用できます。')
    except google.CustomerGoogleError as exc:
        messages.error(request, str(exc))
    response = redirect('customer_application_list')
    response['Referrer-Policy'] = 'no-referrer'
    return response


@application_admin
@require_POST
def google_disconnect(request):
    try:
        google.disconnect(request)
        messages.success(request, 'この環境の申請管理用Google連携を解除しました。ログインとDrive連携には影響しません。')
    except google.CustomerGoogleError as exc:
        messages.error(request, str(exc))
    return redirect('customer_application_list')