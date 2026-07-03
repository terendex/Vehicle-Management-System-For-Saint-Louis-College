"""
Shared audit-trail helpers.

Every admin mutation in the system (add / edit / delete) should leave an
AuditLog row. Use `audit()` inside APIViews, or mix `AuditedViewSetMixin`
into a ModelViewSet to log all three CRUD operations automatically.
"""
from .models import AuditLog


def _client_ip(request):
    xf = request.META.get('HTTP_X_FORWARDED_FOR')
    if xf:
        return xf.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def audit(request, action, details='', target_user=None):
    """Write one audit entry; never raises (auditing must not break the action)."""
    try:
        user = getattr(request, 'user', None)
        AuditLog.objects.create(
            actor=user if (user and user.is_authenticated) else None,
            action=action,
            target_user=target_user,
            details=details,
            ip_address=_client_ip(request),
        )
    except Exception:
        pass


class AuditedViewSetMixin:
    """
    Adds audit-log entries for create / update / delete on a ModelViewSet.
    Set `audit_label` to override the model's verbose name in the entry.
    """
    audit_label = None

    def _audit_name(self) -> str:
        if self.audit_label:
            return self.audit_label
        return self.get_queryset().model._meta.verbose_name.title()

    def _audit_desc(self, obj) -> str:
        return str(obj)

    def _actor_name(self) -> str:
        return getattr(self.request.user, 'full_name', '') or str(self.request.user)

    def perform_create(self, serializer):
        obj = serializer.save()
        audit(self.request, AuditLog.Action.RECORD_CREATED,
              f"{self._audit_name()} added | {self._audit_desc(obj)} | By: {self._actor_name()}")

    def perform_update(self, serializer):
        obj = serializer.save()
        audit(self.request, AuditLog.Action.RECORD_UPDATED,
              f"{self._audit_name()} updated | {self._audit_desc(obj)} | By: {self._actor_name()}")

    def perform_destroy(self, instance):
        desc = self._audit_desc(instance)
        instance.delete()
        audit(self.request, AuditLog.Action.RECORD_DELETED,
              f"{self._audit_name()} deleted | {desc} | By: {self._actor_name()}")
