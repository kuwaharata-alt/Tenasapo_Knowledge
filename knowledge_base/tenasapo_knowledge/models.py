from django.db import models
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from calendar import monthrange
from datetime import timedelta
import os


INLINE_PREVIEWABLE_EXTENSIONS = {
    '.pdf',
    '.png',
    '.jpg',
    '.jpeg',
    '.gif',
    '.webp',
    '.svg',
    '.txt',
    '.csv',
    '.json',
    '.md',
}


def can_inline_preview_file(file_name: str) -> bool:
    extension = os.path.splitext((file_name or '').lower())[1]
    return extension in INLINE_PREVIEWABLE_EXTENSIONS


from .storage import HybridGoogleDriveStorage
google_drive_storage = HybridGoogleDriveStorage()


def project_document_upload_path(instance, filename):
    safe_project_number = (instance.project_number or '').strip()
    safe_customer_name = (instance.customer_name or '').strip()
    
    # 【案件番号】顧客名 フォルダ名を構成する
    folder_part = f"【{safe_project_number}】{safe_customer_name}"
    if not safe_project_number and not safe_customer_name:
        folder_part = "未特定案件"
    elif not safe_project_number:
        folder_part = safe_customer_name
    elif not safe_customer_name:
        folder_part = f"【{safe_project_number}】"
        
    return f"{folder_part}/Nexusドキュメント/{filename}"


def project_document_revision_upload_path(instance, filename):
    doc = instance.document
    safe_project_number = (doc.project_number or '').strip()
    safe_customer_name = (doc.customer_name or '').strip()
    
    folder_part = f"【{safe_project_number}】{safe_customer_name}"
    if not safe_project_number and not safe_customer_name:
        folder_part = "未特定案件"
    elif not safe_project_number:
        folder_part = safe_customer_name
    elif not safe_customer_name:
        folder_part = f"【{safe_project_number}】"
        
    return f"{folder_part}/Nexusドキュメント/履歴/{filename}"


def default_expires_on():
    today = timezone.localdate()
    year = today.year
    month = today.month + 6
    if month > 12:
        year += (month - 1) // 12
        month = ((month - 1) % 12) + 1
    day = min(today.day, monthrange(year, month)[1])
    target_date = today.replace(year=year, month=month, day=day)
    if target_date.weekday() == 5:
        return target_date - timedelta(days=1)
    if target_date.weekday() == 6:
        return target_date - timedelta(days=2)
    return target_date


class Customer(models.Model):
    name = models.CharField('顧客名', max_length=120, unique=True)
    users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='accessible_customers',
        verbose_name='閲覧可能ユーザー',
    )
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = '顧客'
        verbose_name_plural = '顧客'
        ordering = ['name']

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    USER_TYPE_CUSTOMER = 'customer'
    USER_TYPE_SYSTENA = 'systena'
    USER_TYPE_CHOICES = (
        (USER_TYPE_CUSTOMER, 'カスタマー'),
        (USER_TYPE_SYSTENA, 'システナ'),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='knowledge_profile',
        verbose_name='ユーザー',
    )
    uid = models.CharField(
        'ユーザーID',
        max_length=6,
        blank=True,
        null=True,
        unique=True,
        help_text='数字6桁',
    )
    display_name = models.CharField('表示名', max_length=150, blank=True)
    company_name = models.CharField('会社名', max_length=120)
    user_type = models.CharField(
        'ユーザー区分',
        max_length=20,
        choices=USER_TYPE_CHOICES,
        default=USER_TYPE_CUSTOMER,
    )
    email_addresses = models.TextField('メールアドレス（複数）', blank=True)
    department = models.CharField('所属部署', max_length=255, blank=True)
    group = models.CharField('グループ', max_length=255, blank=True)
    position = models.CharField('役職', max_length=255, blank=True)
    note = models.TextField('備考', blank=True)
    skip_login_lp = models.BooleanField('ログイン後LPを表示しない', default=False)
    google_first_login_done = models.BooleanField(
        'Google初回ログイン登録完了',
        default=True,
        help_text='Google認証で初回ログインした際の初回登録フォーム完了フラグ。False=フォーム表示が必要。',
    )
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'ユーザープロフィール'
        verbose_name_plural = 'ユーザープロフィール'
        ordering = ['user__username']

    def __str__(self):
        return f'{self.display_name or self.user.username} ({self.company_name})'


