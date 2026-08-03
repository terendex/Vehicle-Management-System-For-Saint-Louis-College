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

## 3. Redis — not needed, and skipping it saves money

**Do not add a Redis service for this deployment.** It would be a second
always-on container for no benefit.

The in-memory channel layer is per-process, which only matters when something
*outside* the web process needs to push to browsers. Nothing here does:
`broadcast_change` is called solely from views and signals, both of which run
inside the same Daphne process, and no Celery task calls it. With a single
replica and no worker service, in-memory is sufficient.

Add Redis only if you later run a Celery worker or raise the replica count.
`settings.py` already picks it up from `REDIS_URL` with no code change.

## 4. Environment variables

Set these under **Variables**. `RAILWAY_PUBLIC_DOMAIN` is injected by Railway
and is picked up automatically for both `ALLOWED_HOSTS` and
`CSRF_TRUSTED_ORIGINS` — you do not add it yourself.

| Variable | Value | Notes |
|---|---|---|
| `SECRET_KEY` | *(50+ random chars)* | Generate: `python -c "import secrets;print(secrets.token_urlsafe(64))"` |
| `DEBUG` | `false` | Already the default on Railway — the platform's own env vars flip it, so forgetting this variable fails closed rather than exposing stack traces. The app refuses to boot without `SECRET_KEY` when it is off. |
| `DATABASE_URL` | *(Neon pooled connection string)* | Use the `-pooler` host |
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

## 6. Custom domain — `spvvs.slc-sflu.edu.ph`

The app is usable on its `*.up.railway.app` URL immediately. Moving it to the
school subdomain `spvvs.slc-sflu.edu.ph` is optional and can be done any time
after the first deploy. It involves three parties, one action each:

**1. Us — add the domain in Railway (2 min).**
Service → Settings → Networking → **Custom Domain** → enter `spvvs.slc-sflu.edu.ph`.
Railway then displays two DNS records — a **CNAME** and a **TXT** — with values
unique to this service. They do not exist until the domain is added here, so
this step must come first.

**2. SLC IT — add those records to `slc-sflu.edu.ph` DNS (5 min).**
This is the blocking step: `slc-sflu.edu.ph` is the school's domain, so only
whoever administers its DNS can point the subdomain. Send them the two records
from step 1, e.g.:

> Please add these records to `slc-sflu.edu.ph` so our capstone system can use
> `spvvs.slc-sflu.edu.ph`:
> - **CNAME** — `spvvs` → `<target Railway shows>`
> - **TXT** — `<name/value Railway shows>`
>
> They point the subdomain at our hosted app; SSL is automatic. No existing
> `slc-sflu.edu.ph` record is affected.

**3. Railway — automatic.**
Once the records resolve (minutes to a few hours), Railway provisions and
renews the TLS certificate itself. No cost; custom domains work on every plan.

**After it resolves, update these variables** so emails and CSRF use the new
host — do this *after*, not before, or password-reset and QR links point at a
domain that does not answer yet:

```
ALLOWED_HOSTS=localhost,127.0.0.1,spvvs.slc-sflu.edu.ph
CSRF_TRUSTED_ORIGINS=https://spvvs.slc-sflu.edu.ph
FRONTEND_URL=https://spvvs.slc-sflu.edu.ph
BACKEND_URL=https://spvvs.slc-sflu.edu.ph
```

Both the Railway URL and the custom domain serve simultaneously, so there is no
downtime during the switch. Rename any shared `*.up.railway.app` link *before*
handing it out, since every rename breaks previously saved links.

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

To get scanning back, run the same app on a campus machine against this same
database: see **[CAMPUS_SETUP.md](CAMPUS_SETUP.md)**. No code changes, no extra
hosting cost.

**Nothing Celery-based runs**, because no worker service is deployed:

- The **ML retrain button** does nothing — it is the only `.delay()` call in the
  codebase. Restoring it needs a worker service (start command
  `cd backend && celery -A config worker --loglevel=info --pool=solo`, same
  variables) plus Redis as a broker — two extra always-on containers.
- The **daily maintenance jobs** (`auto_manage_events`, `purge_old_records`) are
  on a beat schedule that nothing is running. These matter more: without them
  the events list stops rolling over. Fix them for free with
  `python manage.py run_maintenance` on a schedule — see
  **[CAMPUS_SETUP.md](CAMPUS_SETUP.md)**. No broker, no worker, no cost.
  `auto_archive_expired_accounts` is the exception: the server runs it itself on
  a daily in-process thread, so owner-account expiry works with no scheduling.
  Set `DISABLE_DAILY_SCHEDULER=1` here if you would rather the campus machine
  own it — its clock is Manila time, and these jobs are date-keyed.

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
