from django.urls import re_path
from .consumers import ScanLiveConsumer

websocket_urlpatterns = [
    re_path(r"^ws/scan/live/?$", ScanLiveConsumer.as_asgi()),
]
