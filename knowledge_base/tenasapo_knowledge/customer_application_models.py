"""顧客申請の永続状態。models.pyからインポートして登録する。"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class CustomerApplicationImportState(models.Model):
    """追記専用シートの次回開始行。原本の行削除・挿入・並べ替えは禁止。"""

    source_key = models.CharField(max_length=255, primary_key=True)
    next_row = models.PositiveBigIntegerField(default=2)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(next_row__gte=2), name='customer_app_cursor_gte_2',
            ),
        ]


class CustomerApplication(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', '未承認'
        APPROVED = 'approved', '承認済み'
        REJECTED = 'rejected', '却下'

    class DraftState(models.TextChoices):
        PENDING = 'pending', '未作成'
        CREATING = 'creating', '作成中'
        CREATED = 'created', '作成済み'
        FAILED = 'failed', '失敗'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_key = models.CharField(max_length=255, unique=True)
    submitted_at = models.DateTimeField('申請日時')
    applicant_email = models.EmailField('回答者メールアドレス', max_length=150)
    email = models.EmailField('招待先メールアドレス', max_length=150)
    company_name = models.CharField('会社名', max_length=120)
    department = models.CharField('部署名', max_length=255, blank=True)
    display_name = models.CharField('契約者氏名', max_length=150)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='customer_applications',
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reviewed_customer_applications',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    draft_id = models.CharField(max_length=255, blank=True, default='')
    draft_error = models.TextField(blank=True, default='')
    invitation_token = models.TextField(blank=True, default='')
    draft_state = models.CharField(
        max_length=10, choices=DraftState.choices, default=DraftState.PENDING,
    )
    draft_started_at = models.DateTimeField(null=True, blank=True)
    draft_attempt_id = models.UUIDField(null=True, blank=True, editable=False)
    draft_google_sub = models.CharField(max_length=255, blank=True, default='')
    draft_mailbox_email = models.EmailField(max_length=254, blank=True, default='')
    draft_remote_attempted = models.BooleanField(default=False)
    draft_message_id = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['-submitted_at', '-created_at']
        verbose_name = '顧客申請'
        verbose_name_plural = '顧客申請'
        constraints = [
            models.UniqueConstraint(
                Lower('email'), condition=models.Q(status='approved'),
                name='customer_app_approved_email_ci',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=['pending', 'approved', 'rejected']),
                name='customer_app_valid_status',
            ),
            models.CheckConstraint(
                condition=models.Q(draft_state__in=['pending', 'creating', 'created', 'failed']),
                name='customer_app_valid_draft_state',
            ),
        ]

    def __str__(self):
        return str(self.pk)