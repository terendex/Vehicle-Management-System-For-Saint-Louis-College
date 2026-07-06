from django.urls import path
from . import views

urlpatterns = [
    path('register/',                   views.RegisterView.as_view(),          name='register'),
    path('me/',                         views.MeView.as_view(),                name='me'),
    path('me/registration/',            views.MyRegistrationView.as_view(),    name='my-registration'),
    path('change-password/',            views.ChangePasswordView.as_view(),    name='change-password'),
    path('users/',                      views.UserListView.as_view(),          name='user-list'),
    path('users/<int:pk>/',             views.UserDetailView.as_view(),        name='user-detail'),
    path('users/<int:pk>/update/',      views.UserUpdateView.as_view(),        name='user-update'),
    path('users/<int:pk>/delete/',      views.UserDeleteView.as_view(),        name='user-delete'),
    path('users/<int:pk>/toggle-status/', views.UserToggleStatusView.as_view(), name='user-toggle-status'),
    path('replace-admin/',              views.AdminReplaceView.as_view(),      name='replace-admin'),
    path('audit-logs/',                 views.AuditLogListView.as_view(),       name='audit-log-list'),
    path('audit-logs/export/',          views.AuditLogExportView.as_view(),    name='audit-log-export'),
    path('audit-logs/clear/',           views.AuditLogClearView.as_view(),     name='audit-log-clear'),
    path('audit-logs/stats/',           views.AuditLogStatsView.as_view(),    name='audit-log-stats'),
    path('dashboard/stats/',            views.DashboardStatsView.as_view(),    name='dashboard-stats'),
    path('password-reset/request/',     views.PasswordResetRequestView.as_view(),  name='password-reset-request'),
    path('password-reset/confirm/',     views.PasswordResetConfirmView.as_view(),  name='password-reset-confirm'),
    path('users/<int:pk>/qr/',          views.GuardQRView.as_view(),               name='guard-qr'),
    path('users/<int:pk>/regenerate-qr/', views.RegenerateGuardQRView.as_view(),   name='guard-regenerate-qr'),
    path('admin/create-guard/',         views.AdminCreateGuardView.as_view(),      name='admin-create-guard'),
    path('admin/create-owner/',         views.AdminCreateOwnerView.as_view(),      name='admin-create-owner'),
    path('notifications/',              views.NotificationListView.as_view(),      name='notification-list'),
    path('notifications/mark-read/',    views.NotificationMarkReadView.as_view(),  name='notification-mark-read'),
]