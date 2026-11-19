import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('tenasapo_knowledge', '0090_customer_application_google'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomerApplicationGASNonce',
            fields=[
                ('nonce_hash', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('expires_at', models.DateTimeField(db_index=True)),
            ],
        ),
        migrations.CreateModel(
            name='CustomerApplicationGASJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('backend', models.CharField(default='gas', editable=False, max_length=8)),
                ('environment', models.CharField(editable=False, max_length=16)),
                ('attempt_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('mailbox', models.EmailField(editable=False, max_length=254)),
                ('message_id', models.CharField(editable=False, max_length=255, unique=True)),
                ('creation_authorized', models.BooleanField(default=False, editable=False)),
                ('state', models.CharField(choices=[('pending', 'Pending'), ('attempted', 'Attempted'), ('created', 'Created'), ('uncertain', 'Uncertain')], default='pending', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_claimed_at', models.DateTimeField(blank=True, null=True)),
                ('attempted_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('application', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='gas_job', to='tenasapo_knowledge.customerapplication')),
            ],
            options={'constraints': [
                models.CheckConstraint(condition=models.Q(backend='gas'), name='customer_gas_backend'),
                models.CheckConstraint(condition=models.Q(environment__in=['development', 'production']), name='customer_gas_environment'),
                models.CheckConstraint(condition=models.Q(state__in=['pending', 'attempted', 'created', 'uncertain']), name='customer_gas_state'),
            ]},
        ),
    ]