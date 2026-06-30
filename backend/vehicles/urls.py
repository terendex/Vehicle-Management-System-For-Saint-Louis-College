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
    path('register/open/',           views.PublicOpenRegistrationView.as_view(), name='open-registration'),
    path('register/status/',         views.RegistrationStatusView.as_view(),     name='registration-status'),
    path('register/schedule-slots/', views.ScheduleSlotsView.as_view(),          name='schedule-slots'),

    # CDSO walk-in direct registration (auto-accepted, no pending)
    path('register/direct/', views.CdsoDirectRegisterView.as_view(), name='direct-registration'),

    # Reference lists (public, for registration form dropdowns)
    path('departments/', views.DepartmentListView.as_view(), name='department-list'),
    path('programs/',    views.ProgramListView.as_view(),    name='program-list'),

    # Parking availability (authenticated)
    path('parking-availability/', views.ParkingAvailabilityView.as_view(), name='parking-availability'),

    # Parking zone MJPEG stream (JWT via ?token= query param)
    path('parking-zones/<int:pk>/stream/', views.parking_stream_view, name='parking-stream'),

    # Registration management (Admin/CDSO)
    path('registrations/pending/',         views.PendingRegistrationsListView.as_view(), name='list-pending-registrations'),
    path('registrations/<int:pk>/accept/', views.AcceptRegistrationView.as_view(),       name='accept-registration'),
    path('registrations/<int:pk>/reject/', views.RejectRegistrationView.as_view(),       name='reject-registration'),

    # System-wide settings (admin / CDSO)
    path('system-settings/', views.SystemSettingsView.as_view(), name='system-settings'),

    # Parking notices (admin/CDSO broadcast, owner read)
    path('notices/',          views.ParkingNoticeView.as_view(),       name='parking-notices'),
    path('notices/<int:pk>/', views.ParkingNoticeDetailView.as_view(), name='parking-notice-detail'),

    # Registration period management (admin/CDSO)
    path('registration-periods/',              views.RegistrationPeriodListCreateView.as_view(),  name='registration-periods'),
    path('registration-periods/<int:pk>/activate/', views.RegistrationPeriodActivateView.as_view(), name='registration-period-activate'),

    path('', include(router.urls)),
]