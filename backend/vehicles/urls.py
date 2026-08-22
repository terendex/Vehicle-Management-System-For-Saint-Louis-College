from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('rules',           views.RuleConstraintViewSet, basename='rule-constraint')
router.register('reference-items', views.ReferenceItemViewSet,  basename='reference-item')
router.register('parking',         views.ParkingSpaceViewSet,   basename='parking-space')
router.register('parking-zones',   views.ParkingZoneViewSet,    basename='parking-zone')
router.register('cameras',         views.CameraViewSet,         basename='camera')
router.register('',                views.VehicleViewSet,        basename='vehicle')

urlpatterns = [
    # Public online registration (goes to PENDING for CDSO review)
    path('register/open/',           views.PublicOpenRegistrationView.as_view(),      name='open-registration'),
    path('register/status/',         views.RegistrationStatusView.as_view(),          name='registration-status'),
    path('register/schedule-slots/', views.ScheduleSlotsView.as_view(),               name='schedule-slots'),
    path('register/availability/',   views.RegistrationAvailabilityView.as_view(),    name='registration-availability'),
    path('register/documents/',      views.UploadRegistrationDocumentsView.as_view(), name='upload-registration-documents'),
    path('register/payment/',        views.RegistrationPaymentView.as_view(),        name='registration-payment'),
    # Legacy path - the already-built frontend bundle still posts here.
    path('register/license-image/',  views.UploadRegistrationDocumentsView.as_view(), name='upload-license-image'),

    # CDSO walk-in direct registration (auto-accepted, no pending)
    path('register/direct/', views.CdsoDirectRegisterView.as_view(), name='direct-registration'),

    # Reference lists (public, for registration form dropdowns)
    path('departments/', views.DepartmentListView.as_view(), name='department-list'),
    path('programs/',    views.ProgramListView.as_view(),    name='program-list'),

    # Parking availability (authenticated)
    path('parking-availability/', views.ParkingAvailabilityView.as_view(), name='parking-availability'),
    path('parking-zones/attribute-double-park/', views.AttributeDoubleParkingView.as_view(), name='attribute-double-park'),

    # Parking zone MJPEG stream (JWT via ?token= query param)
    path('parking-zones/<int:pk>/stream/', views.parking_stream_view, name='parking-stream'),

    # Registration management (Admin/CDSO)
    path('registrations/report/excel/',    views.RegistrationReportExcelView.as_view(),  name='registration-report-excel'),
    path('registrations/report/pdf/',       views.RegistrationReportPdfView.as_view(),    name='registration-report-pdf'),
    path('registrations/report/summary-pdf/', views.RegistrationSummaryReportPdfView.as_view(), name='registration-summary-report-pdf'),
    path('registrations/summary/',         views.RegistrationSummaryView.as_view(),      name='registration-summary'),
    path('registrations/pending/',         views.PendingRegistrationsListView.as_view(), name='list-pending-registrations'),
    path('registrations/<int:pk>/accept/', views.AcceptRegistrationView.as_view(),       name='accept-registration'),
    path('registrations/<int:pk>/reject/', views.RejectRegistrationView.as_view(),       name='reject-registration'),

    # System-wide settings (admin / CDSO)
    path('system-settings/', views.SystemSettingsView.as_view(), name='system-settings'),

    # Events (admin / CDSO)
    path('events/',           views.EventListCreateView.as_view(), name='event-list'),
    path('events/<int:pk>/',  views.EventDetailView.as_view(),     name='event-detail'),

    # Parking notices (admin/CDSO broadcast, owner read)
    path('notices/',          views.ParkingNoticeView.as_view(),       name='parking-notices'),
    path('notices/<int:pk>/', views.ParkingNoticeDetailView.as_view(), name='parking-notice-detail'),

    # Registration period management (admin/CDSO)
    path('registration-periods/',              views.RegistrationPeriodListCreateView.as_view(),  name='registration-periods'),
    path('registration-periods/<int:pk>/activate/', views.RegistrationPeriodActivateView.as_view(), name='registration-period-activate'),
    path('registration-periods/<int:pk>/',      views.RegistrationPeriodDetailView.as_view(),      name='registration-period-detail'),

    # Supplier management (admin only)
    path('suppliers/',                             views.SupplierListCreateView.as_view(), name='supplier-list'),
    path('suppliers/<int:pk>/',                    views.SupplierDetailView.as_view(),     name='supplier-detail'),
    path('suppliers/<int:pk>/plates/',             views.SupplierPlateView.as_view(),      name='supplier-plate-add'),
    path('suppliers/<int:pk>/plates/<int:plate_pk>/', views.SupplierPlateView.as_view(),   name='supplier-plate-delete'),

    # Scheduled visits — advance coordination for visitors/suppliers (admin only)
    path('scheduled-visits/',        views.ScheduledVisitListCreateView.as_view(), name='scheduled-visit-list'),
    path('scheduled-visits/<int:pk>/', views.ScheduledVisitDetailView.as_view(),   name='scheduled-visit-detail'),

    path('', include(router.urls)),
]
