from django.contrib import admin
from .models import (
    ArticleGood,
    ArticleAttachment,
    Customer,
    FAQCategory,
    KnowledgeArticle,
    LoginHistory,
    Manual,
    TipsGood,
    TipsArticle,
    UserProfile,
    ViewHistory,
)


class ArticleAttachmentInline(admin.TabularInline):
    model = ArticleAttachment
    extra = 1
    fields = ('file', 'display_name', 'uploaded_at')
    readonly_fields = ('uploaded_at',)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at', 'updated_at')
    search_fields = ('name', 'users__username', 'users__email')
    filter_horizontal = ('users',)


@admin.register(KnowledgeArticle)
class KnowledgeArticleAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'category',
        'customer',
        'is_published',
        'is_approved',
        'visible_to_customer',
        'visible_to_systena',
        'answer_view_count',
        'published_at',
        'expires_on',
        'created_by',
    )
    list_filter = (
        'is_published',
        'is_approved',
        'visible_to_customer',
        'visible_to_systena',
        'category',
        'customer',
        'published_at',
        'expires_on',
    )
    search_fields = ('title', 'category', 'summary', 'body', 'customer__name')
    autocomplete_fields = ('customer', 'created_by')
    inlines = (ArticleAttachmentInline,)
    date_hierarchy = 'published_at'

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        if not obj.created_by_name:
            obj.created_by_name = obj.created_by.get_username() if obj.created_by else request.user.get_username()
        super().save_model(request, obj, form, change)


@admin.register(ArticleAttachment)
class ArticleAttachmentAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'article', 'uploaded_at')
    list_filter = ('uploaded_at',)
    search_fields = ('display_name', 'file', 'article__title')


@admin.register(FAQCategory)
class FAQCategoryAdmin(admin.ModelAdmin):
    list_display = ('parent_name', 'child_name', 'created_at', 'updated_at')
    search_fields = ('parent_name', 'child_name')


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'company_name', 'user_type', 'created_at', 'updated_at')
    list_filter = ('user_type',)
    search_fields = ('user__username', 'company_name', 'email_addresses', 'note')


@admin.register(Manual)
class ManualAdmin(admin.ModelAdmin):
    list_display = ('title', 'is_published', 'order', 'created_by', 'created_at', 'updated_at')
    list_filter = ('is_published',)
    search_fields = ('title', 'description')
    ordering = ('order', '-created_at')

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(TipsArticle)
class TipsArticleAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'target_os',
        'category',
        'is_published',
        'is_approved',
        'visible_to_customer',
        'visible_to_systena',
        'published_at',
        'expires_on',
        'created_by',
    )
    list_filter = (
        'is_published',
        'is_approved',
        'visible_to_customer',
        'visible_to_systena',
        'category',
        'published_at',
        'expires_on',
    )
    search_fields = ('title', 'target_os', 'category', 'body')
    autocomplete_fields = ('created_by', 'approved_by')
    date_hierarchy = 'published_at'

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        if not obj.created_by_name:
            obj.created_by_name = obj.created_by.get_username() if obj.created_by else request.user.get_username()
        super().save_model(request, obj, form, change)


@admin.register(LoginHistory)
class LoginHistoryAdmin(admin.ModelAdmin):
    list_display = ('logged_in_at', 'logged_out_at', 'username', 'ip_address')
    list_filter = ('logged_in_at',)
    search_fields = ('username', 'ip_address', 'user_agent')
    readonly_fields = ('user', 'username', 'ip_address', 'user_agent', 'logged_in_at', 'logged_out_at')


@admin.register(ViewHistory)
class ViewHistoryAdmin(admin.ModelAdmin):
    list_display = (
        'viewed_at',
        'username',
        'login_history',
        'page_name',
        'search_query',
        'parent_category',
        'category',
        'path',
        'ip_address',
    )
    list_filter = ('viewed_at', 'page_name')
    search_fields = (
        'username',
        'page_name',
        'path',
        'search_query',
        'parent_category',
        'category',
        'ip_address',
        'user_agent',
    )
    readonly_fields = (
        'user',
        'login_history',
        'username',
        'page_name',
        'path',
        'search_query',
        'parent_category',
        'category',
        'ip_address',
        'user_agent',
        'viewed_at',
    )


@admin.register(ArticleGood)
class ArticleGoodAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'article', 'user')
    list_filter = ('created_at',)
    search_fields = ('article__title', 'user__username')


@admin.register(TipsGood)
class TipsGoodAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'tip', 'user')
    list_filter = ('created_at',)
    search_fields = ('tip__title', 'user__username')
