from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('', views.ViolationViewSet, basename='violation')

urlpatterns = [
    path('my/', views.MyViolationsView.as_view(), name='my-violations'),
    path('guard/', views.GuardViolationsView.as_view(), name='guard-violations'),
    path('report/excel/', views.ViolationReportExcelView.as_view(), name='violation-report-excel'),
    path('report/pdf/', views.ViolationReportPdfView.as_view(), name='violation-report-pdf'),
    path('', include(router.urls)),
]