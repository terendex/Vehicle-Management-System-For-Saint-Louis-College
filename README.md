# Smart Parking and Vehicle Verification System — Saint Louis College

An AI-powered smart parking and vehicle verification system using license plate recognition, built for Philippine plate formats. Manages entry rules for students, employees, fetchers/droppers, and visitors, and monitors parking space occupancy in real time.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [Running the Project](#running-the-project)
- [Deployment](#deployment)
- [API Endpoints](#api-endpoints)
- [User Roles](#user-roles)

---

## Overview

This system automates vehicle entry and parking monitoring at Saint Louis College by scanning and recognizing Philippine license plates via camera. It enforces entry rules based on owner type (student, employee, fetcher/dropper, or visitor), checks schedules, tracks real-time parking space occupancy, and provides a web interface for administrators and guards to manage devices, monitor cameras, and retrieve vehicle owner information.

---

## Features

- **Camera-based plate scanning** — automatic detection at entry gates via webcam or IP cameras (RTSP)
- **ML plate recognition** — YOLOv8 detection + EasyOCR text extraction
- **Real-time bounding boxes** — 60fps canvas overlay with smooth LERP interpolation between 2fps backend detections
- **Multi-camera support** — monitor multiple RTSP IP cameras simultaneously
- **Device Management** — centralized admin panel to add, edit, and remove IP cameras; auto-named Cam 1, Cam 2, … with gap-filling; cameras auto-connect when visiting Entry or Parking pages
- **Smart parking occupancy** — AI detects vehicles inside parking space bounding boxes and marks spaces red/green in real time
- **Philippine plate validation** — supports all standard PH plate formats
- **Schedule-based entry** — students register for one whole rotation, MWF (Mon/Wed/Fri) or TTHF (Tue/Thu/Fri); SpEd gets every campus day, and CDSO can still assign specific days when accepting an application
- **Employee open access** — employees allowed entry on any campus day (Mon–Sat)
- **Visitor pass system** — visitors declare office destination, office confirms entry
- **Violation tracking** — flags vehicles with unresolved violations
- **Mobile web scanner** — guards can scan plates from any device
- **Open registration** — public application form with live duplicate checking; CDSO reviews and accepts or rejects
- **Role-based access** — CDSO (admin), Security, and Vehicle Owner roles
- **Access logs** — full history of every scan and entry attempt
- **ML sample collection** — every scan is stored for later review; retraining is triggered manually and never runs on its own from live scans
- **Reports** — Audit Log, Violations and Vehicle Registrations exportable as branded PDF or Excel
- **Backup & restore** — download a full JSON snapshot of system data, or restore from one; a safety snapshot is taken automatically before any restore
- **Vehicle pass fees** — configurable student/fetcher and employee amounts, shown on the public registration form
- **Scheduled visits** — log expected visitors and suppliers ahead of arrival
- **Help & user manual** — in-app guidance for every role

---

## Project Structure

```
Vehicle-Management-System-For-Saint-Louis-College/
│
├── frontend/                        # React (Vite)
│   ├── src/
│   │   ├── api/
│   │   │   ├── axios.js               # Axios instance
│   │   │   ├── auth.js                # Auth endpoints
│   │   │   ├── vehicles.js
│   │   │   ├── scanning.js
│   │   │   ├── registration.js
│   │   │   ├── parking.js
│   │   │   ├── cameras.js             # Device Management — camera CRUD
│   │   │   └── users.js
│   │   ├── components/
│   │   │   ├── Auth/
│   │   │   │   └── ProtectedRoute.jsx
│   │   │   └── Layout/
│   │   │       ├── AdminLayout.jsx
│   │   │       ├── SecurityLayout.jsx
│   │   │       └── OwnerLayout.jsx
│   │   ├── hooks/
│   │   │   ├── useScanStream.js       # Webcam → WS → 60fps canvas (live scan)
│   │   │   ├── useRtspStream.js       # Single RTSP camera → WS → 60fps canvas
│   │   │   └── useMultiRtspStream.js  # Multiple RTSP cameras, one WS per camera
│   │   ├── pages/
│   │   │   ├── Login/
│   │   │   │   └── LoginPage.jsx
│   │   │   ├── Register/
│   │   │   │   └── RegisterPage.jsx
│   │   │   ├── Admin/
│   │   │   │   ├── AdminDashboard.jsx
│   │   │   │   ├── VehicleRegistration.jsx
│   │   │   │   ├── UserManagement.jsx
│   │   │   │   ├── EntryManagement.jsx
│   │   │   │   ├── ParkingManagement.jsx
│   │   │   │   ├── DeviceManagement.jsx   # Camera CRUD — add/edit/remove IP cameras
│   │   │   │   ├── RuleConstraints.jsx
│   │   │   │   └── AuditLog.jsx
│   │   │   ├── Security/
│   │   │   │   ├── SecurityDashboard.jsx
│   │   │   │   ├── SecurityEntryManagement.jsx
│   │   │   │   └── SecurityAuditLog.jsx
│   │   │   ├── VehicleOwner/
│   │   │   │   └── OwnerDashboard.jsx
│   │   │   └── NotFoundPage.jsx
│   │   ├── stores/
│   │   │   └── authStore.js           # Zustand auth state
│   │   ├── App.jsx
│   │   └── main.jsx
│   ├── .env.example
│   ├── package.json
│   └── vite.config.js
│
├── backend/                         # Django
│   ├── config/                      # Project settings
│   │   ├── settings.py
│   │   ├── asgi.py                  # ASGI entry point (required for WebSockets)
│   │   └── urls.py
│   ├── accounts/                    # Users and roles
│   ├── vehicles/                    # Vehicles and owners
│   ├── scanning/                    # Plate scanning, access logs, visitor passes
│   │   ├── consumers.py             # WebSocket consumers (ScanLiveConsumer, RtspStreamConsumer)
│   │   ├── ml/
│   │   │   ├── detection.py         # YOLOv8 inference, adaptive preprocessing, NMS
│   │   │   ├── proximity_tracker.py # Frame-to-frame vehicle tracking by centroid distance
│   │   │   ├── reader.py            # EasyOCR pipeline for license plate text extraction
│   │   │   ├── train.py             # YOLOv8 training (offline + incremental)
│   │   │   ├── validator.py         # Philippine plate regex validation
│   │   │   ├── collector.py         # Auto-collect scan data for retraining
│   │   │   ├── weights/             # Trained model weights (not in git — transfer manually)
│   │   │   │   ├── best.pt          # Best checkpoint (used in production)
│   │   │   │   └── last.pt          # Latest checkpoint (for resuming)
│   │   │   └── dataset/             # Training images and YOLO labels
│   │   ├── tasks.py                 # Celery task: ML retrain job
│   │   ├── views.py                 # Scan + ML sample/retrain endpoints
│   │   ├── models.py                # AccessLog, VisitorPass, MLTrainingSample
│   │   └── serializers.py           # DRF serializers including ML sample
│   ├── violations/                  # Violation tracking
│   ├── manage.py
│   ├── requirements.txt
│   └── .env.example
│
├── docs/
│   └── DATA_AND_ML_MIGRATION.md     # Guide for migrating DB and ML assets
│
├── .gitignore
└── README.md
```

---

## Prerequisites

| Tool | Version | Download |
|---|---|---|
| Git | Latest | https://git-scm.com |
| VS Code | Latest | https://code.visualstudio.com |
| Node.js | 20+ | https://nodejs.org |
| Python | 3.11 | https://python.org/downloads/release/python-3119 |
| Redis | Latest | [Memurai](https://www.memurai.com) (Windows) or https://redis.io/downloads |

> **Use Python 3.11 specifically.** Python 3.12+ may cause issues with EasyOCR and OpenCV.
> **PostgreSQL is no longer required locally.** The project uses a shared Neon cloud database — get the `.env` file from a teammate.

### VS Code Extensions
Install these when VS Code prompts "Install recommended extensions?" or search manually (`Ctrl+Shift+X`):

- Python (Microsoft)
- Pylance (Microsoft)
- ES7+ React/Redux Snippets
- Tailwind CSS IntelliSense
- Prettier
- ESLint
- GitLens
- GitHub Pull Requests
- Thunder Client
- DotENV

---

## Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/your-org/Vehicle-Management-System-For-Saint-Louis-College.git
cd Vehicle-Management-System-For-Saint-Louis-College
code .
```

### 2. Set Up Environment Files

Get the shared `.env` file from a teammate (sent via group chat — never committed to Git) and place it at `backend/.env`.

For the frontend:
```bash
cp frontend/.env.example frontend/.env
```

The frontend `.env` only needs `VITE_API_BASE_URL=http://localhost:8000` — no secrets.

### 3. Set Up Redis

Redis is still required locally for Celery background tasks.

**Windows** — install **Memurai** and ensure the service is running:
```
Services app → Memurai → Running
```
If not running: right-click → Start.

**Linux** — install Redis via your package manager and ensure the service is running:
```bash
sudo apt install redis-server      # Debian/Ubuntu
sudo systemctl enable --now redis-server
```

### 4. Set Up the Backend

```bash
cd backend

# Create virtual environment
py -m venv venv          # Windows
python3 -m venv venv     # Linux

# Activate
venv\Scripts\activate    # Windows
source venv/bin/activate # Linux

# Install dependencies
pip install -r requirements.txt

# Apply any pending migrations
python manage.py migrate
```

> No need to create a local database — `DATABASE_URL` in your `.env` points to the shared Neon database.

### 5. Set Up the Frontend

```bash
cd frontend
npm install
```

---

## Environment Variables

### `backend/.env`

| Variable | Description | Example |
|---|---|---|
| `SECRET_KEY` | Django secret key. Required when `DEBUG` is off — the app refuses to start without it | `your-secret-key` |
| `DEBUG` | Debug mode. Defaults to `True` locally, but **`False` on Railway** so a forgotten variable fails closed instead of exposing stack traces | `True` |
| `ALLOWED_HOSTS` | Comma-separated allowed hosts. `RAILWAY_PUBLIC_DOMAIN` is appended automatically when present | `localhost,127.0.0.1` |
| `DATABASE_URL` | Neon PostgreSQL connection string | `postgresql://user:pass@host/db?sslmode=require` |
| `DB_NAME` | Local DB fallback (ignored when DATABASE_URL is set) | `plate_db` |
| `DB_USER` | Local DB fallback | `postgres` |
| `DB_PASSWORD` | Local DB fallback | `password` |
| `DB_HOST` | Local DB fallback | `127.0.0.1` |
| `DB_PORT` | Local DB fallback | `5432` |
| `USE_R2` | Enable Cloudflare R2 image storage | `true` |
| `R2_ACCESS_KEY_ID` | R2 API access key | — |
| `R2_SECRET_ACCESS_KEY` | R2 API secret key | — |
| `R2_BUCKET_NAME` | R2 bucket name | `slc-entry-management-ml` |
| `R2_ACCOUNT_ID` | Cloudflare account ID | — |
| `R2_PUBLIC_URL` | R2 public bucket URL | `pub-xxxx.r2.dev` |
| `CORS_ALLOWED_ORIGINS` | Allowed frontend origins | `http://localhost:5173` |
| `EMAIL_HOST_USER` | Gmail email for sending emails | `your-email@gmail.com` |
| `EMAIL_HOST_PASSWORD` | Gmail app password | `your-app-password` |
| `FRONTEND_URL` | Frontend URL for emails | `http://localhost:5173` |
| `BACKEND_URL` | Backend URL | `http://localhost:8000` |
| `ACCESS_TOKEN_LIFETIME_MINUTES` | JWT access token expiry | `60` |
| `REFRESH_TOKEN_LIFETIME_DAYS` | JWT refresh token expiry | `7` |
| `CELERY_BROKER_URL` | Redis broker URL | `redis://127.0.0.1:6379/0` |
| `CELERY_RESULT_BACKEND` | Redis result backend URL | `redis://127.0.0.1:6379/0` |
| `ML_SAMPLE_BATCH_SIZE` | New samples needed to trigger retraining | `50` |
| `ML_CONFIDENCE_THRESHOLD` | Min confidence to auto-label a sample | `0.6` |
| `ML_AUTO_RETRAIN_ENABLED` | Enable/disable automatic retraining | `true` |

Deployment-only variables (ignored in local development):

| Variable | Description | Example |
|---|---|---|
| `REDIS_URL` | Channels layer + Django cache. Unset falls back to in-memory, which is fine at one replica with no Celery worker | — |
| `DJANGO_ADMIN_URL` | Prefix for Django admin. Not `admin` — the React app owns that path on a shared origin | `django-admin` |
| `CSRF_TRUSTED_ORIGINS` | Extra trusted origins; the Railway domain is added automatically | `https://example.com` |
| `SECURE_SSL_REDIRECT` | Set `false` on the campus PC — the LAN has no TLS and the redirect would loop | `true` |
| `RUN_MIGRATIONS` | Run migrations at container start. Set `false` on the campus PC so only Railway migrates the shared DB | `true` |
| `DRF_PAGE_SIZE` | Page size for endpoints that opt in with `?page=` | `50` |

See [`backend/.env.campus.example`](backend/.env.campus.example) for a filled-in
campus template.

### `frontend/.env`

| Variable | Description | Example |
|---|---|---|
| `VITE_API_BASE_URL` | Django backend URL | `http://localhost:8000` |

> **Never commit `.env` files.** Only `.env.example` files are tracked in Git.

---

## Running the Project

> **Prerequisite:** Ensure Redis is running before starting the backend (Memurai on Windows, `redis-server` on Linux).

Open a terminal per service in VS Code (`` Ctrl+` `` to open, split icon to add more). **Start them in this order** and wait for each to be ready before starting the next:

| # | Service | Command (from repo root) | Wait for |
|---|---|---|---|
| 1 | **Backend** | `cd backend` → activate venv → `daphne -b 127.0.0.1 -p 8000 config.asgi:application` | `Listening on TCP address 127.0.0.1:8000` |
| 2 | **Frontend** | `cd frontend` → `npm run dev` | `Local: http://localhost:5173/` |
| 3 | Celery *(optional — manual retrain)* | `cd backend` → activate venv → `python -m celery -A config worker -l info --pool=solo` | `celery@… ready` |
| 4 | Tunnel *(optional — public access)* | `cloudflared tunnel --url http://localhost:5173` | the `https://….trycloudflare.com` URL |

> ⚠️ **Order matters.** The tunnel forwards to **Vite on `5173`**, *not* Django. If Vite isn't running when you start the tunnel, the public URL returns **HTTP 530** (Cloudflare can't reach the origin). Always start the tunnel **last**.
>
> Quick sanity check that a service is up:
> ```powershell
> netstat -ano | findstr :8000    # Daphne
> netstat -ano | findstr :5173    # Vite
> ```

### Terminal 1 — Backend (Django + Daphne)

```bash
cd backend
venv\Scripts\activate    # Windows
source venv/bin/activate # Linux
daphne -b 127.0.0.1 -p 8000 config.asgi:application
```

> **Daphne is required** — `python manage.py runserver` uses WSGI and does not support WebSockets. Camera scanning will not work without Daphne.
>
> On first connection after starting Daphne, the YOLO model and EasyOCR load into memory (takes ~10–25s on CPU). Subsequent connections are instant.

| URL | Description |
|---|---|
| `http://localhost:8000/api/` | Django REST API |
| `http://localhost:8000/admin/` | Admin panel |
| `http://localhost:8000/api/auth/login/` | Get JWT tokens |

### Terminal 2 — Frontend

```bash
cd frontend
npm run dev
```

| URL | Description |
|---|---|
| `http://localhost:5173` | React web app |

### Terminal 3 — Celery Worker (required for manual ML retraining)

```bash
cd backend
venv\Scripts\activate    # Windows
source venv/bin/activate # Linux

# Windows
python -m celery -A config worker -l info --pool=solo
# Linux
python -m celery -A config worker -l info
```

> `--pool=solo` is required on Windows to avoid `billiard` semaphore errors. On Linux, omit it — the default prefork pool works fine.

### Terminal 4 — Cloudflare Tunnel (optional, for external access)

Exposes the app to the internet — useful for testing on other devices (phone camera, QR scans) or sharing a live demo. Requires [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).

```bash
cloudflared tunnel --url http://localhost:5173
```

It prints **your own** public URL — a random word combo like `https://runtime-clothing-feeding-already.trycloudflare.com`.

> ⚠️ `YOUR-TUNNEL-URL` below is a **placeholder**. Copy the real URL that *your* `cloudflared` window printed — pasting the placeholder gives `ERR_NAME_NOT_RESOLVED` in the browser.

Vite already allows `.trycloudflare.com` hosts and proxies `/api` and `/ws` to Django, so API and WebSocket traffic flow through the tunnel with **no frontend changes**.

**After starting the tunnel, point `backend/.env` at the new URL.** Use the helper — it edits only the four tunnel keys and leaves the rest of `.env` untouched:

```bash
cd backend
venv\Scripts\activate                # Windows
python set_tunnel_url.py https://YOUR-TUNNEL-URL.trycloudflare.com
```

That sets:

```env
ALLOWED_HOSTS=localhost,127.0.0.1,.trycloudflare.com
CORS_ALLOWED_ORIGINS=http://localhost:5173,https://YOUR-TUNNEL-URL.trycloudflare.com
FRONTEND_URL=https://YOUR-TUNNEL-URL.trycloudflare.com
BACKEND_URL=https://YOUR-TUNNEL-URL.trycloudflare.com
```

`FRONTEND_URL` matters most — emailed links (password reset, registration status) and the Registration Form QR are built from it.

Then **restart Daphne** so the new `.env` is loaded.

> The free tunnel gets a **new random URL every session**, so update `backend/.env` and restart Daphne each time. A named tunnel on a Cloudflare account gives you a fixed domain instead.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Daphne won't start — *"address already in use"* / error `10048` | An old Daphne is still holding port 8000 (Ctrl+C went to the wrong terminal, or the window was closed) | `netstat -ano \| findstr :8000` → `taskkill /PID <pid> /F`, then start Daphne again |
| Public tunnel URL returns **HTTP 530** | **Vite isn't running** — the tunnel points at `5173` and has no origin to reach | Start `npm run dev` **first**, confirm `:5173` is listening, then start the tunnel |
| Tunnel URL loads, but login/API fails | Daphne isn't running | Check `netstat -ano \| findstr :8000`; start Daphne |
| App works, but emailed links / Registration QR point to an **old** URL | Daphne is still running the previous `.env` | `python set_tunnel_url.py <new-url>` → **restart Daphne** (`.env` is read only at startup) |
| Backend code changes don't take effect | Daphne has **no auto-reload** | Restart Daphne after every `.py` or `.env` change (the frontend *does* hot-reload) |
| cloudflared logs `Failed to initialize DNS local resolver` | Harmless — that's cloudflared's optional local DNS proxy, unrelated to your tunnel | Ignore it; check for `Registered tunnel connection` instead |
| Tunnel URL changed again | Quick tunnels are random per run | Re-run `set_tunnel_url.py` + restart Daphne, or create a named tunnel |

---

## Deployment

Everything above describes **local development**. Deployment runs the system in
two halves, because one of them can do something the other cannot.

| | Cloud half (Railway) | Campus half (on-site PC) |
|---|---|---|
| URL | `https://<app>.up.railway.app` | `http://<campus-ip>:8000` |
| Reachable off-campus | **yes** | no |
| Live camera scanning | **no** | **yes** |
| Registration, approvals, violations, reports, QR | yes | yes |
| Django admin (`/django-admin/`) | **yes** | no — plain HTTP blocks the secure cookie |
| Hosting cost | Railway usage | none — your hardware |

Both halves run the same code against the **same Neon database** and the **same
R2 bucket**, so they always show identical data. A scan at the gate is visible
on the Railway URL immediately.

### Why two halves

The backend — not the browser — opens the RTSP connection: `RtspStreamConsumer`
receives a `{"type":"start","rtsp_url":…}` message and calls
`cv2.VideoCapture()` server-side. The cameras live on the campus LAN at
`192.168.137.x`, and a cloud container has no route to a private address. So
whichever machine runs Django must be on the camera network.

Everything that is not a camera works fine in the cloud, which is why the public
half is worth having.

### Setting it up

| Guide | Covers |
|---|---|
| **[DEPLOY.md](DEPLOY.md)** | Railway: Railpack build, env vars, region, what to watch in the build log |
| **[CAMPUS_SETUP.md](CAMPUS_SETUP.md)** | On-site instance: config, `run-campus.ps1`, scheduled maintenance |

Quick start for the campus half, on a PC that can reach the cameras — clone the
repo, then:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-campus.ps1
```

That is the whole setup. On first run the script creates the virtualenv,
installs dependencies, writes `backend\.env` from the campus template, and asks
once for the values it cannot work out — `SECRET_KEY`, `DATABASE_URL` and the R2
keys, all copied from Railway. Re-run it any time; use `-Reconfigure` to change
those answers.

Everything else it derives on every run: this machine's LAN address (so a new
DHCP lease never means editing a file), `RUN_MIGRATIONS=false` so the campus
half can never migrate the shared schema, and a reachability ping against the
cameras actually registered in the database. The React bundle is rebuilt only
when the sources changed, and `npm ci` runs only when the lockfile moved.

### Things that differ from local development

- **Django admin is at `/django-admin/`, not `/admin/`.** On a single origin the
  React app owns `/admin`, `/admin/vehicles`, `/admin/users` — the two collided.
- **No Celery worker is deployed.** The ML retrain button therefore does
  nothing, and `auto_manage_events` does not run off its beat schedule —
  schedule `python manage.py run_maintenance` instead, which runs synchronously
  with no broker and no worker. See [CAMPUS_SETUP.md](CAMPUS_SETUP.md).
  The other two jobs, `auto_archive_expired_accounts` and `purge_old_records`,
  need no scheduling: the server runs them daily on its own thread
  (`vehicles/scheduler.py`), because the System Settings expiry and retention
  cards promise they happen automatically.
- **Redis is not required.** With one replica and no worker, nothing pushes to
  browsers from outside the web process, so the in-memory channel layer is
  enough. `settings.py` picks up `REDIS_URL` automatically if you ever add one.
- **`USE_R2=true` is mandatory in the cloud.** Railway's filesystem is
  ephemeral; with local storage every uploaded photo is destroyed on redeploy.
- **The campus PC needs a static IP** (or a DHCP reservation), or guards'
  bookmarks break and `ALLOWED_HOSTS` stops matching.

---

## API Endpoints

### Auth
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `POST` | `/api/auth/login/` | Login — returns access + refresh token | No |
| `POST` | `/api/auth/refresh/` | Refresh access token | No |
| `POST` | `/api/auth/verify/` | Verify token | No |
| `GET` | `/api/accounts/me/` | Get current logged-in user | Yes |
| `POST` | `/api/accounts/register/` | Create new user (admin only) | Admin |

### User Management
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/accounts/users/` | List all users | Admin |
| `GET` | `/api/accounts/users/{id}/` | Get user details | Admin |
| `PATCH` | `/api/accounts/users/{id}/update/` | Update user | Admin |
| `DELETE` | `/api/accounts/users/{id}/delete/` | Delete user | Admin |
| `POST` | `/api/accounts/users/{id}/toggle-status/` | Enable/disable user | Admin |
| `POST` | `/api/accounts/replace-admin/` | Replace current admin | Admin |
| `GET` | `/api/accounts/audit-logs/` | List audit logs | Yes |
| `GET` | `/api/accounts/audit-logs/stats/` | Audit log statistics | Admin |
| `GET` | `/api/accounts/audit-logs/export/` | Download the filtered audit log as Excel | Admin |
| `GET` | `/api/accounts/audit-logs/export-pdf/` | Download the filtered audit log as PDF | Admin |
| `DELETE` | `/api/accounts/audit-logs/clear/` | Delete all audit log records | Admin |

### Vehicles and Owners
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/vehicles/` | List all vehicles | Yes |
| `POST` | `/api/vehicles/` | Register new vehicle | Admin |
| `GET` | `/api/vehicles/{id}/` | Get vehicle by ID | Yes |
| `PATCH` | `/api/vehicles/{id}/authorize/` | Toggle entry authorization | Admin |
| `GET` | `/api/vehicles/owners/` | List all owners | Admin |
| `POST` | `/api/vehicles/owners/` | Register new owner | Admin |
| `GET` | `/api/vehicles/rules/` | List entry rules | Admin |
| `POST` | `/api/vehicles/rules/` | Create rule | Admin |
| `GET` | `/api/vehicles/vehicle-types/` | List vehicle type access rules | Admin |
| `POST` | `/api/vehicles/vehicle-types/` | Create vehicle type rule | Admin |

### Registration

Registration is open to the public — no invite token is required. Applications
land in a pending queue for the CDSO to accept or reject.

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `POST` | `/api/vehicles/register/open/` | Submit a vehicle registration application | No |
| `GET` | `/api/vehicles/register/status/` | Whether registration is open, plus the current vehicle pass fees | No |
| `GET` | `/api/vehicles/register/schedule-slots/` | Remaining capacity per campus day, plus per rotation under `groups` | No |
| `GET` | `/api/vehicles/register/availability/` | Live duplicate check for plate, email, licence, student/employee ID | No |
| `POST` | `/api/vehicles/register/license-image/` | Attach a driver's licence photo to a submitted application | No |
| `POST` | `/api/vehicles/register/direct/` | CDSO walk-in registration (auto-accepted, skips the queue) | Admin |
| `GET` | `/api/vehicles/registrations/pending/` | List pending registrations | Admin |
| `POST` | `/api/vehicles/registrations/{id}/accept/` | Accept registration | Admin |
| `POST` | `/api/vehicles/registrations/{id}/reject/` | Reject registration | Admin |
| `GET` | `/api/vehicles/registrations/report/excel/` | Download the filtered registrations report as Excel | Admin |
| `GET` | `/api/vehicles/registrations/report/pdf/` | Download the filtered registrations report as PDF | Admin |

### Scanning
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `POST` | `/api/scan/` | Scan plate image — returns entry decision | Yes |
| `GET` | `/api/scan/logs/` | List recent access logs | Yes |
| `GET` | `/api/scan/offices/` | List all offices | Yes |
| `GET` | `/api/scan/visitor-pass/` | List today's visitor passes | Yes |
| `POST` | `/api/scan/visitor-pass/` | Create visitor pass at gate | Yes |
| `POST` | `/api/scan/visitor-pass/{id}/printed/` | Confirm the slip was printed — logs the entry | Yes |
| `PATCH` | `/api/scan/visitor-pass/{id}/extend/` | Extend the allowed stay | Yes |
| `POST` | `/api/scan/visitor-pass/{id}/exit/` | Record the visitor's exit | Yes |
| `POST` | `/api/scan/visitor-pass/exit-scan/` | Record an exit by scanning the pass QR | Yes |

### WebSocket Endpoints (Daphne required)

| Endpoint | Description |
|---|---|
| `ws://localhost:8000/ws/scan/live/?token=<JWT>` | Live webcam scanning — client sends JPEG frames, server returns detection tracks |
| `ws://localhost:8000/ws/scan/rtsp/?token=<JWT>` | RTSP IP camera streaming — client sends `{type:"start", rtsp_url:"rtsp://..."}`, server streams frames and tracks back |

**WS message types (server → client):**

| Type | Payload | Description |
|---|---|---|
| `connected` | `{message, gpu}` | Sent once on open — confirms ASGI mode and GPU status |
| `tracks` | `{tracks: [{track_id, bbox, class_name, plate_text, ocr_done}]}` | Sent every processed frame |
| `ocr_update` | `{track_id, plate_text}` | Plate text confirmed by EasyOCR |
| `result` | `{results: [...]}` | Plate matched against vehicle database |
| `frame` | `{image_b64}` | RTSP only — JPEG frame from IP camera |
| `status` | `{connected, message}` | RTSP only — stream connection status |
| `error` | `{message}` | Processing or connection error |

### ML Training & Feedback
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/scan/ml/samples/` | List collected training samples | Yes |
| `PATCH` | `/api/scan/ml/samples/{id}/` | Approve/reject/correct a sample label | Yes |
| `GET` | `/api/scan/ml/stats/` | Dashboard stats for sample collection | Yes |
| `POST` | `/api/scan/ml/retrain/` | Manually trigger an incremental retrain | Yes |

### Device Management (IP Cameras)
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/vehicles/cameras/` | List all cameras (supports `?assignment=entry\|parking`) | Yes |
| `POST` | `/api/vehicles/cameras/` | Add a camera — auto-assigns name (Cam 1, Cam 2, …) with gap-filling | Admin |
| `PATCH` | `/api/vehicles/cameras/{id}/` | Edit camera IP, credentials, RTSP URL, or assignment | Admin |
| `DELETE` | `/api/vehicles/cameras/{id}/` | Remove camera — slot name is reused for the next addition | Admin |
| `GET` | `/api/vehicles/cameras/next-name/` | Preview next auto-assigned name before adding | Yes |

### Violations
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/violations/` | List all violations | Yes |
| `POST` | `/api/violations/` | Add a violation | Yes |
| `PATCH` | `/api/violations/{id}/` | Update or resolve violation | Yes |
| `GET` | `/api/violations/report/excel/` | Download the filtered violations report as Excel | Admin |
| `GET` | `/api/violations/report/pdf/` | Download the filtered violations report as PDF | Admin |

### System Settings, Backup & Scheduling
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/vehicles/system-settings/` | Read system settings (retention, dedup window, vehicle pass fees) | Yes |
| `PATCH` | `/api/vehicles/system-settings/` | Update system settings | Admin |
| `GET` | `/api/accounts/system/backup/` | Download a JSON snapshot of all application data | Admin |
| `POST` | `/api/accounts/system/restore/` | Restore from an uploaded JSON backup (atomic, auto safety snapshot) | Admin |
| `GET` | `/api/vehicles/scheduled-visits/` | List scheduled visits (`?upcoming=1` for pending arrivals) | Admin |
| `POST` | `/api/vehicles/scheduled-visits/` | Log an expected visitor or supplier | Admin |
| `PATCH` | `/api/vehicles/scheduled-visits/{id}/` | Update or mark a scheduled visit as arrived | Admin |
| `GET` | `/api/scan/gates/` | List gates (`?all=1` includes inactive) | Yes |
| `POST` | `/api/scan/gates/` | Create a gate | Admin |
| `PATCH` | `/api/scan/gates/{id}/` | Rename a gate or toggle it active | Admin |

### Scan Response Examples

```json
// Student on wrong day
{
  "plate_number": "ABC1234",
  "status": "wrong_day",
  "allowed": false,
  "message": "Student is on MWF schedule. Today (Tuesday) is not an allowed day.",
  "vehicle": { "owner": { "full_name": "Juan dela Cruz", "schedule": "MWF" } },
  "has_violations": false
}

// Visitor pending office confirmation
{
  "plate_number": "XYZ5678",
  "status": "pending",
  "allowed": false,
  "message": "Waiting for confirmation from Registrar's Office."
}

// Employee — always authorized
{
  "plate_number": "EMP1234",
  "status": "authorized",
  "allowed": true,
  "message": "Employee — Maria Santos. Entry granted."
}
```

---

## User Roles

| Role | Access |
|---|---|
| **CDSO (Admin)** | Full system access — manage users, vehicles, owners, rules, violations, reports, backups, audits |
| **Security** | Scan plates, view logs, manage visitor passes, view own statistics |
| **Vehicle Owner** | View own registered vehicles, history, and entry status |

### Accounts

The system ships with one seeded administrator; all other accounts are created
through the app.

| Role | Email | Password |
|---|---|---|
| Admin | `admin@slc.edu.ph` | set on first setup — see below |

Passwords are intentionally **not** listed here: this is a public repository and
the deployment is reachable on the public internet, so a working credential in
this file is a live account anyone could use.

**First-time login / forgotten password** — reset it against the database, then
log in at `/login`:

```bash
cd backend && python manage.py changepassword admin@slc.edu.ph
```

On Railway, run that from the service's **Console** tab. Locally, run it from an
activated virtual environment. To create additional administrators, use
`python manage.py createsuperuser`.

---

## License

For internal use only. © 2026 Saint Louis College.
