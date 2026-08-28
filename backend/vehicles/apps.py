from django.apps import AppConfig


class VehiclesConfig(AppConfig):
    name = 'vehicles'

    def ready(self):
        # Archives expired owner accounts daily without Celery or a hand-registered
        # Windows task. Starts a thread only in the server process — see
        # vehicles/scheduler.py.
        from . import scheduler
        scheduler.start()

        # Keeps a detector running for every zone that has a camera, instead of
        # waiting for someone to press Start Detection after every restart.
        # Same server-process guard as the scheduler — and additionally a
        # host guard: it only runs where the cameras are actually reachable,
        # which is the campus server, not the cloud one. See
        # detection_supervisor._autodetect_disabled for why that matters.
        from . import detection_supervisor
        detection_supervisor.start()

        # Builds the S3 signing client off the request path. Without this the
        # first reviewer to open Vehicle Registration after a restart waits ~1s
        # for botocore to load. Same server-process guard as the two above.
        from . import document_warmup
        document_warmup.start()
