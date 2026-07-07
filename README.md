# Smart Parking and Vehicle Verification System — Saint Louis College

An AI-powered smart parking and vehicle verification system using license plate recognition, built for Philippine plate formats. Manages entry rules for students, employees, fetchers, visitors, and supplier vehicles, and monitors parking space occupancy in real time.

---

## Table of Contents

- [Overview](#overview)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Running the Project](#running-the-project)
- [Running with Docker](#running-with-docker)
- [Git Workflow](#git-workflow)
- [User Roles](#user-roles)

---

## Overview

This system automates vehicle entry and parking monitoring at Saint Louis College by scanning and recognizing Philippine license plates via camera. It enforces entry rules based on owner type (student, employee, fetcher, or visitor), checks assigned campus day schedules, tracks real-time parking space occupancy, and provides a web interface for admins, CDSO staff, and security guards to manage devices, monitor cameras, and retrieve vehicle owner information.

### How Entry Works

Every vehicle scan goes through `check_entry()` (`backend/scanning/entry_logic.py`) in the following order:

1. **Open Campus Mode** — if enabled by an admin via System Settings, all vehicles are granted entry (`open_entry`) with no further checks; the access log is still recorded as authorized.
2. **Outstanding violation fee** — a vehicle with a `FEE_IMPOSED` violation (3rd offense, unpaid ₱150) is denied entry regardless of owner type until it's settled at the CDSO office.
3. **Visitor / gate-issued vehicle** — unregistered plates or owners with `owner_type = visitor` are checked against an active, unexpired `VisitorPass` for today rather than the owner-type rules below.
4. **Authorization & account checks** — `vehicle.is_authorized` must be `True` and the owner's account must be active.
5. **Owner-type rules:**

| Owner Type | Entry Condition |
|---|---|
| **Student** | Must be on an assigned campus day (e.g. Mon/Wed/Fri). Optionally restricted by a time window rule. |
| **Fetcher** | Same as student — campus days + optional time rule, unless registered as a *standby* fetcher (parks and waits), in which case the time window doesn't apply. |
| **Employee** | Checked against an EMPLOYEE rule constraint (allowed days + time window). Unrestricted if no rule is active. |
| **Visitor** | Must have an active `VisitorPass` for today, issued at the gate by a guard. |

**Supplier vehicles** (delivery trucks etc.) bypass `check_entry()` entirely — they're matched against the `Supplier`/`SupplierPlate` registry (managed under Supplier Management) and auto-permitted without an owner account, mirrored between the manual-entry flow and the camera scan flow.

When a scan is denied, an `UNAUTHORIZED` violation is auto-created (deduplicated per 5 minutes per plate). Guards can override a denial with a logged reason, or issue a visitor pass for unregistered visitors.

### Scan Deduplication

The live WebSocket stream (webcam and RTSP) deduplicates repeated scans of the same plate within a configurable window (`SystemSettings.scan_dedup_seconds`, default 60 s). Manual plate entry and photo upload have no cooldown — every call is logged.

### Live Data Updates

Beyond the plate-scan streams, a separate `realtime` app pushes a lightweight `{resource, action}` event over `ws://.../ws/updates/` whenever a watched model (`accounts`, `vehicles`, `scanning`, `violations`) is created, updated, or deleted. The frontend's `LiveUpdatesProvider` (`frontend/src/realtime/`) opens one WebSocket per session and lets any open page refetch instead of polling.

### Vehicle Registration & Pass Applications

Applicants apply for a vehicle pass through the public **`/register`** form — no login required:

- **Self-service** — from the Login page, *Apply for a Vehicle Pass* opens the form, or *Scan to apply on your phone* shows a QR code that opens `/register` on a mobile device.
- **Walk-in** — at the CDSO office, an admin opens **Vehicle Registration Management → Registration Form QR** to display or print a QR code that walk-in applicants scan to register from their own phone.

Submitted applications land in **Vehicle Registration Management**, where Admin/CDSO review them (filterable by status and date range), then accept or reject. Accepted registrations issue the owner a vehicle QR pass.

### Security Guard Login (Gate Kiosks)

Guards sign in via credentials or QR at the security login link:

- **[http://localhost:5173/security/guard-login](http://localhost:5173/security/guard-login)** — standard guard login (pick a gate on screen).
- **[http://localhost:5173/security/guard-login/gate1](http://localhost:5173/security/guard-login/gate1)** / **[/gate4](http://localhost:5173/security/guard-login/gate4)** — kiosk mode, which pins the station to that gate (back-button trapped, gate switching disabled).

Legacy `/security/qr-login` links redirect automatically.

---

## Project Structure

```
Vehicle-Management-System-For-Saint-Louis-College/
│
├── frontend/                        # React 19 + Vite
│   ├── src/
│   │   ├── api/
│   │   │   ├── axios.js
│   │   │   ├── auth.js
│   │   │   ├── vehicles.js          # includes supplier endpoints
│   │   │   ├── scanning.js
│   │   │   ├── registration.js
│   │   │   ├── parking.js
│   │   │   ├── cameras.js
│   │   │   ├── notifications.js
│   │   │   └── users.js
│   │   ├── components/
│   │   │   ├── Auth/
│   │   │   │   └── ProtectedRoute.jsx
│   │   │   ├── Layout/
│   │   │   │   ├── AdminLayout.jsx
│   │   │   │   ├── SecurityLayout.jsx
│   │   │   │   └── OwnerLayout.jsx
│   │   │   ├── ComboBox.jsx
│   │   │   ├── NotificationBell.jsx
│   │   │   └── QrScanModal.jsx
│   │   ├── context/
│   │   │   └── CameraContext.jsx
│   │   ├── realtime/                 # ws/updates/ client (live refetch, no library)
│   │   │   ├── LiveUpdatesContext.js
│   │   │   ├── LiveUpdatesProvider.jsx
│   │   │   └── useLiveUpdates.js
│   │   ├── hooks/
│   │   │   ├── useScanStream.js
│   │   │   ├── useRtspStream.js
│   │   │   └── useMultiRtspStream.js
│   │   ├── pages/
│   │   │   ├── Login/
│   │   │   ├── Register/
│   │   │   ├── ForgotPassword/
│   │   │   ├── ResetPassword/
│   │   │   ├── Policy/              # Privacy Policy + Vehicle Pass Terms
│   │   │   ├── Admin/
│   │   │   │   ├── AdminDashboard.jsx
│   │   │   │   ├── VehicleRegistration.jsx
│   │   │   │   ├── UserManagement.jsx
│   │   │   │   ├── OperationsCenter.jsx
│   │   │   │   ├── ParkingSpaceManagement.jsx   # parking + events, replaces old ParkingManagement/Events
│   │   │   │   ├── DeviceManagement.jsx
│   │   │   │   ├── RuleConstraints.jsx
│   │   │   │   ├── ViolationsManagement.jsx
│   │   │   │   ├── SupplierManagement.jsx       # supplier/delivery auto-entry plates
│   │   │   │   ├── AuditLog.jsx
│   │   │   │   └── SystemSettings.jsx
│   │   │   ├── Security/
│   │   │   │   ├── SecurityEntryManagement.jsx
│   │   │   │   ├── SecurityParkingView.jsx
│   │   │   │   ├── SecurityAuditLogPage.jsx
│   │   │   │   └── SecurityQRLogin.jsx          # guard login/kiosk
│   │   │   └── VehicleOwner/
│   │   │       └── OwnerDashboard.jsx
│   │   ├── stores/
│   │   │   └── authStore.js         # Zustand
│   │   ├── utils/
│   │   │   └── plateFormat.js
│   │   ├── App.jsx
│   │   └── main.jsx
│   ├── .env.example
│   ├── package.json
│   └── vite.config.js
│
├── backend/                         # Django 4.2 + Daphne (ASGI)
│   ├── config/
│   │   ├── settings.py
│   │   ├── asgi.py                  # combines scanning + realtime websocket routes
│   │   └── urls.py
│   ├── accounts/                    # Users, roles, audit logs, admin notifications
│   ├── vehicles/                    # Vehicles, registrations, cameras, events,
│   │   │                            # parking, rule constraints, suppliers
│   │   └── migrations/
│   ├── violations/                  # Violation tracking and fines
│   ├── scanning/
│   │   ├── consumers.py             # WebSocket: ScanLiveConsumer, RtspStreamConsumer
│   │   ├── entry_logic.py           # check_entry() — authorization rules per owner type
│   │   ├── views.py                 # REST endpoints: scan, manual entry, visitor pass, override, exit
│   │   ├── models.py                # Gate, Office, AccessLog, VisitorPass, GuardShift, MLTrainingSample
│   │   ├── serializers.py
│   │   ├── management/commands/
│   │   │   ├── sync_ml_weights.py   # push/pull YOLO weights to/from Cloudflare R2
│   │   │   ├── upload_media_to_r2.py
│   │   │   └── backfill_gate.py
│   │   └── ml/
│   │       ├── detection.py         # YOLO plate/vehicle detection + adaptive preprocessing
│   │       ├── reader.py            # PaddleOCR pipeline — reads text from plate crops
│   │       ├── proximity_tracker.py # Frame-to-frame tracking by centroid distance
│   │       ├── validator.py         # Philippine plate regex validation
│   │       ├── collector.py         # Auto-collects scan data for ML retraining
│   │       ├── train.py             # YOLO training (offline + incremental)
│   │       ├── weights/             # Trained model weights (not in git — synced via R2)
│   │       │   ├── best.pt
│   │       │   └── last.pt
│   │       └── dataset/             # Training images and YOLO labels
│   ├── realtime/                    # Generic "data changed" WebSocket fan-out (ws/updates/)
│   │   ├── broadcast.py
│   │   ├── consumers.py
│   │   ├── routing.py
│   │   └── signals.py               # post_save/post_delete hooks on watched apps
│   ├── Dockerfile
│   ├── entrypoint.sh                # pulls ML weights from R2 on first container start
│   ├── manage.py
│   ├── requirements.txt
│   └── .env.example
│
├── docker-compose.yml                # redis + backend + celery + frontend
├── .gitignore
└── README.md
```

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Git | Latest | https://git-scm.com |
| VS Code | Latest | https://code.visualstudio.com |
| Node.js | 20+ | https://nodejs.org |
| Python | 3.11 | Use 3.11 specifically — 3.12+ may break PaddleOCR/OpenCV. (The Docker image uses 3.12 with pinned deps — see [Running with Docker](#running-with-docker).) |
| Redis | Latest | [Memurai](https://www.memurai.com) on Windows, or https://redis.io/downloads on Linux |
| Docker Desktop | Latest | Optional — only needed for the [Docker](#running-with-docker) workflow |

> **PostgreSQL is not required locally.** The project uses a shared Neon cloud database — get the `.env` file from a teammate.
>
> **Cloudflare R2** is used for uploaded vehicle/registration images and for syncing trained ML weights (`best.pt`/`last.pt`) between machines — also configured via the shared `.env`. Local dev works without R2 (falls back to local disk storage and locally-trained weights).

### Recommended VS Code Extensions

Install when VS Code prompts "Install recommended extensions?" or search manually (`Ctrl+Shift+X`):

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

Get the shared `backend/.env` from a teammate (sent via group chat — never committed to Git). It includes the Neon `DATABASE_URL`, Cloudflare R2 credentials, and email/JWT settings.

For the frontend:
```bash
cp frontend/.env.example frontend/.env
```

The frontend `.env` only needs one variable — no secrets:
```
VITE_API_BASE_URL=http://127.0.0.1:8000
```

### 3. Start Redis

Redis is required locally for Celery background tasks and the WebSocket channel layer.

**Windows** — install Memurai and confirm it is running:
```
Services app → Memurai → Running
```
If not running: right-click → Start.

**Linux:**
```bash
sudo apt install redis-server
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

# Apply migrations
python manage.py migrate
```

### 5. Set Up the Frontend

```bash
cd frontend
npm install
```

---

## Running the Project

> Ensure Redis is running before starting the backend.

Open **three terminals** in VS Code (`` Ctrl+` `` to open, click split icon to add more).

### Terminal 1 — Backend (Daphne)

```bash
cd backend
venv\Scripts\activate    # Windows
source venv/bin/activate # Linux
daphne -b 127.0.0.1 -p 8000 config.asgi:application
```

> `python manage.py runserver` does **not** support WebSockets. Daphne is required for live camera scanning and the live-updates channel.
>
> On first start, the YOLO model and PaddleOCR load into memory (~10–25 s on CPU). Subsequent connections are instant. If `scanning/ml/weights/best.pt` is missing and R2 isn't configured, detection runs without a YOLO plate crop (PaddleOCR still runs on the full frame) — see `backend/entrypoint.sh` / `sync_ml_weights`.

### Terminal 2 — Frontend

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173` in your browser.

### Terminal 3 — Celery Worker

```bash
cd backend
venv\Scripts\activate    # Windows
source venv/bin/activate # Linux

# Windows
python -m celery -A config worker -l info --pool=solo

# Linux
python -m celery -A config worker -l info
```

> `--pool=solo` is required on Windows. On Linux, omit it.

### Terminal 4 — ngrok (optional, for external access)

Expose the app for testing on other devices or sharing a live demo.

**One-time setup:**
```bash
winget install ngrok.ngrok
ngrok config add-authtoken <your-token>
```

**Start tunnel:**
```bash
ngrok http --domain=preconcurrently-inorganic-nicolle.ngrok-free.dev 5173
```

**Update `backend/.env` while tunnel is running:**
```
ALLOWED_HOSTS=localhost,127.0.0.1,preconcurrently-inorganic-nicolle.ngrok-free.dev
CORS_ALLOWED_ORIGINS=http://localhost:5173,https://preconcurrently-inorganic-nicolle.ngrok-free.dev
FRONTEND_URL=https://preconcurrently-inorganic-nicolle.ngrok-free.dev
```

Then restart Daphne. All API and WebSocket traffic proxies through Vite to the local backend automatically.

---

## Running with Docker

`docker-compose.yml` at the repo root spins up Redis, the Django/Daphne backend, a Celery worker, and the Vite dev server as four services:

```bash
docker compose up --build
```

- Backend → `http://localhost:8000` (source is bind-mounted for live reload)
- Frontend → `http://localhost:5173`
- Redis → exposed on host port `6380` (mapped to container `6379`)

Requires `backend/.env` to exist first (see [Getting Started](#getting-started)). On first boot, `entrypoint.sh` pulls `best.pt` from Cloudflare R2 if it's not already present under `backend/scanning/ml/weights/` and R2 credentials are configured; otherwise the backend starts without a YOLO plate crop. This is an alternative to the manual three-terminal setup above — use whichever you prefer, they don't need to run at the same time.

---

## Git Workflow

### Branch Structure

```
main          ← production only, never push directly
│
└── dev       ← integration branch, all PRs merge here
    ├── feat/your-feature
    ├── fix/your-fix
    └── ...
```

> Nobody pushes directly to `main` or `dev`. All changes go through a Pull Request reviewed by at least one other member.

### Day-to-Day Flow

```bash
# Start a new feature
git checkout dev
git pull origin dev
git checkout -b feat/your-feature

# Work, commit often
git add <files>
git commit -m "feat: describe what you did"

# Push and open a PR into dev
git push origin feat/your-feature
```

---

## User Roles

| Role | Access |
|---|---|
| **Admin** | Full system access — manage users, vehicles, registrations, rules, violations, parking, devices, suppliers, audit logs, system settings |
| **CDSO** | Shares the settings/entries/parking/violations screens with Admin (no separate page set) — `/cdso` redirects straight to System Settings |
| **Security** | Gate entry scanning (camera + manual), visitor pass issuance, exit logging, override, parking view, audit log |
| **Vehicle Owner** | View own registration, QR code, violations, parking availability, and announcements |

Supplier/delivery vehicles are not a login role — they're plates registered under Supplier Management that auto-pass the entry check without an owner account.

### Demo Credentials

| Role | Email | Password |
|---|---|---|
| Admin | admin@slc.edu.ph | Admin123! |

---

## License

For internal use only. &copy; 2026 Saint Louis College.
