# Smart Parking and Vehicle Verification System — Saint Louis College

An AI-powered smart parking and vehicle verification system using license plate recognition, built for Philippine plate formats. Manages entry rules for students, employees, fetchers, and visitors, and monitors parking space occupancy in real time.

---

## Table of Contents

- [Overview](#overview)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Running the Project](#running-the-project)
- [Git Workflow](#git-workflow)
- [User Roles](#user-roles)

---

## Overview

This system automates vehicle entry and parking monitoring at Saint Louis College by scanning and recognizing Philippine license plates via camera. It enforces entry rules based on owner type (student, employee, fetcher, or visitor), checks assigned campus day schedules, tracks real-time parking space occupancy, and provides a web interface for administrators and security guards to manage devices, monitor cameras, and retrieve vehicle owner information.

### How Entry Works

Every vehicle scan goes through `check_entry()` in the following order:

1. **Open Campus Mode** — if enabled by an admin via System Settings, all vehicles are granted entry with no further checks.
2. **Vehicle lookup** — plate must be registered in the system.
3. **Authorization check** — `vehicle.is_authorized` must be `True`.
4. **Account status** — the owner's account must be active.
5. **Owner-type rules:**

| Owner Type | Entry Condition |
|---|---|
| **Student** | Must be on an assigned campus day (e.g. Mon/Wed/Fri). Optionally restricted by a time window rule. |
| **Fetcher** | Same as student — campus days + optional time rule. |
| **Employee** | Checked against an EMPLOYEE rule constraint (allowed days + time window). Unrestricted if no rule is active. |
| **Visitor** | Must have an active `VisitorPass` for today, issued at the gate by a guard. |

When a scan is denied, an `UNAUTHORIZED` violation is auto-created (deduplicated per 5 minutes per plate). Guards can override a denial with a logged reason, or issue a visitor pass for unregistered visitors.

### Scan Deduplication

The live WebSocket stream (webcam and RTSP) deduplicates repeated scans of the same plate within a configurable window (`SystemSettings.scan_dedup_seconds`, default 60 s). Manual plate entry and photo upload have no cooldown — every call is logged.

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
├── frontend/                        # React (Vite)
│   ├── src/
│   │   ├── api/
│   │   │   ├── axios.js
│   │   │   ├── auth.js
│   │   │   ├── vehicles.js
│   │   │   ├── scanning.js
│   │   │   ├── registration.js
│   │   │   ├── parking.js
│   │   │   ├── cameras.js
│   │   │   └── users.js
│   │   ├── components/
│   │   │   ├── Auth/
│   │   │   │   └── ProtectedRoute.jsx
│   │   │   └── Layout/
│   │   │       ├── AdminLayout.jsx
│   │   │       ├── SecurityLayout.jsx
│   │   │       └── OwnerLayout.jsx
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
│   │   │   │   ├── ParkingManagement.jsx
│   │   │   │   ├── DeviceManagement.jsx
│   │   │   │   ├── RuleConstraints.jsx
│   │   │   │   ├── ViolationsManagement.jsx
│   │   │   │   ├── Events.jsx
│   │   │   │   ├── AuditLog.jsx
│   │   │   │   ├── GuardMonitor.jsx
│   │   │   │   ├── GateActivityMonitor.jsx
│   │   │   │   └── SystemSettings.jsx
│   │   │   ├── Security/
│   │   │   │   ├── SecurityEntryManagement.jsx
│   │   │   │   ├── SecurityParkingView.jsx
│   │   │   │   ├── SecurityAuditLogPage.jsx
│   │   │   │   └── SecurityQRLogin.jsx
│   │   │   └── VehicleOwner/
│   │   │       └── OwnerDashboard.jsx
│   │   ├── stores/
│   │   │   └── authStore.js
│   │   ├── App.jsx
│   │   └── main.jsx
│   ├── .env.example
│   ├── package.json
│   └── vite.config.js
│
├── backend/                         # Django + Daphne (ASGI)
│   ├── config/
│   │   ├── settings.py
│   │   ├── asgi.py
│   │   └── urls.py
│   ├── accounts/                    # Users, roles, audit logs
│   ├── vehicles/                    # Vehicles, registrations, cameras, events, parking
│   ├── violations/                  # Violation tracking and fines
│   ├── scanning/
│   │   ├── consumers.py             # WebSocket: ScanLiveConsumer, RtspStreamConsumer
│   │   ├── entry_logic.py           # check_entry() — authorization rules per owner type
│   │   ├── views.py                 # REST endpoints: scan, manual entry, visitor pass, override, exit
│   │   ├── models.py                # AccessLog, VisitorPass, GuardShift, MLTrainingSample
│   │   ├── serializers.py
│   │   └── ml/
│   │       ├── detection.py         # YOLO plate/vehicle detection + adaptive preprocessing
│   │       ├── reader.py            # PaddleOCR pipeline — reads text from plate crops
│   │       ├── proximity_tracker.py # Frame-to-frame tracking by centroid distance
│   │       ├── validator.py         # Philippine plate regex validation
│   │       ├── collector.py         # Auto-collects scan data for ML retraining
│   │       ├── train.py             # YOLO training (offline + incremental)
│   │       ├── weights/             # Trained model weights (not in git — transfer manually)
│   │       │   ├── best.pt
│   │       │   └── last.pt
│   │       └── dataset/             # Training images and YOLO labels
│   ├── manage.py
│   ├── requirements.txt
│   └── .env.example
│
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
| Python | 3.11 | Use 3.11 specifically — 3.12+ may break PaddleOCR/OpenCV |
| Redis | Latest | [Memurai](https://www.memurai.com) on Windows, or https://redis.io/downloads on Linux |

> **PostgreSQL is not required locally.** The project uses a shared Neon cloud database — get the `.env` file from a teammate.

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

Get the shared `backend/.env` from a teammate (sent via group chat — never committed to Git).

For the frontend:
```bash
cp frontend/.env.example frontend/.env
```

The frontend `.env` only needs one variable — no secrets:
```
VITE_API_BASE_URL=http://localhost:8000
```

### 3. Start Redis

Redis is required locally for Celery background tasks.

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

> `python manage.py runserver` does **not** support WebSockets. Daphne is required for live camera scanning.
>
> On first start, the YOLO model and PaddleOCR load into memory (~10–25 s on CPU). Subsequent connections are instant.

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
| **Admin** | Full system access — manage users, vehicles, registrations, rules, violations, parking, cameras, events, audit logs, system settings |
| **CDSO** | Settings, operations center, parking, violations, events |
| **Security** | Gate entry scanning (camera + manual), visitor pass issuance, exit logging, override, audit log |
| **Vehicle Owner** | View own registration, QR code, violations, parking availability, and announcements |

### Demo Credentials

| Role | Email | Password |
|---|---|---|
| Admin | admin@slc.edu.ph | Admin123! |

---

## License

For internal use only. &copy; 2026 Saint Louis College.
