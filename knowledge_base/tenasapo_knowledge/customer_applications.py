"""管理者操作から呼ぶ顧客申請サービス（権限・CSRF検証は呼出元の責務）。

import_applications(actor) -> int: 永続cursorから最大10ページを読み、新規件数を返す。
    ページ単位で申請とcursorを同時確定。原本は追記専用（行削除・移動は禁止）。
approve_application(pk, reviewer) -> CustomerApplication: 新規承認後に下書きを作成。
    下書き失敗時も承認とユーザーは確定済み。ApplicationError後は下書きAPIで再試行。
    承認済みはそのまま返す（外部通信なし）。却下済みはエラー。
create_invitation_draft(pk, actor) -> CustomerApplication: 承認者本人のみ。既存IDは再作成しない。
    作成中はエラー、5分経過した作成処理は再試行可能。送信結果不明時は照合だけ行う。
    保存するのはDjango標準tokenのみ。保持tokenの失効時は再発行せず安全に停止。

外部通信を含むAPIは、呼出元のDBトランザクションの外で使用すること。
外部エラー・応答詳細・個人情報・tokenをログや例外メッセージへ出力しない。
"""

import ipaddress
import re
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .customer_application_models import CustomerApplication, CustomerApplicationImportState
from . import customer_application_google as google
from .models import Customer, UserProfile


__all__ = [
    'ApplicationError', 'import_applications', 'approve_application',
    'create_invitation_draft',
]

SPREADSHEET_ID = '1JaWW0c_CE9W5_egLvoqrWcnwuIbb6QCERBRrQvAE5Pg'
SHEET_ID = '1583871824'
PAGE_SIZE = 200
MAX_PAGES = 10
_CONTROL = re.compile(r'[\x00-\x1f\x7f\u2028\u2029]')
_EMAIL = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?'
    r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+'
)
_REMOTE_ERROR = '申請連携を完了できませんでした。設定を確認して再試行してください。'
_ROW_ERROR = '申請データの形式が正しくありません。回答シートを確認してください。'
_DRAFT_ERROR = '招待メールの下書きを作成できませんでした。設定を確認して再試行してください。'


class ApplicationError(Exception):
    """利用者へ表示可能な、秘密・外部応答詳細を含まない業務エラー。"""


class _ApplicationForm(forms.Form):
    source_key = forms.CharField(max_length=255)
    submitted_at = forms.DateTimeField(input_formats=[
        '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S',
        '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M',
    ])
    applicant_email = forms.EmailField(max_length=150)
    email = forms.EmailField(max_length=150)
    company_name = forms.CharField(max_length=120)
    department = forms.CharField(max_length=255, required=False)
    display_name = forms.CharField(max_length=150)

    def clean(self):
        cleaned = super().clean()
        for field in ('applicant_email', 'email'):
            value = cleaned.get(field)
            if value and not _EMAIL.fullmatch(value):
                self.add_error(field, '単一のメールアドレスを指定してください。')
        return cleaned


def _call_google(payload: dict, actor) -> dict:
    """既存Googleログイン用clientで追加認可した管理者のAPIのみ使用する。"""
    try:
        if payload['action'] == 'list_applications':
            return google.list_applications(actor, payload['start_row'])
        if payload['action'] == 'create_invitation_draft':
            item = _get_application(payload['application_id'])
            return google.create_draft(actor, item, payload['subject'], payload['body'])
        raise ApplicationError(_REMOTE_ERROR)
    except google.CustomerGoogleError as exc:
        raise ApplicationError(str(exc)) from None


