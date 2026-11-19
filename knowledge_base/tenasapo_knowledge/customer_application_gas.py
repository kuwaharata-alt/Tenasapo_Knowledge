"""Outbound-only GAS bridge. HTTP adapters must use non_atomic_requests.

authorize() commits the nonce before JSON decoding or dispatch; callers must not
wrap it in an outer transaction. No network I/O or token generation occurs here.
"""

import hashlib
import hmac
import re
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, connection, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from . import customer_applications as service
from .chat_schedule import current_chat_environment
from .customer_application_gas_models import CustomerApplicationGASJob as Job
from .customer_application_gas_models import CustomerApplicationGASNonce as Nonce
from .customer_application_models import CustomerApplication as Application
from .customer_application_models import CustomerApplicationImportState as ImportState
from .customer_application_google import HEADERS

MAX_BODY = 512 * 1024
PATHS = {
    'ping': '/api/customer-applications/gas/status/',
    'import': '/api/customer-applications/gas/import/',
    'claim': '/api/customer-applications/gas/claim/',
    'ack': '/api/customer-applications/gas/ack/',
}
_ERROR = '顧客申請のGAS連携を完了できません。設定と処理状態を確認してください。'
_UNCERTAIN = 'GAS_DRAFT_UNCERTAIN'


class GasError(service.ApplicationError):
    """Fixed public message, stable machine code, no external/secret details."""

    def __init__(self, code='invalid_request', status=400):
        self.code = code
        self.status = status
        super().__init__(_ERROR)


def _fail(code='invalid_request', status=400):
    raise GasError(code, status)


@sensitive_variables()
def gas_configuration():
    """Return {environment, actor, mailbox}; never return the shared secret."""
    if getattr(settings, 'CUSTOMER_APPLICATION_BACKEND', '') != 'gas':
        _fail('backend_disabled', 503)
    secret = getattr(settings, 'CUSTOMER_APPLICATION_GAS_SECRET', '')
    actor_id = getattr(settings, 'CUSTOMER_APPLICATION_GAS_ACTOR_ID', None)
    mailbox = getattr(settings, 'CUSTOMER_APPLICATION_GAS_MAILBOX', '')
    domain = getattr(settings, 'GOOGLE_WORKSPACE_DOMAIN', '')
    environment = current_chat_environment()
    if (not isinstance(secret, str) or not 32 <= len(secret) <= 4096
            or isinstance(actor_id, bool) or not isinstance(actor_id, (str, int))
            or not re.fullmatch(r'[1-9][0-9]{0,18}', str(actor_id))
            or not isinstance(mailbox, str) or len(mailbox) > 254
            or not isinstance(domain, str) or not domain
            or environment not in ('development', 'production')):
        _fail('invalid_configuration', 503)
    try:
        secret.encode('utf-8')
        validate_email(mailbox)
        if (re.search(r'[\s\x00-\x1f\x7f]', mailbox)
                or mailbox.rsplit('@', 1)[1].casefold() != domain.casefold()):
            _fail('invalid_configuration', 503)
        actor = get_user_model().objects.get(pk=actor_id, is_active=True, is_staff=True)
    except (ValidationError, ValueError, UnicodeError, get_user_model().DoesNotExist):
        _fail('invalid_configuration', 503)
    if actor.email != mailbox:
        _fail('invalid_configuration', 503)
    return {'environment': environment, 'actor': actor, 'mailbox': mailbox}


def _actor_configuration(actor):
    config = gas_configuration()
    if (not isinstance(actor, get_user_model()) or actor.pk != config['actor'].pk
            or actor._state.adding or not actor.is_active or not actor.is_staff
            or actor.email != config['mailbox']
            or str(getattr(actor, '_view_mode_override', '')).lower() in ('demo', 'cs')):
        _fail('forbidden', 403)
    return config


