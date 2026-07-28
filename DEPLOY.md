# Deploying to Railway

The whole system ships as **one Railway service**, built by **Railpack**: the
build compiles the React bundle and Django serves it alongside the API on a
single origin. You open one URL and the app is there.

---

## 1. Create the service

1. Railway → **New Project** → **Deploy from GitHub repo** → pick this repo.
2. Railway reads [`railway.json`](railway.json) (`builder: RAILPACK`) and the
   build plan in [`railpack.json`](railpack.json). No further configuration.

### How the Railpack build is wired

This repo is the awkward case for an auto-detecting builder — one service needs
**two** runtimes (Node to build React, Python to run Django and the ML stack) —
so [`railpack.json`](railpack.json) pins the details rather than leaving them to
detection:

- `packages` installs **both** Python 3.12 and Node 22. Python is pinned to 3.12
  because Railpack defaults to 3.13, and the pinned `paddlepaddle`/`torch`
  wheels are not guaranteed to exist for it.
- The `install` step installs **CPU-only torch first**, from the PyTorch index,
  before `backend/requirements.txt`. Order matters: the PyPI default would drag
  in ~2.5 GB of CUDA libraries that a Railway container can never use.
- `buildAptPackages` / `deploy.aptPackages` provide `libgl1`, `libglib2.0-0` and
  `libgomp1`, which OpenCV and the Torch kernels need at build and at runtime.
- Only the conventional `install` and `build` step names are used, so Railpack's
  own layer wiring applies instead of hand-rolled `inputs`.
- The root [`requirements.txt`](requirements.txt) exists purely as a detection
  marker (it just defers to `backend/requirements.txt`). Without a dependency
  file at the root, the build can fail with *"Railpack could not determine how
  to build the app"*.

> **Do not move a `Dockerfile` back to the repository root.** Railway currently
> prefers a root Dockerfile over an explicitly configured `RAILPACK` builder, so
> its presence silently bypasses everything above. The previous Docker build is
> kept as a fallback at [`docker/Dockerfile`](docker/Dockerfile) — out of the
> auto-detect path on purpose. To use it instead, set `builder` to `DOCKERFILE`
> and `dockerfilePath` to `docker/Dockerfile` in `railway.json`.

## 2. Set the region — do this before the first deploy

**Settings → Region → `Southeast Asia (Singapore)`.**

This is the single largest performance decision in the deployment. The Neon
database is in `ap-southeast-1`. Same-region traffic is ~1–5 ms per query;
running the container in a US region puts ~200 ms on *every* query, and a page
that makes ten queries becomes two seconds slower for no other reason.

## 3. Add Redis

Railway → **New** → **Database** → **Redis**. Then reference it in the
variables below. Without Redis the app still runs, but live updates broadcast
from the Celery worker never reach browsers, because the fallback channel layer
is per-process.

## 4. Environment variables

Set these under **Variables**. `RAILWAY_PUBLIC_DOMAIN` is injected by Railway
and is picked up automatically for both `ALLOWED_HOSTS` and
`CSRF_TRUSTED_ORIGINS` — you do not add it yourself.

| Variable | Value | Notes |
|---|---|---|
| `SECRET_KEY` | *(50+ random chars)* | Generate: `python -c "import secrets;print(secrets.token_urlsafe(64))"` |
| `DEBUG` | `false` | Already the default on Railway — the platform's own env vars flip it, so forgetting this variable fails closed rather than exposing stack traces. The app refuses to boot without `SECRET_KEY` when it is off. |
| `DATABASE_URL` | *(Neon pooled connection string)* | Use the `-pooler` host |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Railway resolves this reference |
| `CELERY_BROKER_URL` | `${{Redis.REDIS_URL}}` | |
| `CELERY_RESULT_BACKEND` | `${{Redis.REDIS_URL}}` | |
| `USE_R2` | `true` | **Required** — see the warning below |
| `R2_ACCESS_KEY_ID` | | |
| `R2_SECRET_ACCESS_KEY` | | |
| `R2_BUCKET_NAME` | | |
| `R2_ACCOUNT_ID` | | |
| `R2_PUBLIC_URL` | e.g. `pub-xxxx.r2.dev` | Public bucket host |
| `EMAIL_HOST_USER` | | Gmail address |
| `EMAIL_HOST_PASSWORD` | | Gmail **app password**, not the account password |
| `FRONTEND_URL` | `https://<your>.up.railway.app` | Used in emailed links |
| `BACKEND_URL` | `https://<your>.up.railway.app` | Same value — one origin |
| `DJANGO_ADMIN_URL` | `django-admin` | Change to something unguessable |