class FAQCategory(models.Model):
    parent_name = models.CharField('大カテゴリ', max_length=120)
    middle_name = models.CharField('中カテゴリ', max_length=120, blank=True, default='')
    child_name = models.CharField('小カテゴリ', max_length=120)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'FAQカテゴリ'
        verbose_name_plural = 'FAQカテゴリ'
        ordering = ['parent_name', 'middle_name', 'child_name']
        constraints = [
            models.UniqueConstraint(
                fields=['parent_name', 'middle_name', 'child_name'],
                name='unique_faq_category_triplet',
            ),
        ]

    @property
    def full_name(self):
        if self.middle_name:
            return f'{self.parent_name}/{self.middle_name}/{self.child_name}'
        return f'{self.parent_name}/{self.child_name}'

    def __str__(self):
        return self.full_name


class FAQParentCategorySetting(models.Model):
    name = models.CharField('大カテゴリ名', max_length=120, unique=True)
    visible_to_customer = models.BooleanField('カスタマーユーザーに表示', default=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'FAQ大カテゴリ設定'
        verbose_name_plural = 'FAQ大カテゴリ設定'
        ordering = ['name']

    def __str__(self):
        return self.name


class KnowledgeArticle(models.Model):
    title = models.CharField('タイトル', max_length=200)
    target_os = models.CharField('対象OS', max_length=120, blank=True)
    category = models.CharField('カテゴリ', max_length=180, blank=True)
    tags = models.CharField('タグ', max_length=300, blank=True)
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='articles',
        verbose_name='顧客',
    )
    summary = models.TextField('概要', blank=True)
    body = models.TextField('本文')
    is_published = models.BooleanField('公開', default=True)
    is_approved = models.BooleanField('承認済み', default=True)
    standard_contract_only = models.BooleanField('テナサポStandard契約者限定', default=False)
    visible_to_customer = models.BooleanField('カスタマーユーザー向け表示', default=True)
    visible_to_systena = models.BooleanField('システナユーザー向け表示', default=True)
    answer_view_count = models.PositiveIntegerField('回答表示回数', default=0)
    published_at = models.DateTimeField('公開日時', default=timezone.now)
    source_published_at = models.DateField('ソース公開日', null=True, blank=True)
    expires_on = models.DateField('掲載期限', null=True, blank=True, default=default_expires_on)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_knowledge_articles',
        verbose_name='作成者',
    )
    created_by_name = models.CharField('投稿者名', max_length=150, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_knowledge_articles',
        verbose_name='承認者',
    )
    approved_by_name = models.CharField('承認者名', max_length=150, blank=True)
    ai_review = models.TextField('AIレビュー', blank=True)
    remand_reason = models.TextField('差戻し理由', blank=True)
    reference_links = models.JSONField('参考リンク', default=list, blank=True, help_text='参照用のURLを保存するリスト')
    management_code = models.CharField('管理番号', max_length=10, unique=True, blank=True, null=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'ナレッジ記事'
        verbose_name_plural = 'ナレッジ記事'
        ordering = ['-published_at', '-created_at']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.management_code:
            prefix = 'FQ'
            last = KnowledgeArticle.objects.filter(
                management_code__startswith=prefix
            ).order_by('management_code').last()
            if last and last.management_code:
                try:
                    num = int(last.management_code[len(prefix):]) + 1
                except ValueError:
                    num = 1
            else:
                num = 1
            self.management_code = f'{prefix}{num:05d}'
        super().save(*args, **kwargs)

    def get_related_articles(self, limit=5, approved_only=True):
        """タグ一致（2個以上）で関連記事を取得
        approved_only=True: 承認済みのみ（カスタマー向け）
        approved_only=False: 未承認も含む（システナ/管理者向け）
        """
        from django.db.models import Q
        from django.conf import settings as _settings
        from django.utils import timezone
        
        # 除外するID（自身）
        exclude_ids = [self.id]
        
        # 有効期限フィルター（expires_on がない、または今日以降）
        today = timezone.localdate()
        active_filter = Q(expires_on__isnull=True) | Q(expires_on__gte=today)
        
        # 対象記事（公開済み・有効期限内・カスタマー向け公開）
        all_articles = KnowledgeArticle.objects.filter(
            is_published=True,
            visible_to_customer=True,
        ).filter(active_filter).exclude(id__in=exclude_ids)
        
        # 承認済みフィルター（カスタマー向けのみ）
        if approved_only:
            faq_approval_enabled = getattr(_settings, 'FAQ_APPROVAL_ENABLED', False)
            if faq_approval_enabled:
                all_articles = all_articles.filter(is_approved=True)
        
        own_tags = set(self.parsed_tags)
        if len(own_tags) < 2:
            return []

        scored_articles = []
        for candidate in all_articles:
            candidate_tags = set(candidate.parsed_tags)
            matched_tags = own_tags & candidate_tags
            if len(matched_tags) < 2:
                continue
            scored_articles.append((len(matched_tags), candidate.published_at, candidate.created_at, candidate))

        scored_articles.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [item[3] for item in scored_articles[:limit]]
    
    @staticmethod
    def parse_tags(tags_text):
        tags = []
        seen = set()
        for raw_tag in (tags_text or '').replace('、', ',').split(','):
            tag = raw_tag.strip().lower()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)
        return tags

    @property
    def parsed_tags(self):
        return self.parse_tags(self.tags)

    def get_all_related_articles(self):
        """システナ/管理者向け: 未承認も含む関連記事を取得"""
        return self.get_related_articles(approved_only=False)


