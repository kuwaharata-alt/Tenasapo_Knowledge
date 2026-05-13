from django.db import models
from django.conf import settings
from django.utils import timezone


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
    company_name = models.CharField('会社名', max_length=120)
    user_type = models.CharField(
        'ユーザー区分',
        max_length=20,
        choices=USER_TYPE_CHOICES,
        default=USER_TYPE_CUSTOMER,
    )
    email_addresses = models.TextField('メールアドレス（複数）', blank=True)
    note = models.TextField('備考', blank=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'ユーザープロフィール'
        verbose_name_plural = 'ユーザープロフィール'
        ordering = ['user__username']

    def __str__(self):
        return f'{self.user.username} ({self.company_name})'


class FAQCategory(models.Model):
    parent_name = models.CharField('大カテゴリ', max_length=120)
    child_name = models.CharField('小カテゴリ', max_length=120)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'FAQカテゴリ'
        verbose_name_plural = 'FAQカテゴリ'
        ordering = ['parent_name', 'child_name']
        constraints = [
            models.UniqueConstraint(
                fields=['parent_name', 'child_name'],
                name='unique_faq_category_pair',
            ),
        ]

    @property
    def full_name(self):
        return f'{self.parent_name}/{self.child_name}'

    def __str__(self):
        return self.full_name


class KnowledgeArticle(models.Model):
    title = models.CharField('タイトル', max_length=200)
    category = models.CharField('カテゴリ', max_length=120, blank=True)
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
    visible_to_customer = models.BooleanField('カスタマーユーザー向け表示', default=True)
    visible_to_systena = models.BooleanField('システナユーザー向け表示', default=True)
    answer_view_count = models.PositiveIntegerField('回答表示回数', default=0)
    published_at = models.DateTimeField('公開日時', default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_knowledge_articles',
        verbose_name='作成者',
    )
    created_by_name = models.CharField('投稿者名', max_length=150, blank=True)
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'ナレッジ記事'
        verbose_name_plural = 'ナレッジ記事'
        ordering = ['-published_at', '-created_at']

    def __str__(self):
        return self.title


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
