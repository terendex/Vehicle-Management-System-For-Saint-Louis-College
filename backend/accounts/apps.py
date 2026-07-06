from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = 'accounts'

    def ready(self):
        from .notifications import connect_notification_signals
        connect_notification_signals()