class KnowledgeArticleImageAttachment(models.Model):
    article = models.ForeignKey(
        KnowledgeArticle,
        on_delete=models.CASCADE,
        related_name='images',
        verbose_name='ナレッジ記事',
    )
    file = models.FileField('画像ファイル', upload_to='knowledge_attachments/%Y/%m/')
    display_name = models.CharField('表示名', max_length=200, blank=True)
    uploaded_at = models.DateTimeField('アップロード日時', auto_now_add=True)

    class Meta:
        verbose_name = 'ナレッジ記事画像'
        verbose_name_plural = 'ナレッジ記事画像'
        ordering = ['uploaded_at', 'id']

    def __str__(self):
        return f'{self.article.title} - {self.display_name or self.file.name}'

    @property
    def download_url(self):
        return reverse('knowledge_file_download', kwargs={'kind': 'article-image', 'pk': self.pk})


class TipsArticle(models.Model):
    title = models.CharField('タイトル', max_length=200)
    target_os = models.CharField('対象OS', max_length=120, blank=True)
    category = models.CharField('カテゴリ', max_length=180, blank=True)
    tags = models.CharField('タグ', max_length=300, blank=True)
    body = models.TextField('内容')
    pdf_file = models.FileField('PDFファイル', upload_to='tips_attachments/%Y/%m/', blank=True)
    is_published = models.BooleanField('公開', default=True)
    is_approved = models.BooleanField('承認済み', default=True)
    standard_contract_only = models.BooleanField('テナサポStandard契約者限定', default=False)
    visible_to_customer = models.BooleanField('カスタマーユーザー向け表示', default=True)
    visible_to_systena = models.BooleanField('システナユーザー向け表示', default=True)
    view_count = models.PositiveIntegerField('閲覧回数', default=0)
    published_at = models.DateTimeField('公開日時', default=timezone.now)
    source_published_at = models.DateField('ソース公開日', null=True, blank=True)
    expires_on = models.DateField('掲載期限', null=True, blank=True, default=default_expires_on)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_tips_articles',
        verbose_name='作成者',
    )
    created_by_name = models.CharField('投稿者名', max_length=150, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_tips_articles',
        verbose_name='承認者',
    )
    approved_by_name = models.CharField('承認者名', max_length=150, blank=True)
    ai_review = models.TextField('AIレビュー', blank=True)
    remand_reason = models.TextField('差戻し理由', blank=True)
    reference_links = models.JSONField('参考リンク', default=list, blank=True, help_text='参照用のURLを保存するリスト')
    management_code = models.CharField('管理番号', max_length=10, unique=True, blank=True, null=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'Tips'
        verbose_name_plural = 'Tips'
        ordering = ['-published_at', '-created_at']

    def __str__(self):
        return self.title

    @property
    def pdf_download_url(self):
        if not self.pdf_file:
            return ''
        return reverse('knowledge_file_download', kwargs={'kind': 'tip-pdf', 'pk': self.pk})

    def save(self, *args, **kwargs):
        if not self.management_code:
            prefix = 'TP'
            last = TipsArticle.objects.filter(
                management_code__startswith=prefix
            ).order_by('management_code').last()
            if last and last.management_code:
                try:
                    num = int(last.management_code[len(prefix):]) + 1
                except ValueError:
                    num = 1
            else:
                num = 1
            self.management_code = f'{prefix}{num:05d}'
        super().save(*args, **kwargs)

    def get_related_articles(self, limit=5, approved_only=True):
        """タグ一致（2個以上）で関連記事を取得
        approved_only=True: 承認済みのみ（カスタマー向け）
        approved_only=False: 未承認も含む（システナ/管理者向け）
        """
        from django.db.models import Q
        from django.conf import settings as _settings
        from django.utils import timezone
        
        # 除外するID（自身）
        exclude_ids = [self.id]
        
        # 有効期限フィルター（expires_on がない、または今日以降）
        today = timezone.localdate()
        active_filter = Q(expires_on__isnull=True) | Q(expires_on__gte=today)
        
        # 対象記事（公開済み・有効期限内・カスタマー向け公開）
        all_articles = TipsArticle.objects.filter(
            is_published=True,
            visible_to_customer=True,
        ).filter(active_filter).exclude(id__in=exclude_ids)
        
        # 承認済みフィルター（カスタマー向けのみ）
        if approved_only:
            faq_approval_enabled = getattr(_settings, 'FAQ_APPROVAL_ENABLED', False)
            if faq_approval_enabled:
                all_articles = all_articles.filter(is_approved=True)
        
        own_tags = set(self.parsed_tags)
        if len(own_tags) < 2:
            return []

        scored_articles = []
        for candidate in all_articles:
            candidate_tags = set(candidate.parsed_tags)
            matched_tags = own_tags & candidate_tags
            if len(matched_tags) < 2:
                continue
            scored_articles.append((len(matched_tags), candidate.published_at, candidate.created_at, candidate))

        scored_articles.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [item[3] for item in scored_articles[:limit]]
    
    @staticmethod
    def parse_tags(tags_text):
        tags = []
        seen = set()
        for raw_tag in (tags_text or '').replace('、', ',').split(','):
            tag = raw_tag.strip().lower()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)
        return tags

    @property
    def parsed_tags(self):
        return self.parse_tags(self.tags)

    def get_all_related_articles(self):
        """システナ/管理者向け: 未承認も含む関連記事を取得"""
        return self.get_related_articles(approved_only=False)


