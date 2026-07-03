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
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework_simplejwt.views import (
    TokenRefreshView,
    TokenVerifyView,
)
from accounts.views import CustomTokenObtainPairView, QRLoginView, GuardCredentialLoginView

urlpatterns = [
    path('admin/',                      admin.site.urls),

    # JWT Auth
    path('api/auth/login/',             CustomTokenObtainPairView.as_view(),  name='token_obtain'),
    path('api/auth/qr-login/',          QRLoginView.as_view(),                name='qr_login'),
    path('api/auth/guard-login/',       GuardCredentialLoginView.as_view(),   name='guard_login'),
    path('api/auth/refresh/',           TokenRefreshView.as_view(),           name='token_refresh'),
    path('api/auth/verify/',            TokenVerifyView.as_view(),            name='token_verify'),

    # Apps
    path('api/accounts/',               include('accounts.urls')),
    path('api/vehicles/',               include('vehicles.urls')),
    path('api/scan/',                   include('scanning.urls')),
    path('api/violations/',             include('violations.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)