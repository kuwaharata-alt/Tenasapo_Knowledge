from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('tenasapo_knowledge', '0089_customer_application'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomerApplicationGoogleState',
            fields=[
                ('state_hash', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('expires_at', models.DateTimeField(db_index=True)),
            ],
        ),
        migrations.CreateModel(
            name='CustomerApplicationGoogleConnection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('environment', models.CharField(max_length=16, choices=[('development', '開発'), ('production', '本番')])),
                ('email', models.EmailField(max_length=254)),
                ('google_sub', models.CharField(max_length=255)),
                ('client_id', models.CharField(max_length=255)),
                ('encrypted_refresh_token', models.TextField(editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='customer_application_google_connections', to=settings.AUTH_USER_MODEL)),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('environment', 'actor'), name='customer_google_env_actor_unique')]},
        ),
        migrations.AddField(model_name='customerapplication', name='draft_google_sub', field=models.CharField(max_length=255, default='', blank=True)),
        migrations.AddField(model_name='customerapplication', name='draft_mailbox_email', field=models.EmailField(max_length=254, default='', blank=True)),
        migrations.AddField(model_name='customerapplication', name='draft_remote_attempted', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='customerapplication', name='draft_message_id', field=models.CharField(max_length=255, default='', blank=True)),
    ]