class TipsImageAttachment(models.Model):
    tip = models.ForeignKey(
        TipsArticle,
        on_delete=models.CASCADE,
        related_name='images',
        verbose_name='Tips',
    )
    file = models.FileField('画像ファイル', upload_to='tips_attachments/%Y/%m/')
    display_name = models.CharField('表示名', max_length=200, blank=True)
    uploaded_at = models.DateTimeField('アップロード日時', auto_now_add=True)

    class Meta:
        verbose_name = 'Tips画像'
        verbose_name_plural = 'Tips画像'
        ordering = ['uploaded_at', 'id']

    def __str__(self):
        return self.display_name or self.file.name

    @property
    def download_url(self):
        return reverse('knowledge_file_download', kwargs={'kind': 'tip-image', 'pk': self.pk})


class TipsAttachment(models.Model):
    tip = models.ForeignKey(
        TipsArticle,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name='Tips',
    )
    file = models.FileField('ファイル', upload_to='tips_attachments/%Y/%m/')
    display_name = models.CharField('表示名', max_length=200, blank=True)
    uploaded_at = models.DateTimeField('アップロード日時', auto_now_add=True)

    class Meta:
        verbose_name = 'Tips添付ファイル'
        verbose_name_plural = 'Tips添付ファイル'
        ordering = ['uploaded_at', 'id']

    def __str__(self):
        return self.display_name or self.file.name

    @property
    def download_url(self):
        return reverse('knowledge_file_download', kwargs={'kind': 'tip-attachment', 'pk': self.pk})

    @property
    def can_inline_preview(self):
        return can_inline_preview_file(getattr(self.file, 'name', ''))


