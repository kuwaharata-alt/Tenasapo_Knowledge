"""Monthly Chat configuration and conservative, non-retrying delivery ledger."""

from datetime import datetime, time
import uuid
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, models, transaction
from django.utils import timezone


JAPAN_TIMEZONE = ZoneInfo('Asia/Tokyo')


def current_chat_environment():
    return 'development' if settings.DEBUG else 'production'


def notification_datetime(day, at=time(9, 0)):
    """UI date-only selections default to 09:00 JST, never server-local time."""
    return datetime.combine(day, at, tzinfo=JAPAN_TIMEZONE)


def validate_schedule_month(value):
    if value.day != 1:
        raise ValidationError('対象月は月初日を指定してください。')


def validate_aware_datetime(value):
    if timezone.is_naive(value):
        raise ValidationError('通知日時にはタイムゾーンを指定してください。')


class ChatNotificationSchedule(models.Model):
    class Kind(models.TextChoices):
        DEADLINE = 'deadline', '投稿期限'
        REPORT = 'report', '投稿レポート'

    class Environment(models.TextChoices):
        DEVELOPMENT = 'development', '開発'
        PRODUCTION = 'production', '本番'
        UNASSIGNED = 'unassigned', '未割当（旧予約・送信対象外）'

    environment = models.CharField('環境', max_length=16, choices=Environment.choices, default='unassigned')
    revision = models.PositiveIntegerField('予約版', default=1, editable=False)
    event_id = models.UUIDField(default=uuid.uuid4, editable=False)
    kind = models.CharField('通知種別', max_length=16, choices=Kind.choices, blank=True, default='')
    target_month = models.DateField('対象月', validators=[validate_schedule_month])
    deadline = models.DateField('投稿期限')
    deadline_notice_at = models.DateTimeField('期限通知日時', validators=[validate_aware_datetime])
    report_notice_at = models.DateTimeField('投稿状況通知日時', validators=[validate_aware_datetime])
    enabled = models.BooleanField('有効', default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+', editable=False)

    class Meta:
        app_label = 'tenasapo_knowledge'
        ordering = ['target_month']
        constraints = [
            models.CheckConstraint(condition=models.Q(target_month__day=1), name='chat_schedule_month_start'),
            models.UniqueConstraint(fields=['environment', 'target_month', 'revision'], name='chat_schedule_env_month_rev'),
            models.CheckConstraint(condition=models.Q(environment__in=['development', 'production', 'unassigned']), name='chat_schedule_valid_env'),
            # Blank kinds are retained only for the deprecated combined API.
            models.UniqueConstraint(fields=['environment', 'target_month'], condition=models.Q(enabled=True, kind=''), name='chat_schedule_one_enabled'),
            models.UniqueConstraint(fields=['event_id'], condition=models.Q(enabled=True), name='chat_event_one_enabled'),
            models.CheckConstraint(condition=models.Q(kind__in=['', 'deadline', 'report']), name='chat_schedule_valid_kind'),
        ]

    def __str__(self):
        return self.target_month.strftime('%Y-%m')

    @property
    def notice_at(self):
        return self.deadline_notice_at if self.kind == 'deadline' else self.report_notice_at

    def clean(self):
        super().clean()
        errors = {}
        for field in ('deadline_notice_at', 'report_notice_at'):
            value = getattr(self, field)
            if value is not None and timezone.is_naive(value):
                errors[field] = '通知日時にはタイムゾーンを指定してください。'
        if errors:
            raise ValidationError(errors)


class ChatNotificationDelivery(models.Model):
    class Kind(models.TextChoices):
        DEADLINE = 'deadline', '投稿期限'
        REPORT = 'report', '投稿状況'

    class State(models.TextChoices):
        PENDING = 'pending', '未送信'
        SENDING = 'sending', '送信中'
        SENT = 'sent', '送信済み'
        FAILED = 'failed', '失敗'
        UNKNOWN = 'unknown', '送信結果不明'

    schedule = models.ForeignKey(ChatNotificationSchedule, on_delete=models.CASCADE, related_name='deliveries')
    kind = models.CharField(max_length=16, choices=Kind.choices)
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    claimed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'tenasapo_knowledge'
        constraints = [models.UniqueConstraint(fields=['schedule', 'kind'], name='chat_delivery_schedule_kind')]


class ScheduleConflict(ValidationError):
    """The calendar was changed/claimed concurrently; reload, never retry blindly."""


def save_schedule(*, target_month, deadline, deadline_notice_at, report_notice_at,
                  enabled, actor, expected_revision=0, environment=None):
    """Create an immutable revision in the running environment and return it.

    expected_revision is the latest revision displayed by the caller (0 for a
    new month), including disabled revisions. All calendar writers must use this
    service. Attempted/terminal kinds are carried forward, never reset to pending.
    No HTTP, implicit environment adoption, or automatic conflict retries.
    """
    running = current_chat_environment()
    if environment is not None and environment != running:
        raise ValidationError('実行環境と予約環境が一致しません。')
    if actor is None or not getattr(actor, 'pk', None) or not actor.is_authenticated:
        raise ValidationError('保存者を指定してください。')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValidationError('予約版が不正です。')
    try:
        with transaction.atomic():
            versions = list(ChatNotificationSchedule.objects.select_for_update().filter(
                environment=running, target_month=target_month).order_by('pk'))
            if any(s.kind for s in versions):
                raise ScheduleConflict('独立予約は種類ごとの予約画面で変更してください。', code='conflict')
            latest = max((s.revision for s in versions), default=0)
            if expected_revision != latest:
                raise ScheduleConflict('予約が変更されています。再読込してください。', code='conflict')
            deliveries = list(ChatNotificationDelivery.objects.select_for_update().filter(
                schedule__in=versions).order_by('pk'))
            if any(d.state == 'sending' for d in deliveries):
                raise ScheduleConflict('送信処理中の予約は変更できません。', code='conflict')
            schedule = ChatNotificationSchedule(
                environment=running, revision=latest + 1, target_month=target_month,
                deadline=deadline, deadline_notice_at=deadline_notice_at,
                report_notice_at=report_notice_at, enabled=enabled, updated_by=actor)
            # Validate before disabling anything. The conditional unique constraint
            # is checked by the database after the old active revision is disabled.
            schedule.full_clean(validate_constraints=False)
            ChatNotificationSchedule.objects.filter(pk__in=[s.pk for s in versions], enabled=True).update(
                enabled=False, updated_at=timezone.now())
            schedule.save(force_insert=True)
            for kind in ChatNotificationDelivery.Kind.values:
                protected = next((d for d in reversed(deliveries) if d.kind == kind and (
                    d.attempts or d.state != 'pending' or d.claimed_at or d.sent_at)), None)
                ChatNotificationDelivery.objects.create(
                    schedule=schedule, kind=kind,
                    state=(protected.state if protected.state != 'pending' else 'unknown') if protected else 'pending',
                    attempts=protected.attempts if protected else 0,
                    claimed_at=protected.claimed_at if protected else None,
                    sent_at=protected.sent_at if protected else None,
                    last_error=protected.last_error if protected else '')
            return schedule
    except (IntegrityError, OperationalError):
        raise ScheduleConflict('予約の保存が競合しました。再読込してください。', code='conflict') from None


def save_reservation(*, target_month, kind, deadline, notice_at, enabled, actor,
                     schedule_id=None, expected_revision=0, environment=None):
    """Explicit new event or immutable revision of one event. Never reset a ledger.

    The monthly revision unique constraint serializes even concurrent first
    inserts on SQLite/PostgreSQL; a conflict is returned without an auto retry.
    All historical timestamps are reserved, including disabled/failed events.
    """
    running = current_chat_environment()
    if environment is not None and environment != running:
        raise ValidationError('実行環境と予約環境が一致しません。')
    if (actor is None or not getattr(actor, 'pk', None) or not actor.is_authenticated
            or not actor.is_staff):
        raise ValidationError('スタッフの保存者を指定してください。')
    if kind not in ChatNotificationSchedule.Kind.values:
        raise ValidationError('通知種別が不正です。')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValidationError('予約版が不正です。')
    try:
        with transaction.atomic():
            rows = list(ChatNotificationSchedule.objects.select_for_update().filter(
                environment=running, target_month=target_month).order_by('pk'))
            old = next((s for s in rows if s.pk == schedule_id and s.kind == kind), None)
            if schedule_id is not None and old is None:
                raise ScheduleConflict('対象の予約がありません。再読込してください。', code='conflict')
            versions = [s for s in rows if old and s.event_id == old.event_id]
            if old:
                latest = max(versions, key=lambda s: s.revision)
                if latest.pk != old.pk or old.revision != expected_revision:
                    raise ScheduleConflict('予約が変更されています。再読込してください。', code='conflict')
            elif expected_revision != 0:
                raise ScheduleConflict('新規予約の予約版が不正です。再読込してください。', code='conflict')
            # A legacy combined writer cannot coexist with the independent UI.
            if any(not s.kind for s in rows):
                raise ScheduleConflict('旧形式の予約があります。独立予約への移行が必要です。', code='conflict')
            deliveries = list(ChatNotificationDelivery.objects.select_for_update().filter(
                schedule__in=versions, kind=kind).order_by('pk'))
            protected = next((d for d in reversed(deliveries) if d.attempts or d.state != 'pending'
                              or d.claimed_at or d.sent_at), None)
            if any(d.state == 'sending' for d in deliveries):
                raise ScheduleConflict('送信処理中の予約は変更できません。', code='conflict')
            if protected and (enabled or notice_at != old.notice_at or deadline != old.deadline):
                raise ValidationError('試行済みの予約は変更・再有効化できません。別の通知日時で新規予約してください。')
            if any(s.kind == kind and s.notice_at == notice_at and
                   (old is None or s.event_id != old.event_id) for s in rows):
                raise ValidationError('同じ月・種類・通知日時の予約は既にあります。新しい通知日時を指定してください。')
            reservation = ChatNotificationSchedule(
                environment=running, target_month=target_month, kind=kind,
                event_id=old.event_id if old else uuid.uuid4(),
                revision=max((s.revision for s in rows), default=0) + 1,
                deadline=deadline, deadline_notice_at=notice_at, report_notice_at=notice_at,
                enabled=enabled, updated_by=actor)
            reservation.full_clean(validate_constraints=False)
            ChatNotificationSchedule.objects.filter(pk__in=[s.pk for s in versions], enabled=True).update(
                enabled=False, updated_at=timezone.now())
            reservation.save(force_insert=True)
            ChatNotificationDelivery.objects.create(
                schedule=reservation, kind=kind,
                state=('unknown' if protected.state == 'pending' else protected.state) if protected else 'pending',
                attempts=protected.attempts if protected else 0,
                claimed_at=protected.claimed_at if protected else None,
                sent_at=protected.sent_at if protected else None,
                last_error=protected.last_error if protected else '')
            return reservation
    except (IntegrityError, OperationalError):
        raise ScheduleConflict('予約の保存が競合しました。再読込してください。', code='conflict') from None