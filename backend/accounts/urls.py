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
    path('audit-logs/stats/',           views.AuditLogStatsView.as_view(),    name='audit-log-stats'),
    path('dashboard/stats/',            views.DashboardStatsView.as_view(),    name='dashboard-stats'),
    path('password-reset/request/',     views.PasswordResetRequestView.as_view(),  name='password-reset-request'),
    path('password-reset/confirm/',     views.PasswordResetConfirmView.as_view(),  name='password-reset-confirm'),
]