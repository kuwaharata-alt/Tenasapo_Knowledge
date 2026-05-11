from django.contrib import admin
from .models import ArticleAttachment, Customer, FAQCategory, KnowledgeArticle, UserProfile
from .models import ArticleAttachment, Customer, FAQCategory, KnowledgeArticle, Manual, UserProfile


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
    list_display = ('title', 'category', 'customer', 'is_published', 'published_at', 'created_by')
    list_filter = ('is_published', 'category', 'customer', 'published_at')
    search_fields = ('title', 'category', 'summary', 'body', 'customer__name')
    autocomplete_fields = ('customer', 'created_by')
    inlines = (ArticleAttachmentInline,)
    date_hierarchy = 'published_at'

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
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
    list_display = ('user', 'company_name', 'created_at', 'updated_at')
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
