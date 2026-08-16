from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('', views.ViolationViewSet, basename='violation')

urlpatterns = [
    # Must precede the router: the router's detail route would otherwise
    # swallow '<pk>/evidence/'.
    path('<int:pk>/evidence/', views.ViolationEvidenceView.as_view(), name='violation-evidence'),
    path('my/', views.MyViolationsView.as_view(), name='my-violations'),
    path('guard/', views.GuardViolationsView.as_view(), name='guard-violations'),
    path('confiscated/', views.ConfiscatedAccountsView.as_view(), name='confiscated-accounts'),
    path('confiscated/<int:pk>/lift/', views.LiftConfiscationView.as_view(), name='confiscation-lift'),
    path('confiscated/<int:pk>/registration/', views.RegistrationPermissionView.as_view(), name='confiscation-registration'),
    path('report/excel/', views.ViolationReportExcelView.as_view(), name='violation-report-excel'),
    path('report/pdf/', views.ViolationReportPdfView.as_view(), name='violation-report-pdf'),
    path('', include(router.urls)),
]