def _validate_row(raw, start_row: int, end_row: int) -> dict:
    if not isinstance(raw, dict):
        raise ApplicationError(_ROW_ERROR)
    for name in _ApplicationForm.base_fields:
        value = raw.get(name)
        if not isinstance(value, str) or _CONTROL.search(value):
            raise ApplicationError(_ROW_ERROR)
    form = _ApplicationForm(raw)
    if not form.is_valid():
        raise ApplicationError(_ROW_ERROR)
    cleaned = form.cleaned_data
    match = re.fullmatch(
        re.escape(f'{SPREADSHEET_ID}:{SHEET_ID}:') + r'([1-9][0-9]*)', cleaned['source_key'],
    )
    if not match or not start_row <= int(match.group(1)) < end_row:
        raise ApplicationError(_ROW_ERROR)
    return cleaned


def import_applications(actor) -> int:
    """HTTPはtransaction外。検証済みページとcursorをCAS付きatomicで保存。"""
    state, _ = CustomerApplicationImportState.objects.get_or_create(
        source_key=f'{SPREADSHEET_ID}:{SHEET_ID}',
    )
    start_row = state.next_row
    count = 0
    for _ in range(MAX_PAGES):
        page = _call_google({'action': 'list_applications', 'start_row': start_row}, actor)
        if not isinstance(page, dict) or 'next_row' not in page:
            raise ApplicationError(_ROW_ERROR)
        applications, next_row = page.get('applications'), page['next_row']
        if not isinstance(applications, list) or len(applications) > PAGE_SIZE:
            raise ApplicationError(_ROW_ERROR)
        if next_row is not None and (
            type(next_row) is not int or not start_row < next_row <= start_row + PAGE_SIZE
        ):
            raise ApplicationError(_ROW_ERROR)
        end_row = next_row if next_row is not None else start_row + PAGE_SIZE
        rows = []
        seen = set()
        for raw in applications:
            cleaned = _validate_row(raw, start_row, end_row)
            if cleaned['source_key'] in seen:
                raise ApplicationError(_ROW_ERROR)
            seen.add(cleaned['source_key'])
            rows.append(cleaned)
        # 応答内の最大行+1を保存し、
        # 全空の最終ページならstart_rowを維持して将来の追記を取りこぼさない。
        cursor = next_row if next_row is not None else max(
            (int(row['source_key'].rsplit(':', 1)[1]) + 1 for row in rows),
            default=start_row,
        )
        page_count = 0
        try:
            with transaction.atomic():
                # HTTP中に別workerが進めたcursorなら、このページを一切保存しない。
                # 行ロック非対応DBでも条件付きUPDATEを比較・更新の一操作にする。
                changed = CustomerApplicationImportState.objects.filter(
                    pk=state.pk, next_row=start_row,
                ).update(next_row=cursor)
                if changed != 1:
                    raise ApplicationError('申請の取込が競合しました。再試行してください。')
                for row in rows:
                    source_key = row['source_key']
                    defaults = {key: value for key, value in row.items() if key != 'source_key'}
                    _, created = CustomerApplication.objects.get_or_create(
                        source_key=source_key, defaults=defaults,
                    )
                    page_count += int(created)
        except IntegrityError:
            raise ApplicationError('申請の保存が競合しました。再試行してください。') from None
        count += page_count
        if next_row is None:
            break
        start_row = next_row
    return count


def _get_application(pk, *, lock=False) -> CustomerApplication:
    queryset = CustomerApplication.objects
    if lock:
        queryset = queryset.select_for_update()
    try:
        return queryset.get(pk=pk)
    except (CustomerApplication.DoesNotExist, ValidationError, ValueError, TypeError):
        raise ApplicationError('対象の申請が見つかりません。') from None


def _ensure_email_available(email: str) -> None:
    users = get_user_model()
    if users.objects.filter(Q(username__iexact=email) | Q(email__iexact=email)).exists():
        raise ApplicationError('招待先メールアドレスは既に登録されています。')
    if CustomerApplication.objects.filter(status='approved', email__iexact=email).exists():
        raise ApplicationError('招待先メールアドレスは既に承認されています。')
    for value in UserProfile.objects.filter(email_addresses__icontains=email).values_list(
        'email_addresses', flat=True,
    ).iterator():
        if email.casefold() in {item.casefold() for item in re.split(r'[,;、\s]+', value or '') if item}:
            raise ApplicationError('招待先メールアドレスは既に登録されています。')


