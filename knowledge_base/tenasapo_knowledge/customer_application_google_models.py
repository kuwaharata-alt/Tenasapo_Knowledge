"""顧客申請専用の暗号化資格情報。Driveの資格情報とは共有しない。"""

from django.conf import settings
from django.db import models


class CustomerApplicationGoogleState(models.Model):
    """全worker共通のOAuth state消費記録。秘密のstate自体は保存しない。"""

    state_hash = models.CharField(max_length=64, primary_key=True)
    expires_at = models.DateTimeField(db_index=True)


class CustomerApplicationGoogleConnection(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='customer_application_google_connections',
    )
    environment = models.CharField(max_length=16, choices=[
        ('development', '開発'), ('production', '本番'),
    ])
    email = models.EmailField(max_length=254)
    google_sub = models.CharField(max_length=255)
    client_id = models.CharField(max_length=255)
    encrypted_refresh_token = models.TextField(editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'tenasapo_knowledge'
        constraints = [models.UniqueConstraint(
            fields=['environment', 'actor'], name='customer_google_env_actor_unique',
        )]

    def __str__(self):
        return '顧客申請Google連携'

    def __repr__(self):
        return '<CustomerApplicationGoogleConnection>'