class ConvenienceFeature(models.Model):
    TYPE_SHORTCUT = 'shortcut'
    TYPE_COMMAND = 'command'
    TYPE_CHOICES = (
        (TYPE_SHORTCUT, 'ショートカット'),
        (TYPE_COMMAND, 'コマンド'),
    )

    USAGE_FREQUENCY_CHOICES = (
        ('1', '1'),
        ('2', '2'),
        ('3', '3'),
        ('4', '4'),
        ('5', '5'),
    )

    reference_type = models.CharField('種別', max_length=20, choices=TYPE_CHOICES, default=TYPE_SHORTCUT)
    category = models.CharField('カテゴリ', max_length=120)
    middle_category = models.CharField('中カテゴリ', max_length=120, blank=True, default='')
    usage_frequency = models.CharField('使用頻度', max_length=1, choices=USAGE_FREQUENCY_CHOICES, default='3')
    shortcut_key = models.CharField('ショートカットキー', max_length=120)
    display_text = models.CharField('内容', max_length=200)
    note = models.TextField('備考', blank=True)
    image = models.FileField('画像', upload_to='manuals/%Y/%m/', blank=True)
    management_code = models.CharField('管理番号', max_length=10, unique=True, blank=True, null=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'クイックリファレンス'
        verbose_name_plural = 'クイックリファレンス'
        ordering = ['reference_type', 'category', 'middle_category', 'display_text', 'id']

    def __str__(self):
        return f'{self.category} - {self.display_text}'

    def save(self, *args, **kwargs):
        if not self.management_code:
            prefix = 'QR'
            last = ConvenienceFeature.objects.filter(
                management_code__startswith=prefix
            ).order_by('management_code').last()
            if last and last.management_code:
                try:
                    num = int(last.management_code[len(prefix):]) + 1
                except ValueError:
                    num = 1
            else:
                num = 1
            self.management_code = f'{prefix}{num:05d}'
        super().save(*args, **kwargs)


class ConvenienceFavorite(models.Model):
    feature = models.ForeignKey(
        ConvenienceFeature,
        on_delete=models.CASCADE,
        related_name='favorites',
        verbose_name='クイックリファレンス',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='convenience_favorites',
        verbose_name='ユーザー',
    )
    created_at = models.DateTimeField('作成日時', auto_now_add=True)

    class Meta:
        verbose_name = 'クイックリファレンスお気に入り'
        verbose_name_plural = 'クイックリファレンスお気に入り'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['feature', 'user'],
                name='unique_convenience_favorite_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.feature_id} - {self.user_id}'


class ConvenienceCategory(models.Model):
    reference_type = models.CharField('大カテゴリ', max_length=20, choices=ConvenienceFeature.TYPE_CHOICES)
    category = models.CharField('中カテゴリ', max_length=120)
    middle_category = models.CharField('小カテゴリ', max_length=120, blank=True, default='')
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'QRカテゴリ'
        verbose_name_plural = 'QRカテゴリ'
        ordering = ['reference_type', 'category', 'middle_category', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['reference_type', 'category', 'middle_category'],
                name='unique_qr_category_path',
            ),
        ]

    def __str__(self):
        parts = [self.reference_type, self.category]
        if self.middle_category:
            parts.append(self.middle_category)
        return ' / '.join(parts)


