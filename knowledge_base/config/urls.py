"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.static import serve
from tenasapo_knowledge import views as tk_views

urlpatterns = [
    path('', include('tenasapo_knowledge.urls')),
    path('manuals', tk_views.ManualListView.as_view(), name='manual_list_no_slash'),
    path('manuals/', tk_views.ManualListView.as_view(), name='manual_list_root'),
    path('manuals/create/', tk_views.ManualCreateView.as_view(), name='manual_create_root'),
    path('manuals/<int:pk>/', tk_views.ManualDetailView.as_view(), name='manual_detail_root'),
    path('manuals/<int:pk>/edit/', tk_views.ManualUpdateView.as_view(), name='manual_edit_root'),
    path('manuals/<int:pk>/delete/', tk_views.ManualDeleteView.as_view(), name='manual_delete_root'),
    path(
        'accounts/login/',
        tk_views.HomeRedirectLoginView.as_view(template_name='registration/login.html'),
        name='login',
    ),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('admin/', admin.site.urls),
]

# static / media をDEBUG問わず配信（waitress等の本番サーバー対応）
urlpatterns += [
    path('static/<path:path>', serve, {'document_root': settings.BASE_DIR / 'logo'}),
    path('media/<path:path>', serve, {'document_root': settings.MEDIA_ROOT}),
]
