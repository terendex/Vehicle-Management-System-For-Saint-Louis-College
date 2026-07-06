from django.apps import AppConfig


class RealtimeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'realtime'

    def ready(self):
        # Connect post_save/post_delete signals for the watched apps so any
        # CRUD write fans out a "data_changed" notification to open pages.
        from . import signals
        signals.connect_signals()