class RelatedTag(models.Model):
    name = models.CharField('タグ名', max_length=60, unique=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = '関連タグ'
        verbose_name_plural = '関連タグ'
        ordering = ['name']

    def __str__(self):
        return self.name


class ArticleAttachment(models.Model):
    PLACEMENT_ATTACHMENT = 'attachment'
    PLACEMENT_QUESTION = 'question'
    PLACEMENT_ANSWER = 'answer'
    PLACEMENT_CHOICES = (
        (PLACEMENT_ATTACHMENT, '添付'),
        (PLACEMENT_QUESTION, '質問画像'),
        (PLACEMENT_ANSWER, '回答画像'),
    )

    article = models.ForeignKey(
        KnowledgeArticle,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name='記事',
    )
    file = models.FileField('ファイル', upload_to='knowledge_attachments/%Y/%m/')
    placement = models.CharField(
        '表示位置',
        max_length=20,
        choices=PLACEMENT_CHOICES,
        default=PLACEMENT_ATTACHMENT,
    )
    display_name = models.CharField('表示名', max_length=200, blank=True)
    uploaded_at = models.DateTimeField('アップロード日時', auto_now_add=True)

    class Meta:
        verbose_name = '添付ファイル'
        verbose_name_plural = '添付ファイル'
        ordering = ['display_name', 'file']

    def __str__(self):
        return self.display_name or self.file.name

    @property
    def download_url(self):
        return reverse('knowledge_file_download', kwargs={'kind': 'article-attachment', 'pk': self.pk})

    @property
    def can_inline_preview(self):
        return can_inline_preview_file(getattr(self.file, 'name', ''))


class Manual(models.Model):
    title = models.CharField('タイトル', max_length=200)
    description = models.TextField('説明', blank=True)
    pdf_file = models.FileField('PDFファイル', upload_to='manuals/%Y/%m/')
    is_published = models.BooleanField('公開', default=True)
    order = models.PositiveIntegerField('表示順', default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_manuals',
        verbose_name='作成者',
    )
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'マニュアル'
        verbose_name_plural = 'マニュアル'
        ordering = ['order', '-created_at']

    def __str__(self):
        return self.title


class ProjectDocument(models.Model):
    DOCUMENT_TYPE_PROCEDURE = 'procedure'
    DOCUMENT_TYPE_HEARING = 'hearing_sheet'
    DOCUMENT_TYPE_PARAMETER = 'parameter_sheet'
    DOCUMENT_TYPE_CHOICES = (
        (DOCUMENT_TYPE_PROCEDURE, '手順書'),
        (DOCUMENT_TYPE_HEARING, 'ヒアリングシート'),
        (DOCUMENT_TYPE_PARAMETER, 'パラメータシート'),
    )

    project_number = models.CharField('案件番号', max_length=60)
    customer_name = models.CharField('顧客名', max_length=200, default='')
    product_name = models.CharField('製品名', max_length=150, blank=True, default='')
    product_version = models.CharField('バージョン', max_length=80, blank=True, default='')
    title = models.CharField('ドキュメントタイトル', max_length=200)
    category = models.CharField('カテゴリ', max_length=180)
    document_type = models.CharField('分類', max_length=30, choices=DOCUMENT_TYPE_CHOICES)
    file_office = models.FileField('Officeドキュメント', storage=google_drive_storage, upload_to=project_document_upload_path, blank=True, null=True)
    file_pdf = models.FileField('PDFドキュメント', storage=google_drive_storage, upload_to=project_document_upload_path, blank=True, null=True)
    is_published = models.BooleanField('公開', default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_project_documents',
        verbose_name='アップロード者',
    )
    created_by_name = models.CharField('アップロード者名', max_length=150, blank=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'ドキュメント保管'
        verbose_name_plural = 'ドキュメント保管'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'【{self.project_number}】{self.customer_name} - {self.title}'

    @property
    def download_office_url(self):
        if self.file_office:
            return reverse('knowledge_file_download', kwargs={'kind': 'project-document-office', 'pk': self.pk})
        return ''

    @property
    def download_pdf_url(self):
        if self.file_pdf:
            return reverse('knowledge_file_download', kwargs={'kind': 'project-document-pdf', 'pk': self.pk})
        return ''


class ProjectDocumentRevisionHistory(models.Model):
    document = models.ForeignKey(
        ProjectDocument,
        on_delete=models.CASCADE,
        related_name='revisions',
        verbose_name='対象ドキュメント',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='project_document_revisions',
        verbose_name='更新者',
    )
    updated_by_name = models.CharField('更新者名', max_length=150)
    updated_at = models.DateTimeField('更新日時', auto_now_add=True)
    revision_note = models.TextField('更新内容')
    file_office = models.FileField('Officeドキュメント', storage=google_drive_storage, upload_to=project_document_revision_upload_path, blank=True, null=True)
    file_pdf = models.FileField('PDFドキュメント', storage=google_drive_storage, upload_to=project_document_revision_upload_path, blank=True, null=True)

    class Meta:
        verbose_name = 'プロジェクトドキュメント更新履歴'
        verbose_name_plural = 'プロジェクトドキュメント更新履歴'
        ordering = ['-updated_at', '-id']

    def __str__(self):
        return f'{self.updated_at:%Y-%m-%d} {self.updated_by_name} - {self.revision_note[:20]}'

    @property
    def download_office_url(self):
        if self.file_office:
            return reverse('knowledge_file_download', kwargs={'kind': 'project-document-revision-office', 'pk': self.pk})
        return ''

    @property
    def download_pdf_url(self):
        if self.file_pdf:
            return reverse('knowledge_file_download', kwargs={'kind': 'project-document-revision-pdf', 'pk': self.pk})
        return ''


class LoginHistory(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='login_histories',
        verbose_name='ユーザー',
    )
    username = models.CharField('ユーザー名', max_length=150)
    auth_provider = models.CharField('認証プロバイダ', max_length=50, blank=True, default='')
    auth_account_email = models.CharField('認証アカウントメール', max_length=255, blank=True, default='')
    auth_account_uid = models.CharField('認証アカウントUID', max_length=255, blank=True, default='')
    ip_address = models.CharField('IPアドレス', max_length=64, blank=True)
    user_agent = models.TextField('User-Agent', blank=True)
    logged_in_at = models.DateTimeField('ログイン日時', auto_now_add=True)
    logged_out_at = models.DateTimeField('ログアウト日時', null=True, blank=True)

    class Meta:
        verbose_name = 'ログイン履歴'
        verbose_name_plural = 'ログイン履歴'
        ordering = ['-logged_in_at']

    def __str__(self):
        return f'{self.username} - {self.logged_in_at:%Y-%m-%d %H:%M:%S}'


class ViewHistory(models.Model):
    login_history = models.ForeignKey(
        LoginHistory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='view_histories',
        verbose_name='ログイン履歴',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='view_histories',
        verbose_name='ユーザー',
    )
    username = models.CharField('ユーザー名', max_length=150)
    page_name = models.CharField('ページ名', max_length=200)
    path = models.CharField('パス', max_length=255)
    search_query = models.CharField('検索キーワード', max_length=200, blank=True)
    parent_category = models.CharField('大カテゴリ', max_length=120, blank=True)
    category = models.CharField('小カテゴリ', max_length=120, blank=True)
    ip_address = models.CharField('IPアドレス', max_length=64, blank=True)
    user_agent = models.TextField('User-Agent', blank=True)
    viewed_at = models.DateTimeField('閲覧日時', auto_now_add=True)

    class Meta:
        verbose_name = '閲覧履歴'
        verbose_name_plural = '閲覧履歴'
        ordering = ['-viewed_at']

    def __str__(self):
        return f'{self.username} - {self.page_name} - {self.viewed_at:%Y-%m-%d %H:%M:%S}'


class RevisionHistory(models.Model):
    CATEGORY_NEW_FEATURE = 'new_feature'
    CATEGORY_UPDATE = 'update'
    CATEGORY_DELETE = 'delete'
    CATEGORY_BUG_FIX = 'bug_fix'
    CATEGORY_CHOICES = (
        (CATEGORY_NEW_FEATURE, '新規機能追加'),
        (CATEGORY_UPDATE, '更新/改修'),
        (CATEGORY_BUG_FIX, '不具合修正'),
        (CATEGORY_DELETE, '削除'),
    )

    updated_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='revision_histories',
        verbose_name='更新者ユーザー',
    )
    updated_by_name = models.CharField('更新者', max_length=150)
    update_date = models.DateTimeField('更新日', auto_now_add=True)
    category = models.CharField('カテゴリ', max_length=20, choices=CATEGORY_CHOICES)
    title = models.CharField('タイトル', max_length=200, blank=True)
    update_content = models.TextField('更新内容', blank=True)

    class Meta:
        verbose_name = '更新履歴'
        verbose_name_plural = '更新履歴'
        ordering = ['-update_date', '-id']

    def __str__(self):
        category_label = dict(self.CATEGORY_CHOICES).get(self.category, self.category)
        return f'{self.update_date:%Y-%m-%d} {category_label} {self.title}'.strip()


class ArticleGood(models.Model):
    article = models.ForeignKey(
        KnowledgeArticle,
        on_delete=models.CASCADE,
        related_name='goods',
        verbose_name='記事',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='article_goods',
        verbose_name='ユーザー',
    )
    created_at = models.DateTimeField('作成日時', auto_now_add=True)

    class Meta:
        verbose_name = 'FAQグッド'
        verbose_name_plural = 'FAQグッド'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['article', 'user'],
                name='unique_article_good_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.article_id} - {self.user_id}'


class TipsGood(models.Model):
    tip = models.ForeignKey(
        TipsArticle,
        on_delete=models.CASCADE,
        related_name='goods',
        verbose_name='Tips',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tips_goods',
        verbose_name='ユーザー',
    )
    created_at = models.DateTimeField('作成日時', auto_now_add=True)

    class Meta:
        verbose_name = 'Tipsグッド'
        verbose_name_plural = 'Tipsグッド'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tip', 'user'],
                name='unique_tip_good_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.tip_id} - {self.user_id}'


class ArticleFavorite(models.Model):
    article = models.ForeignKey(
        KnowledgeArticle,
        on_delete=models.CASCADE,
        related_name='favorites',
        verbose_name='記事',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='article_favorites',
        verbose_name='ユーザー',
    )
    created_at = models.DateTimeField('作成日時', auto_now_add=True)

    class Meta:
        verbose_name = 'FAQお気に入り'
        verbose_name_plural = 'FAQお気に入り'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['article', 'user'],
                name='unique_article_favorite_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.article_id} - {self.user_id}'


class TipsFavorite(models.Model):
    tip = models.ForeignKey(
        TipsArticle,
        on_delete=models.CASCADE,
        related_name='favorites',
        verbose_name='Tips',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tips_favorites',
        verbose_name='ユーザー',
    )
    created_at = models.DateTimeField('作成日時', auto_now_add=True)

    class Meta:
        verbose_name = 'Tipsお気に入り'
        verbose_name_plural = 'Tipsお気に入り'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tip', 'user'],
                name='unique_tip_favorite_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.tip_id} - {self.user_id}'