> **`USE_R2=true` is not optional in production.** Railway's filesystem is
> ephemeral: with local storage, every licence photo and violation evidence
> image is destroyed on each redeploy. The app prints a warning at boot if this
> is left off.

## 5. Deploy

Push to the branch. Railway builds, runs migrations at container start, and
gates the deploy on `/healthz`.

---

## What changed to make this work

- **Django admin moved off `/admin/`** to `/django-admin/`. The React app owns
  `/admin`, `/admin/vehicles`, `/admin/users` and friends; on a shared origin
  the two collided.
- **One origin, no CORS.** Axios' relative `/api` baseURL and the WebSocket
  helper both follow the browser's own origin, so no rebuild is needed to change
  domains.
- **CPU-only torch.** PyPI's default Linux wheel bundles ~2.5 GB of CUDA
  libraries that a Railway container cannot use. The CPU wheel runs the same
  models at ~200 MB.
- **ML weights ride along in the repo.** Railpack builds from the git contents,
  and both detectors (`plate_detector` and `vehicle_detector`, 22 MB + 42 MB)
  are deliberately tracked, so they need no special handling. The fallback
  Docker build does need it — `.dockerignore` excludes `*.pt` wholesale and
  re-admits those two by negation.
- **Redis channel layer**, replacing the in-process one that silently dropped
  every broadcast sent from Celery.
- **Bytecode is pre-compiled at build time.** Otherwise every container start
  re-parses the whole torch/paddle/ultralytics source tree before serving its
  first request.

## Known limitations

**Live camera scanning does not work from Railway.** The cameras are on the
campus LAN (`192.168.137.x`, see `IP_CAMERA_IPS.md`) and a cloud container
cannot route to a private address. Everything else — registration, approvals,
violations, reports, QR scanning, parking records, the admin UI — works fully.
Restoring live scanning requires an on-campus agent that owns the RTSP streams
and posts detections up to this API; that is not built yet.

**Keep this service at a single replica.** The YOLO/Paddle models are loaded per
process and the parking-camera threads hold per-process state, so scaling out
needs that state externalised first. Railway defaults to one replica; do not
raise it (replica counts live under `multiRegionConfig`, not a `numReplicas`
field — that key is not valid in `railway.json` and is rejected).

## Local development is unaffected

`DEBUG` defaults to `true` off-platform, the SPA catch-all only activates when a
built frontend is present, and `docker-compose.yml` still uses
`backend/Dockerfile`. Nothing in the existing `npm run dev` + `manage.py
runserver` workflow changes. Install dependencies from
`backend/requirements.txt` as before — the root `requirements.txt` is only a
build-detection marker.

## What is verified, and what the first build will tell you

Verified locally: every `railpack.json` build command run verbatim against this
repo (`npm --prefix frontend run build`, the `dist` copy, `collectstatic`),
`start.sh` parses and runs via `sh` without an executable bit, both JSON files
are well-formed, and the assembled app serves the SPA, a hashed asset, the API
and `/healthz` correctly.

Not verified: the Railpack build itself. Railpack needs BuildKit to run, so it
cannot be exercised on this machine. The first Railway build is the real test —
watch the log for two things: that it reports **Railpack** (not Docker) as the
builder, and that the torch install pulls from `download.pytorch.org`, not PyPI.
