"""Shared at-most-once claims and completion, with no transaction around HTTP."""

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F, Q
from django.utils import timezone

from .chat_schedule import (
    ChatNotificationDelivery as Delivery, ChatNotificationSchedule as Schedule,
    current_chat_environment,
)
from .notifications import GoogleChatPayloadTooLarge
from .drive_oauth import get_drive_upload_config, upload_pdf_direct
from .report_drive_upload import get_upload_config, report_pdf_enabled, upload_report_pdf, validate_pdf


CLAIM_TIMEOUT = timedelta(minutes=30)
MAX_OVERDUE = timedelta(hours=48)


def protected_deliveries(schedule, kind):
    rows = Delivery.objects.filter(
        schedule__environment=schedule.environment,
        schedule__target_month=schedule.target_month, kind=kind,
    )
    if schedule.kind:
        # Also defend against an accidentally reset row or duplicate event at
        # the same historical timestamp, without blocking a genuinely new event.
        field = 'deadline_notice_at' if kind == 'deadline' else 'report_notice_at'
        rows = rows.filter(Q(schedule__event_id=schedule.event_id) |
                           Q(**{'schedule__kind': kind, 'schedule__' + field: schedule.notice_at}))
    return rows.exclude(schedule=schedule).filter(
        Q(attempts__gt=0) | ~Q(state='pending') | Q(claimed_at__isnull=False) | Q(sent_at__isnull=False))


def can_attempt(schedule, kind):
    """Read-only UI hint; claim_delivery always revalidates under a lock."""
    if (kind not in Delivery.Kind.values or not schedule.enabled
            or schedule.environment != current_chat_environment()
            or (schedule.kind and schedule.kind != kind)):
        return False
    newer = Schedule.objects.filter(environment=schedule.environment,
                                    target_month=schedule.target_month, revision__gt=schedule.revision)
    if schedule.kind:
        newer = newer.filter(event_id=schedule.event_id)
    if newer.exists():
        return False
    if protected_deliveries(schedule, kind).exists():
        return False
    existing = schedule.deliveries.filter(kind=kind).first()
    return existing is None or (existing.state == 'pending' and not existing.attempts
                               and existing.claimed_at is None and existing.sent_at is None)


def claim_delivery(schedule_id, kind, now, *, manual=False, expected_revision=None):
    """Lock revisions in writer order, then CAS a clean pending ledger row."""
    if kind not in Delivery.Kind.values:
        raise ValueError('invalid notification kind')
    if getattr(settings, 'GOOGLE_CHAT_SCHEDULED_SEND_ENABLED', False) is not True:
        return None
    if manual and (type(expected_revision) is not int or expected_revision < 1):
        return None
    field = 'deadline_notice_at' if kind == 'deadline' else 'report_notice_at'
    try:
        with transaction.atomic():
            candidate = Schedule.objects.filter(pk=schedule_id, environment=current_chat_environment()).first()
            if candidate is None:
                return None
            versions = list(Schedule.objects.select_for_update().filter(
                environment=candidate.environment, target_month=candidate.target_month).order_by('pk'))
            event_versions = [row for row in versions if not candidate.kind or row.event_id == candidate.event_id]
            schedule = max(event_versions, key=lambda row: row.revision)
            if schedule.pk != schedule_id or not schedule.enabled:
                return None
            if schedule.kind and schedule.kind != kind:
                return None
            if expected_revision is not None and schedule.revision != expected_revision:
                return None
            if not manual and getattr(schedule, field) > now:
                return None
            if protected_deliveries(schedule, kind).exists():
                return None
            delivery, _ = Delivery.objects.get_or_create(schedule=schedule, kind=kind)
            pending = Delivery.objects.filter(
                pk=delivery.pk, state='pending', attempts=0, claimed_at__isnull=True, sent_at__isnull=True)
            if not manual and now - getattr(schedule, field) > MAX_OVERDUE:
                pending.update(state='failed', last_error='通知日時から48時間を超過したため送信しません。', updated_at=now)
                return None
            if not pending.update(state='sending', claimed_at=now, attempts=F('attempts') + 1,
                                  last_error='', updated_at=now):
                return None
            delivery.refresh_from_db()
            delivery.schedule = schedule  # Immutable claimed snapshot for message generation.
            return delivery
    except (IntegrityError, OperationalError):
        # A concurrent writer/SQLite lock is not permission to retry a send.
        return None


def finish_delivery(delivery, state, error=''):
    """A stale worker cannot overwrite unknown or disable a newly edited revision."""
    now = timezone.now()
    with transaction.atomic():
        versions = list(Schedule.objects.select_for_update().filter(
            environment=delivery.schedule.environment,
            target_month=delivery.schedule.target_month).order_by('pk'))
        values = dict(state=state, last_error=error, updated_at=now)
        if state == 'sent':
            values['sent_at'] = now
        changed = Delivery.objects.filter(pk=delivery.pk, state='sending',
                                          claimed_at=delivery.claimed_at).update(**values)
        if not changed:
            return 'unknown'
        event_versions = [row for row in versions if not delivery.schedule.kind
                  or row.event_id == delivery.schedule.event_id]
        latest = max(event_versions, key=lambda row: row.revision, default=None)
        if state == 'sent' and latest and latest.pk == delivery.schedule_id and latest.revision == delivery.schedule.revision:
            sent_kinds = set(Delivery.objects.filter(schedule=latest, state='sent').values_list('kind', flat=True))
            if latest.kind or set(Delivery.Kind.values) <= sent_kinds:
                Schedule.objects.filter(pk=latest.pk, revision=latest.revision, enabled=True).update(
                    enabled=False, updated_at=now)
    return state


def send_claimed_delivery(delivery, now, *, target_url, deadline_builder, report_builder, sender):
    """Dependencies are explicit so existing command patch points keep working."""
    try:
        target_url()
        message = (deadline_builder(delivery.schedule, now) if delivery.kind == 'deadline'
                   else report_builder(delivery.schedule, now))
        pdf = None
        if delivery.kind == 'report' and report_pdf_enabled():
            from .scheduled_report_pdf import build_scheduled_report_pdf

            backend = getattr(settings, 'GOOGLE_DRIVE_REPORT_BACKEND', 'gas')
            if backend not in ('gas', 'oauth'):
                raise ValueError('invalid report backend')
            config = (get_drive_upload_config(delivery.schedule.updated_by)
                      if backend == 'oauth' else get_upload_config())
            pdf = build_scheduled_report_pdf(delivery.schedule, now)
            validate_pdf(pdf)
    except GoogleChatPayloadTooLarge:
        return finish_delivery(delivery, 'failed', '通知のサイズ上限を超過したため送信しません。')
    except Exception:
        return finish_delivery(delivery, 'failed', '送信前の設定・データ確認に失敗しました。')
    try:
        if pdf is not None:
            schedule = delivery.schedule
            filename = (f'Nexus_月次レポート_{schedule.target_month:%Y-%m}_'
                        f'{schedule.environment}_{schedule.event_id.hex}_r{schedule.revision}.pdf')
            pdf_url = (upload_pdf_direct(pdf, filename, config) if backend == 'oauth'
                       else upload_report_pdf(pdf, filename, config))
            message = report_builder(schedule, now, pdf_url=pdf_url)
        result = sender(message)
        if not isinstance(result, dict) or result.get('confirmed') is not True:
            raise ValueError('unconfirmed result')
    except Exception:
        return finish_delivery(delivery, 'unknown', '送信・PDF保存の結果を確認できません。送信先とDriveを確認し、手動で判断してください。')
    return finish_delivery(delivery, 'sent')