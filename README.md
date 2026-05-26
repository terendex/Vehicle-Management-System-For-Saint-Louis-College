# Vehicle-Management-System-For-Saint-Louis-College

A smart vehicle entry management system using AI-powered license plate recognition, built for Philippine plate formats.

---

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
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

This system automates vehicle entry management by scanning and recognizing Philippine license plates via camera. It cross-references plates against a database to authorize or deny entry, and provides a mobile-friendly web interface for guards to manually scan plates and retrieve vehicle owner information.

---

## Features

- 📷 **Camera-based plate scanning** — automatic detection at entry gates
- 🤖 **ML plate recognition** — YOLOv8 detection + EasyOCR text extraction
- 🇵🇭 **Philippine plate validation** — supports all standard PH plate formats
- ✅ **Entry authorization** — instant allow/deny based on registered plates
- 📱 **Mobile web scanner** — guards can scan plates from any device
- 👤 **Owner lookup** — returns full vehicle and owner info on scan
- ⚠️ **Violation tracking** — flags vehicles with unresolved violations
- 🔐 **Role-based access** — Guard, Supervisor, and Admin roles
- 📋 **Access logs** — full history of every scan and entry attempt

---

## Tech Stack

### Frontend
| Package | Purpose |
|---|---|
| React + Vite | UI framework and build tool |
| Tailwind CSS | Styling |
| React Router v6 | Navigation |
| TanStack Query | Server state and caching |
| Zustand | Global state management |
| Axios | HTTP client |
| react-webcam | Camera access on mobile |
| Lucide React | Icons |
| Sonner | Toast notifications |

### Backend
| Package | Purpose |
|---|---|
| FastAPI | REST API framework |
| SQLAlchemy + Alembic | ORM and database migrations |
| PostgreSQL | Main database |
| Redis | Caching |
| EasyOCR | Plate text extraction |
| OpenCV | Image preprocessing |
| YOLOv8 (Ultralytics) | Plate region detection |
| PyJWT + Passlib | Authentication and security |

### Infrastructure
| Tool | Purpose |
|---|---|
| Docker + Docker Compose | Containerization |
| Nginx | Reverse proxy |

---

## Project Structure

```
plate-system/
├── frontend/                  # React (Vite)
│   └── src/
│       ├── api/               # Axios client and API calls
│       ├── components/        # Reusable UI components
│       ├── pages/             # Route-level pages
│       └── store/             # Zustand global state
│
├── backend/                   # Python (FastAPI)
│   └── app/
│       ├── api/routes/        # scan, auth, vehicles, violations
│       ├── core/              # config, security helpers
│       ├── db/                # models, schemas, session
│       └── ml/                # detector, reader, validator
│
├── .vscode/                   # Shared editor settings
├── docker-compose.yml
└── README.md
```

---

## Prerequisites

Make sure these are installed before starting:

| Tool | Version | Download |
|---|---|---|
| Git | Latest | https://git-scm.com |
| VS Code | Latest | https://code.visualstudio.com |
| Node.js | 20+ | https://nodejs.org |
| Python | 3.11+ | https://python.org |
| Docker Desktop | Latest | https://docker.com/products/docker-desktop |
| PostgreSQL | 16 | via Docker (see below) |

---

## Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/your-org/plate-system.git
cd plate-system
```

### 2. Open in VS Code

```bash
code .
```

> VS Code will prompt **"Install recommended extensions?"** — click **Yes**

### 3. Start Database and Redis

```bash
docker compose up db redis -d
```

### 4. Set Up the Backend

```bash
cd backend

# Create and activate virtual environment
python -m venv venv

# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run database migrations
python manage.py run migrations

# Seed test data (optional)
python seed.py
```

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
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://user:password@localhost:5432/plate_db` |
| `SECRET_KEY` | JWT signing secret (keep private) | `your-secret-key-here` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379` |
| `ALLOWED_ORIGINS` | Allowed frontend URLs (comma-separated) | `http://localhost:5173` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT expiry duration | `60` |

### `frontend/.env`

| Variable | Description | Example |
|---|---|---|
| `VITE_API_BASE_URL` | Backend API base URL | `http://localhost:8000` |

> ⚠️ **Never commit `.env` files.** Only `.env.example` files are tracked in Git.

---

## Running the Project

Open **two terminals** in VS Code (`Ctrl+\``)

### Terminal 1 — Backend

```bash
cd backend
source venv/bin/activate        # Windows: venv\Scripts\activate
uvicorn app.main:app --reload --port 8000
```

API will be available at: `http://localhost:8000`  
Interactive API docs: `http://localhost:8000/docs`

### Terminal 2 — Frontend

```bash
cd frontend
npm run dev
```

Web app will be available at: `http://localhost:5173`

---

## API Endpoints

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `POST` | `/api/auth/login` | Login and get token | ❌ |
| `POST` | `/api/scan/` | Scan a plate image | ✅ |
| `GET` | `/api/vehicles/` | List all vehicles | ✅ |
| `GET` | `/api/vehicles/{plate}` | Get vehicle by plate | ✅ |
| `POST` | `/api/vehicles/` | Register new vehicle | ✅ Admin |
| `PATCH` | `/api/vehicles/{plate}/authorize` | Toggle authorization | ✅ Admin |
| `GET` | `/api/violations/` | List violations | ✅ |
| `POST` | `/api/violations/` | Add a violation | ✅ |

> Full interactive docs available at `/docs` when the backend is running.

---

## Git Workflow

### Branch Naming

```
feat/short-description     # new feature
fix/short-description      # bug fix
chore/short-description    # config, deps, tooling
docs/short-description     # documentation only
```

### Commit Message Format

```
feat(scope): short description
fix(scope): short description
chore(scope): short description
docs(scope): short description

# Examples:
feat(ml): add YOLOv8 plate region detector
fix(ocr): handle blurry image edge case
feat(ui): add mobile scan page
chore(deps): update easyocr version
```

### Daily Workflow

```bash
# 1. Always pull latest dev first
git checkout dev
git pull origin dev

# 2. Create your feature branch
git checkout -b feat/your-feature

# 3. Write your code, then stage and commit
git add .
git commit -m "feat(scope): what you did"

# 4. Push your branch
git push origin feat/your-feature

# 5. Open a Pull Request into dev on GitHub
```

### Branch Structure

```
main          ← production only, never push directly
│
└── dev       ← integration branch, all PRs go here
    │
    ├── feat/plate-scanner
    ├── feat/auth
    ├── feat/vehicle-crud
    ├── feat/violations
    ├── feat/mobile-scan-ui
    └── feat/gate-integration
```

> **Rule:** Nobody pushes directly to `main` or `dev`. Everything goes through a Pull Request.

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

For internal use only. © 2025 Your Organization.