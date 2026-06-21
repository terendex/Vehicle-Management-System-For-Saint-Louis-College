from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('rules',         views.RuleConstraintViewSet,    basename='rule-constraint')
router.register('vehicle-types', views.VehicleTypeAccessViewSet, basename='vehicle-type-access')
router.register('owners',        views.OwnerViewSet)
router.register('parking',       views.ParkingSpaceViewSet,      basename='parking-space')
router.register('parking-zones', views.ParkingZoneViewSet,       basename='parking-zone')
router.register('',              views.VehicleViewSet,           basename='vehicle')

urlpatterns = [
    # Registration token endpoints (Admin) — kept for backwards compat
    path('tokens/generate/', views.GenerateRegistrationTokenView.as_view(), name='generate-token'),
    path('tokens/clear/',    views.ClearTokensView.as_view(),               name='clear-tokens'),
    path('tokens/',          views.ListRegistrationTokensView.as_view(),    name='list-tokens'),
    path('tokens/<int:pk>/toggle/', views.ToggleTokenView.as_view(),        name='toggle-token'),
    path('tokens/<int:pk>/',        views.DeleteTokenView.as_view(),        name='delete-token'),

    # Public registration endpoints
    path('register/validate-token/<uuid:token>/', views.ValidateTokenView.as_view(),        name='validate-token'),
    path('register/submit/',                      views.PublicRegisterVehicleView.as_view(), name='submit-registration'),
    path('register/open/',                        views.PublicOpenRegistrationView.as_view(), name='open-registration'),
    path('register/status/',                      views.RegistrationStatusView.as_view(),    name='registration-status'),
    path('register/schedule-slots/',              views.ScheduleSlotsView.as_view(),         name='schedule-slots'),

    # Public reference lists (for registration form dropdowns)
    path('departments/', views.DepartmentListView.as_view(), name='department-list'),
    path('programs/',    views.ProgramListView.as_view(),    name='program-list'),

    # Parking availability (authenticated)
    path('parking-availability/', views.ParkingAvailabilityView.as_view(), name='parking-availability'),

    # Registration management (Admin)
    path('registrations/pending/',              views.PendingRegistrationsListView.as_view(), name='list-pending-registrations'),
    path('registrations/<int:pk>/accept/',      views.AcceptRegistrationView.as_view(),       name='accept-registration'),
    path('registrations/<int:pk>/reject/',      views.RejectRegistrationView.as_view(),       name='reject-registration'),

    path('', include(router.urls)),
]