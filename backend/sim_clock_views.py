"""/api/system/test-clock/ — the Test Clock page's endpoint (admin only).

Registered in config/urls.py only when sim_settings is running, so on Railway,
the campus server or a normal `dev.ps1` the URL does not exist at all.
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

import sim_clock_actions as actions
from accounts.views import IsAdminRole


class TestClockView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        return Response(actions.status())

    def post(self, request):
        action = (request.data.get('action') or '').strip()
        try:
            if action == 'enable':
                return Response(actions.enable(True))
            if action == 'disable':
                return Response(actions.enable(False))
            if action == 'set':
                return Response(actions.set_to(actions.parse_when(request.data.get('when'))))
            if action == 'advance':
                return Response(actions.advance(**actions.parse_step(request.data.get('step'))))
            if action == 'reset':
                return Response(actions.reset())
            if action == 'run_jobs':
                return Response(actions.run_jobs())
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'error': f'Unknown action {action!r}.'}, status=status.HTTP_400_BAD_REQUEST)
