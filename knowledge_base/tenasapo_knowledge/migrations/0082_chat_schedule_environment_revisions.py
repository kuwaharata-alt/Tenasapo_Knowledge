from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import tenasapo_knowledge.chat_schedule


def quarantine_legacy(apps, schema_editor):
    # Never infer the environment of old reservations from this migration host.
    apps.get_model('tenasapo_knowledge', 'ChatNotificationSchedule').objects.using(
        schema_editor.connection.alias).update(enabled=False, environment='unassigned')


class Migration(migrations.Migration):
    dependencies = [
        ('tenasapo_knowledge', '0081_chat_notification_schedule'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='chatnotificationschedule', name='environment',
            field=models.CharField('環境', max_length=16, default='unassigned', choices=[
                ('development', '開発'), ('production', '本番'), ('unassigned', '未割当（旧予約・送信対象外）')]),
        ),
        migrations.AddField(
            model_name='chatnotificationschedule', name='revision',
            field=models.PositiveIntegerField('予約版', default=1, editable=False),
        ),
        migrations.AddField(
            model_name='chatnotificationschedule', name='updated_by',
            field=models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    editable=False, related_name='+', on_delete=django.db.models.deletion.SET_NULL),
        ),
        migrations.AlterField(
            model_name='chatnotificationschedule', name='target_month',
            field=models.DateField('対象月', validators=[tenasapo_knowledge.chat_schedule.validate_schedule_month]),
        ),
        migrations.RunPython(quarantine_legacy, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='chatnotificationschedule',
            constraint=models.UniqueConstraint(fields=['environment', 'target_month', 'revision'], name='chat_schedule_env_month_rev'),
        ),
        migrations.AddConstraint(
            model_name='chatnotificationschedule',
            constraint=models.UniqueConstraint(fields=['environment', 'target_month'], condition=models.Q(enabled=True), name='chat_schedule_one_enabled'),
        ),
        migrations.AddConstraint(
            model_name='chatnotificationschedule',
            constraint=models.CheckConstraint(condition=models.Q(environment__in=['development', 'production', 'unassigned']), name='chat_schedule_valid_env'),
        ),
    ]