def _next_uid() -> str:
    used = set()
    for username in get_user_model().objects.filter(username__istartswith='CSUser').values_list(
        'username', flat=True,
    ).iterator():
        match = re.fullmatch(r'CSUser([0-9]{6})', username, flags=re.IGNORECASE)
        if match:
            used.add(int(match.group(1)))
    for uid in UserProfile.objects.exclude(uid__isnull=True).exclude(uid='').values_list(
        'uid', flat=True,
    ).iterator():
        if re.fullmatch(r'[0-9]{6}', uid.strip()):
            used.add(int(uid.strip()))
    for number in range(800001, 900000):
        if number not in used:
            return str(number)
    raise ApplicationError('契約アカウント番号の発行上限に達しました。')


def approve_application(pk, reviewer) -> CustomerApplication:
    """最大10回の衝突再試行。下書き通信は承認transaction終了後に実行。"""
    for _ in range(10):
        try:
            with transaction.atomic():
                application = _get_application(pk, lock=True)
                if application.status == CustomerApplication.Status.APPROVED:
                    return application
                if application.status != CustomerApplication.Status.PENDING:
                    raise ApplicationError('却下された申請は承認できません。')
                _ensure_email_available(application.email)
                uid = _next_uid()
                # None（使用不可）ではなく、共有も保存もしないランダムな初期秘密を使う。
                user = get_user_model().objects.create_user(
                    username=application.email, email=application.email,
                    password=secrets.token_urlsafe(48), is_active=True,
                    is_staff=False, is_superuser=False,
                )
                UserProfile.objects.create(
                    user=user, uid=uid, display_name=application.display_name,
                    company_name=application.company_name, department=application.department,
                    user_type=UserProfile.USER_TYPE_CUSTOMER,
                    email_addresses=application.email, google_first_login_done=True,
                    exclude_from_analysis=True,
                )
                customer, _ = Customer.objects.get_or_create(name=application.company_name)
                customer.users.add(user)
                application.status = CustomerApplication.Status.APPROVED
                application.created_user = user
                application.reviewed_by = reviewer
                application.reviewed_at = timezone.now()
                application.invitation_token = default_token_generator.make_token(user)
                application.save(update_fields=[
                    'status', 'created_user', 'reviewed_by', 'reviewed_at', 'invitation_token',
                ])
            break
        except IntegrityError:
            # atomic全体を戻すため、ユーザー・プロフィール・M2Mの残骸は残らない。
            continue
    else:
        raise ApplicationError('承認処理が競合しました。登録状況を確認して再試行してください。') from None
    return create_invitation_draft(application.pk, reviewer)


def _public_origin() -> str:
    value = getattr(settings, 'CUSTOMER_APPLICATION_PUBLIC_URL', '')
    try:
        if not isinstance(value, str) or not value or _CONTROL.search(value) or any(c.isspace() for c in value):
            raise ValueError
        parts = urlsplit(value)
        if (
            parts.scheme != 'https' or not value.startswith('https://')
            or not parts.hostname or parts.username is not None or parts.password is not None
            or parts.path or parts.query or parts.fragment or '?' in value or '#' in value
        ):
            raise ValueError
        host = parts.hostname.encode('idna').decode('ascii').lower()
        if ':' in host:
            ipaddress.IPv6Address(host)
            host = f'[{host}]'
        elif not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host):
            raise ValueError
        port = parts.port
        origin = f'https://{host}' + (f':{port}' if port not in (None, 443) else '')
        # urlsplitが空ポート等を寛容に受理するため、入力netlocも照合する。
        permitted = {host, f'{host}:443'} if port in (None, 443) else {f'{host}:{port}'}
        if parts.netloc.lower().encode('idna').decode('ascii') not in permitted:
            raise ValueError
        return origin
    except (ValueError, UnicodeError):
        raise ApplicationError('招待URLの公開先設定を確認してください。') from None


