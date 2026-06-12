# Vehicle Management System — Frontend

React + Vite frontend for the Saint Louis College Vehicle Management System.

## Features

- **Multi-role dashboard** — Admin, Security, and Vehicle Owner views
- **Mobile camera scanning** — Real-time license plate detection
- **Visitor pass management** — Create and track visitor entries
- **Vehicle registration** — Self-service registration with token validation
- **Access logs** — View scan history and entry decisions
- **Rule management** — Configure entry schedules and vehicle type restrictions

## Tech Stack

- React 19 with Vite 8
- Tailwind CSS v4
- TanStack Query + Zustand for state
- React Hook Form + Zod for validation

## Getting Started

```bash
cd frontend
npm install
```

Create `.env`:
```bash
cp .env.example .env
# Edit VITE_API_BASE_URL if backend runs on different port
```

Run development server:
```bash
npm run dev
```

## Environment Variables

| Variable | Description |
|---|---|
| `VITE_API_BASE_URL` | Django backend URL (default: `http://localhost:8000`) |