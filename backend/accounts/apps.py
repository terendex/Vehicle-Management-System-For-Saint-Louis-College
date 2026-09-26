from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = 'accounts'

    def ready(self):
        from .notifications import connect_notification_signals
        connect_notification_signals()
        # After every `migrate`, make the database's own ON DELETE rules match
        # the models, so rows deleted by hand in the Neon console cascade the
        # same way app deletes do. See accounts/db_delete_rules.py.
        from django.db.models.signals import post_migrate
        from .db_delete_rules import sync_fk_delete_rules
        post_migrate.connect(sync_fk_delete_rules, sender=self,
                             dispatch_uid='accounts.sync_fk_delete_rules')