@sensitive_variables()
def authorize(request):
    """Validate fixed endpoint + raw-body HMAC; consume nonce independently."""
    config = gas_configuration()
    if (request.method != 'POST' or request.path not in PATHS.values()
            or request.META.get('QUERY_STRING', '')
            or request.content_type != 'application/json'):
        _fail('unauthorized', 401)
    timestamp = request.headers.get('X-Nexus-Timestamp', '')
    nonce = request.headers.get('X-Nexus-Nonce', '')
    signature = request.headers.get('X-Nexus-Signature', '')
    if (not re.fullmatch(r'[0-9]{1,12}', timestamp)
            or not re.fullmatch(r'[0-9a-f]{32}', nonce)
            or not re.fullmatch(r'[0-9a-f]{64}', signature)
            or abs(int(time.time()) - int(timestamp)) > 300):
        _fail('unauthorized', 401)
    length = request.META.get('CONTENT_LENGTH', '')
    if length and (not re.fullmatch(r'[0-9]{1,12}', length) or int(length) > MAX_BODY):
        _fail('body_too_large', 413)
    try:
        raw_body = request.body
    except RequestDataTooBig:
        _fail('body_too_large', 413)
    if len(raw_body) > MAX_BODY:
        _fail('body_too_large', 413)
    canonical = '\n'.join([
        'nexus-gas-v1', config['environment'], request.method, request.path,
        timestamp, nonce, hashlib.sha256(raw_body).hexdigest(),
    ])
    expected = hmac.new(
        settings.CUSTOMER_APPLICATION_GAS_SECRET.encode('utf-8'),
        canonical.encode('utf-8'), hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        _fail('unauthorized', 401)
    if connection.in_atomic_block:
        _fail('transaction_not_supported', 503)
    nonce_hash = hashlib.sha256((config['environment'] + nonce).encode('utf-8')).hexdigest()
    try:
        with transaction.atomic(durable=True):
            Nonce.objects.filter(expires_at__lt=timezone.now()).delete()
            Nonce.objects.create(
                nonce_hash=nonce_hash,
                expires_at=timezone.now() + timedelta(seconds=601),
            )
    except IntegrityError:
        _fail('replay', 409)
    return config['actor']


def _uuid(value):
    if not isinstance(value, str) or len(value) not in (32, 36):
        _fail()
    try:
        return uuid.UUID(value)
    except ValueError:
        _fail()


def _source_key():
    return f'{service.SPREADSHEET_ID}:{service.SHEET_ID}'


def _cursor():
    return ImportState.objects.filter(pk=_source_key()).values_list('next_row', flat=True).first() or 2


def _import(payload):
    start, end = payload.get('start_row'), payload.get('next_row')
    rows = payload.get('applications')
    if (payload.get('spreadsheet_id') != service.SPREADSHEET_ID
            or payload.get('sheet_id') != service.SHEET_ID or payload.get('headers') != HEADERS
            or type(start) is not int or type(end) is not int
            or not 2 <= start <= end <= min(start + 200, 9223372036854775807)
            or not isinstance(rows, list) or len(rows) > 200):
        _fail('invalid_page')
    try:
        cleaned = [service._validate_row(row, start, end) for row in rows]
    except service.ApplicationError:
        raise GasError('invalid_page') from None
    if len({row['source_key'] for row in cleaned}) != len(cleaned):
        _fail('invalid_page')
    with transaction.atomic():
        state, _ = ImportState.objects.get_or_create(source_key=_source_key())
        # CAS protects cursor and every inserted row, including on SQLite.
        current = ImportState.objects.get(pk=state.pk).next_row
        if start < current and end <= current:
            return {'ok': True, 'next_row': current, 'imported': 0}
        if start != current:
            _fail('cursor_conflict', 409)
        if ImportState.objects.filter(pk=state.pk, next_row=start).update(next_row=end) != 1:
            _fail('cursor_conflict', 409)
        imported = 0
        for row in cleaned:
            values = dict(row)
            key = values.pop('source_key')
            _, created = Application.objects.get_or_create(source_key=key, defaults=values)
            imported += int(created)
    return {'ok': True, 'next_row': end, 'imported': imported}


def _eligible(application, actor):
    user = application.created_user
    if (application.status != Application.Status.APPROVED
            or application.reviewed_by_id != actor.pk or user is None or not user.is_active
            or not user.has_usable_password()):
        _fail('application_not_eligible', 409)


def _token_valid(application):
    if not application.invitation_token or not default_token_generator.check_token(
        application.created_user, application.invitation_token,
    ):
        _fail('invitation_expired', 409)
    service._public_origin()


def _oauth_markers(application):
    return bool(application.draft_google_sub or application.draft_remote_attempted
                or application.draft_id or application.draft_message_id
                or application.draft_mailbox_email or application.draft_attempt_id
                or application.draft_state == Application.DraftState.CREATING)


def _pinned(job, application, config):
    if (job.backend != 'gas' or job.environment != config['environment']
            or job.actor_id != config['actor'].pk or job.mailbox != config['mailbox']
            or application.draft_google_sub
            or (application.draft_mailbox_email and application.draft_mailbox_email != job.mailbox)
            or (application.draft_message_id and application.draft_message_id != job.message_id)
            or (application.draft_attempt_id and application.draft_attempt_id != job.attempt_id)):
        _fail('ownership_conflict', 409)


def queue_draft(pk, actor):
    """Pin a GAS job without generating a token or authorizing remote creation."""
    config = _actor_configuration(actor)
    with transaction.atomic():
        application = service._get_application(pk, lock=True)
        _eligible(application, config['actor'])
        job = Job.objects.filter(application=application).first()
        if job:
            _pinned(job, application, config)
            return application
        _token_valid(application)
        if _oauth_markers(application):
            _fail('backend_conflict', 409)
        Job.objects.create(
            application=application, actor=config['actor'], environment=config['environment'],
            mailbox=config['mailbox'],
            message_id=f'<nexus-application-{application.pk}@customer-applications.invalid>',
        )
        application.draft_state = Application.DraftState.PENDING
        application.draft_error = ''
        application.save(update_fields=['draft_state', 'draft_error'])
        return application


def _claim(payload, config):
    excluded = payload.get('exclude_ids', [])
    if not isinstance(excluded, list) or len(excluded) > 20:
        _fail()
    excluded = [_uuid(value) for value in excluded]
    jobs = Job.objects.filter(
        actor=config['actor'], environment=config['environment'], mailbox=config['mailbox'],
    ).exclude(state=Job.State.CREATED).exclude(application_id__in=excluded).filter(
        Q(creation_authorized=True) | Q(state=Job.State.PENDING),
    )
    if 'application_id' in payload:
        jobs = jobs.filter(application_id=_uuid(payload['application_id']))
    ids = list(jobs.order_by(F('last_claimed_at').asc(nulls_first=True), 'created_at').values_list(
        'application_id', flat=True,
    )[:10])
    for pk in ids:
        with transaction.atomic():
            application = service._get_application(pk, lock=True)
            job = Job.objects.select_for_update().get(application=application)
            if job.state == Job.State.CREATED:
                continue
            try:
                _eligible(application, config['actor'])
                _pinned(job, application, config)
            except GasError:
                continue
            allow_create = not job.creation_authorized and job.state == Job.State.PENDING
            result = {
                'application_id': str(application.pk), 'attempt_id': str(job.attempt_id),
                'mailbox': job.mailbox, 'message_id': job.message_id, 'allow_create': False,
            }
            if allow_create:
                if _oauth_markers(application):
                    continue
                try:
                    _token_valid(application)
                    invitation = service.build_invitation_payload(application)
                    if (not isinstance(invitation, dict) or set(invitation) != {'to', 'subject', 'body'}
                            or invitation['to'] != application.email
                            or any(not isinstance(invitation[name], str) or not invitation[name]
                                   for name in ('to', 'subject', 'body'))):
                        _fail('invalid_invitation', 409)
                except service.ApplicationError:
                    job.state = Job.State.UNCERTAIN
                    job.last_claimed_at = timezone.now()
                    job.save(update_fields=['state', 'last_claimed_at'])
                    continue
                # Conditional authorization is a second guard on databases without row locks.
                if Job.objects.filter(pk=job.pk, creation_authorized=False, state='pending').update(
                    creation_authorized=True, state=Job.State.ATTEMPTED,
                ) != 1:
                    continue
                job.creation_authorized = True
                job.state = Job.State.ATTEMPTED
                job.attempted_at = timezone.now()
                application.draft_remote_attempted = True
                application.draft_message_id = job.message_id
                application.draft_mailbox_email = job.mailbox
                application.draft_attempt_id = job.attempt_id
                application.draft_state = Application.DraftState.CREATING
                application.draft_started_at = job.attempted_at
                application.draft_error = ''
                application.save(update_fields=[
                    'draft_remote_attempted', 'draft_message_id', 'draft_mailbox_email',
                    'draft_attempt_id', 'draft_state', 'draft_started_at', 'draft_error',
                ])
                result.update(invitation)
                result['allow_create'] = True
            else:
                if not job.creation_authorized:
                    continue
                # Recovery never needs a live token and never exposes a new body.
                result['to'] = application.email
            job.last_claimed_at = timezone.now()
            job.save(update_fields=['last_claimed_at', 'attempted_at', 'state'])
            return {'ok': True, 'job': result}
    return {'ok': True, 'job': None}


def _ack(payload, config):
    pk, attempt = _uuid(payload.get('application_id')), _uuid(payload.get('attempt_id'))
    status, draft_id = payload.get('status'), payload.get('draft_id', '')
    if (status not in ('created', 'uncertain') or not isinstance(draft_id, str)
            or (status == 'created' and not re.fullmatch(r'[A-Za-z0-9_-]{1,255}', draft_id))
            or (status == 'uncertain' and draft_id)):
        _fail('invalid_ack')
    with transaction.atomic():
        application = service._get_application(pk, lock=True)
        job = Job.objects.select_for_update().filter(application=application).first()
        if job is None:
            _fail('stale_attempt', 409)
        _eligible(application, config['actor'])
        _pinned(job, application, config)
        if (not job.creation_authorized or job.attempt_id != attempt
                or application.draft_attempt_id != job.attempt_id
                or not application.draft_remote_attempted
                or application.draft_message_id != job.message_id
                or application.draft_mailbox_email != job.mailbox):
            _fail('stale_attempt', 409)
        if job.state == Job.State.CREATED:
            if status == 'created' and draft_id != application.draft_id:
                _fail('ack_conflict', 409)
            return {'ok': True, 'application_id': str(pk), 'status': 'created'}
        if application.draft_id:
            _fail('ack_conflict', 409)
        if status == 'created':
            application.draft_id = draft_id
            application.draft_state = Application.DraftState.CREATED
            application.draft_error = ''
            job.state = Job.State.CREATED
            job.completed_at = timezone.now()
        else:
            application.draft_state = Application.DraftState.FAILED
            application.draft_error = _UNCERTAIN
            job.state = Job.State.UNCERTAIN
        application.save(update_fields=['draft_id', 'draft_state', 'draft_error'])
        job.save(update_fields=['state', 'completed_at'])
    return {'ok': True, 'application_id': str(pk), 'status': status}


def dispatch(action, payload, actor):
    """Dispatch a decoded JSON object after authorize(); no implicit auth bypass."""
    config = _actor_configuration(actor)
    if not isinstance(payload, dict) or not isinstance(action, str):
        _fail()
    if action == 'ping':
        return {'ok': True, 'service': 'nexus-customer-application-gas-v1',
                'environment': config['environment'], 'next_row': _cursor(), 'mailbox': config['mailbox']}
    if action == 'import':
        return _import(payload)
    if action == 'claim':
        return _claim(payload, config)
    if action == 'ack':
        return _ack(payload, config)
    _fail()