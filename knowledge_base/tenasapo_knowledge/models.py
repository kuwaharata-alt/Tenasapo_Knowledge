from django.db import models
from django.conf import settings
from django.utils import timezone
from calendar import monthrange
from datetime import timedelta


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
    note = models.TextField('備考', blank=True)
    skip_login_lp = models.BooleanField('ログイン後LPを表示しない', default=False)
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
    remand_reason = models.TextField('差し戻し理由', blank=True)
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


class TipsArticle(models.Model):
    title = models.CharField('タイトル', max_length=200)
    target_os = models.CharField('対象OS', max_length=120, blank=True)
    category = models.CharField('カテゴリ', max_length=180, blank=True)
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
    remand_reason = models.TextField('差し戻し理由', blank=True)
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
