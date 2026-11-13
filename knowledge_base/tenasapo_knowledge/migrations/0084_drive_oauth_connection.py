from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('tenasapo_knowledge', '0083_independent_chat_reservations'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DriveOAuthConnection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('environment', models.CharField(max_length=16, choices=[
                    ('development', '開発'), ('production', '本番')])),
                ('google_sub', models.CharField(max_length=255)),
                ('email', models.EmailField(max_length=254)),
                ('client_id', models.CharField(max_length=255)),
                ('encrypted_refresh_token', models.TextField(editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name='drive_oauth_connections', to=settings.AUTH_USER_MODEL)),
            ],
            options={'constraints': [models.UniqueConstraint(
                fields=('environment', 'actor'), name='drive_oauth_environment_actor_unique')]},
        ),
    ]
