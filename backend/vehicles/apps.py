from django.apps import AppConfig


class VehiclesConfig(AppConfig):
    name = 'vehicles'

    def ready(self):
        # Archives expired owner accounts daily without Celery or a hand-registered
        # Windows task. Starts a thread only in the server process — see
        # vehicles/scheduler.py.
        from . import scheduler
        scheduler.start()
