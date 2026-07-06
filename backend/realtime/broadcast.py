"""Fan-out helper: notify every connected client that some data changed.

`broadcast_change` is safe to call from synchronous code (signals, views,
management commands). It never raises — a broken/absent channel layer must
not break the DB write that triggered the notification.
"""
import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

# All "updates" clients join this single group.
GROUP = "updates"


def broadcast_change(resource, action="changed", **meta):
    """Tell all open pages that `resource` changed so they can refetch.

    resource: short name of the data that changed (e.g. "vehicleregistration").
    action:   "created" | "updated" | "deleted" | "changed".
    meta:     optional extra fields included in the client payload.
    """
    layer = get_channel_layer()
    if layer is None:
        return

    payload = {"type": "data_changed", "resource": resource, "action": action}
    if meta:
        payload.update(meta)

    try:
        async_to_sync(layer.group_send)(GROUP, {"type": "broadcast", "payload": payload})
    except Exception:
        # Never let a notification failure bubble into the caller's transaction.
        logger.exception("[realtime] failed to broadcast %s/%s", resource, action)
