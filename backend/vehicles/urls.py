from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('rules',       views.RuleConstraintViewSet, basename='rule-constraint')
router.register('vehicle-types', views.VehicleTypeAccessViewSet, basename='vehicle-type-access')
router.register('owners',      views.OwnerViewSet)
router.register('',            views.VehicleViewSet, basename='vehicle')

urlpatterns = [
    # Registration token endpoints (Admin)
    # NOTE: Specific named paths MUST come before parameterised <int:pk> paths
    # so Django doesn't try to coerce 'generate', 'clear', etc. into integers.
    path('tokens/generate/', views.GenerateRegistrationTokenView.as_view(), name='generate-token'),
    path('tokens/clear/',    views.ClearTokensView.as_view(),               name='clear-tokens'),
    path('tokens/',          views.ListRegistrationTokensView.as_view(),    name='list-tokens'),
    path('tokens/<int:pk>/toggle/', views.ToggleTokenView.as_view(),        name='toggle-token'),
    path('tokens/<int:pk>/',        views.DeleteTokenView.as_view(),        name='delete-token'),

    # Public endpoints
    path('register/validate-token/<uuid:token>/', views.ValidateTokenView.as_view(),       name='validate-token'),
    path('register/submit/',                      views.PublicRegisterVehicleView.as_view(), name='submit-registration'),

    # Pending Registration endpoints (Admin)
    path('registrations/pending/',              views.PendingRegistrationsListView.as_view(), name='list-pending-registrations'),
    path('registrations/<int:pk>/accept/',      views.AcceptRegistrationView.as_view(),       name='accept-registration'),
    path('registrations/<int:pk>/reject/',      views.RejectRegistrationView.as_view(),       name='reject-registration'),

    path('', include(router.urls)),
]