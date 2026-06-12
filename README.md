# 🚗 Vehicle Management System — Saint Louis College

An AI-powered vehicle entry management system using license plate recognition, built for Philippine plate formats. Manages entry rules for students, employees, fetchers/droppers, and visitors.

---

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Entry Rules](#entry-rules)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [Running the Project](#running-the-project)
- [API Endpoints](#api-endpoints)
- [Git Workflow](#git-workflow)
- [Team](#team)

---

## Overview

This system automates vehicle entry at Saint Louis College by scanning and recognizing Philippine license plates via camera. It enforces entry rules based on owner type (student, employee, fetcher/dropper, or visitor), checks schedules, and provides a mobile-friendly web interface for guards to manually scan plates and retrieve vehicle owner information.

---

## Features

- 📷 **Camera-based plate scanning** — automatic detection at entry gates
- 🤖 **ML plate recognition** — YOLOv8 detection + EasyOCR text extraction
- 🇵🇭 **Philippine plate validation** — supports all standard PH plate formats
- 🗓️ **Schedule-based entry** — MWF and TTHS schedules for students and fetchers
- 👔 **Employee open access** — employees allowed entry any day
- 🪪 **Visitor pass system** — visitors declare office destination, office confirms entry
- ⚠️ **Violation tracking** — flags vehicles with unresolved violations
- 📱 **Mobile web scanner** — guards can scan plates from any device
- 📝 **Secure Registration** — admin generates one-time tokens for self-registration
- 🔐 **Role-based access** — Guard, Supervisor, Admin, and Office Staff roles
- 📋 **Access logs** — full history of every scan and entry attempt
- 🧠 **ML feedback loop** — automatically collects scan data and retrains YOLOv8 when enough new samples accumulate

---

## Entry Rules

| Owner Type | Entry Rule |
|---|---|
| **Student** | Only on their assigned schedule (MWF or TTHS) |
| **Fetcher / Dropper** | Only on their assigned schedule (MWF or TTHS) |
| **Employee** | Allowed any day, any time |
| **Visitor** | Must have a visitor pass confirmed by the destination office |

### Schedule Days
| Schedule | Allowed Days |
|---|---|
| MWF | Monday, Wednesday, Friday |
| TTHS | Tuesday, Thursday, Saturday |

### Visitor Flow
```
1. Visitor arrives at gate
2. Guard scans plate → "No pass found"
3. Guard creates visitor pass (vehicle, office, purpose)
4. Office staff confirms or rejects the pass
5. Guard re-scans → "Authorized" if confirmed
```

---

## Tech Stack

### Backend
| Package | Purpose |
|---|---|
| Django | Web framework |
| Django REST Framework | REST API |
| djangorestframework-simplejwt | JWT authentication |
| django-cors-headers | Cross-origin requests from React |
| django-channels | WebSocket support |
| Daphne | ASGI server for WebSockets |
| psycopg2-binary | PostgreSQL connector |
| Pillow | Image handling |
| EasyOCR | Plate text extraction |
| OpenCV | Image preprocessing |
| YOLOv8 (Ultralytics) | Plate region detection |
| python-dotenv | Environment variable loading |
| Celery + Redis | Background tasks |

### Frontend
| Package | Purpose |
|---|---|
| React + Vite | UI framework and build tool |
| Tailwind CSS v4 | Styling (via `@tailwindcss/vite` plugin) |
| React Router v6 | Navigation |
| TanStack Query | Server state and caching |
| Zustand | Global state management |
| Axios | HTTP client |
| react-webcam | Camera access on mobile |
| Lucide React | Icons |
| Sonner | Toast notifications |
| date-fns | Date formatting and manipulation |
| React Hook Form + Zod | Forms and validation |
| TanStack Table | Data tables |

### Infrastructure
| Tool | Purpose |
|---|---|
| PostgreSQL | Main database (installed locally, must be running as service) |
| Redis | Caching and task queue (Memurai on Windows, installed locally) |

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
│   │   │   └── users.js
│   │   ├── components/
│   │   │   ├── Auth/
│   │   │   │   └── ProtectedRoute.jsx
│   │   │   └── Layout/
│   │   │       ├── AdminLayout.jsx
│   │   │       ├── SecurityLayout.jsx
│   │   │       └── OwnerLayout.jsx
│   │   ├── hooks/
│   │   │   └── useScanStream.js
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
│   │   └── urls.py
│   ├── accounts/                    # Users and roles
│   ├── vehicles/                    # Vehicles and owners
│   ├── scanning/                    # Plate scanning, access logs, visitor passes
│   │   ├── ml/
│   │   │   ├── reader.py            # YOLO + EasyOCR inference pipeline
│   │   │   ├── train.py             # YOLOv8 training (offline + incremental)
│   │   │   ├── validator.py         # Philippine plate regex validation
│   │   │   └── collector.py         # Auto-collect scan data for retraining
│   │   ├── tasks.py                 # Celery task: ML retrain job
│   │   ├── views.py                 # Scan + ML sample/retrain endpoints
│   │   ├── models.py                # AccessLog, VisitorPass, MLTrainingSample
│   │   └── serializers.py           # DRF serializers including ML sample
│   ├── violations/                  # Violation tracking
│   ├── manage.py
│   ├── requirements.txt
│   └── .env.example
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
| PostgreSQL | 16 | https://www.postgresql.org/download |
| Redis | Latest | [Memurai](https://www.memurai.com) (Windows) or https://redis.io/downloads |

> ⚠️ **Use Python 3.11 specifically.** Python 3.12+ may cause issues with EasyOCR and OpenCV.
> ⚠️ **PostgreSQL must be running** before starting the backend. Ensure the PostgreSQL service is started via Services or `pg_ctl`.

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

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

Open both `.env` files and fill in your values.

### 3. Set Up PostgreSQL

Ensure PostgreSQL service is running (check Services app → PostgreSQL → Running), then create the database:

```bash
# Open psql as the postgres superuser
psql -U postgres

# Inside psql, create the database
CREATE DATABASE plate_db;
\q
```

Update `backend/.env` with your local PostgreSQL credentials (`DB_USER`, `DB_PASSWORD`, etc.).

### 4. Set Up Redis

Start Redis locally. On Windows, **Memurai** (a native Redis-compatible server) is recommended and starts automatically as a service after installation:

```bash
# WSL / Linux / Mac
redis-server

# Windows with Memurai — ensure the service is running
# Check: Services app → Memurai → Running
# If not running: Services app → Memurai → Start
```

### 5. Set Up the Backend

```bash
cd backend

# Create virtual environment
py -m venv venv

# Activate (Windows)
venv\Scripts\activate

# Activate (Mac/Linux)
# source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run migrations per app
python manage.py makemigrations accounts
python manage.py makemigrations vehicles
python manage.py makemigrations scanning
python manage.py makemigrations violations
python manage.py migrate

# Create admin user
python manage.py createsuperuser
```

### 6. Set Up the Frontend

```bash
cd frontend
npm install
```

---

## Environment Variables

### `backend/.env`

| Variable | Description | Example |
|---|---|---|
| `SECRET_KEY` | Django secret key — keep private | `your-secret-key` |
| `DEBUG` | Debug mode | `True` |
| `ALLOWED_HOSTS` | Comma-separated allowed hosts | `localhost,127.0.0.1` |
| `DB_NAME` | PostgreSQL database name | `plate_db` |
| `DB_USER` | PostgreSQL user | `postgres` |
| `DB_PASSWORD` | PostgreSQL password | `password` |
| `DB_HOST` | PostgreSQL host | `127.0.0.1` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `CORS_ALLOWED_ORIGINS` | Allowed frontend origins | `http://localhost:5173` |
| `EMAIL_HOST_USER` | Gmail email for sending emails | `your-email@gmail.com` |
| `EMAIL_HOST_PASSWORD` | Gmail app password | `your-app-password` |
| `FRONTEND_URL` | Frontend URL for emails | `http://localhost:5173` |
| `BACKEND_URL` | Backend URL for emails | `http://localhost:8000` |
| `ACCESS_TOKEN_LIFETIME_MINUTES` | JWT access token expiry | `60` |
| `REFRESH_TOKEN_LIFETIME_DAYS` | JWT refresh token expiry | `7` |
| `CELERY_BROKER_URL` | Redis broker URL | `redis://127.0.0.1:6379/0` |
| `CELERY_RESULT_BACKEND` | Redis result backend URL | `redis://127.0.0.1:6379/0` |
| `ML_SAMPLE_BATCH_SIZE` | New samples needed to trigger retraining | `50` |
| `ML_CONFIDENCE_THRESHOLD` | Min confidence to auto-label a sample | `0.6` |
| `ML_AUTO_RETRAIN_ENABLED` | Enable/disable automatic retraining | `true` |

### `frontend/.env`

| Variable | Description | Example |
|---|---|---|
| `VITE_API_BASE_URL` | Django backend URL | `http://localhost:8000` |

> ⚠️ **Never commit `.env` files.** Only `.env.example` files are tracked in Git.

---

## Running the Project

> **Prerequisites:** Ensure PostgreSQL and Redis (Memurai) services are running before starting the backend.

Open **three terminals** in VS Code (`` Ctrl+` `` to open terminal, click the split icon to add more).

### Terminal 1 — Backend (Django + Daphne)

Daphne is used as the ASGI server to support WebSocket connections for real-time scanning:

```bash
cd backend
venv\Scripts\activate
daphne -b 127.0.0.1 -p 8000 config.asgi:application
```

> **Note:** For development without WebSocket features, you can use Django's built-in server:
> ```bash
> python manage.py runserver
> ```

| URL | Description |
|---|---|
| `http://localhost:8000/api/` | Django REST API |
| `http://localhost:8000/admin/` | Admin panel (login with superuser) |
| `http://localhost:8000/api/auth/login/` | Get JWT tokens |

### Terminal 2 — Frontend

```bash
cd frontend
npm run dev
```

| URL | Description |
|---|---|
| `http://localhost:5173` | React web app |

### Terminal 3 — Celery Worker (required for ML retraining)

```bash
cd backend
venv\Scripts\activate
python -m celery -A config worker -l info --pool=solo
```

> Redis (Memurai) must be running before starting Celery. On Windows, `--pool=solo` is required to avoid `billiard` semaphore / fork errors.

---

## API Endpoints

### Auth
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `POST` | `/api/auth/login/` | Login — returns access + refresh token | ❌ |
| `POST` | `/api/auth/refresh/` | Refresh access token | ❌ |
| `POST` | `/api/auth/verify/` | Verify token | ❌ |
| `GET` | `/api/accounts/me/` | Get current logged-in user | ✅ |
| `POST` | `/api/accounts/register/` | Create new user (admin only) | ✅ Admin |

### User Management
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/accounts/users/` | List all users (non-admin) | ✅ Admin |
| `GET` | `/api/accounts/users/{id}/` | Get user details | ✅ Admin |
| `PATCH` | `/api/accounts/users/{id}/update/` | Update user | ✅ Admin |
| `DELETE` | `/api/accounts/users/{id}/delete/` | Delete user | ✅ Admin |
| `POST` | `/api/accounts/users/{id}/toggle-status/` | Enable/disable user | ✅ Admin |
| `POST` | `/api/accounts/replace-admin/` | Replace current admin | ✅ Admin |
| `GET` | `/api/accounts/audit-logs/` | List audit logs | ✅ |
| `GET` | `/api/accounts/audit-logs/stats/` | Audit log statistics | ✅ Admin |

### Vehicles and Owners
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/vehicles/` | List all vehicles | ✅ |
| `POST` | `/api/vehicles/` | Register new vehicle | ✅ Admin |
| `GET` | `/api/vehicles/{id}/` | Get vehicle by ID | ✅ |
| `PATCH` | `/api/vehicles/{id}/authorize/` | Toggle entry authorization | ✅ Admin |
| `GET` | `/api/vehicles/owners/` | List all owners | ✅ Admin |
| `POST` | `/api/vehicles/owners/` | Register new owner | ✅ Admin |
| `GET` | `/api/vehicles/rules/` | List entry rules | ✅ Admin |
| `POST` | `/api/vehicles/rules/` | Create rule | ✅ Admin |
| `GET` | `/api/vehicles/vehicle-types/` | List vehicle type access rules | ✅ Admin |
| `POST` | `/api/vehicles/vehicle-types/` | Create vehicle type rule | ✅ Admin |

### Secure Registration
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `POST` | `/api/vehicles/tokens/generate/` | Generate a new registration token | ✅ Admin |
| `GET` | `/api/vehicles/tokens/` | List all registration tokens | ✅ Admin |
| `DELETE` | `/api/vehicles/tokens/{id}/` | Delete a registration token | ✅ Admin |
| `POST` | `/api/vehicles/tokens/{id}/toggle/` | Enable/disable registration token | ✅ Admin |
| `DELETE` | `/api/vehicles/tokens/clear/` | Clear used/expired tokens | ✅ Admin |
| `GET` | `/api/vehicles/register/validate-token/{token}/` | Validate a public token | ❌ |
| `POST` | `/api/vehicles/register/submit/` | Submit vehicle registration application | ❌ |
| `GET` | `/api/vehicles/registrations/pending/` | List pending registrations | ✅ Admin |
| `POST` | `/api/vehicles/registrations/{id}/accept/` | Accept registration and create vehicle/owner | ✅ Admin |
| `POST` | `/api/vehicles/registrations/{id}/reject/` | Reject registration application | ✅ Admin |

### Scanning
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `POST` | `/api/scan/` | Scan plate image — returns entry decision | ✅ |
| `GET` | `/api/scan/logs/` | List recent access logs | ✅ |
| `GET` | `/api/scan/offices/` | List all offices | ✅ |
| `GET` | `/api/scan/visitor-pass/` | List today's visitor passes | ✅ |
| `POST` | `/api/scan/visitor-pass/` | Create visitor pass at gate | ✅ |
| `PATCH` | `/api/scan/visitor-pass/{id}/` | Confirm or reject visitor pass | ✅ |

### ML Training & Feedback
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/scan/ml/samples/` | List collected training samples | ✅ |
| `PATCH` | `/api/scan/ml/samples/{id}/` | Approve/reject/correct a sample label | ✅ |
| `GET` | `/api/scan/ml/stats/` | Dashboard stats for sample collection | ✅ |
| `POST` | `/api/scan/ml/retrain/` | Manually trigger an incremental retrain | ✅ |

### Violations
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/violations/` | List all violations | ✅ |
| `POST` | `/api/violations/` | Add a violation | ✅ |
| `PATCH` | `/api/violations/{id}/` | Update or resolve violation | ✅ |

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

## Git Workflow

### Branch Structure
```
main          ← production only, never push directly
│
└── dev       ← integration branch, all PRs merge here
    ├── feat/plate-scanner
    ├── feat/auth
    ├── feat/vehicle-crud
    ├── feat/visitor-pass
    ├── feat/violations
    └── feat/mobile-scan-ui
```

> **Rule:** Nobody pushes directly to `main` or `dev`. Everything goes through a Pull Request.

---

## User Roles

| Role | Access |
|---|---|
| **Admin** | Full system access — manage users, vehicles, owners, rules, tokens, violations, audits |
| **Security** | Scan plates, view logs, manage visitor passes, view own statistics |
| **Vehicle Owner** | View own registered vehicles, history, and entry status |

### Demo Credentials

| Role | Email | Password |
|---|---|---|
| Admin | admin@example.com | admin123 |
| Security | juan@slc.edu | security123 |
| Vehicle Owner | ana@slc.edu | owner123 |

> **Note:** Use these credentials to log in at `http://localhost:5173`. Change passwords after initial setup.

---

## Team

| Name | Role |
|---|---|
| | ML / Plate Recognition |
| | Backend / API |
| | Frontend / Mobile UI |
| | Database / DevOps |

---

## License

For internal use only. © 2026 Saint Louis College.