from django.urls import path
from . import twofa_api, views

urlpatterns = [
    path('register/',                   views.RegisterView.as_view(),          name='register'),
    path('me/',                         views.MeView.as_view(),                name='me'),
    path('me/registration/',            views.MyRegistrationView.as_view(),    name='my-registration'),
    path('me/plate-swap/',              views.MyPlateSwapView.as_view(),       name='my-plate-swap'),
    path('change-password/',            views.ChangePasswordView.as_view(),    name='change-password'),
    path('users/',                      views.UserListView.as_view(),          name='user-list'),
    path('users/<int:pk>/',             views.UserDetailView.as_view(),        name='user-detail'),
    path('users/<int:pk>/update/',      views.UserUpdateView.as_view(),        name='user-update'),
    path('users/<int:pk>/delete/',      views.UserDeleteView.as_view(),        name='user-delete'),
    path('users/<int:pk>/toggle-status/', views.UserToggleStatusView.as_view(), name='user-toggle-status'),
    path('users/<int:pk>/registration-pdf/', views.UserRegistrationPdfView.as_view(), name='user-registration-pdf'),
    path('replace-admin/',              views.AdminReplaceView.as_view(),      name='replace-admin'),
    path('audit-logs/',                 views.AuditLogListView.as_view(),       name='audit-log-list'),
    path('audit-logs/export/',          views.AuditLogExportView.as_view(),    name='audit-log-export'),
    path('audit-logs/export-pdf/',      views.AuditLogPdfExportView.as_view(), name='audit-log-export-pdf'),
    path('audit-logs/clear/',           views.AuditLogClearView.as_view(),     name='audit-log-clear'),
    path('audit-logs/stats/',           views.AuditLogStatsView.as_view(),    name='audit-log-stats'),
    path('dashboard/stats/',            views.DashboardStatsView.as_view(),    name='dashboard-stats'),
    path('system/backup/',              views.SystemBackupView.as_view(),      name='system-backup'),
    path('system/restore/',             views.SystemRestoreView.as_view(),     name='system-restore'),
    path('system/backups/',             views.SystemBackupListView.as_view(),  name='system-backup-list'),
    # <name> is a filename, so no slashes: the view still re-checks that it
    # resolves inside the backups directory before it opens anything.
    path('system/backups/<str:name>/',  views.SystemBackupFileView.as_view(),  name='system-backup-file'),
    path('password-reset/request/',     views.PasswordResetRequestView.as_view(),  name='password-reset-request'),
    path('password-reset/confirm/',     views.PasswordResetConfirmView.as_view(),  name='password-reset-confirm'),
    path('guard-qr-available/',         views.GuardQrAvailabilityView.as_view(),   name='guard-qr-available'),
    path('users/<int:pk>/qr/',          views.GuardQRView.as_view(),               name='guard-qr'),
    path('admin/create-guard/',         views.AdminCreateGuardView.as_view(),      name='admin-create-guard'),
    path('admin/create-owner/',         views.AdminCreateOwnerView.as_view(),      name='admin-create-owner'),
    path('notifications/',              views.NotificationListView.as_view(),      name='notification-list'),
    path('notifications/mark-read/',    views.NotificationMarkReadView.as_view(),  name='notification-mark-read'),
    path('notifications/clear/',        views.NotificationClearView.as_view(),     name='notification-clear'),

    # ── Two-factor authentication (Google Authenticator / TOTP) ─────────────
    # setup + confirm + verify are AllowAny by design: they run while a login
    # is paused, before any session token exists. Each one is gated by a signed
    # challenge that is only issued after the right password was given.
    path('2fa/setup/',        twofa_api.TwoFactorSetupView.as_view(),       name='twofa-setup'),
    path('2fa/confirm/',      twofa_api.TwoFactorConfirmView.as_view(),     name='twofa-confirm'),
    path('2fa/verify/',       twofa_api.TwoFactorVerifyView.as_view(),      name='twofa-verify'),
    path('2fa/step-up/',      twofa_api.TwoFactorStepUpView.as_view(),      name='twofa-step-up'),
    path('2fa/status/',       twofa_api.TwoFactorStatusView.as_view(),      name='twofa-status'),
    path('2fa/backup-codes/', twofa_api.TwoFactorBackupCodesView.as_view(), name='twofa-backup-codes'),
    path('users/<int:pk>/2fa/reset/', twofa_api.TwoFactorResetView.as_view(), name='twofa-reset'),
]