"""The Policies page (/policy): read by anyone, edited by the CDSO.

Only edited wording is stored (see PolicyDocument). A tab with no row reports
`content: None`, and the page falls back to the wording built into the frontend.
"""
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.audit import audit
from accounts.models import AuditLog
from accounts.twofa_api import HasRecentTwoFactor

from .models import PolicyDocument
from .views import IsAdminOrCdso

# Generous for a policy (the full privacy policy is about 14,000 characters),
# small enough that a pasted mistake cannot bloat every page load.
MAX_POLICY_LENGTH = 100_000


def _serialize(doc):
    """One tab as the page reads it. `doc` is None for a tab never edited."""
    if doc is None:
        return {'content': None, 'updated_at': None, 'updated_by': None}
    return {
        'content':    doc.content,
        'updated_at': doc.updated_at.isoformat(),
        'updated_by': doc.updated_by.full_name if doc.updated_by else None,
    }


class PolicyListView(APIView):
    """Every tab at once. Public: the page is open to applicants with no account."""
    permission_classes = [permissions.AllowAny]
    # Public page: a stale Bearer token left in the browser must not 401 it.
    authentication_classes = []

    def get(self, request):
        stored = {d.key: d for d in PolicyDocument.objects.select_related('updated_by')}
        return Response({key: _serialize(stored.get(key)) for key in PolicyDocument.Key.values})


class PolicyDetailView(APIView):
    """Save (PUT) or restore the built-in wording of (DELETE) one tab.

    CDSO only, with a fresh two-factor step-up, the same bar as System
    Settings: this is the text applicants agree to.
    """
    permission_classes = [IsAdminOrCdso, HasRecentTwoFactor]

    def put(self, request, key):
        if key not in PolicyDocument.Key.values:
            return Response({'detail': 'Unknown policy.'}, status=status.HTTP_404_NOT_FOUND)
        content = request.data.get('content')
        if not isinstance(content, str) or not content.strip():
            return Response({'content': 'The policy cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
        if len(content) > MAX_POLICY_LENGTH:
            return Response({'content': f'The policy is too long (over {MAX_POLICY_LENGTH:,} characters).'},
                            status=status.HTTP_400_BAD_REQUEST)

        doc, created = PolicyDocument.objects.update_or_create(
            key=key, defaults={'content': content, 'updated_by': request.user},
        )
        audit(request, AuditLog.Action.RECORD_UPDATED,
              f"Policy updated | {doc.get_key_display()} | By: {request.user.full_name}")
        return Response(_serialize(doc))

    def delete(self, request, key):
        if key not in PolicyDocument.Key.values:
            return Response({'detail': 'Unknown policy.'}, status=status.HTTP_404_NOT_FOUND)
        deleted, _ = PolicyDocument.objects.filter(key=key).delete()
        if deleted:   # nothing to restore is not worth an audit line
            audit(request, AuditLog.Action.RECORD_UPDATED,
                  f"Policy restored to default | {PolicyDocument.Key(key).label} | By: {request.user.full_name}")
        return Response(_serialize(None))
