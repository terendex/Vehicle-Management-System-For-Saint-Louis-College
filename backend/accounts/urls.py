from django.urls import path
from . import views

urlpatterns = [
    path('register/',                   views.RegisterView.as_view(),          name='register'),
    path('me/',                         views.MeView.as_view(),                name='me'),
    path('users/',                      views.UserListView.as_view(),          name='user-list'),
    path('users/<int:pk>/',             views.UserDetailView.as_view(),        name='user-detail'),
    path('users/<int:pk>/update/',      views.UserUpdateView.as_view(),        name='user-update'),
    path('users/<int:pk>/delete/',      views.UserDeleteView.as_view(),        name='user-delete'),
    path('users/<int:pk>/toggle-status/', views.UserToggleStatusView.as_view(), name='user-toggle-status'),
    path('replace-admin/',              views.AdminReplaceView.as_view(),      name='replace-admin'),
]