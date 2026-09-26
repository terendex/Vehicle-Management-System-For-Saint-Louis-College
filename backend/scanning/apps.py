from django.apps import AppConfig


class ScanningConfig(AppConfig):
    name = 'scanning'

    def ready(self):
        from . import signals  # noqa: F401 — connects the AccessLog receivers
