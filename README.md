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
| PostgreSQL | Main database (installed locally) |
| Redis | Caching and task queue (installed locally) |

---

## Project Structure

```
Vehicle-Management-System-For-Saint-Louis-College/
│
├── frontend/                        # React (Vite)
│   ├── src/
│   │   ├── api/
│   │   │   └── client.js            # Axios instance
│   │   ├── components/              # Reusable UI components
│   │   ├── pages/
│   │   │   ├── LoginPage.jsx
│   │   │   ├── ScanPage.jsx         # Mobile camera scanner
│   │   │   ├── DashboardPage.jsx
│   │   │   └── VehiclesPage.jsx
│   │   ├── stores/
│   │   │   └── authStore.js         # Zustand auth state
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
│   │   └── ml/
│   │       ├── reader.py            # EasyOCR
│   │       ├── detector.py          # YOLOv8
│   │       └── validator.py         # PH plate regex
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
| Redis | Latest | https://redis.io/downloads (Windows: use [Memurai](https://www.memurai.com) or WSL) |

> ⚠️ **Use Python 3.11 specifically.** Python 3.12+ may cause issues with EasyOCR and OpenCV.

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

Make sure PostgreSQL is running locally, then create the database:

```bash
# Open psql as the postgres superuser
psql -U postgres

# Inside psql, create the database
CREATE DATABASE plate_db;
\q
```

Update `backend/.env` with your local PostgreSQL credentials (`DB_USER`, `DB_PASSWORD`, etc.).

### 4. Set Up Redis

Start Redis locally. On Windows, use **Memurai** (a native Redis-compatible server) or run Redis via **WSL**:

```bash
# WSL / Linux / Mac
redis-server

# Or if using Memurai on Windows, it starts automatically as a service
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
| `DB_NAME` | PostgreSQL database name | `plate_db` |
| `DB_USER` | PostgreSQL user | `postgres` |
| `DB_PASSWORD` | PostgreSQL password | `password` |
| `DB_HOST` | PostgreSQL host | `localhost` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `ALLOWED_HOSTS` | Comma-separated allowed hosts | `localhost,127.0.0.1` |
| `CORS_ALLOWED_ORIGINS` | Allowed frontend origins | `http://localhost:5173` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379` |
| `ACCESS_TOKEN_LIFETIME_MINUTES` | JWT access token expiry | `60` |
| `REFRESH_TOKEN_LIFETIME_DAYS` | JWT refresh token expiry | `7` |

### `frontend/.env`

| Variable | Description | Example |
|---|---|---|
| `VITE_API_BASE_URL` | Django backend URL | `http://localhost:8000` |

> ⚠️ **Never commit `.env` files.** Only `.env.example` files are tracked in Git.

---

## Running the Project

Open **three terminals** in VS Code (`` Ctrl+` `` to open terminal, click the split icon to add more).

### Terminal 1 — Backend

```bash
cd backend
venv\Scripts\activate
python manage.py runserver
```

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

### Terminal 3 — Celery Worker (optional, for background tasks)

```bash
cd backend
venv\Scripts\activate
celery -A config worker --loglevel=info
```

---

## API Endpoints

### Auth
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `POST` | `/api/auth/login/` | Login — returns access + refresh token | ❌ |
| `POST` | `/api/auth/refresh/` | Refresh access token | ❌ |
| `GET` | `/api/accounts/me/` | Get current logged-in user | ✅ |
| `POST` | `/api/accounts/register/` | Create new user (admin only) | ✅ Admin |

### Vehicles and Owners
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/vehicles/` | List all vehicles | ✅ |
| `POST` | `/api/vehicles/` | Register new vehicle | ✅ |
| `GET` | `/api/vehicles/{id}/` | Get vehicle by ID | ✅ |
| `PATCH` | `/api/vehicles/{id}/authorize/` | Toggle entry authorization | ✅ |
| `GET` | `/api/vehicles/owners/` | List all owners | ✅ |
| `POST` | `/api/vehicles/owners/` | Register new owner | ✅ |

### Secure Registration
| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `POST` | `/api/vehicles/tokens/generate/` | Generate a new registration token | ✅ Admin |
| `GET` | `/api/vehicles/tokens/` | List all registration tokens | ✅ Admin |
| `POST` | `/api/vehicles/tokens/{id}/toggle/` | Enable/disable registration token | ✅ Admin |
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
| **Security** | Scan plates, view logs, basic access management |
| **Vehicle Owner** | View own registered vehicles, history, and status |
| **Admin** | Full access — manage users, vehicles, owners, rules, settings |

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