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
        # Same server-process guard as the scheduler — see detection_supervisor.
        from . import detection_supervisor
        detection_supervisor.start()
