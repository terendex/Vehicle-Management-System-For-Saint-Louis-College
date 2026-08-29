"""URL configuration for config project.

In production the React SPA and this API share a single origin (see
FRONTEND_BUILD_DIR in settings): WhiteNoise serves the bundle's hashed assets,
and the catch-all at the bottom of this file hands every non-API path to
index.html so client-side routing survives a hard refresh or a shared link.
"""
import re

from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse, HttpResponse
from rest_framework_simplejwt.views import (
    TokenRefreshView,
    TokenVerifyView,
)
from accounts.views import CustomTokenObtainPairView, QRLoginView, GuardCredentialLoginView


def healthz(_request):
    """Liveness probe for Railway's deploy gate.

    Deliberately does not touch the database: this answers whether the container
    is up and serving. A slow cross-region query should not make a healthy
    deploy look dead. Exempted from SECURE_SSL_REDIRECT in settings, because
    Railway probes it over plain HTTP from inside the private network.
    """
    return JsonResponse({'status': 'ok'})


urlpatterns = [
    # Django's admin is NOT at /admin/ — the React app owns that route on this
    # shared origin (/admin, /admin/vehicles, /admin/users, ...). Override the
    # prefix with the DJANGO_ADMIN_URL env var.
    path(f'{settings.DJANGO_ADMIN_URL}/', admin.site.urls),

    path('healthz',                     healthz,                              name='healthz'),

    # JWT Auth
    path('api/auth/login/',             CustomTokenObtainPairView.as_view(),  name='token_obtain'),
    path('api/auth/qr-login/',          QRLoginView.as_view(),                name='qr_login'),
    path('api/auth/guard-login/',       GuardCredentialLoginView.as_view(),   name='guard_login'),
    path('api/auth/refresh/',           TokenRefreshView.as_view(),           name='token_refresh'),
    path('api/auth/verify/',            TokenVerifyView.as_view(),            name='token_verify'),

    # Apps
    path('api/accounts/',               include('accounts.urls')),
    path('api/vehicles/',               include('vehicles.urls')),
    path('api/scan/',                   include('scanning.urls')),
    path('api/violations/',             include('violations.urls')),
]

# Local media serving. With USE_R2=true this is a no-op — files are served from
# R2's public domain instead.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


# ── SPA fallback ──────────────────────────────────────────────────────────────
# Registered only when a built frontend is present, so local development (Vite
# on :5173 proxying to :8000) is completely unaffected.
if settings.FRONTEND_BUILD_DIR.exists():
    _INDEX_PATH = settings.FRONTEND_BUILD_DIR / 'index.html'

    def spa_index(_request):
        # Read from disk per request, not once at import.
        #
        # Holding it in memory meant a deploy only reached people who happened
        # to load "/" — WhiteNoise serves that one off disk — while every deep
        # link and every refresh (/security/parking, /admin/devices …) went on
        # booting the *previous* build until someone restarted the server. Two
        # builds then ran side by side in one browser, and the older one won
        # wherever the guard actually works. A few KB read per navigation is
        # nothing next to that.
        #
        # index.html must never be cached by the browser either: a copy held
        # there would keep requesting the old deploy's hashed asset filenames,
        # which no longer exist, and the app would fail to boot.
        resp = HttpResponse(_INDEX_PATH.read_bytes(), content_type='text/html; charset=utf-8')
        resp['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return resp

    # Anything that is not an API, admin, media or static path is a client-side
    # route: serve the shell and let React Router resolve it. The admin prefix
    # is escaped because it is user-supplied via DJANGO_ADMIN_URL and would
    # otherwise be interpreted as a regex.
    #
    # assets/ is excluded deliberately. WhiteNoise serves the bundle's hashed
    # files from there and falls through to the URLconf when one is absent —
    # which used to hand index.html back for a missing .js chunk, with status
    # 200 and Content-Type text/html. The browser then tried to evaluate HTML
    # as an ES module, the dynamic import rejected on a syntax error, and the
    # route rendered blank. A stale asset reference is a 404, not a page.
    urlpatterns += [
        re_path(
            r'^(?!api/|healthz|media/|static/|assets/|{}/).*$'.format(
                re.escape(settings.DJANGO_ADMIN_URL)
            ),
            spa_index,
            name='spa',
        ),
    ]