def create_invitation_draft(pk, actor) -> CustomerApplication:
    """短いDBロックで作成権を取得し、外部通信後に結果だけを保存する。"""
    attempt_id = uuid.uuid4()
    with transaction.atomic():
        application = _get_application(pk, lock=True)
        if application.status != CustomerApplication.Status.APPROVED:
            raise ApplicationError('承認済みの申請だけ下書きを作成できます。')
        if application.reviewed_by_id != actor.pk:
            raise ApplicationError('下書きの作成・再試行は承認者本人が行ってください。')
        if application.draft_id:
            return application
        now = timezone.now()
        if (
            application.draft_state == CustomerApplication.DraftState.CREATING
            and application.draft_started_at
            and application.draft_started_at > now - timedelta(minutes=5)
        ):
            raise ApplicationError('下書きを作成中です。時間をおいて確認してください。')
        application.draft_state = CustomerApplication.DraftState.CREATING
        application.draft_started_at = now
        application.draft_attempt_id = attempt_id
        application.draft_error = ''
        user = application.created_user
        if user is not None:
            if application.invitation_token:
                if not default_token_generator.check_token(user, application.invitation_token):
                    # 既存下書きのリンクと乖離する再発行は禁止。
                    raise ApplicationError('保持している招待リンクは失効しています。管理者へ確認してください。')
            else:
                application.invitation_token = default_token_generator.make_token(user)
        application.save(update_fields=[
            'draft_state', 'draft_started_at', 'draft_attempt_id', 'draft_error', 'invitation_token',
        ])
    try:
        if user is None or not user.is_active or not user.has_usable_password():
            raise ApplicationError(_DRAFT_ERROR)
        origin = _public_origin()
        path = reverse('password_reset_confirm', kwargs={
            'uidb64': urlsafe_base64_encode(force_bytes(user.pk)),
            'token': application.invitation_token,
        })
        if not path.startswith('/') or path.startswith('//'):
            raise ApplicationError(_DRAFT_ERROR)
        invitation_url = origin + path
        body = (
            f'{application.company_name}\n{application.display_name} 様\n\n'
            'Nexusへのご利用申請を承認いたしました。\n'
            '以下のリンクからパスワードを設定してください。\n\n'
            f'{invitation_url}\n\n'
            f'ログインID: {user.username}\n'
            'リンクには有効期限があります。お早めに設定してください。\n'
            'お心当たりがない場合は、本メールを破棄してください。\n\n'
            f'Nexus申請番号: {application.pk}'
        )
        result = _call_google({
            'action': 'create_invitation_draft', 'application_id': str(application.pk),
            'to': application.email, 'subject': '【Nexus】ご利用申請承認・パスワード設定のご案内',
            'body': body,
        }, actor)
        draft_id = result.get('draft_id') if isinstance(result, dict) else None
        if not isinstance(draft_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,255}', draft_id):
            raise ApplicationError(_DRAFT_ERROR)
    except Exception:
        # 古いワーカーの失敗で、新しい作成処理・成功状態を上書きしない。
        CustomerApplication.objects.filter(
            pk=application.pk, draft_attempt_id=attempt_id, draft_id='',
        ).update(draft_state=CustomerApplication.DraftState.FAILED, draft_error=_DRAFT_ERROR)
        raise ApplicationError(_DRAFT_ERROR) from None
    with transaction.atomic():
        application = _get_application(pk, lock=True)
        if not application.draft_id:
            if application.draft_attempt_id != attempt_id:
                raise ApplicationError('下書きの作成処理が切り替わりました。状態を確認してください。')
            application.draft_id = draft_id
            application.draft_state = CustomerApplication.DraftState.CREATED
            application.draft_error = ''
            application.save(update_fields=['draft_id', 'draft_state', 'draft_error'])
    return application