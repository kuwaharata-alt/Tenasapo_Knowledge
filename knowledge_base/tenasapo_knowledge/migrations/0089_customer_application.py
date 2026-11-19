import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.db.models.functions.text


class Migration(migrations.Migration):
    dependencies = [
        ('tenasapo_knowledge', '0088_terms_header_title'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomerApplicationImportState',
            fields=[
                ('source_key', models.CharField(max_length=255, primary_key=True, serialize=False)),
                ('next_row', models.PositiveBigIntegerField(default=2)),
            ],
            options={
                'constraints': [
                    models.CheckConstraint(condition=models.Q(next_row__gte=2), name='customer_app_cursor_gte_2'),
                ],
            },
        ),
        migrations.CreateModel(
            name='CustomerApplication',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_key', models.CharField(max_length=255, unique=True)),
                ('submitted_at', models.DateTimeField(verbose_name='申請日時')),
                ('applicant_email', models.EmailField(max_length=150, verbose_name='回答者メールアドレス')),
                ('email', models.EmailField(max_length=150, verbose_name='招待先メールアドレス')),
                ('company_name', models.CharField(max_length=120, verbose_name='会社名')),
                ('department', models.CharField(blank=True, max_length=255, verbose_name='部署名')),
                ('display_name', models.CharField(max_length=150, verbose_name='契約者氏名')),
                ('status', models.CharField(choices=[('pending', '未承認'), ('approved', '承認済み'), ('rejected', '却下')], default='pending', max_length=10)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('draft_id', models.CharField(blank=True, default='', max_length=255)),
                ('draft_error', models.TextField(blank=True, default='')),
                ('invitation_token', models.TextField(blank=True, default='')),
                ('draft_state', models.CharField(choices=[('pending', '未作成'), ('creating', '作成中'), ('created', '作成済み'), ('failed', '失敗')], default='pending', max_length=10)),
                ('draft_started_at', models.DateTimeField(blank=True, null=True)),
                ('draft_attempt_id', models.UUIDField(blank=True, editable=False, null=True)),
                ('created_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='customer_applications', to=settings.AUTH_USER_MODEL)),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_customer_applications', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-submitted_at', '-created_at'],
                'verbose_name': '顧客申請',
                'verbose_name_plural': '顧客申請',
                'constraints': [
                    models.UniqueConstraint(django.db.models.functions.text.Lower('email'), condition=models.Q(status='approved'), name='customer_app_approved_email_ci'),
                    models.CheckConstraint(condition=models.Q(status__in=['pending', 'approved', 'rejected']), name='customer_app_valid_status'),
                    models.CheckConstraint(condition=models.Q(draft_state__in=['pending', 'creating', 'created', 'failed']), name='customer_app_valid_draft_state'),
                ],
            },
        ),
    ]