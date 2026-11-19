"""Durable replay protection and pinned, single-creation GAS draft jobs."""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class CustomerApplicationGASNonce(models.Model):
    nonce_hash = models.CharField(max_length=64, primary_key=True)
    expires_at = models.DateTimeField(db_index=True)


class CustomerApplicationGASJob(models.Model):
    class State(models.TextChoices):
        PENDING = 'pending', 'Pending'
        ATTEMPTED = 'attempted', 'Attempted'
        CREATED = 'created', 'Created'
        UNCERTAIN = 'uncertain', 'Uncertain'

    application = models.OneToOneField(
        'CustomerApplication', on_delete=models.CASCADE, related_name='gas_job',
    )
    backend = models.CharField(max_length=8, default='gas', editable=False)
    environment = models.CharField(max_length=16, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    attempt_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    mailbox = models.EmailField(max_length=254, editable=False)
    message_id = models.CharField(max_length=255, unique=True, editable=False)
    creation_authorized = models.BooleanField(default=False, editable=False)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    last_claimed_at = models.DateTimeField(null=True, blank=True)
    attempted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(backend='gas'), name='customer_gas_backend'),
            models.CheckConstraint(
                condition=models.Q(environment__in=['development', 'production']),
                name='customer_gas_environment',
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=['pending', 'attempted', 'created', 'uncertain']),
                name='customer_gas_state',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            old = type(self).objects.get(pk=self.pk)
            pinned = ('application_id', 'backend', 'environment', 'actor_id',
                      'attempt_id', 'mailbox', 'message_id')
            if (any(getattr(self, name) != getattr(old, name) for name in pinned)
                    or (old.creation_authorized and not self.creation_authorized)
                    or (old.state == self.State.CREATED and self.state != old.state)):
                raise ValidationError('GAS job ownership and creation authorization are immutable.')
        return super().save(*args, **kwargs)