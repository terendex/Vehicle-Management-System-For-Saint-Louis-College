from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('owners',   views.OwnerViewSet)
router.register('',         views.VehicleViewSet, basename='vehicle')

urlpatterns = [path('', include(router.urls))]