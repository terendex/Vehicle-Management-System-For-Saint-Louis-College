from django.urls import path
from . import views

urlpatterns = [
    path('',                        views.ScanView.as_view(),          name='scan'),
    path('visitor-pass/',           views.VisitorPassView.as_view(),    name='visitor-pass'),
    path('visitor-pass/<int:pk>/',  views.ConfirmVisitorView.as_view(), name='confirm-visitor'),
    path('offices/',                views.OfficeListView.as_view(),     name='offices'),
]