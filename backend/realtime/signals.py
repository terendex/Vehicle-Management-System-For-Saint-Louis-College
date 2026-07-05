"""Connect post_save/post_delete on all domain models to the broadcast helper.

Any create/update/delete in a watched app pushes a ``data_changed`` message to
open pages via the ``updates`` group. Notifications fire on transaction commit
so clients only refetch data that is actually persisted and visible.
"""
import logging
from django.apps import apps
from django.db import transaction
from django.db.models.signals import post_save, post_delete

from .broadcast import broadcast_change

logger = logging.getLogger(__name__)

# Apps whose model changes should notify open pages. Django's built-ins,
# sessions, and JWT token-blacklist tables are intentionally excluded.
WATCHED_APPS = ("accounts", "vehicles", "scanning", "violations")

# Models that churn in bulk or on nearly every request — broadcasting each one
# would spam clients with pointless refetches. Add model_name (lowercase) here.
EXCLUDED_MODELS = {
    "mltrainingsample",   # bulk-written during ML training
}

# Keep strong refs so the connected handlers aren't garbage-collected.
_handlers = []


def _make_handler(resource):
    def handler(sender, **kwargs):
        deleted = kwargs.get("signal") is post_delete
        if deleted:
            action = "deleted"
        else:
            action = "created" if kwargs.get("created") else "updated"

        # Fire after the surrounding transaction commits (if any). Outside a
        # transaction, on_commit runs immediately.
        try:
            transaction.on_commit(lambda: broadcast_change(resource, action))
        except Exception:
            logger.exception("[realtime] failed to schedule broadcast for %s", resource)

    return handler


def connect_signals():
    for model in apps.get_models():
        if model._meta.app_label not in WATCHED_APPS:
            continue
        resource = model._meta.model_name
        if resource in EXCLUDED_MODELS:
            continue

        handler = _make_handler(resource)
        _handlers.append(handler)
        post_save.connect(handler, sender=model, dispatch_uid=f"rt_save_{resource}", weak=False)
        post_delete.connect(handler, sender=model, dispatch_uid=f"rt_del_{resource}", weak=False)
