"""At-most-once automatic attempts; uncertain outcomes require human review."""

from datetime import timedelta
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from tenasapo_knowledge.chat_schedule import JAPAN_TIMEZONE, current_chat_environment
from tenasapo_knowledge.models import ChatNotificationSchedule, ChatNotificationDelivery
from tenasapo_knowledge.notifications import get_google_chat_target_url, send_scheduled_google_chat_message
from tenasapo_knowledge.scheduled_chat_messages import build_deadline_message, build_report_message


CLAIM_TIMEOUT = timedelta(minutes=30)
MAX_OVERDUE = timedelta(hours=48)


def claim_delivery(schedule_id, kind, now):
    """Serialize on the schedule, then CAS pending -> sending (also for SQLite)."""
    if kind not in ChatNotificationDelivery.Kind.values:
        raise ValueError('invalid notification kind')
    field = 'deadline_notice_at' if kind == ChatNotificationDelivery.Kind.DEADLINE else 'report_notice_at'
    with transaction.atomic():
        schedule = ChatNotificationSchedule.objects.select_for_update().filter(
            pk=schedule_id, environment=current_chat_environment(), enabled=True, **{field + '__lte': now},
        ).first()
        if schedule is None:
            return None
        # Defense in depth: even an old revision edited outside save_schedule
        # must not cause another attempt for the same environment/month/kind.
        protected = ChatNotificationDelivery.objects.filter(
            schedule__environment=schedule.environment,
            schedule__target_month=schedule.target_month, kind=kind,
        ).exclude(schedule=schedule).filter(
            Q(attempts__gt=0) | ~Q(state='pending') | Q(claimed_at__isnull=False) | Q(sent_at__isnull=False))
        if protected.exists():
            return None
        delivery, _ = ChatNotificationDelivery.objects.get_or_create(schedule=schedule, kind=kind)
        pending = ChatNotificationDelivery.objects.filter(
            pk=delivery.pk, state='pending', attempts=0, claimed_at__isnull=True, sent_at__isnull=True)
        if now - getattr(schedule, field) > MAX_OVERDUE:
            pending.update(state='failed', last_error='通知日時から48時間を超過したため送信しません。', updated_at=now)
            return None
        changed = pending.update(state=ChatNotificationDelivery.State.SENDING, claimed_at=now,
                 attempts=F('attempts') + 1, last_error='', updated_at=now)
        if not changed:
            return None
        delivery.refresh_from_db()
        delivery.schedule = schedule  # Locked configuration snapshot, not a later lazy reload.
        return delivery


class Command(BaseCommand):
    help = '月別予約の期限・投稿一覧カードを通知。既定はdry-run、失敗・結果不明は自動再送しません。'

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument('--send', action='store_true', help='明示的に送信と配信台帳更新を許可')
        mode.add_argument('--dry-run', action='store_true', help='送信・DB更新・画像保存なし（既定）')
        parser.add_argument('--environment', choices=['development', 'production'], help='DEBUGとの一致確認（環境切替ではない）')
        parser.add_argument('--max-count', type=int, default=20, help='1回の最大試行件数（1〜100）')
        parser.add_argument('--json', action='store_true', dest='json_payload', help='dry-runで整形済みJSONを表示（実送信時は禁止）')

    def handle(self, *args, **options):
        environment = current_chat_environment()
        if options.get('environment') and options['environment'] != environment:
            raise CommandError('指定環境とDEBUGによる実行環境が一致しません。')
        limit = options.get('max_count', 20)
        if not 1 <= limit <= 100:
            raise CommandError('--max-countは1〜100で指定してください。')
        now = timezone.now()
        dry_run = not options.get('send', False)
        if not dry_run and getattr(settings, 'GOOGLE_CHAT_SCHEDULED_SEND_ENABLED', False) is not True:
            raise CommandError('予約送信は無効です。GOOGLE_CHAT_SCHEDULED_SEND_ENABLED=Trueが必要です。')
        if not dry_run and options.get('json_payload'):
            raise CommandError('--jsonはdry-run専用です。')
        self.stdout.write(f'environment={environment}; mode={"dry-run" if dry_run else "send"}')
        if not dry_run:
            ChatNotificationDelivery.objects.filter(schedule__environment=environment, state='sending').filter(
                Q(claimed_at__lte=now - CLAIM_TIMEOUT) | Q(claimed_at__isnull=True),
            ).update(state='unknown', last_error='送信処理が期限切れです。送信先を確認し、手動で判断してください。', updated_at=now)
        schedules = ChatNotificationSchedule.objects.filter(environment=environment, enabled=True).filter(
            Q(deadline_notice_at__lte=now) | Q(report_notice_at__lte=now),
        ).order_by('target_month', 'revision').prefetch_related('deliveries')
        errors = attempted = 0
        for schedule in schedules.iterator(chunk_size=50):
            for kind, at in (('deadline', schedule.deadline_notice_at), ('report', schedule.report_notice_at)):
                if at > now:
                    continue
                existing = next((d for d in schedule.deliveries.all() if d.kind == kind), None)
                if existing is not None and (existing.state != 'pending' or existing.attempts or existing.claimed_at or existing.sent_at):
                    self.stdout.write(f'skip {schedule.target_month:%Y-%m} {kind}: {existing.state}')
                    continue
                if attempted >= limit:
                    return self.finish(errors)
                if dry_run:
                    attempted += 1
                    self.stdout.write(f'[dry-run] {schedule.target_month:%Y-%m} {kind} {timezone.localtime(at, JAPAN_TIMEZONE):%Y/%m/%d %H:%M} JST')
                    if now - at > MAX_OVERDUE:
                        self.stdout.write('expired: 48時間超過、送信せずfailedにする予定')
                        continue
                    message = build_deadline_message(schedule) if kind == 'deadline' else build_report_message(schedule, now)
                    self.stdout.write(json.dumps(message, ensure_ascii=False, indent=2) if options.get('json_payload')
                                      else (message if isinstance(message, str) else message['text']))
                    continue
                delivery = claim_delivery(schedule.pk, kind, now)
                if delivery is None:
                    continue
                attempted += 1
                # Preflight failures are definitely unsent. Never record exception text/URLs.
                try:
                    get_google_chat_target_url()
                    message = (build_deadline_message(delivery.schedule) if kind == 'deadline'
                               else build_report_message(delivery.schedule, now))
                except Exception:
                    ChatNotificationDelivery.objects.filter(pk=delivery.pk, state='sending').update(
                        state='failed', last_error='送信前の設定・データ確認に失敗しました。', updated_at=timezone.now())
                    errors += 1
                    continue
                try:
                    result = send_scheduled_google_chat_message(message)
                    if not isinstance(result, dict) or result.get('confirmed') is not True:
                        raise ValueError('unconfirmed result')
                except Exception:
                    # A timeout/relay error can occur AFTER Chat accepted the message.
                    ChatNotificationDelivery.objects.filter(pk=delivery.pk, state='sending').update(
                        state='unknown', last_error='送信結果を確認できません。送信先を確認し、手動で判断してください。', updated_at=timezone.now())
                    errors += 1
                    continue
                finished = timezone.now()
                ChatNotificationDelivery.objects.filter(pk=delivery.pk, state='sending').update(
                    state='sent', sent_at=finished, last_error='', updated_at=finished)
                self.stdout.write(f'sent {schedule.target_month:%Y-%m} {kind}')
        self.finish(errors)

    def finish(self, errors):
        if errors:
            raise CommandError(f'{errors}件の通知が失敗または結果不明です。自動再送は行いません。')