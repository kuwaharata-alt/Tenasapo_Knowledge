from django.urls import path
from django.views.generic import RedirectView
from .views import (
    ArticleListView,
    ArticleAttachmentDeleteView,
    FAQCategoryCreateView,
    FAQCategoryUpdateView,
    KnowledgeArticleCreateView,
    KnowledgeArticleDeleteView,
    KnowledgeArticleUpdateView,
    ManualCreateView,
    ManualDeleteView,
    ManualDetailView,
    ManualListView,
    ManualUpdateView,
    UserCreateView,
    UserListView,
    UserPasswordResetView,
)

urlpatterns = [
    path('', ArticleListView.as_view(), name='article_list'),
    path('knowledge/create/', KnowledgeArticleCreateView.as_view(), name='article_create'),
    path('knowledge/<int:pk>/edit/', KnowledgeArticleUpdateView.as_view(), name='article_edit'),
    path('knowledge/<int:pk>/delete/', KnowledgeArticleDeleteView.as_view(), name='article_delete'),
    path(
        'attachments/<int:pk>/delete/',
        ArticleAttachmentDeleteView.as_view(),
        name='attachment_delete',
    ),
    path('categories/create/', FAQCategoryCreateView.as_view(), name='category_create'),
    path('categories/<int:pk>/edit/', FAQCategoryUpdateView.as_view(), name='category_edit'),
    path('users/', UserListView.as_view(), name='user_list'),
    path('users/create/', UserCreateView.as_view(), name='user_create'),
    path(
        'users/<int:pk>/reset-password/',
        UserPasswordResetView.as_view(),
        name='user_password_reset',
    ),
    # マニュアル
    path('manuals', RedirectView.as_view(pattern_name='manual_list', permanent=False)),
    path('manuals/', ManualListView.as_view(), name='manual_list'),
    path('manuals/<int:pk>/', ManualDetailView.as_view(), name='manual_detail'),
    path('manuals/create/', ManualCreateView.as_view(), name='manual_create'),
    path('manuals/<int:pk>/edit/', ManualUpdateView.as_view(), name='manual_edit'),
    path('manuals/<int:pk>/delete/', ManualDeleteView.as_view(), name='manual_delete'),
]
