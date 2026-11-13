from django.db import migrations, models
import django.db.models.deletion
import tenasapo_knowledge.chat_schedule


class Migration(migrations.Migration):
    dependencies = [('tenasapo_knowledge', '0080_posting_quota_alpha_label')]

    operations = [
        migrations.CreateModel(
            name='ChatNotificationSchedule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('target_month', models.DateField(unique=True, validators=[tenasapo_knowledge.chat_schedule.validate_schedule_month], verbose_name='対象月')),
                ('deadline', models.DateField(verbose_name='投稿期限')),
                ('deadline_notice_at', models.DateTimeField(validators=[tenasapo_knowledge.chat_schedule.validate_aware_datetime], verbose_name='期限通知日時')),
                ('report_notice_at', models.DateTimeField(validators=[tenasapo_knowledge.chat_schedule.validate_aware_datetime], verbose_name='投稿状況通知日時')),
                ('enabled', models.BooleanField(default=False, verbose_name='有効')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['target_month'], 'constraints': [models.CheckConstraint(condition=models.Q(target_month__day=1), name='chat_schedule_month_start')]},
        ),
        migrations.CreateModel(
            name='ChatNotificationDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('deadline', '投稿期限'), ('report', '投稿状況')], max_length=16)),
                ('state', models.CharField(choices=[('pending', '未送信'), ('sending', '送信中'), ('sent', '送信済み'), ('failed', '失敗'), ('unknown', '送信結果不明')], default='pending', max_length=16)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('claimed_at', models.DateTimeField(blank=True, null=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.CharField(blank=True, default='', max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('schedule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='tenasapo_knowledge.chatnotificationschedule')),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('schedule', 'kind'), name='chat_delivery_schedule_kind')]},
        ),
    ]