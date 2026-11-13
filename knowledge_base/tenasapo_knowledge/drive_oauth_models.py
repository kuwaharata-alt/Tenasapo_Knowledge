"""Actor/environment-scoped Drive credentials; only ciphertext is persisted."""

from django.conf import settings
from django.db import models


class DriveOAuthConnection(models.Model):
    environment = models.CharField(max_length=16, choices=[
        ('development', '開発'), ('production', '本番'),
    ])
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name='drive_oauth_connections')
    google_sub = models.CharField(max_length=255)
    email = models.EmailField(max_length=254)
    client_id = models.CharField(max_length=255)
    encrypted_refresh_token = models.TextField(editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'tenasapo_knowledge'
        constraints = [models.UniqueConstraint(
            fields=['environment', 'actor'], name='drive_oauth_environment_actor_unique')]
