"""Verify that live updates actually reach the other half.

The campus and Railway halves share one database, so data is identical the
instant it is written. A browser sitting on a page, though, is only *told* by
the half it is connected to — unless both halves share one Redis channel layer.

Getting that wrong fails silently by design: `broadcast_change` swallows channel
layer errors so a notification failure can never break the database write that
triggered it. Screens simply stop refreshing and nobody sees an error.

This command makes it observable. Run it on each half:

    python manage.py check_realtime            # report config + round-trip locally
    python manage.py check_realtime --listen   # wait for a broadcast from the other half
    python manage.py check_realtime --send     # emit one for the other half to catch

To prove the two halves are linked, run `--listen` on one and `--send` on the
other. A message crossing means shared Redis is working; a timeout means they
are still isolated no matter what the config claims.
"""
import time
import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.management.base import BaseCommand

from realtime.broadcast import GROUP, broadcast_change

PROBE_RESOURCE = "_realtime_probe"


class Command(BaseCommand):
    help = "Report whether live updates cross to the other half, and prove it end to end."

    def add_arguments(self, parser):
        parser.add_argument('--listen', action='store_true',
                            help="Wait for a probe broadcast from the other half.")
        parser.add_argument('--send', action='store_true',
                            help="Send one probe broadcast for the other half to catch.")
        parser.add_argument('--timeout', type=int, default=60,
                            help="Seconds to wait in --listen mode (default 60).")

    def handle(self, *args, **opts):
        backend = settings.CHANNEL_LAYERS['default']['BACKEND']
        cross = getattr(settings, 'REALTIME_CROSS_HOST', False)
        loopback = getattr(settings, 'REDIS_IS_LOOPBACK', False)

        self.stdout.write(f"channel layer : {backend}")
        self.stdout.write(f"redis url set : {bool(settings.REDIS_URL)}"
                          + (" (LOOPBACK - not shareable)" if loopback else ""))

        if cross:
            self.stdout.write(self.style.SUCCESS(
                "config        : shared Redis - updates CAN cross to the other half"))
        else:
            self.stdout.write(self.style.WARNING(
                "config        : this host only - updates CANNOT cross to the other half"))

        if opts['send']:
            return self._send()
        if opts['listen']:
            return self._listen(opts['timeout'])

        self._roundtrip()

    # ── one probe out ────────────────────────────────────────────────────
    def _send(self):
        token = uuid.uuid4().hex[:8]
        broadcast_change(PROBE_RESOURCE, action="probe", token=token)
        self.stdout.write(self.style.SUCCESS(f"\nsent probe token={token}"))
        self.stdout.write("Watch for it in `check_realtime --listen` on the other half.")

    def _join(self):
        """Subscribe to the updates group.

        Connecting is itself the step that fails when Redis is unreachable, so
        it has to be inside the guard — otherwise the operator gets a redis-py
        traceback instead of being told live updates are dead.
        """
        layer = get_channel_layer()
        try:
            channel = async_to_sync(layer.new_channel)()
            async_to_sync(layer.group_add)(GROUP, channel)
        except Exception as exc:
            raise SystemExit(
                f"\nCANNOT REACH THE CHANNEL LAYER: {exc}\n"
                f"  REDIS_URL is configured but nothing answers there, so every live\n"
                f"  update on this host is being dropped silently. Screens will never\n"
                f"  refresh on their own. Fix the Redis address or unset REDIS_URL to\n"
                f"  fall back to per-process updates."
            )
        return layer, channel

    # ── wait for a probe from anywhere on the shared layer ───────────────
    def _listen(self, timeout):
        layer, channel = self._join()
        self.stdout.write(f"\nlistening {timeout}s for a probe from the other half...")

        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                remaining = max(1, int(deadline - time.monotonic()))
                try:
                    msg = async_to_sync(layer.receive)(channel)
                except Exception as exc:            # Redis dropped mid-wait
                    raise SystemExit(f"\nchannel layer went away while waiting: {exc}")
                payload = (msg or {}).get('payload', {})
                if payload.get('resource') == PROBE_RESOURCE:
                    self.stdout.write(self.style.SUCCESS(
                        f"\nreceived probe token={payload.get('token')} - "
                        "the two halves ARE linked."))
                    return
                self.stdout.write(f"  (saw {payload.get('resource')}, still waiting, "
                                  f"{remaining}s left)")
        finally:
            self._leave(layer, channel)

        raise SystemExit(
            "\nno probe received - the halves are NOT linked. Check that both "
            "have the same REDIS_URL and that it is reachable from each machine."
        )

    # ── local sanity check: does this host's own layer work at all? ──────
    def _roundtrip(self):
        layer, channel = self._join()
        token = uuid.uuid4().hex[:8]
        try:
            broadcast_change(PROBE_RESOURCE, action="probe", token=token)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                msg = async_to_sync(layer.receive)(channel)
                payload = (msg or {}).get('payload', {})
                if payload.get('token') == token:
                    self.stdout.write(self.style.SUCCESS(
                        "\nlocal round-trip: OK - this host's own screens update live."))
                    return
        except SystemExit:
            raise
        except Exception as exc:
            raise SystemExit(f"\nlocal round-trip FAILED: {exc}\n"
                             "The channel layer is configured but unreachable, so live "
                             "updates are silently dead on this host.")
        finally:
            self._leave(layer, channel)

        raise SystemExit("\nlocal round-trip FAILED: no message came back within 10s.")

    @staticmethod
    def _leave(layer, channel):
        # Best-effort: the layer may already be gone, and failing to unsubscribe
        # must not replace the real diagnosis with a cleanup error.
        try:
            async_to_sync(layer.group_discard)(GROUP, channel)
        except Exception:
            pass
