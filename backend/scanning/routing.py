from django.urls import re_path
from .consumers import ScanLiveConsumer, RtspStreamConsumer

websocket_urlpatterns = [
    re_path(r"^ws/scan/live/?$", ScanLiveConsumer.as_asgi()),
    re_path(r"^ws/scan/rtsp/?$", RtspStreamConsumer.as_asgi()),
]
