"""Split combined schedules, retaining all delivery PKs and conservative guards.

Irreversible: collapsing independently delivered events would lose information.
Stop old schedulers while applying this migration and deploying the new code.
"""
import uuid

from django.db import migrations, models


def split_reservations(apps, schema_editor):
    Schedule = apps.get_model('tenasapo_knowledge', 'ChatNotificationSchedule')
    Delivery = apps.get_model('tenasapo_knowledge', 'ChatNotificationDelivery')
    alias = schema_editor.connection.alias
    schedules = Schedule.objects.using(alias)
    deliveries = Delivery.objects.using(alias)
    groups = list(schedules.values_list('environment', 'target_month').distinct())
    for environment, month in groups:
        rows = list(schedules.filter(environment=environment, target_month=month).order_by('revision'))
        highest = max(row.revision for row in rows)
        for kind in ('deadline', 'report'):
            event = uuid.uuid5(uuid.NAMESPACE_URL, f'nexus-chat/{environment}/{month}/{kind}')
            protected = deliveries.filter(schedule_id__in=[row.pk for row in rows], kind=kind).filter(
                models.Q(attempts__gt=0) | ~models.Q(state='pending') |
                models.Q(claimed_at__isnull=False) | models.Q(sent_at__isnull=False)).exists()
            for row in rows:
                active = row.enabled and not protected and environment != 'unassigned'
                if kind == 'deadline':
                    schedules.filter(pk=row.pk).update(kind=kind, event_id=event, enabled=active)
                else:
                    clone = schedules.create(
                        environment=environment, target_month=month, revision=highest + row.revision,
                        kind=kind, event_id=event, enabled=active, deadline=row.deadline,
                        deadline_notice_at=row.deadline_notice_at, report_notice_at=row.report_notice_at,
                        updated_by_id=row.updated_by_id)
                    schedules.filter(pk=clone.pk).update(created_at=row.created_at, updated_at=row.updated_at)
                    deliveries.filter(schedule_id=row.pk, kind=kind).update(schedule_id=clone.pk)


class Migration(migrations.Migration):
    dependencies = [('tenasapo_knowledge', '0082_chat_schedule_environment_revisions')]
    operations = [
        migrations.RemoveConstraint(model_name='chatnotificationschedule', name='chat_schedule_one_enabled'),
        migrations.AddField(model_name='chatnotificationschedule', name='event_id',
                            field=models.UUIDField(default=uuid.uuid4, editable=False)),
        migrations.AddField(model_name='chatnotificationschedule', name='kind',
                            field=models.CharField('通知種別', max_length=16, blank=True, default='',
                                                   choices=[('deadline', '投稿期限'), ('report', '投稿レポート')])),
        migrations.RunPython(split_reservations),
        migrations.AddConstraint(model_name='chatnotificationschedule', constraint=models.UniqueConstraint(
            fields=['environment', 'target_month'], condition=models.Q(enabled=True, kind=''), name='chat_schedule_one_enabled')),
        migrations.AddConstraint(model_name='chatnotificationschedule', constraint=models.UniqueConstraint(
            fields=['event_id'], condition=models.Q(enabled=True), name='chat_event_one_enabled')),
        migrations.AddConstraint(model_name='chatnotificationschedule', constraint=models.CheckConstraint(
            condition=models.Q(kind__in=['', 'deadline', 'report']), name='chat_schedule_valid_kind')),
    ]